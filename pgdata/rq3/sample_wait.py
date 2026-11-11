"""RQ3 표본 수집기 (문서 3.6절). SIGTERM을 받을 때까지 돈다.

  sample_wait.py --out <run 디렉터리> [--interval 0.1]

100ms마다
  activity.csv.gz : pg_stat_activity (자기 자신 제외) — 표본 시각(now(), 표본 안에서 같음), pid, leader_pid, backend_type, state,
                    wait_event_type, wait_event, application_name, query 앞부분
  progress.csv    : pg_stat_progress_create_index — 재빌드 단계(phase)와 진행량
1초마다
  system.csv      : 파티션 블록 통계 차분(읽기/쓰기 요청·바이트·대기), CPU 사용률(/proc/stat), PGDATA 파일시스템 사용량
DB 연결은 PG* 환경변수를 따른다.
"""
import argparse
import csv
import gzip
import os
import signal
import time
from pathlib import Path

import psycopg

ACT_SQL = """
SELECT extract(epoch FROM now()), pid, leader_pid, backend_type, state,
       wait_event_type, wait_event, application_name, left(regexp_replace(query, '\\s+', ' ', 'g'), 40)
FROM pg_stat_activity WHERE pid <> pg_backend_pid()
"""
PROG_SQL = """
SELECT extract(epoch FROM now()), pid, phase, lockers_total, lockers_done,
       blocks_total, blocks_done, tuples_total, tuples_done
FROM pg_stat_progress_create_index
"""
STOP = False


def on_term(*_):
    global STOP
    STOP = True


def blk_stat(path):
    v = list(map(int, Path(path).read_text().split()))
    # read_ios, read_sectors, read_ticks, write_ios, write_sectors, write_ticks, io_ticks
    return v[0], v[2], v[3], v[4], v[6], v[7], v[9]


def cpu_stat():
    f = list(map(int, Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]))
    idle = f[3] + f[4]                    # idle + iowait
    return sum(f[:8]), idle, f[4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--pgdata", default=os.environ.get("PGDATA", "."))
    a = ap.parse_args()
    out = Path(a.out)
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    st = os.stat(a.pgdata)
    blk = Path(os.path.realpath(f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}")) / "stat"

    with psycopg.connect(autocommit=True, application_name="rq3_sampler") as conn, \
            gzip.open(out / "activity.csv.gz", "wt", newline="") as fa, \
            open(out / "progress.csv", "w", newline="") as fp, \
            open(out / "system.csv", "w", newline="") as fs:
        wa, wp, ws = csv.writer(fa), csv.writer(fp), csv.writer(fs)
        wa.writerow(["t", "pid", "leader_pid", "backend_type", "state", "wait_event_type", "wait_event",
                     "application_name", "query"])
        wp.writerow(["t", "pid", "phase", "lockers_total", "lockers_done", "blocks_total", "blocks_done",
                     "tuples_total", "tuples_done"])
        ws.writerow(["t", "dt", "r_ios", "r_bytes", "r_await_ms", "w_ios", "w_bytes", "w_await_ms", "util",
                     "cpu_busy", "cpu_iowait", "fs_used_bytes"])
        prev_b, prev_c, prev_t = blk_stat(blk), cpu_stat(), time.time()
        next_t, next_sys = time.monotonic(), time.monotonic() + 1.0
        while not STOP:
            for r in conn.execute(ACT_SQL):
                wa.writerow(r)
            for r in conn.execute(PROG_SQL):
                wp.writerow(r)
            now = time.monotonic()
            if now >= next_sys:
                b, c, t = blk_stat(blk), cpu_stat(), time.time()
                d = [x - y for x, y in zip(b, prev_b)]
                tot, idle, iow = (x - y for x, y in zip(c, prev_c))
                dt = t - prev_t
                vfs = os.statvfs(a.pgdata)
                ws.writerow([f"{t:.3f}", f"{dt:.3f}", d[0], d[1] * 512, round(d[2] / d[0], 3) if d[0] else 0,
                             d[3], d[4] * 512, round(d[5] / d[3], 3) if d[3] else 0, round(d[6] / (dt * 1000), 4),
                             round(1 - idle / tot, 4) if tot else 0, round(iow / tot, 4) if tot else 0,
                             (vfs.f_blocks - vfs.f_bfree) * vfs.f_frsize])
                fs.flush()
                prev_b, prev_c, prev_t = b, c, t
                next_sys += 1.0
            next_t += a.interval
            time.sleep(max(0.0, next_t - time.monotonic()))


if __name__ == "__main__":
    main()
