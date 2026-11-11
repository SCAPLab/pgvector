#!/bin/bash
# PostgreSQL 실험 인스턴스 초기화 (root 불필요, 사용자 계정으로 실행)
#   - PGDATA = ivf-frag/pgdata, 포트 5433, unix socket만 사용
#   - postgresql.conf.override 적용, DB ivf 생성, 확장 설치
#   - debug_io_direct = 'data' 로 서버가 뜨는지 확인
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

if [ ! -f "$PGDATA/PG_VERSION" ]; then
  "$PGBIN/initdb" -D "$PGDATA" -E UTF8 --locale=C -A trust --no-instructions
  echo "include '$IVF_ROOT/env/postgresql.conf.override'" >> "$PGDATA/postgresql.conf"
fi

pg_running || pg_start
$PSQL -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='ivf'" | grep -q 1 \
  || "$PGBIN/createdb" ivf
$PSQL -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_prewarm;"

echo "--- 확인"
$PSQL -tAc "SELECT version()"
$PSQL -tAc "SELECT extname, extversion FROM pg_extension ORDER BY 1"
for g in debug_io_direct shared_buffers maintenance_work_mem max_parallel_maintenance_workers \
         jit checkpoint_timeout max_wal_size ivfflat.probes ivfflat.iterative_scan \
         io_combine_limit wal_level data_checksums block_size; do
  printf '%-34s %s\n' "$g" "$($PSQL -tAc "LOAD 'vector'; SHOW $g" 2>&1 | tail -1)"
done
