#!/bin/bash
# 누적 삽입 단계 실행 (문서 3.1~3.2절, 개발·검증 단계).
#   0% : build 직후 상태 측정
#   10/30/50% : pool 삽입 -> 측정 -> REINDEX 대조군(데이터 복제 + 새 인덱스) 측정
# 측정 = VACUUM/CHECKPOINT -> layout_dump -> 인덱스 파일 스냅샷 -> recall@10
#
#   run_stages.sh [단계...]   (기본: 0 10 30 50)
#   CONTROLS="10 30 50"       REINDEX 대조군을 만들 단계 (기본 전부)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../env/config.sh"
cd "$IVF_ROOT"
STAGES_TO_RUN=${*:-0 10 30 50}
CONTROLS=${CONTROLS:-10 30 50}
SNAP=$IVF_ROOT/data/snapshots
ts() { date '+%F %T'; }

snapshot() {   # 인덱스 세그먼트 파일을 O_DIRECT로 복사 (RQ2 재생 도구 개발용)
  local index=$1 tag=$2 rel datadir f
  datadir=$($PSQL -tAc "SHOW data_directory")
  rel=$($PSQL -tAc "SELECT pg_relation_filepath('$index')")
  mkdir -p "$SNAP/$tag"
  for f in "$datadir/$rel" "$datadir/$rel".[0-9]*; do
    [ -e "$f" ] || continue
    dd if="$f" of="$SNAP/$tag/$(basename "$f")" bs=1M iflag=direct oflag=direct status=none
  done
  ls -l "$SNAP/$tag" | tail -n +2
}

recall() {   # recall은 I/O 조건과 무관하므로 shared_buffers 8GB + prewarm으로 빠르게 잰다
  local pct=$1 table=$2 index=$3
  pg_stop; pg_start -c shared_buffers=8GB
  $PSQL -qtAc "SELECT pg_prewarm('$index'), pg_prewarm('queries')" >/dev/null
  $PY load/db_load.py recall --stage "$pct" --table "$table"
  pg_stop; pg_start
}

check_layout() {   # 인덱스 tuple 수 = 테이블 행 수, 고아 페이지 0, (연속 배치면) 모든 list block_runs = 1
  $VENV/bin/python -I - "$IVF_ROOT/results/rq1/layout_$1.json" "$2" "$3" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1])); rows = int(sys.argv[2]); contiguous = sys.argv[3] == "1"
ok = d["total_tuples"] == rows and d["orphan_pages"] == 0
if contiguous:
    ok = ok and d["block_runs"]["max"] == 1
print(f"sanity {'OK' if ok else 'FAIL'}: tuples {d['total_tuples']} rows {rows} "
      f"orphans {d['orphan_pages']} block_runs max {d['block_runs']['max']} (contiguous={contiguous})")
sys.exit(0 if ok else 1)
EOF
}

measure() {   # measure pct table index contiguous(1/0)
  local pct=$1 table=$2 index=$3 contiguous=$4 tag=${2}_${1} rows
  echo "[$(ts)] ===== 측정 $tag"
  $PSQL -qc "VACUUM (FREEZE, ANALYZE) $table" -c "CHECKPOINT"
  sync
  rows=$($PSQL -tAc "SELECT count(*) FROM $table")
  $PY analysis/layout_dump.py --tag "$tag" --index "$index"
  check_layout "$tag" "$rows" "$contiguous"
  snapshot "$index" "$tag"
  recall "$pct" "$table" "$index"
}

pg_running || pg_start
for pct in $STAGES_TO_RUN; do
  if [ "$pct" != 0 ]; then
    echo "[$(ts)] ===== 삽입 -> $pct%"
    $PY load/db_load.py insert --to "$pct"
  fi
  measure "$pct" items items_ivf "$([ "$pct" = 0 ] && echo 1 || echo 0)"
  if [ "$pct" != 0 ] && [[ " $CONTROLS " == *" $pct "* ]]; then
    t=items_r$pct
    echo "[$(ts)] ===== REINDEX 대조군 $t"
    $PSQL -c "DROP TABLE IF EXISTS $t" \
          -c "CREATE TABLE $t WITH (autovacuum_enabled = false) AS SELECT * FROM items"
    $PSQL -qc "VACUUM (FREEZE, ANALYZE) $t"
    bash load/build_index.sh "$t" "${t}_ivf" "reindex_$pct"
    measure "$pct" "$t" "${t}_ivf" 1
  fi
done
echo "[$(ts)] 전체 완료"
