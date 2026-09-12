/* Micro-test: does repeated map_set with an equal key grow the map? */
#include "value.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    rt_init();
    V* m = rt_new_map();
    /* same V* key each time */
    V* k1 = rt_str_copy("total", 5);
    for (int i = 0; i < 100000; i++) {
        rt_map_set_raw(m, rt_incref(k1), rt_int(1));
    }
    printf("same-V key:      n=%lld (expect 1)\n", (long long)m->u.m->n);

    /* fresh equal-bytes key each time (like parsed document strings) */
    V* m2 = rt_new_map();
    for (int i = 0; i < 100000; i++) {
        rt_map_set_raw(m2, rt_str_copy("total", 5), rt_int(1));
    }
    printf("fresh-eq keys:   n=%lld (expect 1)\n", (long long)m2->u.m->n);

    /* interned vs parsed key mix, like rt_index_get(state, "frames") */
    V* m3 = rt_new_map();
    rt_map_set_raw(m3, rt_str_copy("frames", 6), rt_new_list());
    long long misses = 0;
    for (int i = 0; i < 100000; i++) {
        V* k = rt_lit("frames");
        uint64_t h = rt_hash(k);
        MapEntry* e = m3->u.m->b[h & (uint64_t)(m3->u.m->nb - 1)];
        int found = 0;
        while (e) {
            if (e->h == h && rt_veq(e->key, k)) { found = 1; break; }
            e = e->bnext;
        }
        if (!found) misses++;
    }
    printf("lookup misses:   %lld (expect 0)\n", misses);
    printf("m3 n=%lld (expect 1)\n", (long long)m3->u.m->n);
    return 0;
}