"""ivf-frag 공통 상수와 입출력 함수.

다른 스크립트는 `python -I`로 실행하므로 이 모듈을 쓰려면
sys.path에 ivf-frag/lib 을 직접 넣는다 (각 스크립트 맨 위 참고).
"""
from pathlib import Path
import json
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SPLITS = ROOT / "data" / "splits"
RESULTS = ROOT / "results"

SEED = 42
DIM = 128
N_BASE = 10_000_000
N_BUILD = 6_000_000
N_POOL = N_BASE - N_BUILD
N_QUERY = 10_000
LISTS = 2450
PROBES = 50
K = 10
# 삽입 비율(%) -> pool 앞에서부터 넣는 누적 개수 (build 대비 비율)
STAGES = {0: 0, 10: 600_000, 30: 1_800_000, 50: 3_000_000}

BASE_FILE = RAW / f"sift{N_BASE // 1_000_000}m.bvecs"
QUERY_FILE = RAW / "bigann_query.bvecs"

# DB id 규칙: build 행은 섞은 순서의 위치 0..N_BUILD-1,
# pool 행은 N_BUILD + (pool 안 위치). orig_idx = perm[id] (base 파일 안 원래 위치).


def log(msg):
    print(f"[{time.strftime('%F %T')}] {msg}", flush=True)


def read_bvecs(path, count=None):
    """bvecs ([int32 dim][dim x uint8] 반복)를 (n, DIM) uint8 memmap view로 읽는다."""
    mm = np.memmap(path, dtype=np.uint8, mode="r")
    rec = 4 + DIM
    if mm.size % rec:
        raise ValueError(f"{path}: 크기 {mm.size}가 {rec}의 배수가 아님")
    n = mm.size // rec
    if count is not None:
        n = min(n, count)
    mm = mm[: n * rec].reshape(n, rec)
    dims = np.ascontiguousarray(mm[:, :4]).view("<i4").ravel()
    bad = np.flatnonzero(dims != DIM)
    if bad.size:
        raise ValueError(f"{path}: dim != {DIM} 인 벡터 {bad.size}개 (첫 위치 {bad[0]})")
    return mm[:, 4:]


def _read_xvecs(path, dtype):
    raw = np.fromfile(path, dtype="<i4")
    d = int(raw[0])
    raw = raw.reshape(-1, d + 1)
    if not (raw[:, 0] == d).all():
        raise ValueError(f"{path}: 행마다 dim이 다름")
    return raw[:, 1:].copy().view(dtype)


def read_ivecs(path):
    return _read_xvecs(path, "<i4")


def read_fvecs(path):
    return _read_xvecs(path, "<f4")


def sqdist_exact(q, x):
    """정수 연산으로 정확한 제곱 L2 거리. q: (m, D), x: (m, c, D) -> (m, c) int64."""
    diff = x.astype(np.int32) - q[:, None, :].astype(np.int32)
    return np.einsum("mcd,mcd->mc", diff, diff, dtype=np.int64)


def topk_merge(ids_a, d_a, ids_b, d_b, k=K):
    """두 top-k 결과를 합쳐 (거리, id) 순으로 다시 top-k."""
    ids = np.concatenate([ids_a, ids_b], axis=1)
    d = np.concatenate([d_a, d_b], axis=1)
    order = np.lexsort((ids, d), axis=1)[:, :k]
    return np.take_along_axis(ids, order, 1), np.take_along_axis(d, order, 1)


def knn_exact(base_u8, queries_u8, k=K, cand=100, id_offset=0, threads=None, chunk=1000):
    """faiss IndexFlatL2로 후보 cand개를 찾고, 정수 연산으로 다시 정렬해 정확한 top-k.

    반환: ids (nq, k) int64 (base 안 위치 + id_offset), dists (nq, k) int64 (제곱 L2).
    동점은 id가 작은 쪽을 앞에 둔다.
    """
    import faiss
    if threads:
        faiss.omp_set_num_threads(threads)
    xb = np.ascontiguousarray(base_u8, dtype=np.float32)
    index = faiss.IndexFlatL2(DIM)
    index.add(xb)
    del xb
    cand = min(cand, base_u8.shape[0])
    out_i = np.empty((len(queries_u8), k), np.int64)
    out_d = np.empty((len(queries_u8), k), np.int64)
    for s in range(0, len(queries_u8), chunk):
        q = queries_u8[s:s + chunk]
        _, I = index.search(np.ascontiguousarray(q, dtype=np.float32), cand)
        d = sqdist_exact(q, base_u8[I.ravel()].reshape(len(q), cand, DIM))
        order = np.lexsort((I, d), axis=1)[:, :k]
        out_i[s:s + len(q)] = np.take_along_axis(I, order, 1) + id_offset
        out_d[s:s + len(q)] = np.take_along_axis(d, order, 1)
    return out_i, out_d


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
