/* main.c — Gate E native host entry point.
 *
 * Modes:
 *   osakavm <doc.sbc> [guest-args...]            CLI: run main, print stdout
 *   osakavm run <doc.sbc> <args.json>            envelope JSON on stdout
 *   osakavm call <doc.sbc> <fname> <args.json>   envelope JSON on stdout
 *
 * The loader implements the canonical SBC1 schema validation from
 * docs/NATIVE_HOST_CONTRACT.md §6 (sbc._program_from_canonical semantics;
 * the legacy pre-contract schema is deliberately not supported).
 */
#include "value.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

static char* read_file_bytes(const char* path, int64_t* out_len) {
    FILE* f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "osakavm: cannot open %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    rewind(f);
    char* buf = (char*)malloc((size_t)size + 1);
    if (!buf) { fprintf(stderr, "osakavm: out of memory\n"); exit(1); }
    size_t got = fread(buf, 1, (size_t)size, f);
    fclose(f);
    buf[got] = 0;
    *out_len = (int64_t)got;
    return buf;
}

/* map lookup by C-string key (borrowed) */
static V* map_get_cstr(V* mapval, const char* key) {
    if (mapval->tag != T_MAP) return NULL;
    V* k = rt_lit(key);
    uint64_t h = rt_hash(k);
    MapEntry* e = mapval->u.m->b[h & (uint64_t)(mapval->u.m->nb - 1)];
    while (e) {
        if (e->h == h && rt_veq(e->key, k)) return e->val;
        e = e->bnext;
    }
    return NULL;
}

static int map_has_exact_keys(V* mapval, const char** keys, int n) {
    if (mapval->tag != T_MAP) return 0;
    if (mapval->u.m->n != n) return 0;
    for (int i = 0; i < n; i++) {
        if (!map_get_cstr(mapval, keys[i])) return 0;
    }
    return 1;
}

/* Soft-loading mode: loader_error records the message and longjmps back to
 * load_document_soft instead of exiting (used by __sbc_load__, contract §9). */
static int g_soft_load = 0;
static char g_load_err[512];
static jmp_buf g_load_jmp;

static void loader_error(const char* msg) {
    if (g_soft_load) {
        snprintf(g_load_err, sizeof g_load_err, "%s", msg);
        longjmp(g_load_jmp, 1);
    }
    fprintf(stderr, "%s\n", msg);
    exit(1);
}

/* ------------------------------------------------------------------ */
/* SBC1 document loading (contract §6)                                 */
/* ------------------------------------------------------------------ */

static const char* TOP_KEYS[] = {"constants", "format", "functions",
                                 "language_version", "main", "version"};
static const char* FN_KEYS[] = {"code", "constants", "params"};
static const char* INSTR_KEYS[] = {"arg", "line", "op"};
static const char* CONST_KEYS[] = {"data", "kind"};

static void validate_constant_instr(V* rec);

static void validate_constant(V* rec) {
    if (!map_has_exact_keys(rec, CONST_KEYS, 2))
        loader_error("SBC1 constants must contain exactly data and kind");
    V* kind = map_get_cstr(rec, "kind");
    if (!kind || kind->tag != T_STR ||
        (strcmp(kind->u.str.s, "truth") != 0 && strcmp(kind->u.str.s, "grain") != 0)) {
        loader_error("Invalid SBC1 value kind");
    }
    V* data = map_get_cstr(rec, "data");
    if (!data) loader_error("SBC1 constants must contain exactly data and kind");
    switch (data->tag) {
    case T_NULL: case T_BOOL: case T_INT: case T_STR: break;
    case T_FLOAT:
        if (!isfinite(data->u.f))
            loader_error("SBC1 constants must use finite floating-point values");
        break;
    default:
        loader_error("Unsupported SBC1 constant type");
    }
    /* intern the kind string so every kind in the system is a singleton.
     * NOTE: the lookup key is the map key "kind" — NOT the kind value. */
    V* singleton = strcmp(kind->u.str.s, "truth") == 0 ? K_TRUTH : K_GRAIN;
    if (kind != singleton) {
        V* k = rt_lit("kind");
        uint64_t h = rt_hash(k);
        MapEntry* e = rec->u.m->b[h & (uint64_t)(rec->u.m->nb - 1)];
        while (e) {
            if (e->h == h && rt_veq(e->key, k)) {
                rt_decref(e->val);
                e->val = singleton;
                break;
            }
            e = e->bnext;
        }
    }
}

