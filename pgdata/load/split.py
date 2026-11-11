"""base 10M을 seed 고정으로 섞고 build 6M / insert pool 4M으로 나눈다 (문서 1.1절).

산출 (data/splits/):
  perm.npy     int64 (N_BASE,)    perm[id] = base 파일 안 원래 위치 (orig_idx)
  build.npy    uint8 (N_BUILD,128) id 0..N_BUILD-1
  pool.npy     uint8 (N_POOL,128)  id N_BUILD..N_BASE-1 (앞에서부터 삽입)
  queries.npy  uint8 (10000,128)   query id = 위치 + 1
  split.json   설정·검증 정보
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import numpy as np
from common import (BASE_FILE, QUERY_FILE, SPLITS, SEED, N_BASE, N_BUILD, N_POOL,
                    N_QUERY, DIM, read_bvecs, log, save_json)


def main():
    SPLITS.mkdir(parents=True, exist_ok=True)
    want = N_BASE * (4 + DIM)
    size = BASE_FILE.stat().st_size
    if size != want:
        sys.exit(f"{BASE_FILE} 크기 {size} != {want}")
    log(f"base 읽기 {BASE_FILE} ({size} bytes)")
    base = np.ascontiguousarray(read_bvecs(BASE_FILE, N_BASE))
    queries = np.ascontiguousarray(read_bvecs(QUERY_FILE))
    if queries.shape != (N_QUERY, DIM):
        sys.exit(f"query shape {queries.shape}")

    rng = np.random.default_rng(SEED)
    perm = rng.permutation(N_BASE).astype(np.int64)
    log("섞기·분할")
    build = base[perm[:N_BUILD]]
    pool = base[perm[N_BUILD:]]
    assert len(pool) == N_POOL

    np.save(SPLITS / "perm.npy", perm)
    np.save(SPLITS / "build.npy", build)
    np.save(SPLITS / "pool.npy", pool)
    np.save(SPLITS / "queries.npy", queries)

    # 원래 순서가 실제로 뭉쳐 있는지(같은 이미지 벡터가 인접) 참고용 지표
    adj = np.abs(base[1:100001].astype(np.int32) - base[:100000].astype(np.int32)).sum(1)
    rnd = np.abs(build[1:100001].astype(np.int32) - build[:100000].astype(np.int32)).sum(1)
    info = {
        "seed": SEED, "n_base": N_BASE, "n_build": N_BUILD, "n_pool": N_POOL,
        "base_file": str(BASE_FILE), "base_bytes": size,
        "first_vec_head": base[0, :8].tolist(),
        "perm_head": perm[:5].tolist(),
        "mean_L1_adjacent_original": float(adj.mean()),
        "mean_L1_adjacent_shuffled": float(rnd.mean()),
    }
    save_json(SPLITS / "split.json", info)
    log(f"완료 {info}")


if __name__ == "__main__":
    main()
