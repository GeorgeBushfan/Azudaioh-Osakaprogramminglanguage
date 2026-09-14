/* value.h — Gate E native host: value model and runtime API.
 *
 * A runtime value V* is raw data: null, bool, int (int64), float (double),
 * string (byte buffer), list (dynamic array of V*), or map (insertion-ordered
 * hash map keyed by V*).
 *
 * A guest "Value" ([data, kind] pair) is represented literally as a 2-element
 * list V*: element 0 is the data, element 1 is the kind string ("truth" or
 * "grain"). Kind strings are interned singletons (K_TRUTH / K_GRAIN) and are
 * therefore immortal and borrow-safe.
 *
 * Ownership: every runtime function (rt_ and b_ prefixes) CONSUMES its V*
 * arguments (the caller transfers ownership) and returns an OWNED V*.
 * Variable references in
 * generated code use rt_incref. Containers own their elements; extraction
 * increfs. Cached/interned values (small ints, bools, null, interned string
 * literals, kind strings) carry rc == RC_IMMORTAL and are never freed.
 */
#ifndef OSAKA_VALUE_H
#define OSAKA_VALUE_H

#include <stdint.h>
#include <stddef.h>
#include <setjmp.h>

typedef struct V V;
typedef struct Map Map;

enum {
    T_NULL = 0, T_BOOL, T_INT, T_FLOAT, T_STR, T_LIST, T_MAP
};

#define RC_IMMORTAL 0x7FFFFFFF

struct V {
    int32_t rc;
    uint8_t tag;
    union {
        int64_t i;                       /* T_INT   */
        double f;                        /* T_FLOAT */
        int b;                           /* T_BOOL  */
        struct { char* s; int64_t len; uint64_t h; } str;  /* T_STR */
        struct { V** a; int64_t n; int64_t cap; } list;    /* T_LIST */
        Map* m;                          /* T_MAP   */
    } u;
};

/* Ordered map: hash buckets + insertion-order chain. */
typedef struct MapEntry {
    V* key;
    V* val;
    struct MapEntry* bnext;   /* bucket chain */
    struct MapEntry* inext;   /* insertion order */
    struct MapEntry* iprev;
    uint64_t h;
} MapEntry;

struct Map {
    MapEntry** b;
    int64_t nb;               /* bucket count (power of two) */
    int64_t n;                /* entry count */
    MapEntry* head;
    MapEntry* tail;
};

/* Kind singletons (interned; every kind string in the system is one of these). */
extern V g_truth_s, g_grain_s;
#define K_TRUTH (&g_truth_s)
#define K_GRAIN (&g_grain_s)

/* ---------- lifecycle ---------- */
void rt_init(void);              /* initialize singletons and caches */
V* rt_incref(V* v);
void rt_decref(V* v);

/* ---------- constructors (return owned values; immortals where noted) --- */
V* rt_null(void);                /* immortal singleton */
V* rt_bool(int b);               /* cached immortal */
V* rt_int(int64_t i);            /* cached immortal for |i| < 4096 */
V* rt_float(double d);           /* fresh */
V* rt_str_copy(const char* s, int64_t len);  /* fresh */
V* rt_lit(const char* s);        /* interned immortal */
V* rt_lit_n(const char* s, int64_t len);

/* pair constructors: data is consumed, kind is borrowed (must be a kind
 * singleton). The result is a fresh 2-element list [data, kind]. */
V* rt_pair(V* data, V* kind);
V* rt_pair_take_kind(V* data, V* consumed_pair); /* kind stolen from consumed pair */
V* rt_default_ret(void);         /* fresh [null, truth] pair */

/* list/map literals from element pairs (all consumed): host MAKE_LIST /
 * MAKE_MAP semantics — store the elements' .data, kind = all-truth rule. */
V* rt_make_list(int64_t n, V** elems);
V* rt_make_map(int64_t n, V** keys, V** vals);

/* empty-container constructors and raw-element mutation (used by the JSON
 * parser and the loader). rt_list_append / rt_map_set_raw take ownership. */
V* rt_new_list(void);
void rt_list_append(V* lst, V* elem);
V* rt_new_map(void);
void rt_map_set_raw(V* mapval, V* key, V* val);

/* ---------- pair accessors (borrowed views) ---------- */
V* rt_data_of(V* pair);          /* pair->[0], borrowed */
V* rt_kind_of(V* pair);          /* pair->[1], borrowed */

/* ---------- container primitives (host Value semantics) ---------- */
/* INDEX_GET: unwrap pair->[0], index it, wrap with pair->[1]. Consumes both. */
V* rt_index_get(V* cont, V* idx);
/* Literal-key fast paths (no key-pair allocation). rt_index_get_lit
 * consumes cont; rt_index_set_lit consumes cont and val. */
