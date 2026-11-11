#!/usr/bin/env bash
# RQ3: 서비스 중 재빌드(REINDEX INDEX CONCURRENTLY) 경합 (문서 3.6절, 10M·50% 단편화 고정).
#
#   rq3/run_rq3.sh setup     template DB rq3_tpl 생성·검증 (한 번). ivf DB의 items(50%, 단편화 인덱스)를
#                            FILE_COPY로 복제하므로 인덱스 파일의 블록 배치가 그대로 유지된다.
#   rq3/run_rq3.sh maxqps    메모리 조건별 최대 QPS (재빌드 없이 closed loop, -c 16 -j 8, 60초 x 3의 중앙값)
#   rq3/run_rq3.sh runs      ROUNDS 회차 x (메모리 2 x 부하 3 x 병렬도 3) — 회차마다 순서를 섞음, 끝난 run은 건너뜀
#   rq3/run_rq3.sh one MEM LOAD W NAME   run 하나 (시험용, 산출 OUT/runs/NAME)
#
# run 하나: 조건대로 재시작 → template에서 rq3 DB 새로 복제 → (mem이면 prewarm) → 수집기 시작
#           → pgbench -R (open loop) → BASE_S초 뒤 REINDEX CONCURRENTLY → 끝나고 AFTER_S초 뒤 pgbench 종료(SIGALRM)
#           → 8GB+prewarm으로 재시작해 재빌드 후 recall@10
# MEM: io = shared_buffers 128MB (I/O 경합 위주), mem = 8GB + pg_prewarm (CPU 경합 위주)
# env: ROUNDS(3) BASE_S(300) AFTER_S(300) MEMS("io mem") LOADS("30 60 90") WORKERS("0 2 4") OUT(results/rq3)
set -euo pipefail
source "$(dirname "$0")/../env/config.sh"
cd "$IVF_ROOT"

ROUNDS=${ROUNDS:-3}
BASE_S=${BASE_S:-300}
AFTER_S=${AFTER_S:-300}
MAX_T=${MAX_T:-7200}
MEMS=${MEMS:-"io mem"}
LOADS=${LOADS:-"30 60 90"}
WORKERS=${WORKERS:-"0 2 4"}
OUT=${OUT:-$IVF_ROOT/results/rq3}
HELPER="sudo -n /usr/local/sbin/ivf-root"
PGBENCH=$PGBIN/pgbench
DISK=$(lsblk -no PKNAME "$(findmnt -no SOURCE -T "$PGDATA")")
mkdir -p "$OUT/setup" "$OUT/runs"

log() { echo "[$(date '+%F %T')] $*"; }
now() { date +%s.%N; }
db_exists() { [ "$($PSQL -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$1'")" = 1 ]; }

BG_PIDS=()
TUNED=0
cleanup() {
  local rc=$?
  for p in "${BG_PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  if [ $TUNED = 1 ]; then $HELPER quiet off || true; $HELPER perf off || true; fi
  if pg_running; then pg_stop >/dev/null || true; fi
  pg_start >/dev/null || true          # 기본 설정(128MB)으로 켜 둔다
  log "정리 끝 (exit $rc)"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

start_cond() {   # start_cond io|mem — 재시작, page cache 비우기
  if pg_running; then pg_stop >/dev/null; fi
  $HELPER drop-caches >/dev/null
  case $1 in
    io)  pg_start >/dev/null ;;
    mem) pg_start -c shared_buffers=8GB >/dev/null ;;
    *) echo "MEM은 io|mem" >&2; exit 2 ;;
  esac
}

fresh_db() {   # template에서 rq3 DB를 새로 복제
  $PSQL -d postgres -qc "DROP DATABASE IF EXISTS rq3 WITH (FORCE)"
  $PSQL -d postgres -qc "CREATE DATABASE rq3 TEMPLATE rq3_tpl STRATEGY FILE_COPY"
  $PSQL -d rq3 -qc "CHECKPOINT"
}

