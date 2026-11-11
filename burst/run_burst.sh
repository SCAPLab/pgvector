#!/usr/bin/env bash
# 몰린 삽입 실험 실행기.
#   burst/run_burst.sh base        새 기준 DB burst_base: build 600만 적재 -> VACUUM -> ivfflat 생성 -> 배치 분석 -> 복사본
#   burst/run_burst.sh plan        목적지 list 계산, hot/cold 선정, 시나리오 순서와 예측
#   burst/run_burst.sh scenarios   시나리오 s1 s16 s512: 기준 DB 복제 -> 순서대로 삽입 -> 배치 분석 -> 복사본 -> 대조
#   burst/run_burst.sh replay      O_DIRECT 재생 (기준 + 시나리오 3개, B 9개, ROUNDS 회차, hot 512 + cold 128 list)
#   burst/run_burst.sh all         위 순서대로 전부
set -euo pipefail
source "$(dirname "$0")/../env/config.sh"
cd "$IVF_ROOT"
OUT=$IVF_ROOT/results/burst
SNAP=$IVF_ROOT/data/snapshots
ROUNDS=${ROUNDS:-2}
mkdir -p "$OUT"
log() { echo "[$(date '+%F %T')] $*"; }

snapshot() {   # snapshot TAG  (현재 PGDATABASE의 items_ivf 세그먼트를 O_DIRECT로 복사)
  local tag=$1 rel datadir f
  datadir=$($PSQL -tAc "SHOW data_directory")
  rel=$($PSQL -tAc "SELECT pg_relation_filepath('items_ivf')")
  rm -rf "$SNAP/$tag"; mkdir -p "$SNAP/$tag"
  for f in "$datadir/$rel" "$datadir/$rel".[0-9]*; do
    [ -e "$f" ] || continue
    dd if="$f" of="$SNAP/$tag/$(basename "$f")" bs=1M iflag=direct oflag=direct status=none
  done
  log "복사본 $tag: $(du -sh "$SNAP/$tag" | cut -f1)"
}

measure() {   # measure TAG  (VACUUM, CHECKPOINT, 배치 분석, 복사본)
  $PSQL -qc "VACUUM (FREEZE, ANALYZE) items" -c "CHECKPOINT"
  $PY analysis/layout_dump.py --tag "$1" --outdir "$OUT"
  snapshot "$1"
}

do_base() {
  pg_running || pg_start >/dev/null
  log "===== 기준 DB burst_base"
  $PSQL -d postgres -qc "DROP DATABASE IF EXISTS burst_base WITH (FORCE)" -c "CREATE DATABASE burst_base"
  export PGDATABASE=burst_base
  $PSQL -qc "CREATE EXTENSION vector"
  $PY burst/burst.py load-base
  $PSQL -qc "VACUUM (FREEZE, ANALYZE) items" -c "CHECKPOINT"
  local t0 t1
  t0=$(date +%s.%N)
  $PSQL -c "CREATE INDEX items_ivf ON items USING ivfflat (embedding vector_l2_ops) WITH (lists = 2450)"
  t1=$(date +%s.%N)
  $PSQL -qc "CHECKPOINT"
  echo "{\"seconds\": $(echo "$t1 - $t0" | bc), \"index_bytes\": $($PSQL -tAc "SELECT pg_relation_size('items_ivf')"), \"finished\": \"$(date '+%F %T')\"}" \
    > "$OUT/create_index_base.json"
  log "인덱스 생성 $(cat "$OUT/create_index_base.json")"
  measure burst_base
}

do_plan() {
  log "===== 계획"
  $PY burst/burst.py plan
}

do_scenarios() {
  pg_running || pg_start >/dev/null
  for s in ${SCENARIOS:-s1 s16 s512}; do
    log "===== 시나리오 $s"
    $PSQL -d postgres -qc "DROP DATABASE IF EXISTS burst_$s WITH (FORCE)" \
      -c "CREATE DATABASE burst_$s TEMPLATE burst_base STRATEGY FILE_COPY"
    export PGDATABASE=burst_$s
    $PY burst/burst.py insert --scenario "$s"
    measure "burst_$s"
    $PY burst/burst.py check --scenario "$s"
  done
}

do_replay() {
  log "===== 재생 (ROUNDS=$ROUNDS)"
  local base=$OUT
  LAYOUT_DIR=$base LISTS_FILE=$base/replay_lists.txt ROUNDS=$ROUNDS SEED_BASE=3000 OUT=$base/replay \
    TAGS="burst_base burst_s1 burst_s16 burst_s512" replay/run_formal.sh
}

case ${1:-} in
  base) do_base ;;
  plan) do_plan ;;
  scenarios) do_scenarios ;;
  replay) do_replay ;;
  all) do_base; do_plan; do_scenarios; do_replay; log "전체 완료" ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
