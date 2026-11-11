#!/bin/bash
# 데이터 다운로드: base 앞 N개(스트리밍), query 1만 개, groundtruth
# base 전체(92GB)는 받지 않고 압축을 풀면서 앞부분만 잘라 저장한다.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RAW=$ROOT/data/raw
FTP=ftp://ftp.irisa.fr/local/texmex/corpus
N=${N_BASE:-10000000}
mkdir -p "$RAW"
ts() { date '+%F %T'; }

BASE=$RAW/sift$((N / 1000000))m.bvecs
want=$((N * 132))
if [ -f "$BASE" ] && [ "$(stat -c %s "$BASE")" -eq "$want" ]; then
  echo "[$(ts)] base 이미 있음: $BASE"
else
  echo "[$(ts)] base 앞 $N개 스트리밍 다운로드"
  # head가 끝나면 앞 단계는 SIGPIPE로 끝난다. pipefail 없이 받고 크기로 확인한다.
  wget -q -O - "$FTP/bigann_base.bvecs.gz" | gunzip -c | head -c "$want" > "$BASE.part"
  got=$(stat -c %s "$BASE.part")
  if [ "$got" -ne "$want" ]; then
    echo "base 크기 불일치: $got (기대 $want)"; exit 1
  fi
  mv "$BASE.part" "$BASE"
fi

Q=$RAW/bigann_query.bvecs
if [ ! -f "$Q" ] || [ "$(stat -c %s "$Q")" -ne 1320000 ]; then
  echo "[$(ts)] query 다운로드"
  wget -q -O "$RAW/bigann_query.bvecs.gz" "$FTP/bigann_query.bvecs.gz"
  gunzip -c "$RAW/bigann_query.bvecs.gz" > "$Q"
fi
[ "$(stat -c %s "$Q")" -eq 1320000 ] || { echo "query 크기 불일치"; exit 1; }

G=$RAW/gnd
if [ ! -d "$G" ] || [ -z "$(ls -A "$G" 2>/dev/null)" ]; then
  echo "[$(ts)] groundtruth 다운로드"
  wget -q -O "$RAW/bigann_gnd.tar.gz" "$FTP/bigann_gnd.tar.gz"
  rm -rf "$G"; mkdir "$G"
  tar -xzf "$RAW/bigann_gnd.tar.gz" -C "$G" --no-same-owner --no-same-permissions
fi

echo "[$(ts)] 파일 목록"
ls -l "$RAW" "$G"/* 2>/dev/null || ls -lR "$G"
echo "[$(ts)] sha256"
(cd "$RAW" && sha256sum "$(basename "$BASE")" bigann_query.bvecs *.gz) > "$RAW/SHA256SUMS"
cat "$RAW/SHA256SUMS"
echo "[$(ts)] 완료"
