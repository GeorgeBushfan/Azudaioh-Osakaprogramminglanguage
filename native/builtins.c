/* builtins.c — Gate E native host: the host-boundary builtins.
 *
 * Implements the builtins the transpiled vm.saka calls out to the host for,
 * with the exact semantics of vm.py (see docs/NATIVE_HOST_CONTRACT.md §3-§5).
 * All functions consume their pair arguments and return an owned pair.
 */
#include "value.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <errno.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>

extern double math_next_random(void);
extern V* num_bin_data(V* da, V* db, char op);

/* ------------------------------------------------------------------ */
/* reflection                                                          */
/* ------------------------------------------------------------------ */

V* b_json_type(V* v) {
    V* d = v->u.list.a[0];
    int code;
    switch (d->tag) {
    case T_BOOL: code = 5; break;
    case T_NULL: code = 6; break;
    case T_INT:  code = 0; break;
    case T_FLOAT: code = 1; break;
    case T_STR:  code = 2; break;
    case T_LIST: code = 3; break;
    case T_MAP:  code = 4; break;
    default:
        rt_decref(v);
        rt_fail("__json_type__ unsupported constant type");
        return NULL;
    }
    V* out = rt_pair(rt_int(code), K_TRUTH);
    rt_decref(v);
    return out;
}

V* b_is_bool(V* v) {
    int r = v->u.list.a[0]->tag == T_BOOL;
    V* out = rt_pair(rt_bool(r), K_TRUTH);
    rt_decref(v);
    return out;
}

V* b_is_float(V* v) {
    int r = v->u.list.a[0]->tag == T_FLOAT;
    V* out = rt_pair(rt_bool(r), K_TRUTH);
    rt_decref(v);
    return out;
}

V* b_float_repr(V* v) {
    V* d = v->u.list.a[0];
    if (d->tag != T_FLOAT) {
        rt_decref(v);
        rt_fail("__float_repr__ expects a float");
        return NULL;
    }
    int64_t len;
    char* s = py_float_repr(d->u.f, &len);
    V* out = rt_pair(rt_str_copy(s, len), K_TRUTH);
    rt_free_buf(s, len);
    rt_decref(v);
    return out;
}

V* b_push_scope(void) { return rt_pair(rt_null(), K_TRUTH); }

V* b_pop_scope(void) { return rt_pair(rt_null(), K_TRUTH); }

V* b_americaya(V* v) {
    /* vm.py: if v.kind == "grain": rt.info(...) — infos are not part of the
     * observable envelope, so the native host skips the info emission. */
    V* data = v->u.list.a[0];
    rt_incref(data);
    rt_decref(v);
    return rt_pair(data, K_TRUTH);
}

/* ------------------------------------------------------------------ */
/* file I/O                                                            */
/* ------------------------------------------------------------------ */

/* Normalize a path (collapse //, /./, seg/..) like os.path.normpath. */
static void normpath(char* path) {
    const char* p = path;
    int absolute = (*p == '/');
    const char* segs[512];
    int lens[512];
    int n = 0;
    while (*p) {
        while (*p == '/') p++;
        if (!*p) break;
        const char* start = p;
        while (*p && *p != '/') p++;
        int len = (int)(p - start);
        if (len == 1 && start[0] == '.') continue;
        if (len == 2 && start[0] == '.' && start[1] == '.') {
            if (n > 0 && !(lens[n-1] == 2 && segs[n-1][0] == '.' && segs[n-1][1] == '.')) {
                n--;
                continue;
            }
            if (absolute) continue;   /* /.. collapses to / */
        }
        segs[n] = start;
        lens[n] = len;
        n++;
    }
    char out[4096];
    char* o = out;
    if (absolute) *o++ = '/';
    for (int i = 0; i < n; i++) {
        if (i) *o++ = '/';
        memcpy(o, segs[i], (size_t)lens[i]);
        o += lens[i];
    }
    *o = 0;
    if (o == out) strcpy(out, absolute ? "/" : ".");
    strcpy(path, out);
}

