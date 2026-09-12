/* rt.c — Gate E native host runtime: values, ordered maps, and the
 * host-Value-semantics operations the transpiled vm.saka calls.
 *
 * See docs/NATIVE_HOST_CONTRACT.md. Every rt_ function consumes its V*
 * arguments and returns an owned V* (or void). Pairs are 2-element lists:
 * [data, kind]. Kind strings are the interned singletons K_TRUTH/K_GRAIN.
 */
#include "value.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdarg.h>

/* ------------------------------------------------------------------ */
/* Interned singletons and cached constants                            */
/* ------------------------------------------------------------------ */

static V make_immortal(uint8_t tag) {
    V v;
    v.rc = RC_IMMORTAL;
    v.tag = tag;
    memset(&v.u, 0, sizeof v.u);
    return v;
}

V g_truth_s;   /* initialized in rt_init */
V g_grain_s;
static V g_null_v;
static V g_true_v, g_false_v;
static V g_small_ints[8192];   /* rt_int cache for -4096..4095 */

/* intern table for string literals (separate chain arrays per bucket) */
#define INTERN_BUCKETS 65536
typedef struct InternNode { V* v; struct InternNode* next; } InternNode;
typedef struct InternNode InternNodeT;
static InternNodeT* intern_table[INTERN_BUCKETS];

void rt_abort(const char* msg) {
    fprintf(stderr, "native host internal error: %s\n", msg);
    abort();
}

/* ------------------------------------------------------------------ */
/* custom allocator: size-class free lists over large chunks.          */
/* The interpreter allocates/frees many small fixed-size blocks (V      */
/* structs, pair arrays); a free-list allocator removes the malloc      */
/* overhead from the hot path.                                          */
/* ------------------------------------------------------------------ */

#define POOL_MAX 256            /* sizes above this go to malloc */
#define POOL_CLASSES 16         /* 16,32,...,256 byte classes */
#define CHUNK_SIZE (1024 * 1024)

typedef struct FreeNode { struct FreeNode* next; } FreeNode;
static FreeNode* g_free_lists[POOL_CLASSES];
static char* g_chunk = NULL;
static size_t g_chunk_left = 0;

static int pool_class(size_t n) {
    size_t cls = (n + 15) / 16;   /* 1..16 */
    if (cls > POOL_CLASSES) return -1;
    return (int)cls - 1;
}

static void* pool_alloc(size_t n) {
#ifdef OSAKA_NO_POOL
    {
        void* p = malloc(n);
        if (!p) rt_abort("out of memory");
        return p;
    }
#endif
    int cls = pool_class(n);
    if (cls < 0) {
        void* p = malloc(n);
        if (!p) rt_abort("out of memory");
        return p;
    }
    FreeNode* fn = g_free_lists[cls];
    if (fn) {
        g_free_lists[cls] = fn->next;
        return fn;
    }
    size_t sz = 16 * (size_t)(cls + 1);
    if (g_chunk_left < sz) {
        g_chunk = (char*)malloc(CHUNK_SIZE);
        if (!g_chunk) rt_abort("out of memory");
        g_chunk_left = CHUNK_SIZE;
    }
    void* p = g_chunk;
    g_chunk += sz;
    g_chunk_left -= sz;
    return p;
}

static void pool_free(void* p, size_t n) {
#ifdef OSAKA_NO_POOL
    free(p);
    return;
#endif
    if (!p) return;
    int cls = pool_class(n);
    if (cls < 0) { free(p); return; }
    FreeNode* fn = (FreeNode*)p;
    fn->next = g_free_lists[cls];
    g_free_lists[cls] = fn;
}

static void* xmalloc(size_t n) {
    return pool_alloc(n);
}

void rt_free_buf(char* p, int64_t n) { pool_free(p, (size_t)n); }

static void* xrealloc3(void* p, size_t old_n, size_t n) {
    if (!p) return pool_alloc(n);
    if (n <= POOL_MAX && old_n <= POOL_MAX &&
        pool_class(old_n) == pool_class(n)) {
        return p;   /* same size class: keep the block */
    }
    void* q = pool_alloc(n);
    memcpy(q, p, old_n < n ? old_n : n);
    pool_free(p, old_n);
    return q;
}

static uint64_t mix64(uint64_t x) {
    x ^= x >> 33; x *= 0xff51afd7ed558ccdULL;
    x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL;
    x ^= x >> 33;
    return x;
}

static uint64_t fnv1a(const char* s, int64_t n) {
    uint64_t h = 1469598103934665603ULL;
    for (int64_t i = 0; i < n; i++) { h ^= (unsigned char)s[i]; h *= 1099511628211ULL; }
    return h;
}