prewarm() {   # prewarm DB
  $PSQL -d "$1" -qtAc "SELECT pg_prewarm('items_ivf'), pg_prewarm('queries'), pg_prewarm('queries_pkey')" >/dev/null
}

setup() {
  if db_exists rq3_tpl; then log "rq3_tpl 이미 있음"; return; fi
  pg_running || pg_start >/dev/null
  log "template 생성 (ivf → rq3_tpl, FILE_COPY)"
  $PSQL -d postgres -qc "CHECKPOINT"
  $PSQL -d postgres -qc "CREATE DATABASE rq3_tpl TEMPLATE ivf STRATEGY FILE_COPY"
  $PSQL -d rq3_tpl -qc "DROP TABLE items_r10, items_r30, items_r50"
  $PSQL -d rq3_tpl -qc "CHECKPOINT"
  $PSQL -d rq3_tpl -c "SELECT count(*) AS rows, pg_size_pretty(pg_relation_size('items')) AS heap,
                              pg_size_pretty(pg_relation_size('items_ivf')) AS ivf FROM items"

  log "검증 1: 인덱스 블록 배치가 layout_items_50과 같은지"
  PGDATABASE=rq3_tpl $PY analysis/layout_dump.py --tag rq3_tpl --index items_ivf
  mv results/rq1/layout_rq3_tpl.csv results/rq1/layout_rq3_tpl.json "$OUT/setup/"
  $PY - "$OUT/setup/layout_rq3_tpl.csv" results/rq1/layout_items_50.csv <<'EOF'
import sys
import pandas as pd
a, b = (pd.read_csv(p).set_index("list_id")[["n_tuples", "blocks"]] for p in sys.argv[1:3])
same = a.equals(b)
print(f"layout 동일: {same} (lists {len(a)}, block_runs 평균 {pd.read_csv(sys.argv[1]).block_runs.mean():.2f})")
sys.exit(0 if same else 1)
EOF

  log "검증 2: 쿼리 결과 id가 items_50 측정 때와 같은지"
  start_cond mem
  prewarm rq3_tpl
  PGDATABASE=rq3_tpl $PY rq3/recall_fast.py --stage 50 --out "$OUT/setup/recall_tpl.json" \
    --compare results/rq1/result_ids_items_50.npy
  $PY -c "import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))['identical_to_compare'] else 1)" \
    "$OUT/setup/recall_tpl.json"
  $PSQL -d postgres -qc "ALTER DATABASE rq3_tpl WITH ALLOW_CONNECTIONS false"   # 실수로 바꾸지 않게
  start_cond io
  log "template 준비 끝"
}

maxqps() {
  for mem in $MEMS; do
    local f=$OUT/maxqps_$mem.json
    if [ -f "$f" ]; then log "$f 있음, 건너뜀"; continue; fi
    log "최대 QPS 측정: $mem"
    start_cond "$mem"; fresh_db
    [ "$mem" = mem ] && prewarm rq3
    PGDATABASE=rq3 $PGBENCH -n -f rq3/q.sql -c 16 -j 8 -T 30 > /dev/null 2>&1   # 예열
    for i in 1 2 3; do
      PGDATABASE=rq3 $PGBENCH -n -f rq3/q.sql -c 16 -j 8 -T 60 > "$OUT/setup/maxqps_${mem}_$i.txt" 2>&1
      grep -E "^tps|latency average" "$OUT/setup/maxqps_${mem}_$i.txt" | tr '\n' ' '; echo
    done
    $PY - "$mem" "$OUT/setup" "$f" <<'EOF'
import json, re, statistics, sys
mem, d, f = sys.argv[1:4]
tps = [float(re.search(r"^tps = ([\d.]+)", open(f"{d}/maxqps_{mem}_{i}.txt").read(), re.M).group(1)) for i in (1, 2, 3)]
m = statistics.median(tps)
json.dump({"mem": mem, "clients": 16, "threads": 8, "seconds": 60, "tps": tps, "max_qps": m,
           "target_qps": {str(p): round(m * p / 100, 2) for p in (30, 60, 90)}}, open(f, "w"), indent=1)
print(f"{mem}: max QPS {m:.2f} (runs {tps})")
EOF
  done
}

