#!/bin/bash
# ivf-frag: root 권한이 필요한 1회성 설치 작업을 한 번에 수행한다.
#
#   sudo bash /home/dccmercury/kcs2027/ivf-frag/env/root_setup.sh
#
# 하는 일
#   1. 기본 패키지 (gcc/make, git, curl, python3-venv, nvme-cli, fio 등)
#   2. PostgreSQL 공식 저장소(PGDG) 추가 (키 지문 검증)
#   3. 기본 클러스터 자동 생성 끄기 (실험용 DB는 사용자 계정으로 따로 initdb)
#   4. PostgreSQL 17, 18 + pgvector 설치, 버전 고정(apt-mark hold)
#   5. 측정용 도우미 /usr/local/sbin/ivf-root 설치 + 이 도우미만 비밀번호 없이 sudo 허용
#   6. cgroup v2 io 컨트롤러를 사용자 세션에 위임 (RQ3 선택 항목, 다음 로그인/재부팅부터 적용)
#   7. 결과 확인
# 로그: ivf-frag/results/setup/root_setup.log
# 여러 번 실행해도 안전하다.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "sudo로 실행하세요: sudo bash $0" >&2
  exit 1
fi
U=${SUDO_USER:-}
if [ -z "$U" ] || [ "$U" = root ]; then
  echo "일반 사용자 계정에서 sudo로 실행하세요 (SUDO_USER가 필요)." >&2
  exit 1
fi

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname "$HERE")
LOG_DIR=$ROOT/results/setup
install -d -o "$U" -g "$U" "$LOG_DIR"
LOG=$LOG_DIR/root_setup.log
exec > >(tee "$LOG") 2>&1
trap 'rc=$?; [ $rc -ne 0 ] && echo "!!! 실패 (exit $rc, line $LINENO)"; chown "$U:$U" "$LOG" 2>/dev/null || true' EXIT

PG_VERSIONS=(17 18)
PGDG_FPR=B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=l
APT=(apt-get -o DPkg::Lock::Timeout=600 -y)

step() { echo; echo "=== [$(date '+%F %T')] $*"; }

step "0. 시작 (user=$U, root=$ROOT)"
uname -r

step "1. 기본 패키지"
"${APT[@]}" update
"${APT[@]}" install build-essential git curl ca-certificates gnupg \
  python3-venv python3-dev nvme-cli fio bpftrace sysstat e2fsprogs numactl

step "2. PGDG 저장소"
KEY_DIR=/usr/share/postgresql-common/pgdg
KEY=$KEY_DIR/apt.postgresql.org.asc
install -d "$KEY_DIR"
curl -fsSL -o "$KEY.tmp" https://www.postgresql.org/media/keys/ACCC4CF8.asc
GH=$(mktemp -d)
fpr=$(GNUPGHOME=$GH gpg --show-keys --with-colons "$KEY.tmp" | awk -F: '/^fpr/{print $10; exit}')
rm -rf "$GH"
if [ "$fpr" != "$PGDG_FPR" ]; then
  echo "PGDG 키 지문 불일치: $fpr (기대값 $PGDG_FPR)"
  rm -f "$KEY.tmp"
  exit 1
fi
mv "$KEY.tmp" "$KEY"
chmod 0644 "$KEY"
. /etc/os-release
echo "deb [signed-by=$KEY] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
  > /etc/apt/sources.list.d/pgdg.list
cat /etc/apt/sources.list.d/pgdg.list
"${APT[@]}" update

step "3. 기본 클러스터 자동 생성 끄기"
"${APT[@]}" install postgresql-common
CC=/etc/postgresql-common/createcluster.conf
if grep -qE '^[[:space:]]*#?[[:space:]]*create_main_cluster' "$CC"; then
  sed -i -E 's/^[[:space:]]*#?[[:space:]]*create_main_cluster.*/create_main_cluster = false/' "$CC"
else
  echo 'create_main_cluster = false' >> "$CC"
fi
grep -n create_main_cluster "$CC"

step "4. PostgreSQL + pgvector 설치, 버전 고정"
PKGS=()
for v in "${PG_VERSIONS[@]}"; do
  PKGS+=("postgresql-$v" "postgresql-client-$v" "postgresql-$v-pgvector" "postgresql-server-dev-$v")
done
"${APT[@]}" install "${PKGS[@]}"
apt-mark hold "${PKGS[@]}"
echo "-- 자동 생성된 클러스터 (비어 있어야 정상):"
pg_lsclusters || true

step "5. 측정 도우미 ivf-root + sudoers"
bash -n "$HERE/ivf-root"
install -o root -g root -m 0755 "$HERE/ivf-root" /usr/local/sbin/ivf-root
install -d -o root -g root -m 0755 /usr/local/lib/ivf-frag /usr/local/lib/ivf-frag/bt
SUD=$(mktemp)
echo "$U ALL=(root) NOPASSWD: /usr/local/sbin/ivf-root" > "$SUD"
visudo -cf "$SUD"
install -o root -g root -m 0440 "$SUD" /etc/sudoers.d/ivf-frag
rm -f "$SUD"
visudo -c

step "6. cgroup v2 io 컨트롤러 위임 (다음 로그인/재부팅부터 적용)"
install -d /etc/systemd/system/user@.service.d
cat > /etc/systemd/system/user@.service.d/delegate.conf <<'EOF'
[Service]
Delegate=cpu cpuset io memory pids
EOF
systemctl daemon-reload
cat /etc/systemd/system/user@.service.d/delegate.conf

step "7. 확인"
# 확인 단계는 실패해도 설치를 되돌리지 않으므로 pipefail을 끈다
# (head가 먼저 끝나면 앞 명령이 SIGPIPE로 141을 반환하기 때문)
set +o pipefail
for v in "${PG_VERSIONS[@]}"; do
  /usr/lib/postgresql/$v/bin/postgres --version
  printf '  pgvector: '; grep default_version /usr/share/postgresql/$v/extension/vector.control
done
# 고정(hold)된 패키지는 상태가 ii가 아니라 hi로 표시된다
dpkg -l | awk '$1 ~ /^(ii|hi)$/ && $2 ~ /postgresql|pgvector/ {print "  " $1, $2, $3}'
echo "-- hold:"; apt-mark showhold | sed 's/^/  /'
gcc --version | sed -n 1p
make --version | sed -n 1p
git --version
curl --version | sed -n 1p
fio --version
nvme version | sed -n 1p
su - "$U" -c 'python3 -m venv /tmp/ivf-venv-check && /tmp/ivf-venv-check/bin/pip --version && rm -rf /tmp/ivf-venv-check'
echo "-- sudo 허용 (NOPASSWD):"; sudo -l -U "$U" | grep -i ivf-root || true
/usr/local/sbin/ivf-root status

step "완료"
