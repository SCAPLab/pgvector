"""몰린 삽입 실험 집계 (burst/run_burst.sh 산출).

  summarize.py [--rounds 2]

RQ1 (배치, list별): block_runs, lba_runs, 새 페이지 수, 같은 list 새 페이지 간 간격 (페이지 번호 차, LBA 거리)
    LBA는 각 시나리오 DB의 실제 인덱스 파일에 FIEMAP을 다시 걸어 구한다 (layout_dump와 같은 방식).
RQ2 (재생, list 종류 × B): 요청 수, 이상적 요청 수, 읽기 증폭, 읽기 시간 (list별 회차 평균의 평균), 회차 간 CV
    list별 큰 I/O 이득 = 8KB 시간 / 가장 빠른 B의 시간
list 종류: hot (삽입받은 512개), cold (삽입 없음; 재생은 표본 128개). 기준(burst_base)의 hot = 삽입 전 같은 list.
산출: results/burst/perlist_rq1.csv, summary_rq1.csv, summary_rq2.csv, gain_per_list.csv, gain_summary.csv,
      rq1_hot_perlist.csv, rq2_hot_curves.csv (문서 그래프용), summary.json
"""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "analysis"))

import numpy as np
import pandas as pd
from common import RESULTS, save_json, log
from fiemap import fiemap

OUT = RESULTS / "burst"
BLCKSZ, SEG_BLOCKS, FS_BLOCK = 8192, 131072, 4096
TAGS = {"burst_base": "삽입 전", "burst_s1": "모음", "burst_s16": "16개씩 섞음", "burst_s512": "섞음"}


def parse_blocks(s):
    return np.concatenate([np.arange(int(a), int(a) + int(n)) for a, n in (p.split("+") for p in s.split(";"))])


def lba_of_blocks(layout_json):
    """블록 번호 -> 디스크 LBA 바이트 위치 (블록 첫 4KB 기준) 함수를 만든다."""
    j = json.loads(Path(layout_json).read_text())
    base = ROOT / "pgdata" / j["relpath"]
    segs = [base if s == 0 else Path(f"{base}.{s}") for s in range(j["segments"])]
    maps = []
    for p in segs:
        ext = fiemap(str(p))
        maps.append((np.array([e[0] for e in ext], np.int64), np.array([e[1] for e in ext], np.int64),
                     np.array([e[2] for e in ext], np.int64)))
    part = int(j["partition_start_bytes"])

    def f(blocks):
        blocks = np.asarray(blocks, np.int64)
        out = np.empty(len(blocks), np.int64)
        for s in np.unique(blocks // SEG_BLOCKS):
            m = blocks // SEG_BLOCKS == s
            off = (blocks[m] % SEG_BLOCKS) * BLCKSZ
            lo, ph, ln = maps[s]
            i = np.searchsorted(lo, off, side="right") - 1
            assert ((i >= 0) & (off < lo[i] + ln[i])).all()
            out[m] = ph[i] + (off - lo[i]) + part
        return out
    return f


def rq1(hot, cold, base_nblocks):
    rows = []
    for tag, name in TAGS.items():
        L = pd.read_csv(OUT / f"layout_{tag}.csv").set_index("list_id")
        lba = lba_of_blocks(OUT / f"layout_{tag}.json")
        for l in L.index:
            cls = "hot" if l in hot else "cold"
            b = np.sort(parse_blocks(L.at[l, "blocks"]))
            nb = b[b >= base_nblocks] if tag != "burst_base" else np.empty(0, np.int64)
            d_pages = np.diff(nb)
            d_lba = np.abs(np.diff(lba(nb))) if len(nb) > 1 else np.empty(0)
            rows.append({"tag": tag, "scenario": name, "list_id": int(l), "cls": cls,
                         "n_pages": int(L.at[l, "n_pages"]), "new_pages": int(len(nb)),
                         "block_runs": int(L.at[l, "block_runs"]), "lba_runs": int(L.at[l, "lba_runs"]),
                         "gap_pages_median": float(np.median(d_pages)) if len(d_pages) else np.nan,
                         "gap_lba_kb_median": float(np.median(d_lba)) / 1024 if len(d_lba) else np.nan,
                         "gap_lba_kb_p10": float(np.percentile(d_lba, 10)) / 1024 if len(d_lba) else np.nan,
                         "gap_lba_kb_p90": float(np.percentile(d_lba, 90)) / 1024 if len(d_lba) else np.nan})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "perlist_rq1.csv", index=False)
    g = df.groupby(["tag", "scenario", "cls"])
    s = g.agg(lists=("list_id", "size"), new_pages=("new_pages", "mean"), block_runs=("block_runs", "mean"),
              lba_runs=("lba_runs", "mean"), lba_runs_p10=("lba_runs", lambda x: x.quantile(.1)),
              lba_runs_p90=("lba_runs", lambda x: x.quantile(.9)),
              gap_pages_median=("gap_pages_median", "median"), gap_lba_kb_median=("gap_lba_kb_median", "median"),
              gap_lba_kb_p10=("gap_lba_kb_median", lambda x: x.quantile(.1)),
              gap_lba_kb_p90=("gap_lba_kb_median", lambda x: x.quantile(.9))).reset_index()
    # 예측 대조
    for t in ["s1", "s16", "s512"]:
        p = pd.read_csv(OUT / f"pred_{t}.csv").set_index("list_id")
        m = df[df.tag == f"burst_{t}"].set_index("list_id").loc[p.index]
        s.loc[(s.tag == f"burst_{t}"), "block_runs_pred_match"] = float((m.block_runs == p.block_runs).mean())
    s.to_csv(OUT / "summary_rq1.csv", index=False)
    return df, s


