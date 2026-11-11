"""ivfflat 인덱스 파일을 8KB 페이지 단위로 직접 파싱해 list별 배치를 뽑는다 (문서 3.3절, RQ1).

pgvector 0.8.7 ivfflat.h 기준 (src/pgvector-0.8.7 에서 확인):
  block 0  metapage: IvfflatMetaPageData {uint32 magic=0x14FF1A7, uint32 version=1, uint16 dims, uint16 lists}
  block 1~ list 페이지: 아이템마다 IvfflatListData {uint32 startPage, uint32 insertPage, Vector center}
  모든 페이지 special: IvfflatPageOpaqueData {uint32 nextblkno, uint16 unused, uint16 page_id=0xFF84}
  페이지 헤더 24B, line pointer 4B (lp_off:15, lp_flags:2, lp_len:15)

실행 전 CHECKPOINT를 하고, 파일은 O_DIRECT로 읽는다 (page cache 영향 없음).

사용: layout_dump.py --tag 0pct [--index items_ivf] [--outdir results/rq1]
산출: results/rq1/layout_<tag>.csv, layout_<tag>.json
"""
import argparse
import mmap
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import psycopg
from common import RESULTS, log, save_json
from fiemap import fiemap, partition_start_bytes

BLCKSZ = 8192
SEG_BLOCKS = 131072                  # 1GB 세그먼트
FS_BLOCK = 4096
INVALID = 0xFFFFFFFF
MAGIC, PAGE_ID = 0x14FF1A7, 0xFF84
LP_NORMAL = 1


def read_odirect(path, nbytes):
    """파일 앞 nbytes를 O_DIRECT로 읽어 bytes처럼 쓸 수 있는 mmap 버퍼로 반환."""
    buf = mmap.mmap(-1, max(nbytes, mmap.PAGESIZE))
    fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
    try:
        view = memoryview(buf)
        done, chunk = 0, 64 << 20
        while done < nbytes:
            n = os.preadv(fd, [view[done:done + min(chunk, nbytes - done)]], done)
            if n <= 0:
                raise IOError(f"{path}: short read at {done}")
            done += n
    finally:
        os.close(fd)
    return buf


def run_count(sorted_units, step):
    if len(sorted_units) == 0:
        return 0
    return 1 + int(np.count_nonzero(np.diff(sorted_units) != step))