void rt_init(void) {
    g_truth_s = make_immortal(T_STR);
    g_truth_s.u.str.s = (char*)"truth";
    g_truth_s.u.str.len = 5;
    g_truth_s.u.str.h = fnv1a("truth", 5) | 1;
    g_grain_s = make_immortal(T_STR);
    g_grain_s.u.str.s = (char*)"grain";
    g_grain_s.u.str.len = 5;
    g_grain_s.u.str.h = fnv1a("grain", 5) | 1;
    g_null_v = make_immortal(T_NULL);
    g_true_v = make_immortal(T_BOOL);  g_true_v.u.b = 1;
    g_false_v = make_immortal(T_BOOL); g_false_v.u.b = 0;
    for (int i = 0; i < 8192; i++) {
        g_small_ints[i] = make_immortal(T_INT);
        g_small_ints[i].u.i = (int64_t)(i - 4096);
    }
    /* register the kind singletons in the intern table so rt_lit("truth")
     * and rt_lit("grain") return the same singletons (pointer-identical). */
    for (int k = 0; k < 2; k++) {
        V* kv = k ? &g_grain_s : &g_truth_s;
        uint64_t b = (kv->u.str.h) & (INTERN_BUCKETS - 1);
        InternNodeT* node = (InternNodeT*)xmalloc(sizeof(InternNodeT));
        node->v = kv;
        node->next = intern_table[b];
        intern_table[b] = node;
    }
}

/* ------------------------------------------------------------------ */
/* guest-facing failure propagation                                    */
/* ------------------------------------------------------------------ */

jmp_buf rt_err_jmp;
int rt_err_armed = 0;
char* rt_err_message = NULL;

void rt_fail(const char* fmt, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    free(rt_err_message);
    rt_err_message = strdup(buf);
    if (rt_err_armed) longjmp(rt_err_jmp, 1);
    fprintf(stderr, "native host error: %s\n", buf);
    exit(1);
}

/* ------------------------------------------------------------------ */
/* refcounting                                                         */
/* ------------------------------------------------------------------ */

V* rt_incref(V* v) {
    if (v && v->rc != RC_IMMORTAL) v->rc++;
    return v;
}

static void map_free(Map* m);

static void rt_free(V* v) {
    switch (v->tag) {
    case T_STR:
        pool_free(v->u.str.s, (size_t)v->u.str.len + 1);
        pool_free(v, sizeof(V));
        break;
    case T_LIST: {
        V** a = v->u.list.a;
        int64_t n = v->u.list.n;
        int64_t cap = v->u.list.cap;
        /* decref children first (they cannot reach this container), then
         * free the array and the value itself */
        for (int64_t i = 0; i < n; i++) rt_decref(a[i]);
        pool_free(a, (size_t)cap * sizeof(V*));
        pool_free(v, sizeof(V));
        break;
    }
    case T_MAP: {
        Map* m = v->u.m;
        pool_free(v, sizeof(V));
        map_free(m);
        break;
    }
    default:
        pool_free(v, sizeof(V));
    }
}

void rt_decref(V* v) {
    if (!v) return;
    if (v->rc == RC_IMMORTAL) return;
    if (--v->rc == 0) rt_free(v);
}

/* ------------------------------------------------------------------ */
/* constructors                                                        */
/* ------------------------------------------------------------------ */

V* rt_null(void) { return &g_null_v; }

V* rt_bool(int b) { return b ? &g_true_v : &g_false_v; }

V* rt_int(int64_t i) {
    if (i >= -4096 && i < 4096) return &g_small_ints[i + 4096];
    V* v = (V*)xmalloc(sizeof(V));
    v->rc = 1;
    v->tag = T_INT;
    v->u.i = i;
    return v;
}

V* rt_float(double d) {
    V* v = (V*)xmalloc(sizeof(V));
    v->rc = 1;
    v->tag = T_FLOAT;
    v->u.f = d;
    return v;
}

V* rt_lit_n(const char* s, int64_t len) {
    /* String hashes are always fnv1a|1 (matching rt_hash's lazy path) so
     * interned and parsed keys hash identically. */
    uint64_t h = fnv1a(s, len) | 1;
    uint64_t b = h & (INTERN_BUCKETS - 1);
    for (InternNodeT* n = intern_table[b]; n; n = n->next) {
        V* v = n->v;
        if (v->u.str.len == len && memcmp(v->u.str.s, s, (size_t)len) == 0) return v;
    }
    char* buf = (char*)xmalloc((size_t)len + 1);
    memcpy(buf, s, (size_t)len);
    buf[len] = 0;
    V* v = (V*)xmalloc(sizeof(V));
    v->rc = RC_IMMORTAL;
    v->tag = T_STR;
    v->u.str.s = buf;
    v->u.str.len = len;
    v->u.str.h = h;
    InternNodeT* node = (InternNodeT*)xmalloc(sizeof(InternNodeT));
    node->v = v;
    node->next = intern_table[b];
    intern_table[b] = node;
    return v;
}

V* rt_lit(const char* s) { return rt_lit_n(s, (int64_t)strlen(s)); }

V* rt_str_copy(const char* s, int64_t len) {
    V* v = (V*)xmalloc(sizeof(V));
    v->rc = 1;
    v->tag = T_STR;
    char* buf = (char*)xmalloc((size_t)len + 1);
    memcpy(buf, s, (size_t)len);
    buf[len] = 0;
    v->u.str.s = buf;
    v->u.str.len = len;
    v->u.str.h = 0;
    return v;
}

