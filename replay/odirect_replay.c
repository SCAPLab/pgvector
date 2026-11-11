/*
 * odirect_replay: list 하나를 B 크기 정렬 단위로 O_DIRECT 동기 읽기 (QD1) 하는 재생 도구 (문서 3.4절, RQ2).
 * PostgreSQL은 관여하지 않고 인덱스 세그먼트 파일을 직접 읽는다.
 *
 *   odirect_replay <plan> <B> <reps> <out.csv> <seg0> [seg1 ...]
 *
 * plan: 한 줄에 "<list_id> <seg> <offset>" (list별로 묶여 있고 offset 오름차순, offset은 B의 배수).
 *       같은 list_id가 연속된 줄이 list 하나의 로드다. list 순서는 plan 순서를 따른다.
 * out : list_id,B,rep,app_requests,bytes_read,wall_time_us
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef struct { int64_t list; int seg; int64_t off; } Req;

static double now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e6 + ts.tv_nsec / 1e3;
}

int main(int argc, char **argv)
{
    if (argc < 6) {
        fprintf(stderr, "usage: %s <plan> <B> <reps> <out.csv> <seg0> [seg1 ...]\n", argv[0]);
        return 2;
    }
    const char *plan_path = argv[1];
    long B = atol(argv[2]);
    int reps = atoi(argv[3]);
    const char *out_path = argv[4];
    int nseg = argc - 5;
    if (B < 512 || (B & (B - 1)) != 0 || reps < 1) {
        fprintf(stderr, "B는 512 이상의 2의 거듭제곱, reps >= 1\n");
        return 2;
    }

    int *fds = calloc(nseg, sizeof(int));
    for (int s = 0; s < nseg; s++) {
        fds[s] = open(argv[5 + s], O_RDONLY | O_DIRECT);
        if (fds[s] < 0) { fprintf(stderr, "open %s: %s\n", argv[5 + s], strerror(errno)); return 1; }
    }

    FILE *pf = fopen(plan_path, "r");
    if (!pf) { perror(plan_path); return 1; }
    size_t cap = 1 << 16, n = 0;
    Req *rq = malloc(cap * sizeof(Req));
    long long l, o; int sg;
    while (fscanf(pf, "%lld %d %lld", &l, &sg, &o) == 3) {
        if (sg < 0 || sg >= nseg || o % B != 0) {
            fprintf(stderr, "plan 오류: list %lld seg %d off %lld\n", l, sg, o);
            return 1;
        }
        if (n == cap) { cap *= 2; rq = realloc(rq, cap * sizeof(Req)); }
        rq[n++] = (Req){ l, sg, o };
    }
    fclose(pf);

    void *buf;
    if (posix_memalign(&buf, 4096, B) != 0) { fprintf(stderr, "posix_memalign 실패\n"); return 1; }

    FILE *out = fopen(out_path, "w");
    if (!out) { perror(out_path); return 1; }
    fprintf(out, "list_id,B,rep,app_requests,bytes_read,wall_time_us\n");

    for (int r = 0; r < reps; r++) {
        size_t i = 0;
        while (i < n) {
            size_t j = i;
            long long reqs = 0, bytes = 0;
            double t0 = now_us();
            while (j < n && rq[j].list == rq[i].list) {
                ssize_t got = pread(fds[rq[j].seg], buf, B, rq[j].off);
                if (got < 0) {
                    fprintf(stderr, "pread seg %d off %lld: %s\n", rq[j].seg, (long long) rq[j].off, strerror(errno));
                    return 1;
                }
                reqs++;
                bytes += got;
                j++;
            }
            double t1 = now_us();
            fprintf(out, "%lld,%ld,%d,%lld,%lld,%.1f\n", (long long) rq[i].list, B, r, reqs, bytes, t1 - t0);
            i = j;
        }
    }
    fclose(out);
    for (int s = 0; s < nseg; s++) close(fds[s]);
    free(buf);
    free(rq);
    free(fds);
    return 0;
}