static void validate_constant_instr(V* rec) {
    if (!map_has_exact_keys(rec, INSTR_KEYS, 3))
        loader_error("SBC1 instructions must contain exactly op, arg, and line");
    V* op = map_get_cstr(rec, "op");
    V* line = map_get_cstr(rec, "line");
    if (!op || op->tag != T_STR || !line || line->tag != T_INT)
        loader_error("SBC1 instruction op must be a string and line must be an integer");
}

static void validate_function_record(const char* name, V* rec) {
    (void)name;
    if (!map_has_exact_keys(rec, FN_KEYS, 3))
        loader_error("SBC1 function has invalid fields");
    V* params = map_get_cstr(rec, "params");
    if (!params || params->tag != T_LIST)
        loader_error("SBC1 function has invalid parameters");
    for (int64_t i = 0; i < params->u.list.n; i++) {
        if (params->u.list.a[i]->tag != T_STR)
            loader_error("SBC1 function has invalid parameters");
    }
    V* code = map_get_cstr(rec, "code");
    if (!code || code->tag != T_LIST) loader_error("SBC1 function has invalid fields");
    for (int64_t i = 0; i < code->u.list.n; i++)
        validate_constant_instr(code->u.list.a[i]);
    V* consts = map_get_cstr(rec, "constants");
    if (!consts || consts->tag != T_LIST) loader_error("SBC1 function has invalid fields");
    for (int64_t i = 0; i < consts->u.list.n; i++)
        validate_constant(consts->u.list.a[i]);
}

V* load_document(const char* text) {
    const char* jerr = NULL;
    V* doc = json_parse(text, (int64_t)strlen(text), &jerr);
    if (!doc) {
        char msg[256];
        snprintf(msg, sizeof msg, "SBC1 JSON parse error: %s", jerr ? jerr : "?");
        loader_error(msg);
    }
    if (!map_has_exact_keys(doc, TOP_KEYS, 6))
        loader_error("SBC1 document has missing or unknown top-level fields");
    V* format = map_get_cstr(doc, "format");
    V* version = map_get_cstr(doc, "version");
    V* langver = map_get_cstr(doc, "language_version");
    if (!format || format->tag != T_STR || strcmp(format->u.str.s, "SBC") != 0 ||
        !version || version->tag != T_INT || version->u.i != 1)
        loader_error("Unsupported SBC format or version");
    if (!langver || langver->tag != T_STR || strcmp(langver->u.str.s, "1.1") != 0) {
        static char msg[128];
        snprintf(msg, sizeof msg, "Unsupported Osaka language version: %s",
                 langver && langver->tag == T_STR ? langver->u.str.s : "?");
        loader_error(msg);
    }
    V* constants = map_get_cstr(doc, "constants");
    V* main = map_get_cstr(doc, "main");
    V* functions = map_get_cstr(doc, "functions");
    if (!constants || constants->tag != T_LIST || !main || main->tag != T_LIST)
        loader_error("SBC1 constants and main must be arrays");
    if (!functions || functions->tag != T_MAP)
        loader_error("SBC1 functions must be an object");
    for (int64_t i = 0; i < constants->u.list.n; i++)
        validate_constant(constants->u.list.a[i]);
    for (int64_t i = 0; i < main->u.list.n; i++)
        validate_constant_instr(main->u.list.a[i]);
    for (MapEntry* e = functions->u.m->head; e; e = e->inext) {
        if (e->key->tag != T_STR || e->val->tag != T_MAP)
            loader_error("SBC1 function names and records are invalid");
        validate_function_record(e->key->u.str.s, e->val);
    }
    return rt_pair(doc, K_TRUTH);
}

/* Load without exiting: returns NULL and sets *err_out on malformed input
 * (used by __sbc_load__, contract §9). */