/* ------------------------------------------------------------------ */
/* lists                                                               */
/* ------------------------------------------------------------------ */

static void list_append_raw(V* lst, V* elem) {
    if (lst->u.list.n == lst->u.list.cap) {
        int64_t old_cap = lst->u.list.cap;
        int64_t cap = old_cap ? old_cap * 2 : 4;
        lst->u.list.a = (V**)xrealloc3(lst->u.list.a,
                                       (size_t)old_cap * sizeof(V*),
                                       (size_t)cap * sizeof(V*));
        lst->u.list.cap = cap;
    }
    lst->u.list.a[lst->u.list.n++] = elem;
}

static V* new_list(void) {
    V* v = (V*)xmalloc(sizeof(V));
    v->rc = 1;
    v->tag = T_LIST;
    v->u.list.a = NULL;
    v->u.list.n = 0;
    v->u.list.cap = 0;
    return v;
}

V* rt_pair(V* data, V* kind) {
    V* p = new_list();
    list_append_raw(p, data);      /* takes ownership of data */
    list_append_raw(p, kind);      /* kind is an immortal singleton */
    return p;
}

V* rt_pair_take_kind(V* data, V* consumed_pair) {
    V* kind = consumed_pair->u.list.a[1];
    rt_decref(consumed_pair);
    return rt_pair(data, kind);
}

V* rt_default_ret(void) { return rt_pair(rt_null(), K_TRUTH); }

V* rt_data_of(V* pair) { return pair->u.list.a[0]; }
V* rt_kind_of(V* pair) { return pair->u.list.a[1]; }

/* ------------------------------------------------------------------ */
/* ordered map                                                         */
/* ------------------------------------------------------------------ */

static Map* map_new(void) {
    Map* m = (Map*)xmalloc(sizeof(Map));
    m->nb = 8;
    m->b = (MapEntry**)xmalloc((size_t)m->nb * sizeof(MapEntry*));
    memset(m->b, 0, (size_t)m->nb * sizeof(MapEntry*));
    m->n = 0;
    m->head = m->tail = NULL;
    return m;
}

static void map_free(Map* m) {
    MapEntry* e = m->head;
    while (e) {
        MapEntry* next = e->inext;
        rt_decref(e->key);
        rt_decref(e->val);
        pool_free(e, sizeof(MapEntry));
        e = next;
    }
    pool_free(m->b, (size_t)m->nb * sizeof(MapEntry*));
    pool_free(m, sizeof(Map));
}

static MapEntry* map_find(Map* m, V* key, uint64_t h) {
    MapEntry* e = m->b[h & (uint64_t)(m->nb - 1)];
    while (e) {
        if (e->h == h && rt_veq(e->key, key)) return e;
        e = e->bnext;
    }
    return NULL;
}

static void map_insert_new(Map* m, V* key, V* val, uint64_t h) {
    if (m->n >= m->nb * 2) {
        /* rehash, preserving insertion order */
        int64_t nnb = m->nb * 2;
        MapEntry** nb = (MapEntry**)xmalloc((size_t)nnb * sizeof(MapEntry*));
        memset(nb, 0, (size_t)nnb * sizeof(MapEntry*));
        for (MapEntry* e = m->head; e; e = e->inext) {
            e->h = rt_hash(e->key);
            uint64_t bi = e->h & (uint64_t)(nnb - 1);
            e->bnext = nb[bi];
            nb[bi] = e;
        }
        pool_free(m->b, (size_t)m->nb * sizeof(MapEntry*));
        m->b = nb;
        m->nb = nnb;
    }
    MapEntry* e = (MapEntry*)xmalloc(sizeof(MapEntry));
    e->key = key;
    e->val = val;
    e->h = h;
    uint64_t bi = h & (uint64_t)(m->nb - 1);
    e->bnext = m->b[bi];
    m->b[bi] = e;
    e->iprev = m->tail;
    e->inext = NULL;
    if (m->tail) m->tail->inext = e; else m->head = e;
    m->tail = e;
    m->n++;
}

/* map_set: takes ownership of key and val. */
static void map_set(Map* m, V* key, V* val) {
    uint64_t h = rt_hash(key);
    MapEntry* e = map_find(m, key, h);
    if (e) {
        rt_decref(e->val);
        e->val = val;
        rt_decref(key);
        return;
    }
    map_insert_new(m, key, val, h);
}

static MapEntry* map_get_entry(Map* m, V* key) {
    uint64_t h = rt_hash(key);
    return map_find(m, key, h);
}

V* new_map_value(void) {
    V* v = (V*)xmalloc(sizeof(V));
    v->rc = 1;
    v->tag = T_MAP;
    v->u.m = map_new();
    return v;
}

/* ------------------------------------------------------------------ */
/* hashing / equality                                                  */
/* ------------------------------------------------------------------ */

