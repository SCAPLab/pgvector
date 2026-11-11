"""RQ3 집계 (rq3/run_rq3.sh 산출).

  rq3_summary.py [--dir results/rq3] [--warmup 60] [--run NAME]

run마다 (runs/<name>/에 기록)
  timeline.csv    : 재빌드 시작 기준 1초 구간별 도착 쿼리 수, 지연 p50/p95/p99 (도착 = 예정 시작 시각)
  phases.json     : 구간(baseline / rebuild / after)별 지연·처리량·대기 이벤트·디스크·CPU, 재빌드 단계별 시간
구간 정의 (도착 시각 기준):
  baseline = [부하 시작 + warmup, 재빌드 시작), rebuild = [재빌드 시작, 끝), after = [끝, 끝 + after_s)
pgbench -R의 지연은 예정 시작 시각부터 재므로 대기열 지연(schedule lag)을 포함한다 (open loop).
전체 집계: <dir>/summary_runs.csv (run별), <dir>/summary.csv (조건별 평균·표준편차)
"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import numpy as np
import pandas as pd
from common import RESULTS

PHASES = ["baseline", "rebuild", "after"]


def load_pgbench(d):
    frames = []
    for f in sorted(d.glob("pgb.*")):
        x = pd.read_csv(f, sep=" ", header=None,
                        names=["client", "tx", "lat_us", "script", "t_s", "t_us", "lag_us"])
        frames.append(x)
    x = pd.concat(frames, ignore_index=True)
    x["t_done"] = x.t_s + x.t_us / 1e6
    x["t_arr"] = x.t_done - x.lat_us / 1e6          # 예정 시작 시각
    x["lat_ms"] = x.lat_us / 1e3
    return x


def phase_of(t, m, warmup):
    edges = [m["t_load_start"] + warmup, m["t_rebuild_start"], m["t_rebuild_end"],
             m["t_rebuild_end"] + m["after_s"]]
    idx = np.searchsorted(edges, t, side="right") - 1
    lab = np.array(["pre"] + PHASES + ["post"])
    return lab[np.clip(idx + 1, 0, 4)]


def wait_shares(a):
    """active 표본 중 wait_event_type 비율 (없으면 CPU)."""
    a = a[a.state == "active"]
    if a.empty:
        return {}
    s = a.wait_event_type.fillna("CPU").value_counts(normalize=True)
    top = a.assign(w=a.wait_event_type.fillna("CPU") + ":" + a.wait_event.fillna("-")).w.value_counts(normalize=True)
    return {"by_type": s.round(4).to_dict(), "top_events": top.head(6).round(4).to_dict(), "samples": int(len(a))}


def summarize_run(d, warmup):
    m = json.loads((d / "meta.json").read_text())
    q = load_pgbench(d)
    q["phase"] = phase_of(q.t_arr.values, m, warmup)
    t0 = m["t_rebuild_start"]

    # 1초 구간 타임라인
    q["sec"] = np.floor(q.t_arr - t0).astype(int)
    tl = q.groupby("sec").lat_ms.agg(n="size", p50="median", p95=lambda s: s.quantile(0.95),
                                     p99=lambda s: s.quantile(0.99), mean="mean")
    tl.round(3).to_csv(d / "timeline.csv")

    res = {"name": d.name, "mem": m["mem"], "load_pct": m["load_pct"], "workers": m["workers"],
           "target_qps": m["target_qps"], "rebuild_seconds": m["rebuild_seconds"],
           "wal_written_gib": m["wal_written_bytes"] / 2**30, "new_index_gib": m["post"]["index_bytes"] / 2**30,
           "recall_after": m["recall_after"], "pgbench_ended_early": m["pgbench_ended_early"]}
    dur = {"baseline": m["t_rebuild_start"] - m["t_load_start"] - warmup,
           "rebuild": m["rebuild_seconds"], "after": m["after_s"]}
    ph = {}
    for p in PHASES:
        x = q[q.phase == p]
        ph[p] = {"queries": int(len(x)), "seconds": dur[p], "arrival_qps": len(x) / dur[p] if dur[p] > 0 else None,
                 "lat_ms_mean": x.lat_ms.mean(), "lat_ms_p50": x.lat_ms.median(),
                 "lat_ms_p95": x.lat_ms.quantile(0.95), "lat_ms_p99": x.lat_ms.quantile(0.99),
                 "lat_ms_max": x.lat_ms.max(), "lag_ms_mean": x.lag_us.mean() / 1e3,
                 "p99_of_1s_p99": tl.loc[tl.index.isin(np.floor(x.t_arr - t0).astype(int).unique()), "p99"].median()}

    # 대기 이벤트
    a = pd.read_csv(d / "activity.csv.gz")
    a["phase"] = phase_of(a.t.values, m, warmup)
    rb = a[a["query"].fillna("").str.startswith("REINDEX")]
    rb_pid = int(rb.pid.mode().iloc[0]) if not rb.empty else -1
    workers = a[(a.backend_type == "parallel worker") & (a.leader_pid == rb_pid)]
    res["parallel_workers_seen"] = int(workers.pid.nunique())
    res["parallel_workers_max_concurrent"] = int(workers.groupby("t").pid.nunique().max()) if not workers.empty else 0
    for p in PHASES:
        ph[p]["wait_query"] = wait_shares(a[(a.phase == p) & (a.application_name == "pgbench")])
    ph["rebuild"]["wait_rebuild_leader"] = wait_shares(rb[rb.phase == "rebuild"])
    ph["rebuild"]["wait_rebuild_workers"] = wait_shares(workers[workers.phase == "rebuild"])

    # 디스크·CPU
    s = pd.read_csv(d / "system.csv")
    s["phase"] = phase_of(s.t.values, m, warmup)
    fs0 = s.fs_used_bytes.iloc[0]
    res["max_extra_disk_gib"] = (s.fs_used_bytes.max() - fs0) / 2**30
    for p in PHASES:
        x = s[s.phase == p]
        ph[p]["system"] = {"r_iops": (x.r_ios / x.dt).mean(), "r_mbps": (x.r_bytes / x.dt).mean() / 2**20,
                           "r_await_ms": x.r_await_ms.mean(), "w_iops": (x.w_ios / x.dt).mean(),
                           "w_mbps": (x.w_bytes / x.dt).mean() / 2**20, "w_await_ms": x.w_await_ms.mean(),
                           "util": x.util.mean(), "cpu_busy": x.cpu_busy.mean(), "cpu_iowait": x.cpu_iowait.mean()}

    # 재빌드 단계별 시간
    pr = pd.read_csv(d / "progress.csv")
    if not pr.empty:
        g = pr.groupby("phase", sort=False).t.agg(["min", "max"])
        res["rebuild_phases_s"] = {k: round(v, 1) for k, v in (g["max"] - g["min"]).items()}

    out = {**res, "phases": ph}
    (d / "phases.json").write_text(json.dumps(out, indent=1, default=float))
    flat = dict(res)
    flat.pop("rebuild_phases_s", None)
    for p in PHASES:
        for k in ["lat_ms_p50", "lat_ms_p95", "lat_ms_p99", "lat_ms_mean", "arrival_qps", "p99_of_1s_p99"]:
            flat[f"{p}_{k}"] = ph[p][k]
        flat[f"{p}_cpu_busy"] = ph[p]["system"]["cpu_busy"]
        flat[f"{p}_r_mbps"] = ph[p]["system"]["r_mbps"]
        flat[f"{p}_w_mbps"] = ph[p]["system"]["w_mbps"]
        flat[f"{p}_query_io_wait"] = ph[p]["wait_query"].get("by_type", {}).get("IO", 0.0)
        flat[f"{p}_query_cpu"] = ph[p]["wait_query"].get("by_type", {}).get("CPU", 0.0)
    flat["p99_ratio_rebuild_vs_base"] = ph["rebuild"]["lat_ms_p99"] / ph["baseline"]["lat_ms_p99"]
    flat["p99_ratio_after_vs_base"] = ph["after"]["lat_ms_p99"] / ph["baseline"]["lat_ms_p99"]
    flat["p50_ratio_rebuild_vs_base"] = ph["rebuild"]["lat_ms_p50"] / ph["baseline"]["lat_ms_p50"]
    flat["p50_ratio_after_vs_base"] = ph["after"]["lat_ms_p50"] / ph["baseline"]["lat_ms_p50"]
    return flat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(RESULTS / "rq3"))
    ap.add_argument("--warmup", type=float, default=60)
    ap.add_argument("--run", help="이 run 하나만")
    a = ap.parse_args()
    root = Path(a.dir)
    dirs = [root / "runs" / a.run] if a.run else sorted(p.parent for p in (root / "runs").glob("*/meta.json"))
    rows = [summarize_run(d, a.warmup) for d in dirs]
    if not rows:
        sys.exit("meta.json 있는 run 없음")
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    cols = ["name", "rebuild_seconds", "parallel_workers_max_concurrent", "baseline_lat_ms_p50", "baseline_lat_ms_p99",
            "rebuild_lat_ms_p50", "rebuild_lat_ms_p99", "after_lat_ms_p50", "after_lat_ms_p99",
            "p99_ratio_rebuild_vs_base", "rebuild_query_io_wait", "recall_after", "wal_written_gib", "max_extra_disk_gib"]
    print(df[cols].round(3).to_string(index=False))
    if a.run:
        return
    df.to_csv(root / "summary_runs.csv", index=False, float_format="%.5g")
    num = df.select_dtypes("number").columns.drop(["load_pct", "workers"])
    g = df.groupby(["mem", "load_pct", "workers"])[list(num)]
    summ = g.mean().add_suffix("_mean").join(g.std().add_suffix("_std")).join(g.size().rename("runs"))
    summ.to_csv(root / "summary.csv", float_format="%.5g")
    show = ["rebuild_seconds", "baseline_lat_ms_p99", "rebuild_lat_ms_p99", "after_lat_ms_p99", "p99_ratio_rebuild_vs_base"]
    print("\n" + summ[[f"{c}_mean" for c in show] + [f"{show[-1]}_std", "runs"]].round(2).to_string())


if __name__ == "__main__":
    main()