static char* resolve_path(const char* path) {
    char buf[4096];
    if (path[0] == '/') {
        snprintf(buf, sizeof buf, "%s", path);
    } else {
        char cwd[2048];
        if (!getcwd(cwd, sizeof cwd)) cwd[0] = 0;
        snprintf(buf, sizeof buf, "%s/%s", cwd, path);
    }
    normpath(buf);
    return strdup(buf);
}

static void mkdirs_for(const char* path) {
    char tmp[4096];
    snprintf(tmp, sizeof tmp, "%s", path);
    for (char* p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = 0;
            mkdir(tmp, 0777);   /* EEXIST is fine */
            *p = '/';
        }
    }
}

static char* read_whole_file(const char* rpath, int64_t* out_len, int* err_no) {
    FILE* f = fopen(rpath, "rb");
    if (!f) { *err_no = errno; return NULL; }
    if (fseek(f, 0, SEEK_END) != 0) { *err_no = errno; fclose(f); return NULL; }
    long size = ftell(f);
    if (size < 0) { *err_no = errno; fclose(f); return NULL; }
    rewind(f);
    char* buf = (char*)malloc((size_t)size + 1);
    if (!buf) { fclose(f); *err_no = ENOMEM; return NULL; }
    size_t got = fread(buf, 1, (size_t)size, f);
    int read_err = ferror(f);
    fclose(f);
    if (read_err) { free(buf); *err_no = EIO; return NULL; }
    buf[got] = 0;
    *out_len = (int64_t)got;
    return buf;
}

static int write_file_impl(const char* rpath, const char* data, int64_t len, int* err_no) {
    mkdirs_for(rpath);
    FILE* f = fopen(rpath, "wb");
    if (!f) { *err_no = errno; return 0; }
    size_t got = fwrite(data, 1, (size_t)len, f);
    int ok = (got == (size_t)len) && (fclose(f) == 0);
    if (!ok) *err_no = errno ? errno : EIO;
    return ok;
}

static V* path_str(V* pathPair, const char* who) {
    V* d = pathPair->u.list.a[0];
    if (d->tag != T_STR) {
        rt_decref(pathPair);
        rt_fail("%s failed: path is not a string", who);
        return NULL;
    }
    return d;
}

V* b_read_file(V* path) {
    V* d = path_str(path, "ReadFile");
    char* rpath = resolve_path(d->u.str.s);
    int64_t len;
    int err_no = 0;
    char* content = read_whole_file(rpath, &len, &err_no);
    if (!content) {
        char msg[1024];
        snprintf(msg, sizeof msg, "ReadFile failed: [Errno %d] %s: '%s'",
                 err_no, strerror(err_no), rpath);
        free(rpath);
        rt_decref(path);
        rt_fail("%s", msg);
        return NULL;
    }
    free(rpath);
    V* out = rt_pair_take_kind(rt_str_copy(content, len), path);
    free(content);
    return out;
}

V* b_write_file(V* path, V* content) {
    V* pd = path_str(path, "WriteFile");
    V* cd = content->u.list.a[0];
    char* rpath = resolve_path(pd->u.str.s);
    const char* data = "";
    int64_t len = 0;
    if (cd->tag == T_STR) { data = cd->u.str.s; len = cd->u.str.len; }
    int err_no = 0;
    if (!write_file_impl(rpath, data, len, &err_no)) {
        char msg[1024];
        snprintf(msg, sizeof msg, "WriteFile failed: [Errno %d] %s: '%s'",
                 err_no, strerror(err_no), rpath);
        free(rpath);
        rt_decref(path);
        rt_decref(content);
        rt_fail("%s", msg);
        return NULL;
    }
    free(rpath);
    rt_decref(path);
    rt_decref(content);
    return rt_default_ret();
}

