#!/usr/bin/env bash
# 정식 RQ2 측정 (문서 3.4절): 7개 조건 × 9개 B × ROUNDS 회차, 표본 list 500개, QD1 O_DIRECT.
# 회차마다 조건 순서와 B 순서를 섞는다 (seed SEED_BASE(기본 1000)+회차). 측정 동안 governor performance, quiet on,
# PostgreSQL 정지. 끝나거나 실패하면 모두 원래대로 돌린다.
# 이미 산출 JSON이 있는 (조건, 회차)는 건너뛰므로 중단 뒤 다시 실행하면 이어서 한다.
#
#   replay/run_formal.sh                      # 기본: ROUNDS=5 SAMPLE=500, 산출 results/rq2/formal
#   ROUNDS=1 SAMPLE=5 TAGS=items_0 OUT=/tmp/x replay/run_formal.sh   # 소규모 시험
set -euo pipefail
source "$(dirname "$0")/../env/config.sh"
cd "$IVF_ROOT"

ROUNDS=${ROUNDS:-5}
SAMPLE=${SAMPLE:-500}
OUT=${OUT:-$IVF_ROOT/results/rq2/formal}
TAGS=${TAGS:-"items_0 items_r10_10 items_10 items_r30_30 items_30 items_r50_50 items_50"}
SIZES="4 8 16 32 64 128 256 512 1024"
SEED_BASE=${SEED_BASE:-1000}
LAYOUT_DIR=${LAYOUT_DIR:-}     # 주면 layout_<tag>.csv를 여기서 읽음
LISTS_FILE=${LISTS_FILE:-}     # 주면 표본 대신 이 list 목록을 읽음
HELPER="sudo -n /usr/local/sbin/ivf-root"
mkdir -p "$OUT"

log() { echo "[$(date '+%F %T')] $*"; }

pg_was_running=0
pg_running && pg_was_running=1
cleanup() {
  log "정리: quiet off, perf off$([ $pg_was_running = 1 ] && echo ', PostgreSQL 재시작')"
  $HELPER quiet off || true
  $HELPER perf off || true
  if [ $pg_was_running = 1 ] && ! pg_running; then pg_start >/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

for i in $(seq 60); do   # apt 자동 작업이 돌고 있으면 끝날 때까지 기다림 (최대 30분)
  systemctl is-active --quiet apt-daily.service apt-daily-upgrade.service unattended-upgrades-download.service 2>/dev/null || break
  log "apt 작업 실행 중, 대기"; sleep 30
done

$HELPER perf on
$HELPER quiet on
if pg_running; then pg_stop; fi
sync

{
  echo "start: $(date '+%F %T')"
  echo "rounds: $ROUNDS  sample: $SAMPLE"
  echo "kernel: $(uname -r)"
  echo "governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
  echo "max_sectors_kb: $(cat /sys/block/nvme0n1/queue/max_sectors_kb)"
  echo "postgres_was_running (측정 중 정지, 끝나면 재시작): $pg_was_running"
} >> "$OUT/manifest.txt"

for k in $(seq 1 "$ROUNDS"); do
  plan=$($PY - "$k" "$SEED_BASE" $TAGS <<'EOF'
import sys
import numpy as np
k, seed_base, tags = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3:]
rng = np.random.default_rng(seed_base + k)
sizes = [4, 8, 16, 32, 64, 128, 256, 512, 1024]
for t in rng.permutation(tags):
    print(t, ",".join(str(s) for s in rng.permutation(sizes)))
EOF
)
  log "===== 회차 $k"
  printf 'round %s\n%s\n' "$k" "$plan" >> "$OUT/manifest.txt"
  while read -r tag sizes; do
    label="${tag}_r$k"
    if [ -f "$OUT/replay_$label.json" ]; then log "$label 이미 있음, 건너뜀"; continue; fi
    log "$label  B 순서 $sizes"
    $PY replay/run_replay.py --tag "$tag" --files "data/snapshots/$tag" --sample "$SAMPLE" --reps 1 \
      --sizes "$sizes" --outdir "$OUT" --label "$label" \
      ${LAYOUT_DIR:+--layout "$LAYOUT_DIR/layout_$tag.csv"} ${LISTS_FILE:+--lists "$LISTS_FILE"} < /dev/null
  done <<< "$plan"
done
echo "end: $(date '+%F %T')" >> "$OUT/manifest.txt"
log "전체 완료"
