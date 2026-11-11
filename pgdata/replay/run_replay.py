"""O_DIRECT 재생 실행기 (문서 3.4절, RQ2).

layout CSV(단계 B 산출)의 list별 블록 목록으로 B 정렬 단위 읽기 계획을 만들고
odirect_replay(C)로 실제로 읽는다. 앱 수준 요청 수는 계획에서 바로 나오므로,
측정값(app_requests)이 계획과 같은지도 확인한다.

B 하나를 읽을 때마다 앞뒤로 파티션 블록 통계(/sys/dev/block/M:m/stat)와 NVMe 온도를 기록한다.
장치 읽기 요청 수는 max_sectors_kb 단위로 쪼개진 뒤의 수이므로, 기대값(앱 요청 × ceil(B/최대 요청))과의
차이가 같은 파티션의 다른 I/O(OS 잡음)다.

  run_replay.py --tag items_0 --files data/snapshots/items_0 [--reps 1] [--sample 500]
                [--sizes 4,8,16,32,64,128,256,512,1024] [--outdir results/rq2] [--label items_0]

--sizes는 적힌 순서대로 읽는다 (정식 측정에서 순서를 섞기 위해).
산출: <outdir>/replay_<label>.csv (list_id,B,rep,app_requests,bytes_read,wall_time_us,
      n_pages,block_runs,ideal_requests,read_amp), <outdir>/replay_<label>.json (요약, 장치 기록)
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import numpy as np
import pandas as pd
from common import RESULTS, SEED, LISTS, log, save_json

BLCKSZ = 8192
SEG_BLOCKS = 131072
TOOL = Path(__file__).resolve().parent / "odirect_replay"
STAT_FIELDS = ["read_ios", "read_merges", "read_sectors", "read_ticks",
               "write_ios", "write_merges", "write_sectors", "write_ticks"]


def parse_blocks(s):
    out = []
    for part in str(s).split(";"):
        if not part or part == "nan":
            continue
        a, n = part.split("+")
        out.append(np.arange(int(a), int(a) + int(n), dtype=np.int64))
    return np.concatenate(out) if out else np.empty(0, np.int64)


def units_for(blocks, B):
    """블록들을 덮는 B 정렬 단위 (세그먼트, 세그먼트 안 offset) 목록, 오름차순·중복 제거."""
    off = blocks * BLCKSZ
    first = off // B
    last = (off + BLCKSZ - 1) // B
    u = np.unique(np.concatenate([np.arange(f, l + 1) for f, l in zip(first, last)]) if B < BLCKSZ
                  else first)
    byte = u * B
    seg = byte // (SEG_BLOCKS * BLCKSZ)
    return seg, byte - seg * SEG_BLOCKS * BLCKSZ


class Device:
    """파일이 놓인 파티션의 블록 통계, 디스크의 max_sectors_kb, NVMe 컨트롤러 온도."""

    def __init__(self, path):
        st = os.stat(path)
        self.name = f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}"
        sys_dir = Path(os.path.realpath(f"/sys/dev/block/{self.name}"))
        self.stat_path = sys_dir / "stat"
        disk = sys_dir if (sys_dir / "queue").exists() else sys_dir.parent
        self.disk = disk.name
        self.max_req = int((disk / "queue" / "max_sectors_kb").read_text()) * 1024
        self.temp_path = next((h / "temp1_input" for p in sys_dir.parents
                               for h in sorted(p.glob("hwmon*")) if (h / "temp1_input").exists()), None)

    def stat(self):
        v = self.stat_path.read_text().split()
        return dict(zip(STAT_FIELDS, map(int, v[:len(STAT_FIELDS)])))

    def temp_c(self):
        return int(self.temp_path.read_text()) / 1000 if self.temp_path else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--files", required=True, help="인덱스 세그먼트 파일이 있는 디렉터리")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--sample", type=int, default=500)
    ap.add_argument("--sizes", default="4,8,16,32,64,128,256,512,1024")
    ap.add_argument("--outdir", default=str(RESULTS / "rq2"))
    ap.add_argument("--label", help="산출 파일 이름 (기본: tag)")
    ap.add_argument("--layout", help="layout CSV 경로 (기본: results/rq1/layout_<tag>.csv)")
    ap.add_argument("--lists", help="읽을 list id 목록 파일 (주면 --sample 대신 사용)")
    a = ap.parse_args()
    label = a.label or a.tag
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(a.layout or RESULTS / "rq1" / f"layout_{a.tag}.csv")
    if a.lists:
        sample = np.loadtxt(a.lists, dtype=np.int64)
    else:
        rng = np.random.default_rng(SEED)
        sample = np.sort(rng.choice(LISTS, size=a.sample, replace=False))   # 모든 조건에서 같은 표본
    df = df.set_index("list_id").loc[sample]

    fdir = Path(a.files)
    segs = sorted(fdir.iterdir(), key=lambda p: (0 if "." not in p.name else int(p.name.split(".")[1])))
    if not TOOL.exists():
        subprocess.run(["make", "-C", str(TOOL.parent)], check=True)
    dev = Device(segs[0])
    seg_size = np.array([p.stat().st_size for p in segs], dtype=np.int64)

    rows, device = [], {}
    work = Path(tempfile.mkdtemp(prefix="replay_", dir=outdir))
    blocks_by_list = {lid: parse_blocks(df.at[lid, "blocks"]) for lid in df.index}
    sizes = [int(x) for x in a.sizes.split(",")]
    for Bk in sizes:
        B = Bk * 1024
        planned, expect_ios = {}, 0
        plan, out = work / f"{Bk}.plan", work / f"{Bk}.csv"
        with open(plan, "w") as pf:
            for lid in df.index:
                seg, off = units_for(blocks_by_list[lid], B)
                planned[lid] = len(off)
                got = np.minimum(B, seg_size[seg] - off)       # 파일 끝에서는 짧게 읽힌다
                expect_ios += int((-(-got // dev.max_req)).sum())
                for s, o in zip(seg, off):
                    pf.write(f"{lid} {s} {o}\n")
        n_req = sum(planned.values())
        expect_ios *= a.reps
        t_before, s_before, t0 = dev.temp_c(), dev.stat(), time.monotonic()
        subprocess.run([str(TOOL), str(plan), str(B), str(a.reps), str(out)] + [str(p) for p in segs], check=True)
        t1, s_after, t_after = time.monotonic(), dev.stat(), dev.temp_c()
        r = pd.read_csv(out)
        plan.unlink(); out.unlink()
        r["planned"] = r.list_id.map(planned)
        rows.append(r)

        d = {k: s_after[k] - s_before[k] for k in STAT_FIELDS}
        app_bytes = int(r.bytes_read.sum())
        device[f"{Bk}KB"] = {**d, "app_requests": n_req * a.reps, "app_bytes": app_bytes,
                             "expected_read_ios": expect_ios,
                             "extra_read_ios": d["read_ios"] - expect_ios,
                             "extra_read_bytes": d["read_sectors"] * 512 - app_bytes,
                             "seconds": round(t1 - t0, 3), "temp_c_before": t_before, "temp_c_after": t_after}
        log(f"{label} B={Bk}KB 요청 {n_req:,}건, 장치 읽기 {d['read_ios']:,}건 (기대 {expect_ios:,}), "
            f"쓰기 {d['write_ios']}건, {t1 - t0:.1f}s, {t_before}->{t_after}°C")
    res = pd.concat(rows, ignore_index=True)
    res["n_pages"] = res.list_id.map(df.n_pages)
    res["block_runs"] = res.list_id.map(df.block_runs)
    res["ideal_requests"] = np.ceil(res.n_pages * BLCKSZ / res.B).astype(int)
    res["read_amp"] = res.bytes_read / (res.n_pages * BLCKSZ)
    if not (res.app_requests == res.planned).all():
        sys.exit("측정 요청 수가 계획과 다름")
    work.rmdir()
    res.drop(columns="planned").to_csv(outdir / f"replay_{label}.csv", index=False)

    g = res.groupby("B")
    summ = pd.DataFrame({
        "requests_mean": g.app_requests.mean(), "ideal_mean": g.ideal_requests.mean(),
        "excess_over_ideal_max": g.apply(lambda x: int((x.app_requests - x.ideal_requests).max()), include_groups=False),
        "read_amp_mean": g.read_amp.mean(), "wall_ms_mean": g.wall_time_us.mean() / 1e3,
        "wall_ms_p50": g.wall_time_us.median() / 1e3, "wall_ms_p99": g.wall_time_us.quantile(0.99) / 1e3,
    })
    summ.index = [f"{b // 1024}KB" for b in summ.index]
    save_json(outdir / f"replay_{label}.json",
              {"tag": a.tag, "label": label, "files": str(fdir), "reps": a.reps, "sample": int(len(sample)), "lists_file": a.lists,
               "size_order_kb": sizes, "partition": dev.name, "disk": dev.disk, "max_request_bytes": dev.max_req,
               "block_runs_mean": float(df.block_runs.mean()), "summary": summ.round(3).to_dict("index"),
               "device": device})
    log("\n" + summ.round(2).to_string())


if __name__ == "__main__":
    main()