static V* load_document_soft(const char* text, char** err_out) {
    g_soft_load = 1;
    if (setjmp(g_load_jmp)) {
        g_soft_load = 0;
        *err_out = g_load_err;
        return NULL;
    }
    V* doc = load_document(text);
    g_soft_load = 0;
    return doc;
}

/* ------------------------------------------------------------------ */
/* Stage-1 module loading: __sbc_load__ (contract §9.1)                */
/* ------------------------------------------------------------------ */

/* Python os.path.normpath for POSIX paths: collapses "//", "/./" and
 * "seg/.." (dropping ".." at the root of an absolute path). Empty in → ".". */
static void normpath_into(const char* in, char* out, size_t outsz) {
    size_t len = strlen(in);
    if (len == 0) { snprintf(out, outsz, "."); return; }
    int absolute = (in[0] == '/');
    /* component stack: offsets into a scratch copy */
    char* scratch = (char*)malloc(len + 1);
    const char** comps = (const char**)malloc((len + 1) * sizeof(char*));
    int64_t* comp_lens = (int64_t*)malloc((len + 1) * sizeof(int64_t));
    int ncomp = 0;
    char* w = scratch;
    const char* p = in;
    while (*p) {
        while (*p == '/') p++;
        if (!*p) break;
        const char* start = p;
        while (*p && *p != '/') p++;
        int64_t clen = p - start;
        if (clen == 1 && start[0] == '.') continue;
        if (clen == 2 && start[0] == '.' && start[1] == '.') {
            if (ncomp > 0 && !(comp_lens[ncomp - 1] == 2 &&
                               comps[ncomp - 1][0] == '.' && comps[ncomp - 1][1] == '.')) {
                ncomp--;          /* pop a real component */
                continue;
            }
            if (absolute) continue;   /* ".." at root is dropped */
            /* keep the ".." */
        }
        comps[ncomp] = w;
        comp_lens[ncomp] = clen;
        memcpy(w, start, (size_t)clen);
        w += clen;
        *w = 0;
        w++;
        ncomp++;
    }
    size_t oi = 0;
    if (absolute && oi < outsz - 1) out[oi++] = '/';
    for (int i = 0; i < ncomp; i++) {
        if (i > 0 && oi < outsz - 1) out[oi++] = '/';
        int64_t cl = comp_lens[i];
        for (int64_t j = 0; j < cl && oi < outsz - 1; j++) out[oi++] = comps[i][j];
    }
    out[oi] = 0;
    if (oi == 0) snprintf(out, outsz, absolute ? "/" : ".");
    free(scratch);
    free(comps);
    free(comp_lens);
}

/* Resolve an import path against the importing document's path (contract
 * §9.2): absolute paths stand alone; otherwise join dirname(base) — or the
 * process cwd when base is unset — then normalize. */
static void resolve_module_path(const char* path, const char* base,
                                char* out, size_t outsz) {
    if (path[0] == '/') {
        normpath_into(path, out, outsz);
        return;
    }
    char joined[4096];
    const char* slash = NULL;
    if (base && base[0]) {
        for (const char* q = base; *q; q++)
            if (*q == '/') slash = q;
    }
    if (slash) {
        snprintf(joined, sizeof joined, "%.*s/%s", (int)(slash - base), base, path);
    } else {
        snprintf(joined, sizeof joined, "%s", path);
    }
    normpath_into(joined, out, outsz);
}

