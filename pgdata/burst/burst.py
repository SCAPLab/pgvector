"""몰린 삽입 실험 (RQ1·RQ2 확장): 같은 기준 인덱스·같은 hot list·같은 벡터, 삽입 순서만 다르게.

  burst.py load-base              새 DB(PGDATABASE)에 items 테이블을 만들고 build 600만 개 적재
  burst.py plan                   기준 인덱스 복사본에서 centroid를 읽어 pool 벡터의 목적지 list 계산,
                                  hot/cold list 선정, 시나리오별 삽입 순서와 예측 배치 저장
  burst.py insert --scenario S    순서 파일대로 COPY 삽입 (PGDATABASE = 시나리오 DB)
  burst.py check --scenario S     실측 배치를 계획·예측과 대조

시나리오: s1 = 모음 (list 하나씩 차례로), s16 = 16개씩 섞음 (16개 묶음 안에서 섞고 묶음은 차례로),
          s512 = 섞음 (hot list 512개의 벡터를 전부 섞음). cold list는 삽입 없음.
산출: results/burst/ (plan.json, hot_lists.csv, order_<S>.npy, pred_<S>.csv, replay_lists.txt, check_<S>.json)
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "load"))

import numpy as np
import pandas as pd
import psycopg
from common import SPLITS, RESULTS, N_BUILD, LISTS, DIM, log, save_json
from db_load import HEADER, TRAILER, item_rows, copy_items

BLCKSZ, SEG_BLOCKS, CAP, INVALID = 8192, 131072, 15, 0xFFFFFFFF
OUT = RESULTS / "burst"
SNAP = Path(__file__).resolve().parents[1] / "data" / "snapshots"
SIZES_KB = [4, 8, 16, 32, 64, 128, 256, 512, 1024]
N_HOT, GROUP, INSERT_RATIO, N_COLD_SAMPLE = 512, 16, 0.5, 128
SEED = 2026
TIE_MARGIN = 2.0          # 가장 가까운 두 centroid의 제곱 거리 차가 이보다 작으면 쓰지 않음 (float32 오차 대비)
SCENARIOS = {"s1": "모음", "s16": "16개씩 섞음", "s512": "섞음"}


def parse_blocks(s):
    return np.concatenate([np.arange(int(a), int(a) + int(n)) for a, n in (p.split("+") for p in s.split(";"))])


def runs(sorted_blocks):
    return 1 + int(np.count_nonzero(np.diff(sorted_blocks) != 1))


def requests(blocks, Bk):
    """run_replay.units_for와 같은 계산: 블록들을 덮는 B 정렬 단위 수."""
    B = Bk * 1024
    off = blocks * BLCKSZ
    first, last = off // B, (off + BLCKSZ - 1) // B
    if B < BLCKSZ:
        return int(np.unique(np.concatenate([np.arange(f, l + 1) for f, l in zip(first, last)])).size)
    return int(np.unique(first).size)


# ---------------------------------------------------------------- load-base
def cmd_load_base(a):
    build = np.load(SPLITS / "build.npy", mmap_mode="r")
    perm = np.load(SPLITS / "perm.npy")
    with psycopg.connect(autocommit=False) as conn:
        conn.execute("CREATE TABLE items (id bigint PRIMARY KEY, orig_idx bigint, embedding vector(128)) "
                     "WITH (autovacuum_enabled = false)")
        conn.commit()
        log(f"build {len(build)}개 COPY -> {conn.info.dbname}")
        sec = copy_items(conn, "items", 0, build, perm, 200_000)
        v = conn.execute("SELECT embedding::text FROM items WHERE id = 12345").fetchone()[0]
        back = np.array([float(x) for x in v.strip("[]").split(",")])
        assert np.array_equal(back, build[12345].astype(float)), "items 왕복 불일치"
    save_json(OUT / "load_base.json", {"rows": len(build), "seconds": sec})
    log(f"적재 완료 {sec:.0f}s")


# ---------------------------------------------------------------- plan
def read_centers(tag):
    """인덱스 복사본의 list 정보 페이지에서 (startPage, insertPage, center)를 list 순서대로 읽는다."""
    segs = sorted((SNAP / tag).iterdir(), key=lambda p: 0 if "." not in p.name else int(p.name.split(".")[1]))
    raw = open(segs[0], "rb").read(BLCKSZ * 512)
    page = lambda b: memoryview(raw)[b * BLCKSZ:(b + 1) * BLCKSZ]
    u16 = lambda pg, o: int.from_bytes(pg[o:o + 2], "little")
    u32 = lambda pg, o: int.from_bytes(pg[o:o + 4], "little")
    meta = page(0)
    assert u32(meta, 24) == 0x14FF1A7, "magic 불일치"
    lists = u16(meta, 34)
    starts, inserts, centers, b = [], [], [], 1
    while b != INVALID:
        pg = page(b)
        for k in range((u16(pg, 12) - 24) // 4):
            lp = u32(pg, 24 + 4 * k)
            if (lp >> 15) & 3 != 1:
                continue
            off = lp & 0x7FFF
            dim = int.from_bytes(pg[off + 12:off + 14], "little", signed=True)
            assert dim == DIM, f"center dim {dim}"
            starts.append(u32(pg, off)); inserts.append(u32(pg, off + 4))
            centers.append(np.frombuffer(pg[off + 16:off + 16 + 4 * DIM], dtype="<f4").copy())
        b = u32(pg, BLCKSZ - 8)
    assert len(centers) == lists == LISTS, f"list {len(centers)} != {lists}"
    return np.array(starts), np.array(inserts), np.stack(centers)


def assign(pool, C):
    """pool 벡터마다 가장 가까운 centroid와, 2등과의 제곱 거리 차 (float64로 계산)."""
    C = C.astype(np.float64)
    c2 = (C * C).sum(1)
    n = len(pool)
    best = np.empty(n, np.int32)
    margin = np.empty(n, np.float64)
    t0 = time.time()
    for s in range(0, n, 50_000):
        e = min(n, s + 50_000)
        X = np.asarray(pool[s:e], dtype=np.float64)
        D = c2[None, :] - 2.0 * (X @ C.T)
        two = np.argpartition(D, 1, axis=1)[:, :2]
        d = np.take_along_axis(D, two, 1)
        first = d.argmin(1)
        best[s:e] = two[np.arange(e - s), first]
        margin[s:e] = np.abs(d[:, 0] - d[:, 1])
        if s % 1_000_000 == 0:
            log(f"  배정 {e:,}/{n:,} ({time.time() - t0:.0f}s)")
    return best, margin


def simulate(order_list, base, nblocks0):
    """pgvector 삽입 규칙(마지막 페이지가 차면 파일 끝에 한 장)으로 list별 새 블록 번호를 예측."""
    fill = (base.n_tuples - CAP * (base.n_pages - 1)).to_numpy().copy()
    assert ((fill >= 1) & (fill <= CAP)).all(), "빌드 마지막 페이지 채움 이상"
    nxt, new = nblocks0, defaultdict(list)
    for l in order_list:
        if fill[l] == CAP:
            new[l].append(nxt); nxt += 1; fill[l] = 1
        else:
            fill[l] += 1
    return new, nxt


def cmd_plan(a):
    rng = np.random.default_rng(SEED)
    base = pd.read_csv(OUT / "layout_burst_base.csv").set_index("list_id")
    bj = json.loads((OUT / "layout_burst_base.json").read_text())
    starts, inserts, C = read_centers("burst_base")
    assert (starts == base.start_page.to_numpy()).all() and (inserts == base.insert_page.to_numpy()).all(), \
        "centroid 순서가 layout list 순서와 다름"
    log(f"centroid {len(C)}개 읽음")

    pool = np.load(SPLITS / "pool.npy", mmap_mode="r")
    best, margin = assign(pool, C)
    ok = margin >= TIE_MARGIN
    log(f"배정 완료: 동점 근처로 제외 {int((~ok).sum()):,}개 ({(~ok).mean():.5%})")
    np.save(OUT / "pool_assign.npy", best)

    size = base.n_tuples
    need = np.round(INSERT_RATIO * size).astype(int)
    avail = np.bincount(best[ok], minlength=LISTS)
    med = float(size.median())
    cand = (size - med).abs().sort_values(kind="stable").index
    hot = [int(l) for l in cand if avail[l] >= need[l]][:N_HOT]
    assert len(hot) == N_HOT
    hot_set = set(hot)
    cold = np.array([l for l in range(LISTS) if l not in hot_set])
    cold_sample = np.sort(rng.choice(cold, N_COLD_SAMPLE, replace=False))

    by_list = defaultdict(list)
    for i in np.flatnonzero(ok):
        by_list[int(best[i])].append(i)
    vecs = {l: rng.choice(np.array(by_list[l]), need[l], replace=False) for l in hot}
    hot_order = rng.permutation(hot)
    groups = hot_order.reshape(-1, GROUP)

    orders = {
        "s1": np.concatenate([rng.permutation(vecs[l]) for l in hot_order]),
        "s16": np.concatenate([rng.permutation(np.concatenate([vecs[l] for l in g])) for g in groups]),
        "s512": rng.permutation(np.concatenate([vecs[l] for l in hot_order])),
    }
    total = int(sum(need[l] for l in hot))
    for s, o in orders.items():
        assert len(o) == total and len(np.unique(o)) == total
        np.save(OUT / f"order_{s}.npy", o.astype(np.int64))

    # 예측 배치
    nb0 = int(bj["nblocks"])
    pred_summary = {}
    replay_lists = np.sort(np.concatenate([np.array(hot), cold_sample]))
    for s, o in orders.items():
        new, end = simulate(best[o], base, nb0)
        rows = []
        for l in replay_lists:
            nb = np.array(new.get(l, []), np.int64)
            blocks = np.sort(np.concatenate([parse_blocks(base.at[l, "blocks"]), nb]))
            d = np.diff(nb)
            r = {"list_id": int(l), "hot": l in hot_set, "new_pages": len(nb), "block_runs": runs(blocks),
                 "new_gap_pages_median": float(np.median(d)) if len(d) else np.nan}
            r.update({f"req_{b}": requests(blocks, b) for b in SIZES_KB})
            rows.append(r)
        p = pd.DataFrame(rows)
        p.to_csv(OUT / f"pred_{s}.csv", index=False)
        h = p[p.hot]
        pred_summary[s] = {"nblocks_after": int(end), "hot_block_runs_mean": float(h.block_runs.mean()),
                           "hot_new_pages_mean": float(h.new_pages.mean()),
                           "hot_gap_median": float(h.new_gap_pages_median.median()),
                           "hot_req_mean": {b: float(h[f"req_{b}"].mean()) for b in SIZES_KB}}
        log(f"예측 {s} ({SCENARIOS[s]}): hot block_runs {pred_summary[s]['hot_block_runs_mean']:.1f}, "
            f"간격 중앙값 {pred_summary[s]['hot_gap_median']:.0f}페이지, 1MB 요청 {pred_summary[s]['hot_req_mean'][1024]:.1f}")

    np.savetxt(OUT / "replay_lists.txt", replay_lists, fmt="%d")
    pd.DataFrame({"list_id": hot, "build_size": size.loc[hot].to_numpy(), "insert": [int(need[l]) for l in hot],
                  "group": [int(np.flatnonzero((groups == l).any(1))[0]) for l in hot]}).to_csv(OUT / "hot_lists.csv", index=False)
    save_json(OUT / "plan.json", {
        "seed": SEED, "n_hot": N_HOT, "group": GROUP, "insert_ratio": INSERT_RATIO, "tie_margin": TIE_MARGIN,
        "excluded_near_ties": int((~ok).sum()), "median_build_size": med,
        "hot_size_range": [int(size.loc[hot].min()), int(size.loc[hot].max())],
        "inserted_vectors": total, "cold_sample": cold_sample.tolist(), "scenarios": SCENARIOS,
        "base_nblocks": nb0, "prediction": pred_summary})
    log(f"계획 저장: hot {N_HOT}개 (크기 {size.loc[hot].min()}~{size.loc[hot].max()}), 삽입 {total:,}개, "
        f"재생 대상 list {len(replay_lists)}개")


# ---------------------------------------------------------------- insert
def cmd_insert(a):
    order = np.load(OUT / f"order_{a.scenario}.npy")
    pool = np.load(SPLITS / "pool.npy", mmap_mode="r")
    perm = np.load(SPLITS / "perm.npy")
    t0 = time.time()
    with psycopg.connect(autocommit=False) as conn:
        log(f"{a.scenario} ({SCENARIOS[a.scenario]}): {len(order):,}개 삽입 -> {conn.info.dbname}")
        for s in range(0, len(order), a.batch):
            idx = order[s:s + a.batch]
            ids = N_BUILD + idx
            data = item_rows(ids, perm[ids], np.asarray(pool[np.sort(idx)])[np.argsort(np.argsort(idx))])
            with conn.cursor() as cur:
                with cur.copy("COPY items (id, orig_idx, embedding) FROM STDIN (FORMAT BINARY)") as cp:
                    cp.write(HEADER + data + TRAILER)
            conn.commit()
        n = conn.execute("SELECT count(*) FROM items").fetchone()[0]
    sec = time.time() - t0
    save_json(OUT / f"insert_{a.scenario}.json", {"scenario": a.scenario, "rows": int(len(order)),
                                                   "seconds": sec, "table_rows_after": int(n)})
    log(f"완료 {sec:.0f}s ({len(order) / sec:,.0f} rows/s), 테이블 {n:,}행")


# ---------------------------------------------------------------- check
def cmd_check(a):
    base = pd.read_csv(OUT / "layout_burst_base.csv").set_index("list_id")
    got = pd.read_csv(OUT / f"layout_burst_{a.scenario}.csv").set_index("list_id")
    pred = pd.read_csv(OUT / f"pred_{a.scenario}.csv").set_index("list_id")
    hot = pd.read_csv(OUT / "hot_lists.csv").set_index("list_id")
    plan_ins = pd.Series(0, index=base.index)
    plan_ins.loc[hot.index] = hot["insert"]
    d_tuples = (got.n_tuples - base.n_tuples) - plan_ins
    cold = base.index.difference(hot.index)
    g = got.loc[pred.index]
    res = {
        "scenario": a.scenario,
        "lists_tuple_count_mismatch": int((d_tuples != 0).sum()),
        "cold_lists_changed": int(((got.loc[cold].n_pages != base.loc[cold].n_pages) |
                                   (got.loc[cold].block_runs != base.loc[cold].block_runs)).sum()),
        "replay_lists_block_runs_mismatch": int((g.block_runs != pred.block_runs).sum()),
        "hot_block_runs_mean": float(got.loc[hot.index].block_runs.mean()),
        "hot_block_runs_pred_mean": float(pred[pred.hot].block_runs.mean()),
        "hot_lba_runs_mean": float(got.loc[hot.index].lba_runs.mean()),
        "all_chains_ascending": bool(got.chain_ascending.all()),
    }
    save_json(OUT / f"check_{a.scenario}.json", res)
    log(f"대조 {a.scenario}: {res}")
    if res["lists_tuple_count_mismatch"] or res["cold_lists_changed"]:
        log("경고: 계획과 다른 list가 있음 (목적지 계산 차이 가능성) — 결과 해석 때 check 파일 참고")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("load-base")
    sp.add_parser("plan")
    p = sp.add_parser("insert"); p.add_argument("--scenario", required=True, choices=list(SCENARIOS))
    p.add_argument("--batch", type=int, default=50_000)
    p = sp.add_parser("check"); p.add_argument("--scenario", required=True, choices=list(SCENARIOS))
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    {"load-base": cmd_load_base, "plan": cmd_plan, "insert": cmd_insert, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    main()