def rq2(hot, rounds):
    rows = []
    for tag in TAGS:
        for k in range(1, rounds + 1):
            f = OUT / "replay" / f"replay_{tag}_r{k}.csv"
            r = pd.read_csv(f)
            r["tag"], r["round"] = tag, k
            rows.append(r)
    d = pd.concat(rows, ignore_index=True)
    d["cls"] = np.where(d.list_id.isin(hot), "hot", "cold")
    d["Bk"] = d.B // 1024
    # list별 회차 평균
    pl = d.groupby(["tag", "cls", "list_id", "Bk"]).agg(
        req=("app_requests", "first"), ideal=("ideal_requests", "first"), amp=("read_amp", "first"),
        t_ms=("wall_time_us", lambda x: x.mean() / 1e3)).reset_index()
    if not (d.groupby(["tag", "list_id", "Bk"]).app_requests.nunique() == 1).all():
        sys.exit("회차 간 요청 수가 다름")
    per_round = d.groupby(["tag", "cls", "Bk", "round"]).wall_time_us.mean().div(1e3)
    cv = per_round.groupby(["tag", "cls", "Bk"]).agg(lambda x: x.std() / x.mean() if len(x) > 1 else np.nan)
    s = pl.groupby(["tag", "cls", "Bk"]).agg(lists=("list_id", "nunique"), requests=("req", "mean"),
                                              ideal=("ideal", "mean"), read_amp=("amp", "mean"),
                                              time_ms=("t_ms", "mean")).reset_index()
    s["round_cv"] = s.set_index(["tag", "cls", "Bk"]).index.map(cv)
    s["scenario"] = s.tag.map(TAGS)
    s.to_csv(OUT / "summary_rq2.csv", index=False)
    # list별 이득
    w = pl.pivot_table(index=["tag", "cls", "list_id"], columns="Bk", values="t_ms")
    gain = pd.DataFrame({"t8_ms": w[8], "best_ms": w.min(axis=1), "best_B_kb": w.idxmin(axis=1)})
    gain["gain"] = gain.t8_ms / gain.best_ms
    gain = gain.reset_index()
    gain["scenario"] = gain.tag.map(TAGS)
    gain.to_csv(OUT / "gain_per_list.csv", index=False)
    return s, gain


GAIN_ROWS = [("burst_base", "hot", "base_hot", "삽입 전 hot list", "기준"),
             ("burst_base", "cold", "base_cold", "cold list (삽입 없음)", "기준"),
             ("burst_s1", "hot", "s1", "모음", "시나리오"),
             ("burst_s16", "hot", "s16", "16개씩 섞음", "시나리오"),
             ("burst_s512", "hot", "s512", "섞음", "시나리오")]


