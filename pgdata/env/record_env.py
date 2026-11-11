"""실험 환경 기록 -> results/env.json (문서 1.2, 1.3, 1.7절).

root가 필요한 SSD 정보는 sudo -n ivf-root nvme-info 로 얻는다 (비밀번호 없이 허용됨).
"""
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from common import ROOT, RESULTS, SEED, N_BASE, N_BUILD, LISTS, PROBES, STAGES, save_json


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as e:  # 기록용이므로 실패해도 계속
        return f"ERROR {e}"


def rd(p):
    try:
        return Path(p).read_text().strip()
    except Exception:
        return None


def main():
    pgdata = ROOT / "pgdata"
    dev = "nvme0n1"
    q = f"/sys/block/{dev}/queue"
    env = {
        "recorded": time.strftime("%F %T %z"),
        "host": platform.node(),
        "kernel": platform.release(),
        "os": sh(". /etc/os-release; echo $PRETTY_NAME"),
        "cpu": {
            "model": sh("lscpu | sed -n 's/^Model name: *//p'"),
            "cpus": os.cpu_count(),
            "numa_nodes": sh("lscpu | sed -n 's/^NUMA node(s): *//p'"),
            "governor": rd("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
            "driver": rd("/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver"),
            "boost": rd("/sys/devices/system/cpu/cpufreq/boost"),
        },
        "memory_kb": sh("awk '/MemTotal/{print $2}' /proc/meminfo"),
        "swap": sh("swapon --show --noheadings"),
        "board": f"{rd('/sys/class/dmi/id/board_vendor')} {rd('/sys/class/dmi/id/board_name')}",
        "storage": {
            "device": dev,
            "model": rd(f"/sys/block/{dev}/device/model"),
            "firmware": rd(f"/sys/block/{dev}/device/firmware_rev"),
            "queue": {k: rd(f"{q}/{k}") for k in [
                "max_sectors_kb", "max_hw_sectors_kb", "logical_block_size", "physical_block_size",
                "scheduler", "read_ahead_kb", "nr_requests", "rotational", "write_cache"]},
            "pgdata_mount": sh(f"findmnt -no SOURCE,FSTYPE,OPTIONS -T {pgdata}"),
            "pgdata_fs_free": sh(f"df -B1 --output=avail {pgdata} | tail -1"),
            "partition_start_sectors": rd(f"/sys/block/{dev}/{dev}p2/start"),
            "dedicated_device": False,
            "note": "측정 전용 SSD 없음: OS 루트 파티션(/)에 PGDATA",
        },
        "postgresql": {
            "version": sh("/usr/lib/postgresql/17/bin/postgres --version"),
            "pgdata": str(pgdata),
        },
        "pgvector_source_commit": sh(f"git -C {ROOT}/src/pgvector-0.8.7 log -1 --format=%H"),
        "dpkg": sh("dpkg -l | awk '$2 ~ /postgresql|pgvector|bpftrace|sysstat|e2fsprogs|fio/ {print $2\"=\"$3}'").split("\n"),
        "python": sys.version.split()[0],
        "python_packages": sh(f"{sys.executable} -m pip freeze").split("\n"),
        "experiment": {"seed": SEED, "n_base": N_BASE, "n_build": N_BUILD, "lists": LISTS,
                       "probes": PROBES, "stages": STAGES},
    }
    # PostgreSQL 설정 전체 (서버가 떠 있을 때)
    settings = sh("/usr/lib/postgresql/17/bin/psql -X -tA -F'|' -c "
                  "\"LOAD 'vector'; SELECT name, setting, unit, source FROM pg_settings ORDER BY name\"")
    (RESULTS / "setup").mkdir(parents=True, exist_ok=True)
    (RESULTS / "setup" / "pg_settings.txt").write_text(settings + "\n")
    env["postgresql"]["non_default_settings"] = [
        l for l in settings.split("\n") if l and l.split("|")[-1] not in ("default", "override")]
    env["postgresql"]["select_version"] = sh("/usr/lib/postgresql/17/bin/psql -X -tAc 'SELECT version()'")
    env["postgresql"]["pgvector"] = sh("/usr/lib/postgresql/17/bin/psql -X -tAc "
                                       "\"SELECT extversion FROM pg_extension WHERE extname='vector'\"")
    # SSD 상세 (root 도우미)
    nvme = sh("sudo -n /usr/local/sbin/ivf-root nvme-info")
    (RESULTS / "setup" / "nvme_info.txt").write_text(nvme + "\n")
    for line in nvme.split("\n"):
        k = line.split(":")[0].strip()
        if k in ("mn", "fr", "mdts", "ver", "temperature", "percentage_used", "data_units_written"):
            env["storage"]["nvme_" + k] = line.split(":", 1)[1].strip()
    save_json(RESULTS / "env.json", env)
    print(json.dumps(env, indent=1, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