V* b_append_file(V* path, V* content) {
    V* pd = path_str(path, "AppendFile");
    V* cd = content->u.list.a[0];
    char* rpath = resolve_path(pd->u.str.s);
    /* append: open "a" after ensuring parent dirs */
    mkdirs_for(rpath);
    FILE* f = fopen(rpath, "ab");
    int err_no = errno;
    int ok = 0;
    if (f) {
        const char* data = "";
        int64_t len = 0;
        if (cd->tag == T_STR) { data = cd->u.str.s; len = cd->u.str.len; }
        size_t got = fwrite(data, 1, (size_t)len, f);
        ok = (got == (size_t)len) && (fclose(f) == 0);
        if (!ok) err_no = errno ? errno : EIO;
    }
    if (!ok) {
        char msg[1024];
        snprintf(msg, sizeof msg, "AppendFile failed: [Errno %d] %s: '%s'",
                 err_no, strerror(err_no), rpath);
        free(rpath);
        rt_decref(path);
        rt_decref(content);
        rt_fail("%s", msg);
        return NULL;
    }
    free(rpath);
    rt_decref(path);
    rt_decref(content);
    return rt_default_ret();
}

V* b_file_exists(V* path) {
    V* pd = path_str(path, "FileExists");
    char* rpath = resolve_path(pd->u.str.s);
    struct stat st;
    int exists = stat(rpath, &st) == 0;
    free(rpath);
    /* vm.py returns int 1/0 (not a bool) for FileExists */
    V* out = rt_pair_take_kind(rt_int(exists ? 1 : 0), path);
    return out;
}

V* b_delete_file(V* path) {
    V* pd = path_str(path, "DeleteFile");
    char* rpath = resolve_path(pd->u.str.s);
    struct stat st;
    if (stat(rpath, &st) == 0) {
        if (unlink(rpath) != 0) {
            int err_no = errno;
            char msg[1024];
            snprintf(msg, sizeof msg, "DeleteFile failed: [Errno %d] %s: '%s'",
                     err_no, strerror(err_no), rpath);
            free(rpath);
            rt_decref(path);
            rt_fail("%s", msg);
            return NULL;
        }
    }
    free(rpath);
    V* out = rt_pair_take_kind(rt_int(1), path);
    return out;
}

/* ------------------------------------------------------------------ */
/* collection builtins                                                 */
/* ------------------------------------------------------------------ */

V* b_len(V* x) {
    V* d = x->u.list.a[0];
    if (d->tag != T_STR && d->tag != T_LIST && d->tag != T_MAP) {
        rt_decref(x);
        rt_fail("len() unsupported type");
        return NULL;
    }
    return rt_pair_take_kind(rt_int(rt_len_raw(d)), x);
}

V* b_slice(V* x, V* a, V* b) {
    V* d = x->u.list.a[0];
    if (d->tag != T_STR && d->tag != T_LIST) {
        rt_decref(x); rt_decref(a); rt_decref(b);
        rt_fail("slice() requires list or string");
        return NULL;
    }
    V* da = a->u.list.a[0];
    V* db = b->u.list.a[0];
    if (da->tag != T_INT || db->tag != T_INT) {
        rt_decref(x); rt_decref(a); rt_decref(b);
        rt_fail("slice indices must be integers");
        return NULL;
    }
    int64_t n = d->tag == T_STR ? d->u.str.len : d->u.list.n;
    int64_t start = da->u.i, stop = db->u.i;
    if (start < 0) { start += n; if (start < 0) start = 0; }
    if (start > n) start = n;
    if (stop < 0) stop += n;
    if (stop > n) stop = n;
    if (stop < start) stop = start;
    int64_t m = stop - start;
    V* res;
    if (d->tag == T_STR) {
        res = rt_str_copy(d->u.str.s + start, m);
    } else {
        res = rt_new_list();
        for (int64_t i = start; i < stop; i++) {
            rt_incref(d->u.list.a[i]);
            rt_list_append(res, d->u.list.a[i]);
        }
    }
    int all_truth = (x->u.list.a[1] == K_TRUTH && a->u.list.a[1] == K_TRUTH &&
                     b->u.list.a[1] == K_TRUTH);
    V* out = rt_pair(res, all_truth ? K_TRUTH : K_GRAIN);
    rt_decref(x); rt_decref(a); rt_decref(b);
    return out;
}