uint64_t rt_hash(V* v) {
    switch (v->tag) {
    case T_NULL: return mix64(0x9E3779B97F4A7C15ULL);
    case T_BOOL:
    case T_INT:  return mix64((uint64_t)v->u.i);
    case T_FLOAT: {
        double f = v->u.f;
        if (f == (double)(long long)f && f >= -9.0e18 && f <= 9.0e18)
            return mix64((uint64_t)(long long)f);
        uint64_t bits;
        memcpy(&bits, &f, 8);
        return mix64(bits);
    }
    case T_STR:
        if (!v->u.str.h) v->u.str.h = fnv1a(v->u.str.s, v->u.str.len) | 1;
        return v->u.str.h;
    case T_LIST: {
        uint64_t h = 0x345678ULL;
        for (int64_t i = 0; i < v->u.list.n; i++)
            h = mix64(h ^ rt_hash(v->u.list.a[i]));
        return h;
    }
    case T_MAP: {
        uint64_t h = 0x777777ULL;
        for (MapEntry* e = v->u.m->head; e; e = e->inext)
            h = mix64(h ^ (rt_hash(e->key) * 31 + rt_hash(e->val)));
        return h;
    }
    }
    return 0;
}

static int veq_numeric(V* a, V* b) {
    int fa = a->tag == T_FLOAT, fb = b->tag == T_FLOAT;
    if (!fa && !fb) return a->u.i == b->u.i;
    double da = fa ? a->u.f : (double)a->u.i;
    double db = fb ? b->u.f : (double)b->u.i;
    return da == db;
}

int rt_veq(V* a, V* b) {
    if (a == b) return 1;   /* pointer-identical (interned strings, singletons) */
    int na = a->tag <= T_FLOAT;   /* null/bool/int/float */
    int nb = b->tag <= T_FLOAT;
    if (na && nb) {
        if (a->tag == T_NULL || b->tag == T_NULL) return a->tag == b->tag;
        return veq_numeric(a, b);
    }
    if (a->tag != b->tag) return 0;
    switch (a->tag) {
    case T_STR:
        return a->u.str.len == b->u.str.len &&
               memcmp(a->u.str.s, b->u.str.s, (size_t)a->u.str.len) == 0;
    case T_LIST:
        if (a->u.list.n != b->u.list.n) return 0;
        for (int64_t i = 0; i < a->u.list.n; i++)
            if (!rt_veq(a->u.list.a[i], b->u.list.a[i])) return 0;
        return 1;
    case T_MAP:
        if (a->u.m->n != b->u.m->n) return 0;
        for (MapEntry* e = a->u.m->head; e; e = e->inext) {
            MapEntry* f = map_get_entry(b->u.m, e->key);
            if (!f || !rt_veq(e->val, f->val)) return 0;
        }
        return 1;
    }
    return 0;
}

int rt_truthy_data(V* d) {
    switch (d->tag) {
    case T_NULL: return 0;
    case T_BOOL: return d->u.b;
    case T_INT:  return d->u.i != 0;
    case T_FLOAT: return d->u.f != 0.0;
    case T_STR:  return d->u.str.len > 0;
    case T_LIST: return d->u.list.n > 0;
    case T_MAP:  return d->u.m->n > 0;
    }
    return 0;
}

int64_t rt_len_raw(V* d) {
    switch (d->tag) {
    case T_STR:  return d->u.str.len;
    case T_LIST: return d->u.list.n;
    case T_MAP:  return d->u.m->n;
    }
    rt_abort("len() unsupported type");
    return 0;
}

/* ------------------------------------------------------------------ */
/* pair helpers                                                        */
/* ------------------------------------------------------------------ */

static int kind_is_truth(V* k) {
    if (k == K_TRUTH) return 1;
    if (k == K_GRAIN) return 0;
    /* defensive byte compare (kinds should always be the singletons) */
    return !(k->u.str.len == 5 && memcmp(k->u.str.s, "grain", 5) == 0);
}

static V* truth_and(V* a, V* b) {
    return (kind_is_truth(a) && kind_is_truth(b)) ? K_TRUTH : K_GRAIN;
}

/* ------------------------------------------------------------------ */
/* container primitives                                                */
/* ------------------------------------------------------------------ */

int rt_truthy(V* pair) {
    V* d = pair->u.list.a[0];
    if (d->tag != T_BOOL) rt_abort("condition is not a bool value");
    return d->u.b;
}

V* rt_index_get(V* cont, V* idx) {
    V* raw = cont->u.list.a[0];
    V* kind = cont->u.list.a[1];
    V* id = idx->u.list.a[0];   /* the raw index data */
    V* elem = NULL;
    if (raw->tag == T_LIST) {
        if (id->tag != T_INT && id->tag != T_BOOL) rt_abort("list index is not an integer");
        int64_t i = id->tag == T_INT ? id->u.i : (int64_t)id->u.b;
        if (i < 0) i += raw->u.list.n;
        if (i < 0 || i >= raw->u.list.n) rt_abort("list index out of range");
        elem = raw->u.list.a[i];
    } else if (raw->tag == T_MAP) {
        MapEntry* e = map_get_entry(raw->u.m, id);
        if (!e) rt_abort("map key not found");
        elem = e->val;
    } else if (raw->tag == T_STR) {
        if (id->tag != T_INT && id->tag != T_BOOL) rt_abort("string index is not an integer");
        int64_t i = id->tag == T_INT ? id->u.i : (int64_t)id->u.b;
        if (i < 0) i += raw->u.str.len;
        if (i < 0 || i >= raw->u.str.len) rt_abort("string index out of range");
        elem = rt_lit_n(raw->u.str.s + i, 1);
    } else {
        rt_abort("value is not indexable");
    }
    rt_incref(elem);
    rt_decref(cont);
    rt_decref(idx);
    return rt_pair(elem, kind);
}

