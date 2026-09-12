/* json.c — JSON parser and writer for the Gate E native host.
 *
 * The parser preserves the int/float/bool/null distinction (matching
 * json.loads): numbers with '.', 'e', or 'E' become floats, others int64.
 * Objects become insertion-ordered maps, arrays become lists.
 *
 * The writer serializes raw V* structures (maps -> objects, lists -> arrays).
 * Floats use Python repr formatting (py_float_repr), matching json.dumps.
 */
#include "value.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

/* ------------------------------------------------------------------ */
/* parser                                                              */
/* ------------------------------------------------------------------ */

typedef struct {
    const char* p;
    const char* end;
    const char* err;
    int depth;
} JP;

static void jp_skip_ws(JP* j) {
    while (j->p < j->end) {
        char c = *j->p;
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') j->p++;
        else break;
    }
}

static V* jp_value(JP* j);

static void utf8_encode(char** out, unsigned int cp) {
    if (cp < 0x80) {
        *(*out)++ = (char)cp;
    } else if (cp < 0x800) {
        *(*out)++ = (char)(0xC0 | (cp >> 6));
        *(*out)++ = (char)(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
        *(*out)++ = (char)(0xE0 | (cp >> 12));
        *(*out)++ = (char)(0x80 | ((cp >> 6) & 0x3F));
        *(*out)++ = (char)(0x80 | (cp & 0x3F));
    } else {
        *(*out)++ = (char)(0xF0 | (cp >> 18));
        *(*out)++ = (char)(0x80 | ((cp >> 6) & 0x3F));
        *(*out)++ = (char)(0x80 | ((cp >> 12) & 0x3F));
        *(*out)++ = (char)(0x80 | (cp & 0x3F));
    }
}

static unsigned int hex4(JP* j) {
    unsigned int v = 0;
    for (int i = 0; i < 4; i++) {
        if (j->p >= j->end) { j->err = "unterminated escape"; return 0; }
        char c = *j->p++;
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
        else { j->err = "invalid escape"; return 0; }
    }
    return v;
}

/* Parses a JSON string literal (cursor at the opening quote). */
static V* jp_string(JP* j) {
    if (j->p >= j->end || *j->p != '"') { j->err = "expected string"; return NULL; }
    j->p++;
    const char* q = j->p;   /* scan for the closing quote */
    while (q < j->end && *q != '"') {
        if (*q == '\\') {
            q++;
            if (q >= j->end) { j->err = "unterminated escape"; return NULL; }
        }
        q++;
    }
    if (q >= j->end) { j->err = "unterminated string"; return NULL; }
    int64_t span = q - j->p;
    char* buf = (char*)malloc((size_t)span * 4 + 4);
    if (!buf) { j->err = "out of memory"; return NULL; }
    char* o = buf;
    while (j->p < q) {
        char c = *j->p;
        if (c != '\\') { *o++ = c; j->p++; continue; }
        j->p++;
        char e = *j->p++;
        switch (e) {
        case '"': *o++ = '"'; break;
        case '\\': *o++ = '\\'; break;
        case '/': *o++ = '/'; break;
        case 'b': *o++ = '\b'; break;
        case 'f': *o++ = '\f'; break;
        case 'n': *o++ = '\n'; break;
        case 'r': *o++ = '\r'; break;
        case 't': *o++ = '\t'; break;
        case 'u': {
            unsigned int cp = hex4(j);
            if (j->err) { free(buf); return NULL; }
            if (cp >= 0xD800 && cp <= 0xDBFF && q - j->p >= 6 &&
                j->p[0] == '\\' && j->p[1] == 'u') {
                j->p += 2;
                unsigned int lo = hex4(j);
                if (j->err) { free(buf); return NULL; }
                if (lo >= 0xDC00 && lo <= 0xDFFF) {
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                } else {
                    utf8_encode(&o, cp);
                    cp = lo;
                }
            }
            utf8_encode(&o, cp);
            break;
        }
        default:
            free(buf);
            j->err = "invalid escape";
            return NULL;
        }
    }
    j->p = q + 1;   /* past closing quote */
    V* v = rt_str_copy(buf, o - buf);
    free(buf);
    return v;
}

static V* jp_number(JP* j) {
    const char* start = j->p;
    if (j->p < j->end && *j->p == '-') j->p++;
    while (j->p < j->end && *j->p >= '0' && *j->p <= '9') j->p++;
    int is_float = 0;
    if (j->p < j->end && *j->p == '.') {
        is_float = 1;
        j->p++;
        while (j->p < j->end && *j->p >= '0' && *j->p <= '9') j->p++;
    }
    if (j->p < j->end && (*j->p == 'e' || *j->p == 'E')) {
        is_float = 1;
        j->p++;
        if (j->p < j->end && (*j->p == '+' || *j->p == '-')) j->p++;
        while (j->p < j->end && *j->p >= '0' && *j->p <= '9') j->p++;
    }
    char buf[64];
    int64_t n = j->p - start;
    if (n >= 63) { j->err = "number too long"; return NULL; }
    memcpy(buf, start, (size_t)n);
    buf[n] = 0;
    if (!is_float) {
        errno = 0;
        long long v = strtoll(buf, NULL, 10);
        if (errno == ERANGE) { j->err = "integer constant out of range"; return NULL; }
        return rt_int((int64_t)v);
    }
    return rt_float(strtod(buf, NULL));
}

static V* jp_value(JP* j) {
    if (++j->depth > 500) { j->err = "JSON nesting too deep"; return NULL; }
    jp_skip_ws(j);
    if (j->p >= j->end) { j->err = "unexpected end of JSON"; return NULL; }
    char c = *j->p;
    if (c == '{') {
        j->p++;
        V* m = rt_new_map();
        jp_skip_ws(j);
        if (j->p < j->end && *j->p == '}') { j->p++; j->depth--; return m; }
        for (;;) {
            jp_skip_ws(j);
            V* key = jp_string(j);
            if (j->err) { rt_decref(key); rt_decref(m); return NULL; }
            jp_skip_ws(j);
            if (j->p >= j->end || *j->p != ':') {
                j->err = "expected ':'";
                rt_decref(key); rt_decref(m);
                return NULL;
            }
            j->p++;
            V* val = jp_value(j);
            if (!val) { rt_decref(key); rt_decref(m); return NULL; }
            rt_map_set_raw(m, key, val);   /* takes ownership of both */
            jp_skip_ws(j);
            if (j->p < j->end && *j->p == ',') { j->p++; continue; }
            if (j->p < j->end && *j->p == '}') { j->p++; break; }
            j->err = "expected ',' or '}'";
            rt_decref(m);
            j->depth--;
            return NULL;
        }
        j->depth--;
        return m;
    }
    if (c == '[') {
        j->p++;
        V* lst = rt_new_list();
        jp_skip_ws(j);
        if (j->p < j->end && *j->p == ']') { j->p++; j->depth--; return lst; }
        for (;;) {
            V* val = jp_value(j);
            if (!val) { rt_decref(lst); return NULL; }
            rt_list_append(lst, val);
            jp_skip_ws(j);
            if (j->p < j->end && *j->p == ',') { j->p++; continue; }
            if (j->p < j->end && *j->p == ']') { j->p++; break; }
            j->err = "expected ',' or ']'";
            rt_decref(lst);
            j->depth--;
            return NULL;
        }
        j->depth--;
        return lst;
    }
    if (c == '"') { V* v = jp_string(j); j->depth--; return v; }
    if (c == 't') {
        if (j->end - j->p >= 4 && memcmp(j->p, "true", 4) == 0) { j->p += 4; j->depth--; return rt_bool(1); }
        j->err = "invalid literal"; return NULL;
    }
    if (c == 'f') {
        if (j->end - j->p >= 5 && memcmp(j->p, "false", 5) == 0) { j->p += 5; j->depth--; return rt_bool(0); }
        j->err = "invalid literal"; return NULL;
    }
    if (c == 'n') {
        if (j->end - j->p >= 4 && memcmp(j->p, "null", 4) == 0) { j->p += 4; j->depth--; return rt_null(); }
        j->err = "invalid literal"; return NULL;
    }
    if (c == '-' || (c >= '0' && c <= '9')) {
        V* v = jp_number(j);
        j->depth--;
        return v;
    }
    j->err = "unexpected character";
    return NULL;
}

V* json_parse(const char* text, int64_t len, const char** err) {
    JP j;
    j.p = text;
    j.end = text + len;
    j.err = NULL;
    j.depth = 0;
    V* v = jp_value(&j);
    if (v) {
        jp_skip_ws(&j);
        if (j.p != j.end) {
            rt_decref(v);
            v = NULL;
            if (!j.err) j.err = "trailing data after JSON";
        }
    }
    if (!v && err) *err = j.err ? j.err : "JSON parse error";
    if (v && err) *err = NULL;
    return v;
}

/* ------------------------------------------------------------------ */
/* writer                                                              */
/* ------------------------------------------------------------------ */

static void write_escape(const char* s, int64_t n,
                         void (*emit)(const char*, int64_t, void*), void* ud) {
    char tmp[8];
    emit("\"", 1, ud);
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
        case '"': emit("\\\"", 2, ud); break;
        case '\\': emit("\\\\", 2, ud); break;
        case '\n': emit("\\n", 2, ud); break;
        case '\r': emit("\\r", 2, ud); break;
        case '\t': emit("\\t", 2, ud); break;
        case '\b': emit("\\b", 2, ud); break;
        case '\f': emit("\\f", 2, ud); break;
        default:
            if (c < 0x20) {
                snprintf(tmp, sizeof tmp, "\\u%04x", c);
                emit(tmp, 6, ud);
            } else {
                emit((const char*)&s[i], 1, ud);
            }
        }
    }
    emit("\"", 1, ud);
}