V* b_sbc_load(V* path_pair, V* base_pair) {
    V* pathv = rt_data_of(path_pair);
    V* basev = rt_data_of(base_pair);
    const char* path = (pathv && pathv->tag == T_STR) ? pathv->u.str.s : "";
    const char* base = (basev && basev->tag == T_STR && basev->u.str.len > 0)
                           ? basev->u.str.s : NULL;
    char resolved[4096];
    resolve_module_path(path, base, resolved, sizeof resolved);
    rt_decref(path_pair);
    rt_decref(base_pair);

    FILE* f = fopen(resolved, "rb");
    if (!f) {
        V* m = rt_new_map();
        rt_map_set_raw(m, rt_lit("ok"), rt_int(0));
        char msg[4352];
        snprintf(msg, sizeof msg, "Module file not found: %s", resolved);
        rt_map_set_raw(m, rt_lit("message"), rt_str_copy(msg, (int64_t)strlen(msg)));
        return rt_pair(m, K_TRUTH);
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    rewind(f);
    char* buf = (char*)malloc((size_t)size + 1);
    if (!buf) { fprintf(stderr, "osakavm: out of memory\n"); exit(1); }
    size_t got = fread(buf, 1, (size_t)size, f);
    fclose(f);
    buf[got] = 0;

    char* err = NULL;
    V* docpair = load_document_soft(buf, &err);
    free(buf);
    if (!docpair) {
        V* m = rt_new_map();
        rt_map_set_raw(m, rt_lit("ok"), rt_int(0));
        rt_map_set_raw(m, rt_lit("message"), rt_str_copy(err, (int64_t)strlen(err)));
        return rt_pair(m, K_TRUTH);
    }
    V* docmap = rt_data_of(docpair);
    rt_incref(docmap);
    rt_decref(docpair);
    V* m = rt_new_map();
    rt_map_set_raw(m, rt_lit("ok"), rt_int(1));
    rt_map_set_raw(m, rt_lit("path"), rt_str_copy(resolved, (int64_t)strlen(resolved)));
    rt_map_set_raw(m, rt_lit("document"), docmap);
    return rt_pair(m, K_TRUTH);
}

/* ------------------------------------------------------------------ */
/* envelope helpers                                                    */
/* ------------------------------------------------------------------ */

typedef struct {
    char* buf;
    int64_t len, cap;
} StrBuf;

static void buf_emit(const char* bytes, int64_t len, void* ud) {
    StrBuf* sb = (StrBuf*)ud;
    if (sb->len + len + 1 > sb->cap) {
        sb->cap = (sb->len + len + 1) * 2;
        sb->buf = (char*)realloc(sb->buf, (size_t)sb->cap);
    }
    memcpy(sb->buf + sb->len, bytes, (size_t)len);
    sb->len += len;
    sb->buf[sb->len] = 0;
}

static V* error_envelope(void) {
    /* {"ok": 0, "value": {"stdout": "", "warnings": []},
     *  "diagnostic": {"code": "RUNTIME_ERROR", "message": msg, "where": "?"}} */
    V* value = rt_new_map();
    rt_map_set_raw(value, rt_lit("stdout"), rt_str_copy("", 0));
    rt_map_set_raw(value, rt_lit("warnings"), rt_new_list());
    V* diag = rt_new_map();
    rt_map_set_raw(diag, rt_lit("code"), rt_lit("RUNTIME_ERROR"));
    rt_map_set_raw(diag, rt_lit("message"),
                   rt_str_copy(rt_err_message ? rt_err_message : "?",
                               rt_err_message ? (int64_t)strlen(rt_err_message) : 1));
    rt_map_set_raw(diag, rt_lit("where"), rt_lit("?"));
    V* env = rt_new_map();
    rt_map_set_raw(env, rt_lit("ok"), rt_int(0));
    rt_map_set_raw(env, rt_lit("value"), value);
    rt_map_set_raw(env, rt_lit("diagnostic"), diag);
    return rt_pair(env, K_TRUTH);
}

/* ------------------------------------------------------------------ */
/* run modes                                                           */
/* ------------------------------------------------------------------ */

extern V* f_vm_run(V* document, V* argv);
extern V* f_vm_run_at(V* document, V* argv, V* current_file);
extern V* f_vm_call_func(V* document, V* fname, V* raw_args);

static V* run_document(V* doc, V* argv, const char* docpath) {
    rt_err_armed = 1;
    if (setjmp(rt_err_jmp)) {
        rt_err_armed = 0;
        return error_envelope();
    }
    /* Pass the document path so module imports resolve relative to the
     * document's directory (contract §9.2). */
    V* pathpair = rt_pair(rt_str_copy(docpath, (int64_t)strlen(docpath)), K_TRUTH);
    V* env = f_vm_run_at(doc, argv, pathpair);
    rt_err_armed = 0;
    return env;
}

static V* call_function(V* doc, V* fname, V* raw_args) {
    rt_err_armed = 1;
    if (setjmp(rt_err_jmp)) {
        rt_err_armed = 0;
        return error_envelope();
    }
    V* env = f_vm_call_func(doc, fname, raw_args);
    rt_err_armed = 0;
    return env;
}

int main(int argc, char** argv) {
    rt_init();
    math_seed_reset();

    if (argc < 2) {
        fprintf(stderr,
                "usage: osakavm <doc.sbc> [args...]\n"
                "       osakavm run <doc.sbc> <args.json>\n"
                "       osakavm call <doc.sbc> <fname> <args.json>\n");
        return 2;
    }

    if (strcmp(argv[1], "run") == 0 || strcmp(argv[1], "call") == 0) {
        int is_call = strcmp(argv[1], "call") == 0;
        if (argc < (is_call ? 5 : 4)) {
            fprintf(stderr, "osakavm: missing arguments for %s mode\n", argv[1]);
            return 2;
        }
        int64_t dlen;
        char* dtext = read_file_bytes(argv[2], &dlen);
        V* doc = load_document(dtext);
        free(dtext);
        int64_t alen;
        char* atext = read_file_bytes(argv[is_call ? 4 : 3], &alen);
        const char* jerr = NULL;
        V* args = json_parse(atext, alen, &jerr);
        if (!args) {
            fprintf(stderr, "osakavm: bad args JSON: %s\n", jerr ? jerr : "?");
            return 2;
        }
        V* env;
        if (is_call) {
            if (args->tag != T_LIST) {
                fprintf(stderr, "osakavm: call args JSON must be an array\n");
                return 2;
            }
            V* fname = rt_pair(rt_lit(argv[3]), K_TRUTH);
            V* argpair = rt_pair(args, K_TRUTH);
            env = call_function(doc, fname, argpair);
        } else {
            /* args.json is the guest argv list */
            V* argpair = rt_pair(args, K_TRUTH);
            env = run_document(doc, argpair, argv[2]);
        }
        free(atext);
        /* serialize the envelope's raw map */
        StrBuf sb;
        memset(&sb, 0, sizeof sb);
        json_write(rt_data_of(env), buf_emit, &sb);
        if (sb.buf) {
            fwrite(sb.buf, 1, (size_t)sb.len, stdout);
            fwrite("\n", 1, 1, stdout);
        }
        V* ok = map_get_cstr(rt_data_of(env), "ok");
        return (ok && ok->tag == T_INT && ok->u.i == 1) ? 0 : 1;
    }

    /* CLI mode: osakavm <doc.sbc> [args...] */
    int64_t dlen;
    char* dtext = read_file_bytes(argv[1], &dlen);
    V* doc = load_document(dtext);
    free(dtext);
    V* argvlist = rt_new_list();
    for (int i = 2; i < argc; i++)
        rt_list_append(argvlist, rt_str_copy(argv[i], (int64_t)strlen(argv[i])));
    V* env = run_document(doc, rt_pair(argvlist, K_TRUTH), argv[1]);

    V* envm = rt_data_of(env);
    V* ok = map_get_cstr(envm, "ok");
    if (ok && ok->tag == T_INT && ok->u.i == 1) {
        V* value = map_get_cstr(envm, "value");
        V* stdoutv = map_get_cstr(value, "stdout");
        V* warnings = map_get_cstr(value, "warnings");
        fwrite(stdoutv->u.str.s, 1, (size_t)stdoutv->u.str.len, stdout);
        if (warnings && warnings->tag == T_LIST) {
            for (int64_t i = 0; i < warnings->u.list.n; i++) {
                V* w = warnings->u.list.a[i];
                if (w->tag == T_STR) {
                    fwrite(w->u.str.s, 1, (size_t)w->u.str.len, stderr);
                    fputc('\n', stderr);
                }
            }
        }
        return 0;
    }
    V* diag = map_get_cstr(envm, "diagnostic");
    V* msg = diag ? map_get_cstr(diag, "message") : NULL;
    if (msg && msg->tag == T_STR) {
        fwrite(msg->u.str.s, 1, (size_t)msg->u.str.len, stderr);
        fputc('\n', stderr);
    } else {
        fprintf(stderr, "RUNTIME_ERROR\n");
    }
    return 1;
}