void rt_index_set(V* cont, V* idx, V* val) {
    V* raw = cont->u.list.a[0];
    V* id = idx->u.list.a[0];           /* the raw index/key data */
    V* d = val->u.list.a[0];
    rt_incref(d);                       /* steal the data ref from val */
    if (raw->tag == T_LIST) {
        if (id->tag != T_INT && id->tag != T_BOOL)
            rt_abort("Invalid map/list assignment");
        int64_t i = id->tag == T_INT ? id->u.i : (int64_t)id->u.b;
        if (i < 0) rt_abort("Invalid list assignment: Negative indices not supported");
        while (i >= raw->u.list.n) list_append_raw(raw, rt_null());
        rt_decref(raw->u.list.a[i]);
        raw->u.list.a[i] = d;
    } else if (raw->tag == T_MAP) {
        if (id->tag == T_LIST || id->tag == T_MAP)
            rt_abort("Invalid map/list assignment");
        rt_incref(id);                  /* steal key ref from idx */
        map_set(raw->u.m, id, d);
    } else {
        rt_abort("Invalid map/list assignment");
    }
    rt_decref(cont);
    rt_decref(idx);
    rt_decref(val);
}

/* Literal-key fast paths: same semantics as rt_index_get/rt_index_set with
 * a string-literal map key, but without allocating a key pair. */
V* rt_index_get_lit(V* cont, const char* key) {
    V* raw = cont->u.list.a[0];
    V* kind = cont->u.list.a[1];
    if (raw->tag != T_MAP) rt_abort("literal index on non-map");
    V keyv;
    keyv.rc = RC_IMMORTAL;
    keyv.tag = T_STR;
    keyv.u.str.s = (char*)key;
    keyv.u.str.len = (int64_t)strlen(key);
    keyv.u.str.h = 0;
    MapEntry* e = map_get_entry(raw->u.m, &keyv);
    if (!e) rt_abort("map key not found");
    V* elem = e->val;
    rt_incref(elem);
    rt_decref(cont);
    return rt_pair(elem, kind);
}

void rt_index_set_lit(V* cont, const char* key, V* val) {
    V* raw = cont->u.list.a[0];
    V* d = val->u.list.a[0];
    rt_incref(d);                       /* steal the data ref from val */
    if (raw->tag != T_MAP) rt_abort("literal index on non-map");
    V* k = rt_lit(key);                 /* interned immortal key */
    map_set(raw->u.m, k, d);
    rt_decref(cont);
    rt_decref(val);
}

/* Zero-alloc comparison fast paths for the interpreter dispatch loop.
 * rt_eq_lit / rt_ne_lit consume a pair and compare its data against a C
 * string literal (pointer-identity first, then bytes); they return an owned
 * bool pair with the input's kind. rt_cond_* variants return a plain int
 * for condition positions (no pair allocation at all). */
static int strdata_eq_lit(V* d, const char* lit, int64_t litlen) {
    if (d->tag != T_STR) return 0;
    if (d->u.str.len != litlen) return 0;
    if (d->u.str.s == lit) return 1;   /* interned literal */
    return memcmp(d->u.str.s, lit, (size_t)litlen) == 0;
}

V* rt_eq_lit(V* pair, const char* lit) {
    int r = strdata_eq_lit(pair->u.list.a[0], lit, (int64_t)strlen(lit));
    V* out = rt_pair(rt_bool(r), pair->u.list.a[1]);
    rt_decref(pair);
    return out;
}

V* rt_ne_lit(V* pair, const char* lit) {
    int r = strdata_eq_lit(pair->u.list.a[0], lit, (int64_t)strlen(lit));
    V* out = rt_pair(rt_bool(!r), pair->u.list.a[1]);
    rt_decref(pair);
    return out;
}

int rt_cond_eq_lit(V* pair, const char* lit) {
    int r = strdata_eq_lit(pair->u.list.a[0], lit, (int64_t)strlen(lit));
    rt_decref(pair);
    return r;
}

int rt_cond_ne_lit(V* pair, const char* lit) {
    int r = strdata_eq_lit(pair->u.list.a[0], lit, (int64_t)strlen(lit));
    rt_decref(pair);
    return !r;
}

/* cond: map[key] == lit — consumes the container pair, zero allocation. */
int rt_cond_index_eq_lit(V* cont, const char* key, const char* lit) {
    V* raw = cont->u.list.a[0];
    if (raw->tag != T_MAP) { rt_decref(cont); return 0; }
    V keyv;
    keyv.rc = RC_IMMORTAL;
    keyv.tag = T_STR;
    keyv.u.str.s = (char*)key;
    keyv.u.str.len = (int64_t)strlen(key);
    keyv.u.str.h = 0;
    MapEntry* e = map_get_entry(raw->u.m, &keyv);
    int r = 0;
    if (e) r = strdata_eq_lit(e->val, lit, (int64_t)strlen(lit));
    rt_decref(cont);
    return r;
}