run_one() {   # run_one MEM LOAD W DIR
  local mem=$1 load=$2 w=$3 dir=$4 qps bpid early=0
  qps=$($PY -c "import json,sys; print(json.load(open(sys.argv[1]))['target_qps'][sys.argv[2]])" \
        "$OUT/maxqps_$mem.json" "$load")
  rm -rf "$dir"; mkdir -p "$dir"
  log "run $(basename "$dir"): mem=$mem load=$load% (${qps} QPS) workers=$w"
  start_cond "$mem"; fresh_db
  [ "$mem" = mem ] && prewarm rq3
  export PGDATABASE=rq3

  local pre
  pre=$($PSQL -tAc "SELECT json_build_object('wal_bytes', (SELECT wal_bytes FROM pg_stat_wal),
          'wal_lsn', pg_current_wal_lsn()::text, 'index_bytes', pg_relation_size('items_ivf'),
          'index_relfilenode', pg_relation_filenode('items_ivf'), 'shared_buffers', current_setting('shared_buffers'))")

  $PY rq3/sample_wait.py --out "$dir" --pgdata "$PGDATA" & BG_PIDS+=($!); local spid=$!
  pidstat -u -d -h -C postgres 1 > "$dir/pidstat.txt" 2>&1 & BG_PIDS+=($!); local ppid=$!
  iostat -x -t -y 1 "$DISK" > "$dir/iostat.txt" 2>&1 & BG_PIDS+=($!); local ipid=$!
  sleep 1

  local t_load t_rs t_re t_end
  t_load=$(now)
  $PGBENCH -n -f rq3/q.sql -c 16 -j 8 -R "$qps" -T "$MAX_T" -P 1 --log --log-prefix="$dir/pgb" \
    > "$dir/pgbench.out" 2> "$dir/pgbench.progress" & BG_PIDS+=($!); bpid=$!
  sleep "$BASE_S"
  t_rs=$(now)
  $PSQL -c "SET max_parallel_maintenance_workers = $w" -c "REINDEX (VERBOSE) INDEX CONCURRENTLY items_ivf" \
    > "$dir/reindex.txt" 2>&1
  t_re=$(now)
  log "  재빌드 $(awk -v a="$t_rs" -v b="$t_re" 'BEGIN { printf "%.0f", b - a }')초"
  sleep "$AFTER_S"
  if kill -0 "$bpid" 2>/dev/null; then kill -ALRM "$bpid"; else early=1; fi
  wait "$bpid" || { echo "pgbench 실패" >&2; exit 1; }
  t_end=$(now)
  kill -TERM "$spid"; kill -INT "$ppid" "$ipid"
  wait "$spid" "$ppid" "$ipid" 2>/dev/null || true
  BG_PIDS=()
  if grep -q "aborted" "$dir/pgbench.out" "$dir/pgbench.progress"; then echo "pgbench client aborted" >&2; exit 1; fi

  local post
  post=$($PSQL -tAc "SELECT json_build_object('wal_bytes', (SELECT wal_bytes FROM pg_stat_wal),
          'wal_lsn', pg_current_wal_lsn()::text, 'index_bytes', pg_relation_size('items_ivf'),
          'index_relfilenode', pg_relation_filenode('items_ivf'),
          'wal_diff_bytes', pg_wal_lsn_diff(pg_current_wal_lsn(), '$(echo "$pre" | $PY -c "import json,sys; print(json.load(sys.stdin)['wal_lsn'])")'))")

  log "  재빌드 후 recall (8GB + prewarm)"
  start_cond mem; prewarm rq3
  $PY rq3/recall_fast.py --stage 50 --out "$dir/recall_after.json" --ids-out "$dir/result_ids_after.npy"
  unset PGDATABASE

  $PY - "$dir" "$mem" "$load" "$w" "$qps" "$t_load" "$t_rs" "$t_re" "$t_end" "$early" "$pre" "$post" \
       "$BASE_S" "$AFTER_S" <<'EOF'
import json, re, sys
from pathlib import Path
d = Path(sys.argv[1])
mem, load, w, qps, t_load, t_rs, t_re, t_end, early, pre, post, base_s, after_s = sys.argv[2:15]
out = (d / "pgbench.out").read_text()
def grab(pat, cast=float):
    m = re.search(pat, out, re.M)
    return cast(m.group(1)) if m else None
meta = {
    "mem": mem, "load_pct": int(load), "workers": int(w), "target_qps": float(qps),
    "base_s": int(base_s), "after_s": int(after_s),
    "t_load_start": float(t_load), "t_rebuild_start": float(t_rs), "t_rebuild_end": float(t_re),
    "t_load_end": float(t_end), "rebuild_seconds": float(t_re) - float(t_rs),
    "pgbench_ended_early": early == "1",
    "pgbench": {"transactions": grab(r"actually processed: (\d+)", int), "failed": grab(r"failed transactions: (\d+)", int),
                "tps": grab(r"^tps = ([\d.]+)"), "latency_avg_ms": grab(r"latency average = ([\d.]+)"),
                "lag_avg_ms": grab(r"schedule lag: avg ([\d.]+)")},
    "pre": json.loads(pre), "post": json.loads(post),
    "recall_after": json.loads((d / "recall_after.json").read_text())["recall_at_10"],
}
meta["wal_written_bytes"] = meta["post"]["wal_bytes"] - meta["pre"]["wal_bytes"]
(d / "meta.json").write_text(json.dumps(meta, indent=1))
print(f"  재빌드 {meta['rebuild_seconds']:.0f}s, tps {meta['pgbench']['tps']}, WAL {meta['wal_written_bytes'] / 2**30:.2f}GiB, "
      f"새 인덱스 {meta['post']['index_bytes'] / 2**30:.2f}GiB, recall {meta['recall_after']:.4f}")
EOF
}

