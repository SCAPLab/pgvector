"""그래프 2용 데이터: 시나리오(k)별 hot posting list의 평균 LBA 간격.

LBA 간격 = 같은 posting list에 연이어 할당된 두 새 페이지 사이의 LBA 거리 (summarize.py와 같은 정의·같은 FIEMAP 방식).
posting list마다 간격의 평균을 구하고, hot 512개에 대해 그 평균·p10·p90을 낸다.
검증: posting list별 간격 중앙값이 summarize.py가 저장한 perlist_rq1.csv 값과 같아야 한다 (인덱스 파일이 그대로인지 확인).
산출: results/burst/rq1_gap_by_k.csv (slug, k, lists, gap_kb_mean, gap_kb_p10, gap_kb_p90, gap_kb_median)
실행: .venv/bin/python -I burst/paper/fig2_data.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "burst"))
import summarize as S   # noqa: E402  (lba_of_blocks, parse_blocks, OUT 재사용)

SCEN = [("burst_s1", "s1", 1), ("burst_s16", "s16", 16), ("burst_s512", "s512", 512)]

plan = json.loads((S.OUT / "plan.json").read_text())
hot = set(pd.read_csv(S.OUT / "hot_lists.csv").list_id)
saved = pd.read_csv(S.OUT / "perlist_rq1.csv").set_index(["tag", "list_id"]).gap_lba_kb_median

rows = []
for tag, slug, k in SCEN:
    L = pd.read_csv(S.OUT / f"layout_{tag}.csv").set_index("list_id")
    lba = S.lba_of_blocks(S.OUT / f"layout_{tag}.json")
    means, meds = [], []
    for l in sorted(hot):
        b = np.sort(S.parse_blocks(L.at[l, "blocks"]))
        nb = b[b >= plan["base_nblocks"]]
        d = np.abs(np.diff(lba(nb))) / 1024
        means.append(d.mean())
        meds.append(np.median(d))
        if not np.isclose(np.median(d), saved[(tag, l)], atol=0.051):
            sys.exit(f"{tag} list {l}: 중앙값 {np.median(d)} != 저장값 {saved[(tag, l)]} (인덱스 파일이 바뀜?)")
    m = np.array(means)
    rows.append({"slug": slug, "k": k, "lists": len(m), "gap_kb_mean": round(m.mean(), 1),
                 "gap_kb_p10": round(np.percentile(m, 10), 1), "gap_kb_p90": round(np.percentile(m, 90), 1),
                 "gap_kb_median": round(float(np.median(meds)), 1)})

out = pd.DataFrame(rows)
out.to_csv(S.OUT / "rq1_gap_by_k.csv", index=False)
print(out.to_string(index=False))