def gain_summary(gain):
    """그래프 6용 요약: 조건별 list 이득 p10 / 중앙값 / p90, 가장 흔한 최적 B. -> gain_summary.csv"""
    rows = []
    for tag, cls, slug, label, kind in GAIN_ROWS:
        g = gain[(gain.tag == tag) & (gain.cls == cls)]
        p10, p50, p90 = np.percentile(g.gain, [10, 50, 90])
        rows.append({"slug": slug, "label": label, "kind": kind, "lists": len(g),
                     "gain_p10": round(p10, 3), "gain_median": round(p50, 3), "gain_p90": round(p90, 3),
                     "best_b_kb": int(g.best_B_kb.mode().iloc[0])})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "gain_summary.csv", index=False)
    return df


SCEN = [("burst_base", "base", "삽입 전 (연속)"), ("burst_s1", "s1", "모음"),
        ("burst_s16", "s16", "16개씩 섞음"), ("burst_s512", "s512", "섞음")]


def figure_csvs(r1, s2):
    """그래프 2·3·4용 CSV (hot list만).
    rq1_hot_perlist.csv: list별 extent 수(lba_runs)와 새 페이지 간 LBA 간격 중앙값(KB). 삽입 전은 새 페이지가 없어 제외.
    rq2_hot_curves.csv: 시나리오 × B별 요청 수, 연속이라면 필요한 요청 수, read amplification, 평균 extent 수."""
    p = r1[(r1.cls == "hot") & (r1.tag != "burst_base")]
    lab = {t: (s, l) for t, s, l in SCEN}
    out = pd.DataFrame({"slug": p.tag.map(lambda t: lab[t][0]), "label": p.tag.map(lambda t: lab[t][1]),
                        "list_id": p.list_id, "extents": p.lba_runs, "gap_kb": p.gap_lba_kb_median.round(1)})
    out.to_csv(OUT / "rq1_hot_perlist.csv", index=False)
    ext = r1[r1.cls == "hot"].groupby("tag").lba_runs.mean()
    rows = []
    for tag, slug, label in SCEN:
        x = s2[(s2.tag == tag) & (s2.cls == "hot")].sort_values("Bk")
        for _, r in x.iterrows():
            rows.append({"slug": slug, "label": label, "block_kb": int(r.Bk), "requests": round(r.requests, 2),
                         "ideal": round(r.ideal, 2), "read_amp": round(r.read_amp, 3), "extents": round(ext[tag], 2)})
    pd.DataFrame(rows).to_csv(OUT / "rq2_hot_curves.csv", index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()
    plan = json.loads((OUT / "plan.json").read_text())
    hot = set(pd.read_csv(OUT / "hot_lists.csv").list_id)
    cold = set(range(2450)) - hot
    df, s1 = rq1(hot, cold, plan["base_nblocks"])
    s2, gain = rq2(hot, a.rounds)
    gain_summary(gain)
    figure_csvs(df, s2)
    pd.set_option("display.width", 220)
    log("RQ1\n" + s1.round(2).to_string(index=False))
    piv = lambda col: s2.pivot_table(index=["scenario", "cls"], columns="Bk", values=col)
    log("RQ2 요청 수\n" + piv("requests").round(1).to_string())
    log("RQ2 읽기 증폭\n" + piv("read_amp").round(2).to_string())
    log("RQ2 시간(ms)\n" + piv("time_ms").round(2).to_string())
    log("RQ2 회차 간 CV 최대: " + f"{s2.round_cv.max():.3f}")
    gs = gain.groupby(["scenario", "cls"]).gain.describe(percentiles=[.1, .5, .9])[["count", "10%", "50%", "90%"]]
    gs["best_B_mode_kb"] = gain.groupby(["scenario", "cls"]).best_B_kb.agg(lambda x: x.mode().iloc[0])
    log("큰 I/O 이득 (8KB -> 최적 B)\n" + gs.round(2).to_string())
    save_json(OUT / "summary.json", {"rq1": s1.to_dict("records"), "gain": gs.reset_index().to_dict("records")})


if __name__ == "__main__":
    main()