/* ------------------------------------------------------------------ */
/* literals from element pairs                                         */
/* ------------------------------------------------------------------ */

V* rt_make_list(int64_t n, V** elems) {
    V* raw = new_list();
    int all_truth = 1;
    for (int64_t i = 0; i < n; i++) {
        V* e = elems[i];
        rt_incref(e->u.list.a[0]);
        list_append_raw(raw, e->u.list.a[0]);
        if (!kind_is_truth(e->u.list.a[1])) all_truth = 0;
        rt_decref(e);
    }
    return rt_pair(raw, all_truth ? K_TRUTH : K_GRAIN);
}

V* rt_make_map(int64_t n, V** keys, V** vals) {
    V* raw = new_map_value();
    int all_truth = 1;
    for (int64_t i = 0; i < n; i++) {
        V* k = keys[i];
        V* v = vals[i];
        if (k->u.list.a[0]->tag == T_LIST || k->u.list.a[0]->tag == T_MAP)
            rt_abort("unhashable map key");
        rt_incref(k->u.list.a[0]);
        rt_incref(v->u.list.a[0]);
        map_set(raw->u.m, k->u.list.a[0], v->u.list.a[0]);
        if (!kind_is_truth(v->u.list.a[1])) all_truth = 0;
        rt_decref(k);
        rt_decref(v);
    }
    return rt_pair(raw, all_truth ? K_TRUTH : K_GRAIN);
}

V* rt_new_list(void) { return new_list(); }

void rt_list_append(V* lst, V* elem) { list_append_raw(lst, elem); }

V* rt_new_map(void) { return new_map_value(); }

void rt_map_set_raw(V* mapval, V* key, V* val) {
    /* takes ownership of key and val (raw key/value V*s) */
    map_set(mapval->u.m, key, val);
}

/* ------------------------------------------------------------------ */
/* arithmetic                                                          */
/* ------------------------------------------------------------------ */

static int is_numeric_tag(uint8_t t) { return t == T_INT || t == T_FLOAT || t == T_BOOL; }

static int64_t as_int(V* d) {
    if (d->tag == T_INT) return d->u.i;
    if (d->tag == T_BOOL) return d->u.b;
    rt_abort("expected int");
    return 0;
}

static double as_float(V* d) {
    if (d->tag == T_FLOAT) return d->u.f;
    if (d->tag == T_INT) return (double)d->u.i;
    if (d->tag == T_BOOL) return (double)d->u.b;
    rt_abort("expected number");
    return 0;
}

/* numeric binary op on raw data: both int-ish -> int result, else float. */
V* num_bin_data(V* da, V* db, char op) {
    if (!is_numeric_tag(da->tag) || !is_numeric_tag(db->tag))
        rt_abort("arithmetic on non-numeric operands");
    if (da->tag != T_FLOAT && db->tag != T_FLOAT) {
        int64_t x = as_int(da), y = as_int(db), r = 0;
        switch (op) {
        case '+': r = x + y; break;
        case '-': r = x - y; break;
        case '*': r = x * y; break;
        default: rt_abort("bad int op");
        }
        return rt_int(r);
    }
    double x = as_float(da), y = as_float(db), r = 0;
    switch (op) {
    case '+': r = x + y; break;
    case '-': r = x - y; break;
    case '*': r = x * y; break;
    default: rt_abort("bad float op");
    }
    return rt_float(r);
}

V* rt_add(V* a, V* b) {
    V* da = a->u.list.a[0];
    V* db = b->u.list.a[0];
    V* res;
    if (da->tag == T_STR && db->tag == T_STR) {
        int64_t n = da->u.str.len + db->u.str.len;
        char* buf = (char*)xmalloc((size_t)n);
        memcpy(buf, da->u.str.s, (size_t)da->u.str.len);
        memcpy(buf + da->u.str.len, db->u.str.s, (size_t)db->u.str.len);
        res = rt_str_copy(buf, n);
        pool_free(buf, (size_t)n);
    } else {
        res = num_bin_data(da, db, '+');
    }
    V* out = rt_pair(res, truth_and(a->u.list.a[1], b->u.list.a[1]));
    rt_decref(a);
    rt_decref(b);
    return out;
}

V* rt_sub(V* a, V* b) {
    V* res = num_bin_data(a->u.list.a[0], b->u.list.a[0], '-');
    V* out = rt_pair(res, truth_and(a->u.list.a[1], b->u.list.a[1]));
    rt_decref(a);
    rt_decref(b);
    return out;
}

V* rt_mul(V* a, V* b) {
    V* res = num_bin_data(a->u.list.a[0], b->u.list.a[0], '*');
    V* out = rt_pair(res, truth_and(a->u.list.a[1], b->u.list.a[1]));
    rt_decref(a);
    rt_decref(b);
    return out;
}

