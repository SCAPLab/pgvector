"""FIEMAP ioctl로 파일 offset -> 물리 위치를 구한다 (root 불필요).

physical은 파일시스템이 있는 블록 장치(파티션) 기준이므로,
디스크 기준 LBA는 partition_start_bytes(path)를 더해 구한다.
"""
import fcntl
import os
import struct

FS_IOC_FIEMAP = 0xC020660B
FIEMAP_FLAG_SYNC = 0x1
FIEMAP_EXTENT_LAST = 0x1
HDR = struct.Struct("=QQIIII")          # fm_start, fm_length, fm_flags, fm_mapped, fm_count, reserved
EXT = struct.Struct("=QQQQQIIII")       # logical, physical, length, res64[2], flags, res[3]
FLAG_NAMES = {0x1: "last", 0x2: "unknown", 0x4: "delalloc", 0x8: "encoded", 0x80: "encrypted",
              0x100: "not_aligned", 0x200: "data_inline", 0x400: "data_tail",
              0x800: "unwritten", 0x1000: "merged", 0x2000: "shared"}


def fiemap(path, batch=512):
    """[(logical, physical, length, flags), ...] (바이트 단위)."""
    out = []
    fd = os.open(path, os.O_RDONLY)
    try:
        start = 0
        while True:
            buf = bytearray(HDR.size + EXT.size * batch)
            HDR.pack_into(buf, 0, start, 0xFFFFFFFFFFFFFFFF - start, FIEMAP_FLAG_SYNC, 0, batch, 0)
            fcntl.ioctl(fd, FS_IOC_FIEMAP, buf, True)
            mapped = HDR.unpack_from(buf, 0)[3]
            if mapped == 0:
                break
            last = False
            for i in range(mapped):
                lo, ph, ln, _, _, fl, _, _, _ = EXT.unpack_from(buf, HDR.size + EXT.size * i)
                out.append((lo, ph, ln, fl))
                if fl & FIEMAP_EXTENT_LAST:
                    last = True
            if last:
                break
            start = out[-1][0] + out[-1][2]
    finally:
        os.close(fd)
    return out


def partition_start_bytes(path):
    """파일이 있는 파티션의 디스크 내 시작 위치(바이트)와 장치 이름."""
    st = os.stat(path)
    maj, mnr = os.major(st.st_dev), os.minor(st.st_dev)
    sysdir = f"/sys/dev/block/{maj}:{mnr}"
    start_file = os.path.join(sysdir, "start")
    name = os.path.basename(os.path.realpath(sysdir))
    if os.path.exists(start_file):
        parent = os.path.basename(os.path.dirname(os.path.realpath(sysdir)))
        return int(open(start_file).read()) * 512, name, parent
    return 0, name, name


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        off, part, disk = partition_start_bytes(p)
        print(f"{p}: partition {part} on {disk}, start {off} bytes")
        for lo, ph, ln, fl in fiemap(p):
            names = ",".join(n for b, n in FLAG_NAMES.items() if fl & b)
            print(f"  logical {lo:>12} physical {ph:>14} len {ln:>12} {names}")
