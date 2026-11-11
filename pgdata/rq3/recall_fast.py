"""병렬 recall@10 측정 (RQ3 재빌드 전후, 지연은 재지 않음).

  recall_fast.py --stage 50 --out <json> [--ids-out <npy>] [--compare <npy>] [--threads 16]

DB는 PG* 환경변수(PGDATABASE 등)를 따른다. --compare가 있으면 결과 id가 그 파일과 완전히 같은지도 기록한다
(같은 인덱스면 ivfflat 스캔 결과는 결정적이다).
"""
import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "load"))

import numpy as np
import psycopg
from common import SPLITS, K, PROBES, sqdist_exact, save_json, log
from db_load import vec_by_id

SQL = f"SELECT id FROM items ORDER BY embedding <-> (SELECT v FROM queries WHERE id = %s) LIMIT {K}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, default=50)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ids-out")
    ap.add_argument("--compare")
    ap.add_argument("--threads", type=int, default=16)
    a = ap.parse_args()

    gt = np.load(SPLITS / f"gt_{a.stage}.npz")
    gt_i, gt_d = gt["ids"], gt["dists"]
    queries = np.load(SPLITS / "queries.npy")
    nq = len(queries)
    res = np.full((nq, K), -1, np.int64)

    local = threading.local()
    conns = []

    def conn():
        if not hasattr(local, "c"):
            local.c = psycopg.connect(autocommit=True, application_name="rq3_recall")
            local.c.execute(f"SET ivfflat.probes = {PROBES}")
            conns.append(local.c)
        return local.c

    def run(chunk):
        c = conn()
        for q in chunk:
            rows = c.execute(SQL, (int(q) + 1,)).fetchall()
            res[q, :len(rows)] = [r[0] for r in rows]

    with psycopg.connect(autocommit=True) as c0:
        c0.execute(f"SET ivfflat.probes = {PROBES}")
        plan = "\n".join(r[0] for r in c0.execute("EXPLAIN " + SQL.replace("%s", "1")).fetchall())
        db = c0.execute("SELECT current_database()").fetchone()[0]
    if "Index Scan using items_ivf" not in plan:
        sys.exit(f"items_ivf를 쓰지 않는 plan:\n{plan}")
    with ThreadPoolExecutor(a.threads) as ex:
        list(ex.map(run, np.array_split(np.arange(nq), a.threads * 8)))
    for c in conns:
        c.close()

    hit = np.array([len(set(res[q]) & set(gt_i[q])) for q in range(nq)]) / K
    build = np.load(SPLITS / "build.npy", mmap_mode="r")
    pool = np.load(SPLITS / "pool.npy", mmap_mode="r")
    rd = sqdist_exact(queries, vec_by_id(np.maximum(res, 0), build, pool))
    tie = ((rd <= gt_d[:, K - 1:K]) & (res >= 0)).sum(1) / K
    out = {"database": db, "stage": a.stage, "probes": PROBES, "n_queries": nq,
           "recall_at_10": float(hit.mean()), "recall_at_10_tie_aware": float(tie.mean()), "plan": plan}
    if a.compare:
        ref = np.load(a.compare)
        out["compare_file"] = a.compare
        out["identical_to_compare"] = bool(np.array_equal(ref, res))
        out["rows_differing_from_compare"] = int((ref != res).any(1).sum())
    save_json(Path(a.out), out)
    if a.ids_out:
        np.save(a.ids_out, res)
    log(f"{db}: recall@10 = {out['recall_at_10']:.4f} (tie-aware {out['recall_at_10_tie_aware']:.4f})"
        + (f", 비교 파일과 동일: {out['identical_to_compare']}" if a.compare else ""))


if __name__ == "__main__":
    main()
