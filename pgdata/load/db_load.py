"""DB 적재·삽입·recall 측정.

  schema            items, queries 테이블 생성 (문서 1.6절)
  queries           쿼리 1만 개 적재 (id = 1..10000)
  build             build 600만 적재 (id 0..N_BUILD-1)
  insert --to P     pool을 앞에서부터 누적 P% 지점까지 삽입 (P = 10, 30, 50)
  recall --stage P [--table items]   recall@10 측정 (정답: data/splits/gt_P.npz)

적재는 binary COPY로 한다. pgvector binary 형식: int16 dim, int16 unused(0), float4 x dim (big-endian).
접속 정보는 환경변수(PGHOST, PGPORT, PGDATABASE)를 쓴다 (env/config.sh).
"""
import argparse
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import numpy as np
import psycopg
from common import (SPLITS, RESULTS, N_BUILD, STAGES, K, DIM, PROBES, sqdist_exact, log, save_json)

HEADER = b"PGCOPY\n\xff\r\n\x00" + (0).to_bytes(4, "big") + (0).to_bytes(4, "big")
TRAILER = (-1).to_bytes(2, "big", signed=True)
VEC_LEN = 4 + 4 * DIM

ITEM_DT = np.dtype([("nf", ">i2"), ("l1", ">i4"), ("id", ">i8"), ("l2", ">i4"), ("orig", ">i8"),
                    ("l3", ">i4"), ("dim", ">i2"), ("unused", ">i2"), ("x", ">f4", (DIM,))])
QUERY_DT = np.dtype([("nf", ">i2"), ("l1", ">i4"), ("id", ">i4"),
                     ("l2", ">i4"), ("dim", ">i2"), ("unused", ">i2"), ("x", ">f4", (DIM,))])

SCHEMA = """
CREATE TABLE items (id bigint PRIMARY KEY, orig_idx bigint, embedding vector(128))
  WITH (autovacuum_enabled = false);
CREATE TABLE queries (id int PRIMARY KEY, v vector(128));
"""


def item_rows(ids, orig, vecs):
    rec = np.empty(len(ids), dtype=ITEM_DT)
    rec["nf"], rec["l1"], rec["l2"], rec["l3"] = 3, 8, 8, VEC_LEN
    rec["id"], rec["orig"] = ids, orig
    rec["dim"], rec["unused"] = DIM, 0
    rec["x"] = vecs
    return rec.tobytes()


def copy_items(conn, table, first_id, vecs, perm, batch):
    n = len(vecs)
    t0 = time.time()
    for s in range(0, n, batch):
        e = min(n, s + batch)
        ids = np.arange(first_id + s, first_id + e, dtype=np.int64)
        data = item_rows(ids, perm[ids], np.asarray(vecs[s:e]))
        with conn.cursor() as cur:
            with cur.copy(f"COPY {table} (id, orig_idx, embedding) FROM STDIN (FORMAT BINARY)") as cp:
                cp.write(HEADER + data + TRAILER)
        conn.commit()
        rate = e / (time.time() - t0)
        log(f"  {e}/{n} rows ({rate:,.0f} rows/s)")
    return time.time() - t0


def cmd_schema(conn, a):
    conn.execute(SCHEMA)
    conn.commit()
    log("schema 생성")


def cmd_queries(conn, a):
    q = np.load(SPLITS / "queries.npy")
    rec = np.empty(len(q), dtype=QUERY_DT)
    rec["nf"], rec["l1"], rec["l2"] = 2, 4, VEC_LEN
    rec["id"] = np.arange(1, len(q) + 1)
    rec["dim"], rec["unused"] = DIM, 0
    rec["x"] = q
    with conn.cursor() as cur:
        with cur.copy("COPY queries (id, v) FROM STDIN (FORMAT BINARY)") as cp:
            cp.write(HEADER + rec.tobytes() + TRAILER)
    conn.commit()
    # 왕복 확인: DB에 들어간 값이 원본과 같은지
    got = conn.execute("SELECT v::text FROM queries WHERE id = 1").fetchone()[0]
    back = np.array([float(x) for x in got.strip("[]").split(",")])
    assert np.array_equal(back, q[0].astype(float)), "queries 왕복 불일치"
    log(f"queries {len(q)}개 적재, 왕복 확인 OK")


def cmd_build(conn, a):
    build = np.load(SPLITS / "build.npy", mmap_mode="r")
    perm = np.load(SPLITS / "perm.npy")
    log(f"build {len(build)}개 COPY")
    sec = copy_items(conn, "items", 0, build, perm, a.batch)
    v = conn.execute("SELECT embedding::text FROM items WHERE id = 12345").fetchone()[0]
    back = np.array([float(x) for x in v.strip("[]").split(",")])
    assert np.array_equal(back, build[12345].astype(float)), "items 왕복 불일치"
    save_json(RESULTS / "setup" / "load_build.json", {"rows": len(build), "seconds": sec})
    log(f"완료 {sec:.0f}s, 왕복 확인 OK")


