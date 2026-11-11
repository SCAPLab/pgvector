"""정식 RQ2 측정 집계 (replay/run_formal.sh 산출).

  rq2_summary.py [--dir results/rq2/formal]

조건(tag)·B별로
  - 앱 요청 수: list마다 회차 간 같아야 한다 (계획에서 정해지므로). 다르면 중단.
  - list 평균 읽기 시간: 회차별 평균의 평균·표준편차·변동계수(CV)
  - plateau: 1MB 요청 수 / 표본 block_runs 평균
  - 삽입 조건 / 같은 데이터 양의 REINDEX 조건 읽기 시간 비 (같은 B, 각자 최적 B)
  - 장치 잡음: 장치 읽기 요청 중 재생이 내지 않은 것, 측정 중 쓰기 요청, 온도 범위
산출: <dir>/summary.csv, <dir>/summary.json
"""
import argparse
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import numpy as np
import pandas as pd
from common import RESULTS, save_json

KB = 1024


def condition(tag):
    if tag == "items_0":
        return 0, "build"
    m = re.fullmatch(r"items_r(\d+)_(\d+)", tag)
    if m:
        return int(m.group(2)), "reindex"
    return int(tag.split("_")[1]), "insert"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(RESULTS / "rq2" / "formal"))
    d = Path(ap.parse_args().dir)

    rows, dev = [], []
    for f in sorted(d.glob("replay_*_r*.json")):
        j = json.loads(f.read_text())
        tag, rnd = j["tag"], int(j["label"].rsplit("_r", 1)[1])
        r = pd.read_csv(d / f"replay_{j['label']}.csv")
        r["tag"], r["round"] = tag, rnd
        rows.append(r)
        for b, v in j["device"].items():
            dev.append({"tag": tag, "round": rnd, "B": int(b[:-2]) * KB, **v})
    if not rows:
        sys.exit(f"{d}에 산출 없음")
    res, dev = pd.concat(rows, ignore_index=True), pd.DataFrame(dev)

    # 요청 수는 회차와 무관해야 함
    nuniq = res.groupby(["tag", "B", "list_id"]).app_requests.nunique()
    if (nuniq > 1).any():
        sys.exit(f"회차 간 요청 수가 다른 list {int((nuniq > 1).sum())}개")

    per_round = res.groupby(["tag", "B", "round"]).wall_time_us.mean().div(1e3).rename("wall_ms")
    wall = per_round.groupby(["tag", "B"]).agg(["mean", "median", "std", "count"])
    g = res.groupby(["tag", "B"])
    summ = pd.DataFrame({
        "requests_mean": g.app_requests.mean(),
        "block_runs_mean": g.block_runs.mean(),
        "ideal_mean": g.ideal_requests.mean(),
        "read_amp_mean": g.read_amp.mean(),
        "wall_ms_mean": wall["mean"], "wall_ms_median": wall["median"], "wall_ms_std": wall["std"],
        "wall_ms_cv": wall["std"] / wall["mean"], "rounds": wall["count"],
        "wall_ms_p50": g.wall_time_us.median() / 1e3, "wall_ms_p99": g.wall_time_us.quantile(0.99) / 1e3,
    }).reset_index()
    summ[["stage", "kind"]] = summ.tag.apply(lambda t: pd.Series(condition(t)))
    summ["B_kb"] = summ.B // KB
    summ = summ.sort_values(["stage", "kind", "B"])

    # 삽입 조건 / 같은 단계 REINDEX 조건
    ref = summ[summ.kind != "insert"].set_index(["stage", "B"]).wall_ms_mean
    ins = summ.kind == "insert"
    summ.loc[ins, "slowdown_vs_reindex"] = [w / ref.get((s, b), np.nan)
                                           for s, b, w in summ.loc[ins, ["stage", "B", "wall_ms_mean"]].itertuples(index=False)]
    cols = ["stage", "kind", "tag", "B_kb", "requests_mean", "block_runs_mean", "ideal_mean", "read_amp_mean",
            "wall_ms_mean", "wall_ms_median", "wall_ms_std", "wall_ms_cv", "wall_ms_p50", "wall_ms_p99", "slowdown_vs_reindex", "rounds"]
    summ[cols].to_csv(d / "summary.csv", index=False, float_format="%.4g")

    per_tag = {}
    for tag, s in summ.groupby("tag", sort=False):
        s = s.set_index("B_kb")
        best = int(s.wall_ms_mean.idxmin())
        stage, kind = condition(tag)
        e = {"stage": stage, "kind": kind, "block_runs_mean": float(s.block_runs_mean.iloc[0]),
             "requests_1mb": float(s.requests_mean[1024]),
             "plateau_ratio_1mb": float(s.requests_mean[1024] / s.block_runs_mean.iloc[0]),
             "best_B_kb": best, "best_wall_ms": float(s.wall_ms_mean[best]),
             "best_B_kb_by_median": int(s.wall_ms_median.idxmin()),
             "wall_cv_max": float(s.wall_ms_cv.max())}
        if kind == "insert":
            rs = summ[(summ.stage == stage) & (summ.kind == "reindex")].set_index("B_kb")
            e["reindex_best_B_kb"] = int(rs.wall_ms_mean.idxmin())
            e["best_vs_best_slowdown"] = float(s.wall_ms_mean[best] / rs.wall_ms_mean.min())
            e["best_vs_best_slowdown_by_median"] = float(s.wall_ms_median.min() / rs.wall_ms_median.min())
            e["slowdown_at_1mb"] = float(s.wall_ms_mean[1024] / rs.wall_ms_mean[1024])
        per_tag[tag] = e

    noise = {
        "extra_read_ios_total": int(dev.extra_read_ios.sum()),
        "extra_read_bytes_total": int(dev.extra_read_bytes.sum()),
        "replay_read_ios_total": int(dev.expected_read_ios.sum()),
        "write_ios_total": int(dev.write_ios.sum()),
        "passes_with_extra_reads": int((dev.extra_read_ios != 0).sum()),
        "passes": len(dev),
        "measure_seconds_total": float(dev.seconds.sum()),
        "temp_c_min": float(min(dev.temp_c_before.min(), dev.temp_c_after.min())),
        "temp_c_max": float(max(dev.temp_c_before.max(), dev.temp_c_after.max())),
    }
    save_json(d / "summary.json", {"conditions": per_tag, "device_noise": noise,
                                   "rounds": sorted(res["round"].unique().tolist())})

    pd.set_option("display.width", 200)
    print(pd.DataFrame(per_tag).T.to_string())
    print(json.dumps(noise, ensure_ascii=False))
    w = summ.pivot_table(index="B_kb", columns="tag", values="wall_ms_mean")
    print("\nlist 평균 읽기 시간 (ms)\n" + w[list(per_tag)].round(2).to_string())
    c = summ.pivot_table(index="B_kb", columns="tag", values="wall_ms_cv")
    print("\n회차 간 CV\n" + c[list(per_tag)].round(3).to_string())


if __name__ == "__main__":
    main()
