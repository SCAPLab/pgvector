#!/bin/bash
# ivfflat 인덱스 생성 (문서 1.6절, lists = 2450), 소요 시간 기록.
#   build_index.sh [table] [index] [tag]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../env/config.sh"
TABLE=${1:-items}
INDEX=${2:-items_ivf}
TAG=${3:-build}
OUT=$IVF_ROOT/results/rq1/create_index_${INDEX}_${TAG}.json
mkdir -p "$(dirname "$OUT")"

$PSQL -qc "CHECKPOINT"
rows=$($PSQL -tAc "SELECT count(*) FROM $TABLE")
pmw=$($PSQL -tAc "SHOW max_parallel_maintenance_workers")
mwm=$($PSQL -tAc "SHOW maintenance_work_mem")
t0=$(date +%s.%N)
$PSQL -c "CREATE INDEX $INDEX ON $TABLE USING ivfflat (embedding vector_l2_ops) WITH (lists = 2450)"
t1=$(date +%s.%N)
$PSQL -qc "CHECKPOINT"
size=$($PSQL -tAc "SELECT pg_relation_size('$INDEX')")
sec=$(echo "$t1 - $t0" | bc)
cat > "$OUT" <<EOF
{"table": "$TABLE", "index": "$INDEX", "tag": "$TAG", "rows": $rows, "lists": 2450,
 "seconds": $sec, "index_bytes": $size,
 "max_parallel_maintenance_workers": $pmw, "maintenance_work_mem": "$mwm",
 "finished": "$(date '+%F %T')"}
EOF
cat "$OUT"