V* rt_div(V* a, V* b) {
    V* da = a->u.list.a[0];
    V* db = b->u.list.a[0];
    if (!is_numeric_tag(da->tag) || !is_numeric_tag(db->tag))
        rt_abort("division requires numeric operands");
    double y = as_float(db);
    if (y == 0.0) rt_abort("division by zero");
    V* res = rt_float(as_float(da) / y);
    V* out = rt_pair(res, truth_and(a->u.list.a[1], b->u.list.a[1]));
    rt_decref(a);
    rt_decref(b);
    return out;
}

V* rt_mod(V* a, V* b) {
    V* da = a->u.list.a[0];
    V* db = b->u.list.a[0];
    if (!is_numeric_tag(da->tag) || !is_numeric_tag(db->tag))
        rt_abort("modulo requires numeric operands");
    V* res;
    if (da->tag != T_FLOAT && db->tag != T_FLOAT) {
        int64_t x = as_int(da), y = as_int(db);
        if (y == 0) rt_abort("modulo by zero");
        int64_t r = x % y;
        if (r != 0 && ((r < 0) != (y < 0))) r += y;   /* Python floor-mod */
        res = rt_int(r);
    } else {
        double x = as_float(da), y = as_float(db);
        if (y == 0.0) rt_abort("modulo by zero");
        double r = fmod(x, y);
        if (r != 0.0 && ((r < 0) != (y < 0))) r += y;
        res = rt_float(r);
    }
    V* out = rt_pair(res, truth_and(a->u.list.a[1], b->u.list.a[1]));
    rt_decref(a);
    rt_decref(b);
    return out;
}

V* rt_neg(V* a) {
    V* d = a->u.list.a[0];
    V* res;
    if (d->tag == T_INT) res = rt_int(-d->u.i);
    else if (d->tag == T_BOOL) res = rt_int(-(int64_t)d->u.b);
    else if (d->tag == T_FLOAT) res = rt_float(-d->u.f);
    else rt_abort("unary - on non-numeric");
    V* out = rt_pair(res, a->u.list.a[1]);
    rt_decref(a);
    return out;
}

/* ------------------------------------------------------------------ */
/* comparisons                                                         */
/* ------------------------------------------------------------------ */

static int cmp_data(V* a, V* b) {  /* -1, 0, 1 for compatible types */
    if (a->tag <= T_FLOAT && b->tag <= T_FLOAT &&
        a->tag != T_NULL && b->tag != T_NULL) {
        if (a->tag != T_FLOAT && b->tag != T_FLOAT) {
            int64_t x = as_int(a), y = as_int(b);
            return x < y ? -1 : x > y ? 1 : 0;
        }
        double x = as_float(a), y = as_float(b);
        return x < y ? -1 : x > y ? 1 : 0;
    }
    if (a->tag == T_STR && b->tag == T_STR) {
        int64_t n = a->u.str.len < b->u.str.len ? a->u.str.len : b->u.str.len;
        int c = memcmp(a->u.str.s, b->u.str.s, (size_t)n);
        if (c) return c < 0 ? -1 : 1;
        return a->u.str.len < b->u.str.len ? -1 : a->u.str.len > b->u.str.len ? 1 : 0;
    }
    if (a->tag == T_LIST && b->tag == T_LIST) {
        int64_t n = a->u.list.n < b->u.list.n ? a->u.list.n : b->u.list.n;
        for (int64_t i = 0; i < n; i++) {
            if (!rt_veq(a->u.list.a[i], b->u.list.a[i]))
                return cmp_data(a->u.list.a[i], b->u.list.a[i]);
        }
        return a->u.list.n < b->u.list.n ? -1 : a->u.list.n > b->u.list.n ? 1 : 0;
    }
    rt_abort("ordered comparison of incompatible types");
    return 0;
}

static V* cmp_op(V* a, V* b, int which) {
    /* which: 0=eq 1=ne 2=lt 3=le 4=gt 5=ge */
    V* da = a->u.list.a[0];
    V* db = b->u.list.a[0];
    int r;
    if (which == 0) r = rt_veq(da, db);
    else if (which == 1) r = !rt_veq(da, db);
    else {
        int c = cmp_data(da, db);
        r = which == 2 ? c < 0 : which == 3 ? c <= 0 : which == 4 ? c > 0 : c >= 0;
    }
    V* out = rt_pair(rt_bool(r), truth_and(a->u.list.a[1], b->u.list.a[1]));
    rt_decref(a);
    rt_decref(b);
    return out;
}

V* rt_eq(V* a, V* b) { return cmp_op(a, b, 0); }
V* rt_ne(V* a, V* b) { return cmp_op(a, b, 1); }
V* rt_lt(V* a, V* b) { return cmp_op(a, b, 2); }
V* rt_le(V* a, V* b) { return cmp_op(a, b, 3); }
V* rt_gt(V* a, V* b) { return cmp_op(a, b, 4); }
V* rt_ge(V* a, V* b) { return cmp_op(a, b, 5); }