def cmd_insert(conn, a):
    pool = np.load(SPLITS / "pool.npy", mmap_mode="r")
    perm = np.load(SPLITS / "perm.npy")
    have = conn.execute(f"SELECT count(*) FROM {a.table} WHERE id >= %s", (N_BUILD,)).fetchone()[0]
    want = STAGES[a.to]
    if have > want:
        sys.exit(f"이미 {have}개 삽입됨 (> {want})")
    if have == want:
        log(f"이미 {a.to}% ({want}개)")
        return
    log(f"pool[{have}:{want}] 삽입 -> {a.to}%")
    sec = copy_items(conn, a.table, N_BUILD + have, pool[have:want], perm, a.batch)
    save_json(RESULTS / "rq1" / f"insert_{a.to}.json",
              {"from": int(have), "to": int(want), "rows": int(want - have), "seconds": sec})
    log(f"완료 {sec:.0f}s")


def vec_by_id(ids, build, pool):
    out = np.empty(ids.shape + (DIM,), np.uint8)
    m = ids < N_BUILD
    out[m] = build[ids[m]]
    out[~m] = pool[ids[~m] - N_BUILD]
    return out


def cmd_recall(conn, a):
    gt = np.load(SPLITS / f"gt_{a.stage}.npz")
    gt_i, gt_d = gt["ids"], gt["dists"]
    queries = np.load(SPLITS / "queries.npy")
    build = np.load(SPLITS / "build.npy", mmap_mode="r")
    pool = np.load(SPLITS / "pool.npy", mmap_mode="r")
    nq = a.nq or len(queries)
    sql = (f"SELECT id FROM {a.table} ORDER BY embedding <-> "
           f"(SELECT v FROM queries WHERE id = %s) LIMIT {K}")
    conn.execute(f"SET ivfflat.probes = {PROBES}")
    plan = "\n".join(r[0] for r in conn.execute("EXPLAIN " + sql.replace("%s", "1")).fetchall())
    if "Index Scan" not in plan:
        sys.exit(f"인덱스를 쓰지 않는 plan:\n{plan}")
    res = np.full((nq, K), -1, np.int64)
    lat = np.empty(nq)
    for q in range(nq):
        t = time.perf_counter()
        rows = conn.execute(sql, (q + 1,)).fetchall()
        lat[q] = time.perf_counter() - t
        res[q, :len(rows)] = [r[0] for r in rows]
    hit = np.array([len(set(res[q]) & set(gt_i[q])) for q in range(nq)]) / K
    # 동점 고려: 결과 벡터의 정확한 거리가 정답 10번째 거리 이하이면 정답으로 센다
    rd = sqdist_exact(queries[:nq], vec_by_id(np.maximum(res, 0), build, pool))
    tie = ((rd <= gt_d[:nq, K - 1:K]) & (res >= 0)).sum(1) / K
    out = {
        "stage": a.stage, "table": a.table, "probes": PROBES, "n_queries": nq,
        "recall_at_10": float(hit.mean()), "recall_at_10_tie_aware": float(tie.mean()),
        "latency_ms_p50": float(np.percentile(lat, 50) * 1e3),
        "latency_ms_p99": float(np.percentile(lat, 99) * 1e3),
        "shared_buffers": conn.execute("SHOW shared_buffers").fetchone()[0],
        "plan": plan,
    }
    save_json(RESULTS / "rq1" / f"recall_{a.table}_{a.stage}.json", out)
    np.save(RESULTS / "rq1" / f"result_ids_{a.table}_{a.stage}.npy", res)
    log(f"recall@10 = {out['recall_at_10']:.4f} (tie-aware {out['recall_at_10_tie_aware']:.4f}), "
        f"p50 {out['latency_ms_p50']:.1f}ms")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("schema")
    sp.add_parser("queries")
    p = sp.add_parser("build"); p.add_argument("--batch", type=int, default=200_000)
    p = sp.add_parser("insert"); p.add_argument("--to", type=int, required=True, choices=[10, 30, 50])
    p.add_argument("--batch", type=int, default=50_000); p.add_argument("--table", default="items")
    p = sp.add_parser("recall"); p.add_argument("--stage", type=int, required=True)
    p.add_argument("--table", default="items"); p.add_argument("--nq", type=int, default=0)
    a = ap.parse_args()
    with psycopg.connect(autocommit=False) as conn:
        globals()["cmd_" + a.cmd](conn, a)


if __name__ == "__main__":
    main()
