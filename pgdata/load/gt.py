"""정답(groundtruth) 계산 (문서 3.7절).

  validate : 섞기 전 원래 순서의 10M 전체에서 쿼리 앞 1000개의 top-10을 구하고,
             제공된 10M 정답(idx_10M.ivecs)과 거리 기준으로 비교한다.
  stages   : build 6M에 대한 top-10을 구한 뒤, 삽입 묶음마다 top-10을 구해 합친다.
             data/splits/gt_{0,10,30,50}.npz (ids: DB id, dists: 제곱 L2, int64)
"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import numpy as np
from common import (BASE_FILE, RAW, SPLITS, RESULTS, N_BASE, N_BUILD, STAGES, K, DIM,
                    read_bvecs, read_ivecs, sqdist_exact, knn_exact, topk_merge, log, save_json)


def find_gnd(name):
    hits = sorted((RAW / "gnd").rglob(name))
    if not hits:
        sys.exit(f"{name} 을 data/raw/gnd 에서 찾을 수 없음")
    return hits[0]


def validate(nq):
    queries = np.load(SPLITS / "queries.npy")[:nq]
    base = np.ascontiguousarray(read_bvecs(BASE_FILE, N_BASE))
    log(f"10M 원래 순서에서 쿼리 {nq}개 top-{K}")
    ours_i, ours_d = knn_exact(base, queries)
    gnd_path = find_gnd("idx_10M.ivecs")
    gnd = read_ivecs(gnd_path)[:nq, :K].astype(np.int64)
    gnd_d = sqdist_exact(queries, base[gnd.ravel()].reshape(nq, K, DIM))
    gnd_d_sorted = np.sort(gnd_d, axis=1)
    same_dist = (ours_d == gnd_d_sorted).all(axis=1)
    overlap = np.array([len(set(a) & set(b)) for a, b in zip(ours_i, gnd)]) / K
    res = {
        "gnd_file": str(gnd_path), "n_queries": nq,
        "queries_all_10_distances_equal": int(same_dist.sum()),
        "frac_distances_equal": float(same_dist.mean()),
        "mean_id_overlap": float(overlap.mean()),
        "n_queries_id_mismatch": int((overlap < 1).sum()),
        "provided_gnd_sorted_by_distance": bool((np.diff(gnd_d, axis=1) >= 0).all()),
    }
    bad = np.flatnonzero(~same_dist)[:5]
    res["examples_mismatch"] = [
        {"q": int(i), "ours_d": ours_d[i].tolist(), "gnd_d": gnd_d_sorted[i].tolist()} for i in bad]
    save_json(RESULTS / "setup" / "gt_validate.json", res)
    log(f"검증 결과: {res}")
    if not same_dist.all():
        sys.exit("정답 코드 검증 실패: 거리 목록이 다른 쿼리가 있음")


def stages():
    queries = np.load(SPLITS / "queries.npy")
    build = np.load(SPLITS / "build.npy", mmap_mode="r")
    pool = np.load(SPLITS / "pool.npy", mmap_mode="r")
    log(f"build {len(build)}개 top-{K}")
    ids, d = knn_exact(np.asarray(build), queries, id_offset=0)
    np.savez(SPLITS / "gt_0.npz", ids=ids, dists=d)
    prev = 0
    for pct, n in sorted(STAGES.items()):
        if pct == 0:
            continue
        log(f"삽입 묶음 pool[{prev}:{n}] top-{K} -> {pct}%")
        bi, bd = knn_exact(np.asarray(pool[prev:n]), queries, id_offset=N_BUILD + prev)
        ids, d = topk_merge(ids, d, bi, bd)
        np.savez(SPLITS / f"gt_{pct}.npz", ids=ids, dists=d)
        frac_new = float((ids >= N_BUILD).mean())
        log(f"  {pct}%: 정답 중 삽입 벡터 비율 {frac_new:.3f}")
        prev = n
    log("완료")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["validate", "stages"])
    ap.add_argument("--nq", type=int, default=1000)
    a = ap.parse_args()
    validate(a.nq) if a.mode == "validate" else stages()


if __name__ == "__main__":
    main()