V* rt_bool_not(V* a) {
    V* out = rt_pair(rt_bool(!rt_truthy_data(a->u.list.a[0])), a->u.list.a[1]);
    rt_decref(a);
    return out;
}

/* Matches the Stage 0 compiler's short-circuit forms: `a and b` keeps the
 * left bool/kind when the left is falsy; `a or b` keeps it when truthy.
 * Operands in the transpiled modules are pure, so eager evaluation of both
 * sides is result-equivalent. */
V* rt_bool_and(V* a, V* b) {
    int ta = rt_truthy_data(a->u.list.a[0]);
    V* kind = ta ? truth_and(a->u.list.a[1], b->u.list.a[1]) : a->u.list.a[1];
    V* out = rt_pair(rt_bool(ta && rt_truthy_data(b->u.list.a[0])), kind);
    rt_decref(a);
    rt_decref(b);
    return out;
}

V* rt_bool_or(V* a, V* b) {
    int ta = rt_truthy_data(a->u.list.a[0]);
    V* kind = ta ? a->u.list.a[1] : truth_and(a->u.list.a[1], b->u.list.a[1]);
    V* out = rt_pair(rt_bool(ta || rt_truthy_data(b->u.list.a[0])), kind);
    rt_decref(a);
    rt_decref(b);
    return out;
}

/* ------------------------------------------------------------------ */
/* Python repr(float)                                                  */
/* ------------------------------------------------------------------ */

char* py_float_repr(double x, int64_t* out_len) {
    char buf[64];
    if (isnan(x)) {
        char* s = rt_str_copy("nan", 3)->u.str.s;
        if (out_len) *out_len = 3;
        return s;
    }
    int neg = signbit(x) ? 1 : 0;
    if (isinf(x)) {
        char* s = rt_str_copy(neg ? "-inf" : "inf", neg ? 4 : 3)->u.str.s;
        if (out_len) *out_len = neg ? 4 : 3;
        return s;
    }
    /* shortest round-trip digits */
    int prec;
    for (prec = 1; prec <= 17; prec++) {
        snprintf(buf, sizeof buf, "%.*g", prec, x);
        if (strtod(buf, NULL) == x) break;
    }
    if (prec > 17) prec = 17;
    /* parse buf: [sign] digits [. digits] [e[+-]NN] */
    const char* p = buf;
    if (*p == '-') p++;
    char digits[40];
    int nd = 0;
    int dotpos = -1;   /* index in mantissa where '.' appeared */
    int e10 = 0;
    const char* epos = strchr(p, 'e');
    int mlen = (int)(epos ? epos - p : (int64_t)strlen(p));
    for (int i = 0; i < mlen; i++) {
        if (p[i] == '.') { dotpos = nd; continue; }
        digits[nd++] = p[i];
    }
    if (dotpos < 0) dotpos = nd;
    if (epos) e10 = atoi(epos + 1);
    int decpt = dotpos + e10;
    /* strip leading zeros */
    int lead = 0;
    while (lead < nd - 1 && digits[lead] == '0') { lead++; decpt--; }
    if (lead) {
        memmove(digits, digits + lead, (size_t)(nd - lead));
        nd -= lead;
    }
    /* strip trailing zeros (should not occur with %g, but be safe) */
    while (nd > 1 && digits[nd - 1] == '0') nd--;
    digits[nd] = 0;

    char out[80];
    out[0] = 0;
    int use_exp = (decpt <= -4 || decpt > 16);
    if (x == 0.0) {
        snprintf(out, sizeof out, "%s0.0", neg ? "-" : "");
    } else if (use_exp) {
        int e = decpt - 1;
        if (nd == 1)
            snprintf(out, sizeof out, "%s%ce%c%02d", neg ? "-" : "", digits[0],
                     e < 0 ? '-' : '+', e < 0 ? -e : e);
        else
            snprintf(out, sizeof out, "%s%c.%se%c%02d", neg ? "-" : "", digits[0],
                     digits + 1, e < 0 ? '-' : '+', e < 0 ? -e : e);
    } else if (decpt <= 0) {
        snprintf(out, sizeof out, "%s0.", neg ? "-" : "");
        for (int i = 0; i < -decpt; i++) strcat(out, "0");
        strcat(out, digits);
    } else if (decpt >= nd) {
        snprintf(out, sizeof out, "%s", neg ? "-" : "");
        strcat(out, digits);
        for (int i = 0; i < decpt - nd; i++) strcat(out, "0");
        strcat(out, ".0");
    } else {
        snprintf(out, sizeof out, "%s%.*s.%s", neg ? "-" : "", decpt, digits, digits + decpt);
    }
    if (out_len) *out_len = (int64_t)strlen(out);
    return rt_str_copy(out, (int64_t)strlen(out))->u.str.s;
}

/* ------------------------------------------------------------------ */
/* deterministic Math.random seed                                      */
/* ------------------------------------------------------------------ */

static int64_t g_math_seed = 123456789;

void math_seed_reset(void) { g_math_seed = 123456789; }

double math_next_random(void) {
    g_math_seed = (1103515245LL * g_math_seed + 12345) % 2147483648LL;
    return (double)g_math_seed / 2147483648.0;
}