def runs_str(sorted_units, step):
    """[a, a+step, ..., b] 연속 구간을 'start+count;...' 로 표현."""
    if len(sorted_units) == 0:
        return ""
    brk = np.flatnonzero(np.diff(sorted_units) != step) + 1
    parts = []
    for seg in np.split(sorted_units, brk):
        parts.append(f"{int(seg[0])}+{len(seg)}")
    return ";".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--index", default="items_ivf")
    ap.add_argument("--no-checkpoint", action="store_true")
    ap.add_argument("--outdir", default=str(RESULTS / "rq1"))
    a = ap.parse_args()

    with psycopg.connect(autocommit=True) as conn:
        if not a.no_checkpoint:
            conn.execute("CHECKPOINT")
        datadir = conn.execute("SHOW data_directory").fetchone()[0]
        relpath = conn.execute("SELECT pg_relation_filepath(%s)", (a.index,)).fetchone()[0]
        nbytes = conn.execute("SELECT pg_relation_size(%s)", (a.index,)).fetchone()[0]
        heap_rows = conn.execute(
            "SELECT reltuples::bigint FROM pg_class WHERE oid = "
            "(SELECT indrelid FROM pg_index WHERE indexrelid = %s::regclass)", (a.index,)).fetchone()[0]
    os.sync()
    nblocks = nbytes // BLCKSZ
    base = os.path.join(datadir, relpath)
    nseg = (nblocks + SEG_BLOCKS - 1) // SEG_BLOCKS
    seg_paths = [base if s == 0 else f"{base}.{s}" for s in range(nseg)]
    log(f"{a.index}: {relpath}, {nblocks} blocks, {nseg} segments")

    # --- 페이지 읽기 (O_DIRECT)
    t0 = time.time()
    pages = np.empty((nblocks, BLCKSZ), np.uint8)
    for s, p in enumerate(seg_paths):
        b0, b1 = s * SEG_BLOCKS, min(nblocks, (s + 1) * SEG_BLOCKS)
        buf = read_odirect(p, (b1 - b0) * BLCKSZ)
        pages[b0:b1] = np.frombuffer(buf, np.uint8, (b1 - b0) * BLCKSZ).reshape(-1, BLCKSZ)
        buf.close()
    log(f"읽기 {time.time() - t0:.1f}s")

    u16 = lambda off: pages[:, off:off + 2].copy().view("<u2").ravel()
    pd_lower, pd_special = u16(12), u16(16)
    bad = np.flatnonzero(pd_special != BLCKSZ - 8)
    if bad.size:
        sys.exit(f"pd_special 이상한 페이지 {bad.size}개 (첫 {bad[0]})")
    nextblk = pages[:, BLCKSZ - 8:BLCKSZ - 4].copy().view("<u4").ravel()
    page_id = pages[:, BLCKSZ - 2:BLCKSZ].copy().view("<u2").ravel()
    if not (page_id == PAGE_ID).all():
        sys.exit(f"page_id != 0xFF84 페이지 {(page_id != PAGE_ID).sum()}개")
    nitems = (pd_lower.astype(np.int64) - 24) // 4

    # LP_NORMAL 아이템 수 (페이지별)
    maxit = int(nitems.max())
    n_normal = np.zeros(nblocks, np.int64)
    for k in range(maxit):
        lp = pages[:, 24 + 4 * k:28 + 4 * k].copy().view("<u4").ravel()
        n_normal += ((((lp >> 15) & 3) == LP_NORMAL) & (k < nitems))

    # --- metapage
    magic, version = pages[0, 24:32].view("<u4")
    dims, lists = pages[0, 32:36].view("<u2")
    if magic != MAGIC:
        sys.exit(f"magic {magic:#x} != {MAGIC:#x}")

    # --- list 정보 페이지 (block 1부터 nextblkno 체인)
    list_pages, starts, inserts = [], [], []
    b = 1
    while b != INVALID:
        list_pages.append(b)
        for k in range(int(nitems[b])):
            lp = int(pages[b, 24 + 4 * k:28 + 4 * k].view("<u4")[0])
            if ((lp >> 15) & 3) != LP_NORMAL:
                continue
            off = lp & 0x7FFF
            sp, ip = pages[b, off:off + 8].view("<u4")
            starts.append(int(sp)); inserts.append(int(ip))
        b = int(nextblk[b])
    if len(starts) != lists:
        sys.exit(f"list 아이템 {len(starts)} != metapage lists {lists}")

    # --- FIEMAP: 4KB 단위 물리 위치 (블록 하나 = 4KB 두 개)
    part_off, part, disk = partition_start_bytes(seg_paths[0])
    phys4k = np.full(nblocks * 2, -1, np.int64)
    fs_extents, flags_seen = 0, set()
    for s, p in enumerate(seg_paths):
        ext = fiemap(p)
        fs_extents += len(ext)
        b0, b1 = s * SEG_BLOCKS, min(nblocks, (s + 1) * SEG_BLOCKS)
        units = np.arange((b1 - b0) * 2, dtype=np.int64) * FS_BLOCK      # 파일 내 offset
        lo = np.array([e[0] for e in ext], np.int64)
        ph = np.array([e[1] for e in ext], np.int64)
        ln = np.array([e[2] for e in ext], np.int64)
        flags_seen |= {e[3] for e in ext}
        idx = np.searchsorted(lo, units, side="right") - 1
        ok = (idx >= 0) & (units < lo[idx] + ln[idx])
        if not ok.all():
            sys.exit(f"{p}: FIEMAP에 없는 offset {(~ok).sum()}개")
        phys4k[b0 * 2:b1 * 2] = ph[idx] + (units - lo[idx]) + part_off

    # --- list별 체인
    seen = np.zeros(nblocks, bool)
    seen[0] = True
    seen[list_pages] = True
    rows = []
    for li, (sp, ip) in enumerate(zip(starts, inserts)):
        chain, b = [], sp
        while b != INVALID:
            if seen[b]:
                sys.exit(f"list {li}: block {b} 중복/순환")
            seen[b] = True
            chain.append(b)
            b = int(nextblk[b])
        chain = np.array(chain, np.int64)
        sb = np.sort(chain)
        u = np.sort(np.concatenate([phys4k[2 * chain], phys4k[2 * chain + 1]]))
        rows.append({
            "list_id": li, "start_page": sp, "insert_page": ip,
            "n_tuples": int(n_normal[chain].sum()), "n_pages": len(chain),
            "block_runs": run_count(sb, 1), "lba_runs": run_count(u, FS_BLOCK),
            "chain_ascending": bool((np.diff(chain) > 0).all()),
            "insert_is_last": bool(chain[-1] == ip),
            "blocks": runs_str(sb, 1),
            "lba_extents": runs_str(u // 512, FS_BLOCK // 512),   # 512B sector 단위
        })
    orphans = np.flatnonzero(~seen)

    import pandas as pd
    df = pd.DataFrame(rows)
    out_csv = Path(a.outdir) / f"layout_{a.tag}.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)

    desc = lambda s: {k: float(v) for k, v in s.describe(percentiles=[.5, .9, .99]).items()}
    summary = {
        "tag": a.tag, "index": a.index, "relpath": relpath, "nblocks": int(nblocks),
        "segments": nseg, "index_bytes": int(nbytes), "heap_reltuples": int(heap_rows),
        "magic": hex(int(magic)), "version": int(version), "dims": int(dims), "lists": int(lists),
        "list_info_pages": len(list_pages), "orphan_pages": int(orphans.size),
        "total_tuples": int(df.n_tuples.sum()),
        "tuples_per_page_max": int(n_normal[1 + len(list_pages):].max()) if nblocks > 1 + len(list_pages) else 0,
        "tuples_per_page_mode": int(np.bincount(n_normal[1 + len(list_pages):]).argmax()) if nblocks > 1 + len(list_pages) else 0,
        "empty_lists": int((df.n_tuples == 0).sum()),
        "n_tuples": desc(df.n_tuples), "n_pages": desc(df.n_pages),
        "block_runs": desc(df.block_runs), "lba_runs": desc(df.lba_runs),
        "lists_lba_gt_block_runs": int((df.lba_runs > df.block_runs).sum()),
        "all_chains_ascending": bool(df.chain_ascending.all()),
        "all_insert_page_last": bool(df.insert_is_last.all()),
        "partition": part, "disk": disk, "partition_start_bytes": int(part_off),
        "fs_extents_total": fs_extents,
        "fiemap_flags_seen": sorted(hex(f) for f in flags_seen),
        "created": time.strftime("%F %T"),
    }
    save_json(Path(a.outdir) / f"layout_{a.tag}.json", summary)
    log(f"lists {lists}, tuples {summary['total_tuples']}, pages/list mean {df.n_pages.mean():.1f}, "
        f"block_runs mean {df.block_runs.mean():.2f} max {df.block_runs.max()}, "
        f"lba_runs mean {df.lba_runs.mean():.2f}, orphans {orphans.size}, "
        f"tuples/page mode {summary['tuples_per_page_mode']}")


if __name__ == "__main__":
    main()