V* rt_index_get_lit(V* cont, const char* key);
void rt_index_set_lit(V* cont, const char* key, V* val);
/* INDEX_SET: raw = pair->[0]; raw[idx->data] = val->data. Consumes all. */
void rt_index_set(V* cont, V* idx, V* val);
/* Truthiness of a condition pair (data must be bool). Borrows. */
int rt_truthy(V* pair);

/* ---------- arithmetic / comparison (consume args, return owned pair) --- */
V* rt_add(V* a, V* b);
V* rt_sub(V* a, V* b);
V* rt_mul(V* a, V* b);
V* rt_div(V* a, V* b);           /* Python true division: always float */
V* rt_mod(V* a, V* b);           /* Python floor-mod semantics */
V* rt_neg(V* a);
V* rt_eq (V* a, V* b);
V* rt_ne (V* a, V* b);
V* rt_lt (V* a, V* b);
V* rt_le (V* a, V* b);
V* rt_gt (V* a, V* b);
V* rt_ge (V* a, V* b);
V* rt_bool_not(V* a);
/* Zero-alloc string-literal comparisons (dispatch fast paths). The rt_eq_lit
 * / rt_ne_lit forms consume a pair and return an owned bool pair; the
 * rt_cond_* variants return a plain int for condition positions.
 * rt_cond_index_eq_lit(cont, key, lit) tests map[key] == lit with no
 * allocation at all. */
V* rt_eq_lit(V* pair, const char* lit);
V* rt_ne_lit(V* pair, const char* lit);
int rt_cond_eq_lit(V* pair, const char* lit);
int rt_cond_ne_lit(V* pair, const char* lit);
int rt_cond_index_eq_lit(V* cont, const char* key, const char* lit);
V* rt_bool_and(V* a, V* b);
V* rt_bool_or (V* a, V* b);

/* ---------- value helpers ---------- */
uint64_t rt_hash(V* v);         /* structural hash (cached for strings) */
int rt_veq(V* a, V* b);         /* deep data equality (borrowed) */
int rt_truthy_data(V* data);    /* Python bool(data) on raw data */
int64_t rt_len_raw(V* data);    /* len() of str/list/map raw data */
void rt_abort(const char* msg); /* internal invariant violation */

/* Guest-facing failure propagation: host builtins that would raise on the
 * Python host (file I/O errors, Math domain errors) longjmp to the run-level
 * handler when armed, matching the unhandled-error behavior of vm.saka
 * running on the Python host (vm.saka has no try blocks of its own). */
extern jmp_buf rt_err_jmp;
extern int rt_err_armed;
extern char* rt_err_message;
void rt_fail(const char* fmt, ...);

/* Python repr(float) — see docs/NATIVE_HOST_CONTRACT.md §3.4. */
char* py_float_repr(double x, int64_t* out_len);
/* Release a buffer obtained from the runtime pool (e.g. py_float_repr). */
void rt_free_buf(char* p, int64_t n);

/* ---------- host boundary builtins (consume args, return owned pair) ---- */
V* b_json_type(V* v);
V* b_is_bool(V* v);
V* b_is_float(V* v);
V* b_float_repr(V* v);
V* b_math_call(V* name, V* pairs);
V* b_push_scope(void);
V* b_pop_scope(void);
V* b_read_file(V* path);
V* b_write_file(V* path, V* content);
V* b_append_file(V* path, V* content);
V* b_file_exists(V* path);
V* b_delete_file(V* path);
V* b_slice(V* x, V* a, V* b);
V* b_len(V* x);
V* b_push(V* lst, V* v);
V* b_pop(V* lst);
V* b_keys(V* m);
V* b_values(V* m);
V* b_contains(V* m, V* k);
V* b_americaya(V* v);
/* Stage-1 runtime module loading (contract §9.1): __sbc_load__(path, base).
 * Resolves path against base (the current document path, or null for
 * process-cwd-relative), reads and validates the SBC1 artifact, and returns
 * {"ok": 1, "path": <resolved>, "document": <canonical doc>} or
 * {"ok": 0, "message": <error text>} — never raises. */
V* b_sbc_load(V* path, V* base);

/* Deterministic Math.random seed (reset per run by main.c). */
void math_seed_reset(void);

/* ---------- JSON ---------- */
V* json_parse(const char* text, int64_t len, const char** err);
void json_write(V* v, void (*emit)(const char* bytes, int64_t len, void* ud),
                void* ud);

/* ---------- SBC1 document loading (canonical schema, contract §6) ------- */
/* Returns the document as a pair(map, truth); exits with the contract error
 * text on stderr if the document is malformed. */
V* load_document(const char* text);

#endif /* OSAKA_VALUE_H */