V* b_push(V* lst, V* v) {
    V* raw = lst->u.list.a[0];
    if (raw->tag != T_LIST) {
        rt_decref(lst); rt_decref(v);
        rt_fail("push() requires list");
        return NULL;
    }
    V* d = v->u.list.a[0];
    rt_incref(d);
    rt_list_append(raw, d);
    /* kind update on the wrapper: grain if either side is grain */
    lst->u.list.a[1] = (lst->u.list.a[1] == K_GRAIN || v->u.list.a[1] == K_GRAIN)
                       ? K_GRAIN : K_TRUTH;
    rt_decref(lst);
    rt_decref(v);
    return rt_default_ret();
}

V* b_pop(V* lst) {
    V* raw = lst->u.list.a[0];
    if (raw->tag != T_LIST) {
        rt_decref(lst);
        rt_fail("pop() requires list");
        return NULL;
    }
    if (raw->u.list.n == 0) {
        rt_decref(lst);
        rt_fail("pop() on empty list");
        return NULL;
    }
    V* last = raw->u.list.a[raw->u.list.n - 1];
    rt_incref(last);
    raw->u.list.n--;
    rt_decref(raw->u.list.a[raw->u.list.n]);
    return rt_pair_take_kind(last, lst);
}

V* b_keys(V* m) {
    V* d = m->u.list.a[0];
    if (d->tag != T_MAP) {
        rt_decref(m);
        rt_fail("keys() requires map");
        return NULL;
    }
    V* lst = rt_new_list();
    for (MapEntry* e = d->u.m->head; e; e = e->inext) {
        rt_incref(e->key);
        rt_list_append(lst, e->key);
    }
    return rt_pair_take_kind(lst, m);
}

V* b_values(V* m) {
    V* d = m->u.list.a[0];
    if (d->tag != T_MAP) {
        rt_decref(m);
        rt_fail("values() requires map");
        return NULL;
    }
    V* lst = rt_new_list();
    for (MapEntry* e = d->u.m->head; e; e = e->inext) {
        rt_incref(e->val);
        rt_list_append(lst, e->val);
    }
    return rt_pair_take_kind(lst, m);
}

V* b_contains(V* m, V* k) {
    V* d = m->u.list.a[0];
    if (d->tag != T_MAP) {
        rt_decref(m); rt_decref(k);
        rt_fail("contains() requires map");
        return NULL;
    }
    uint64_t h = rt_hash(k->u.list.a[0]);
    MapEntry* e = d->u.m->b[h & (uint64_t)(d->u.m->nb - 1)];
    int found = 0;
    while (e) {
        if (e->h == h && rt_veq(e->key, k->u.list.a[0])) { found = 1; break; }
        e = e->bnext;
    }
    int truth = (m->u.list.a[1] == K_TRUTH && k->u.list.a[1] == K_TRUTH);
    V* out = rt_pair(rt_bool(found), truth ? K_TRUTH : K_GRAIN);
    rt_decref(m);
    rt_decref(k);
    return out;
}

/* ------------------------------------------------------------------ */
/* vm_int_to_text (native fast path)                                   */
/* ------------------------------------------------------------------ */

/* vm.saka's vm_int_to_text produces the decimal text of an int via
 * repeated-subtraction division — O(value) guest steps. The native
 * version is O(digits) with identical output (Python str(int) text:
 * "-" prefix for negatives, no leading zeros, "0" for zero). */
V* f_vm_int_to_text(V* v_value) {
    V* d = v_value->u.list.a[0];
    if (d->tag != T_INT && d->tag != T_BOOL) {
        rt_decref(v_value);
        rt_abort("vm_int_to_text expects an int");
        return NULL;
    }
    int64_t n = d->tag == T_INT ? d->u.i : (int64_t)d->u.b;
    char buf[32];
    int len = snprintf(buf, sizeof buf, "%lld", (long long)n);
    V* out = rt_pair_take_kind(rt_str_copy(buf, len), v_value);
    return out;
}

