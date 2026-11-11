# ivf-frag 공통 설정 (bash에서 source)
IVF_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PGVER=${PGVER:-17}
PGBIN=/usr/lib/postgresql/$PGVER/bin
PGDATA=$IVF_ROOT/pgdata
export PGHOST=$IVF_ROOT/run
export PGPORT=5433
export PGDATABASE=ivf
VENV=$IVF_ROOT/.venv
PY="$VENV/bin/python -I"
PSQL="$PGBIN/psql -X -v ON_ERROR_STOP=1"

pg_start() {   # pg_start [추가 -c 옵션...]  예: pg_start -c shared_buffers=8GB
  mkdir -p "$PGHOST"
  "$PGBIN/pg_ctl" -D "$PGDATA" -l "$IVF_ROOT/results/setup/pg_server.log" -w -t 300 \
    ${1:+-o "$*"} start
}
pg_stop() {
  "$PGBIN/pg_ctl" -D "$PGDATA" -w -t 600 -m fast stop
}
pg_running() {
  "$PGBIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1
}