static void json_write_v(V* v, void (*emit)(const char*, int64_t, void*), void* ud, int depth) {
    char tmp[64];
    if (depth > 200) { emit("null", 4, ud); return; }
    switch (v->tag) {
    case T_NULL: emit("null", 4, ud); break;
    case T_BOOL: emit(v->u.b ? "true" : "false", v->u.b ? 4 : 5, ud); break;
    case T_INT: {
        snprintf(tmp, sizeof tmp, "%lld", (long long)v->u.i);
        emit(tmp, (int64_t)strlen(tmp), ud);
        break;
    }
    case T_FLOAT: {
        int64_t flen;
        char* s = py_float_repr(v->u.f, &flen);
        emit(s, flen, ud);
        free(s);
        break;
    }
    case T_STR:
        write_escape(v->u.str.s, v->u.str.len, emit, ud);
        break;
    case T_LIST:
        emit("[", 1, ud);
        for (int64_t i = 0; i < v->u.list.n; i++) {
            if (i) emit(",", 1, ud);
            json_write_v(v->u.list.a[i], emit, ud, depth + 1);
        }
        emit("]", 1, ud);
        break;
    case T_MAP: {
        emit("{", 1, ud);
        int first = 1;
        for (MapEntry* e = v->u.m->head; e; e = e->inext) {
            if (!first) emit(",", 1, ud);
            first = 0;
            if (e->key->tag != T_STR) { emit("null", 4, ud); continue; }
            write_escape(e->key->u.str.s, e->key->u.str.len, emit, ud);
            emit(":", 1, ud);
            json_write_v(e->val, emit, ud, depth + 1);
        }
        emit("}", 1, ud);
        break;
    }
    }
}

void json_write(V* v, void (*emit)(const char*, int64_t, void*), void* ud) {
    json_write_v(v, emit, ud, 0);
}