runs() {
  for mem in $MEMS; do [ -f "$OUT/maxqps_$mem.json" ] || { echo "maxqps_$mem.json 없음 (maxqps 먼저)" >&2; exit 1; }; done
  $HELPER perf on; $HELPER quiet on; TUNED=1
  for k in $(seq 1 "$ROUNDS"); do
    local plan
    plan=$($PY - "$k" "$MEMS" "$LOADS" "$WORKERS" <<'EOF'
import sys, itertools
import numpy as np
k = int(sys.argv[1]); conds = list(itertools.product(*(s.split() for s in sys.argv[2:5])))
for i in np.random.default_rng(2000 + k).permutation(len(conds)):
    print(*conds[i])
EOF
)
    log "===== 회차 $k"
    printf 'round %s\n%s\n' "$k" "$plan" >> "$OUT/runs/order.txt"
    while read -r mem load w; do
      local dir=$OUT/runs/${mem}_L${load}_W${w}_r$k
      if [ -f "$dir/meta.json" ]; then log "$(basename "$dir") 있음, 건너뜀"; continue; fi
      run_one "$mem" "$load" "$w" "$dir" < /dev/null
    done <<< "$plan"
  done
  log "전체 완료"
}

case ${1:-} in
  setup) setup ;;
  maxqps) maxqps ;;
  runs) runs ;;
  one) [ $# = 5 ] || { echo "one MEM LOAD W NAME" >&2; exit 2; }
       $HELPER perf on; $HELPER quiet on; TUNED=1
       run_one "$2" "$3" "$4" "$OUT/runs/$5" ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