/* ------------------------------------------------------------------ */
/* Math.* (applied via __math_call__)                                  */
/* ------------------------------------------------------------------ */

static int math_is_num(V* d) {
    return d->tag == T_INT || d->tag == T_FLOAT || d->tag == T_BOOL;
}

static double math_as_f(V* d) {
    if (d->tag == T_FLOAT) return d->u.f;
    if (d->tag == T_INT) return (double)d->u.i;
    return (double)d->u.b;
}

static int math_is_int(V* d) { return d->tag == T_INT || d->tag == T_BOOL; }

static int64_t math_as_i(V* d) {
    return d->tag == T_INT ? d->u.i : (int64_t)d->u.b;
}

/* Applies one Math.* operation to raw operand 2-lists [data, kind].
 * Returns the owned inner result pair [data, kind]. */
static V* math_apply(const char* op, V** ops, int n) {
    V* res = NULL;
    V* kind = K_TRUTH;
    if (strcmp(op, "abs") == 0) {
        V* d = ops[0]->u.list.a[0];
        if (!math_is_num(d)) rt_fail("Math.abs() unsupported type");
        if (d->tag == T_FLOAT) res = rt_float(fabs(d->u.f));
        else res = rt_int(d->tag == T_INT ? (d->u.i < 0 ? -d->u.i : d->u.i)
                                          : (d->u.b < 0 ? -d->u.b : d->u.b));
        kind = ops[0]->u.list.a[1];
    } else if (strcmp(op, "min") == 0 || strcmp(op, "max") == 0) {
        V* a = ops[0]->u.list.a[0];
        V* b = ops[1]->u.list.a[0];
        if (!math_is_num(a) || !math_is_num(b)) rt_fail("Math.%s() unsupported type", op);
        int c = (math_as_f(a) < math_as_f(b)) ? -1 : (math_as_f(a) > math_as_f(b) ? 1 : 0);
        /* Python min/max return the first operand on ties */
        int take_b = (strcmp(op, "min") == 0) ? (c > 0) : (c < 0);
        V* win = take_b ? b : a;
        rt_incref(win);
        res = win;
        kind = (ops[0]->u.list.a[1] == K_TRUTH && ops[1]->u.list.a[1] == K_TRUTH)
               ? K_TRUTH : K_GRAIN;
    } else if (strcmp(op, "pow") == 0) {
        V* a = ops[0]->u.list.a[0];
        V* b = ops[1]->u.list.a[0];
        if (!math_is_num(a) || !math_is_num(b)) rt_fail("Math.pow() unsupported type");
        if (math_is_int(a) && math_is_int(b) && math_as_i(b) >= 0) {
            int64_t base = math_as_i(a), exp = math_as_i(b), r = 1;
            for (int64_t k = 0; k < exp; k++) r *= base;   /* overflow wraps */
            res = rt_int(r);
        } else {
            res = rt_float(pow(math_as_f(a), math_as_f(b)));
        }
        kind = (ops[0]->u.list.a[1] == K_TRUTH && ops[1]->u.list.a[1] == K_TRUTH)
               ? K_TRUTH : K_GRAIN;
    } else if (strcmp(op, "floor") == 0 || strcmp(op, "ceil") == 0 ||
               strcmp(op, "trunc") == 0 || strcmp(op, "round") == 0) {
        V* d = ops[0]->u.list.a[0];
        if (!math_is_num(d)) rt_fail("Math.%s() expects a number", op);
        if (math_is_int(d)) {
            rt_incref(d);
            res = d;
        } else {
            double x = d->u.f;
            int64_t r;
            if (strcmp(op, "floor") == 0) r = (int64_t)floor(x);
            else if (strcmp(op, "ceil") == 0) r = (int64_t)ceil(x);
            else if (strcmp(op, "trunc") == 0) r = (int64_t)trunc(x);
            else r = (int64_t)floor(x + 0.5);   /* JS-like ties toward +inf */
            res = rt_int(r);
        }
        kind = ops[0]->u.list.a[1];
    } else if (strcmp(op, "sign") == 0) {
        V* d = ops[0]->u.list.a[0];
        if (!math_is_num(d)) rt_fail("Math.sign() expects a number");
        double x = math_as_f(d);
        res = rt_int(x > 0 ? 1 : x < 0 ? -1 : 0);
        kind = ops[0]->u.list.a[1];
    } else if (strcmp(op, "clamp") == 0) {
        V* x = ops[0]->u.list.a[0];
        V* lo = ops[1]->u.list.a[0];
        V* hi = ops[2]->u.list.a[0];
        if (!math_is_num(x) || !math_is_num(lo) || !math_is_num(hi))
            rt_fail("Math.clamp() unsupported type");
        if (math_as_f(lo) > math_as_f(hi))
            rt_fail("Math.clamp() requires min <= max");
        /* pick the winning operand object (tag-preserving, like min/max) */
        V* win = x;
        if (math_as_f(win) < math_as_f(lo)) win = lo;
        if (math_as_f(win) > math_as_f(hi)) win = hi;
        rt_incref(win);
        res = win;
        int t = 1;
        for (int i = 0; i < 3; i++) if (ops[i]->u.list.a[1] != K_TRUTH) t = 0;
        kind = t ? K_TRUTH : K_GRAIN;
    } else if (strcmp(op, "sqrt") == 0) {
        V* d = ops[0]->u.list.a[0];
        if (!math_is_num(d)) rt_fail("Math.sqrt() expects a number");
        double x = math_as_f(d);
        if (x < 0) rt_fail("Math.sqrt() domain error");
        res = rt_float(sqrt(x));
        kind = ops[0]->u.list.a[1];
    } else if (strcmp(op, "PI") == 0) {
        res = rt_float(3.141592653589793);
    } else if (strcmp(op, "E") == 0) {
        res = rt_float(2.718281828459045);
    } else if (strcmp(op, "random") == 0) {
        res = rt_float(math_next_random());
    } else if (strcmp(op, "sin") == 0 || strcmp(op, "cos") == 0 ||
               strcmp(op, "tan") == 0 || strcmp(op, "sinh") == 0 ||
               strcmp(op, "cosh") == 0 || strcmp(op, "tanh") == 0) {
        V* d = ops[0]->u.list.a[0];
        if (!math_is_num(d)) rt_fail("Math.%s() expects a number", op);
        double x = math_as_f(d);
        double r = strcmp(op, "sin") == 0 ? sin(x) : strcmp(op, "cos") == 0 ? cos(x)
                 : strcmp(op, "tan") == 0 ? tan(x) : strcmp(op, "sinh") == 0 ? sinh(x)
                 : strcmp(op, "cosh") == 0 ? cosh(x) : tanh(x);
        res = rt_float(r);
        kind = ops[0]->u.list.a[1];
    } else {
        rt_fail("Unknown builtin Math.%s", op);
    }
    return rt_pair(res, kind);
}

V* b_math_call(V* name, V* pairs) {
    V* nd = name->u.list.a[0];
    V* pd = pairs->u.list.a[0];
    if (nd->tag != T_STR || pd->tag != T_LIST) {
        rt_decref(name); rt_decref(pairs);
        rt_fail("__math_call__ expects a name and a pair list");
        return NULL;
    }
    int n = (int)pd->u.list.n;
    V** ops = (V**)malloc((size_t)(n > 0 ? n : 1) * sizeof(V*));
    for (int i = 0; i < n; i++) {
        ops[i] = pd->u.list.a[i];   /* raw 2-list [data, kind]; borrowed */
    }
    V* inner = math_apply(nd->u.str.s, ops, n);
    free(ops);
    rt_decref(name);
    rt_decref(pairs);
    /* vm.py: return Value([ret.data, ret.kind], "truth") */
    return rt_pair(inner, K_TRUTH);
}