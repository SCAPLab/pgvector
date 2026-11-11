# 실행 전 확인 결과 (문서 6절)

pgvector 0.8.7 (`src/pgvector-0.8.7`, git f37c13f) + PostgreSQL 17.11 기준. 2026-11-03.

## 소스 확인

| 항목 | 결과 | 근거 |
|---|---|---|
| `ivfinsert.c` centroid 선택 | list 정보 페이지 전체를 읽어 **모든 centroid(2450개)와 거리 계산**, 최소인 list 선택 | `FindInsertPage` |
| `ivfinsert.c` 새 페이지 획득 | insertPage가 가득 차면 `nextblkno`가 있으면 그쪽, 없으면 **`ReadBufferExtended(P_NEW)`로 파일 끝에 1블록 확장**. 빈 페이지 재사용(FSM) 없음 | `InsertTuple`, `IvfflatNewBuffer` (`ivfutils.c`) |
| `ivfscan.c` 읽기 순서 | probes개 list를 centroid 거리 순으로, list 안에서는 `startPage`부터 `nextblkno` 체인 순서로 **페이지 하나씩 동기 읽기** | `GetScanItems` |
| `ivfscan.c` prefetch / read stream | **없음**. `PrefetchBuffer`, `read_stream` 미사용 → QD1 고정의 근거 성립 | grep 결과 없음 |
| `ivfscan.c` buffer strategy | **`BAS_BULKREAD` 링 버퍼** 사용 (아래 주의 참고) | `ivfflatbeginscan` |
| `ivfflat.h` 레이아웃 | 문서와 일치. magic `0x14FF1A7`, version 1, page_id `0xFF84`, metapage block 0, list 페이지 block 1부터 | `ivfflat.h` |
| 병렬 빌드 | **지원**. heap 스캔 + 정렬만 병렬, k-means와 페이지 쓰기는 leader 혼자 | `ivfbuild.c` (`IvfflatBeginParallel`) |
| 빌드 시 페이지 배치 | list 0부터 차례로, list마다 새 페이지에서 시작해 연속으로 씀 → 빌드 직후 list당 `block_runs = 1` | `InsertTuples` |
| k-means 표본 | list당 50개 (최소 1만 개) = 122,500개, **난수 seed 고정 불가** (`pg_global_prng_state`) → 빌드마다 centroid·list 크기가 달라짐 | `ComputeCenters`, `SampleRows` |
| VACUUM | 삭제가 없으면 인덱스를 건드리지 않음 (`ivfflatvacuumcleanup`은 stats NULL이면 바로 반환) | `ivfvacuum.c` |
| binary COPY 형식 | `int16 dim, int16 unused(0), float4 × dim` (big-endian) | `vector_recv` |

## 실행 확인

| 항목 | 결과 |
|---|---|
| `debug_io_direct = 'data'`로 서버 기동 (ext4) | 정상. 백엔드가 연 데이터 파일 flags에 O_DIRECT(040000) 확인 |
| 페이지당 tuple 수 | **15개** (테스트 인덱스 실측, 문서 예측과 같음) |
| 10M 파일 크기 / 첫 벡터 dim | 1,320,000,000바이트 / 128 |
| 정답 코드 검증 | 원래 순서 10M에서 쿼리 1,000개 top-10이 제공 정답(`idx_10M.ivecs`)과 **거리·ID 모두 1,000/1,000 일치** |
| `REINDEX INDEX CONCURRENTLY` | 아직 미확인 (RQ3 단계에서 확인) |

## 설계에 영향을 주는 점

1. **BAS_BULKREAD 링 버퍼**: ivfflat 스캔은 shared_buffers에 없는 페이지를 작은 링 버퍼(256KB 수준)로 읽으므로,
   shared_buffers가 커도 쿼리만으로는 인덱스가 캐시에 쌓이지 않는다. 메모리 조건(8GB)은 `pg_prewarm`이 필수이고
   (문서대로), I/O-bound 조건에서는 같은 list를 다시 읽어도 매번 디스크에서 읽는다.
2. **PostgreSQL 18**: `io_method` 기본값이 `worker`라 읽기를 별도 I/O worker 프로세스가 수행한다.
   백엔드별 `pread` 추적과 QD1 설명이 달라지므로 PG17로 진행. 18을 쓰려면 `io_method = sync`.
3. **PG17의 읽기 syscall**: 단일 블록 읽기는 `pread64`, 여러 블록을 묶는 경로(pg_prewarm 등 read stream)는 `preadv`.
   eBPF 추적 시 둘 다 잡아야 한다.
4. **k-means 비결정성**: 같은 데이터로 다시 빌드해도 list 크기가 달라진다. 반복 측정 시 빌드도 반복 단위에 포함하는 것이 자연스럽다.

## 저장장치 관련

- 측정 전용 SSD 없음. PGDATA가 OS 루트 파티션(`/dev/nvme0n1p2`, ext4, relatime)에 있음. 파티션 시작 2MiB(sector 4096).
- SSD: Samsung 960 PRO 512GB, FW 2B6QCXP7, critical_warning 0, media_errors 0, available_spare 100%,
  **percentage_used 76%** (정격 수명의 76% 사용), 사용 시간 15,743시간.
- 장치 MDTS = 9 (2MB)인데 커널의 `max_hw_sectors_kb = 128`, `max_segments = 33`.
  AMD IOMMU(ivhd0)가 켜져 있어 DMA 매핑 크기 제한(128KB)이 걸린 것으로 보인다 (확인 필요).
  부트 옵션 `iommu=pt` 등으로 바뀔 수 있으나 재부팅과 root가 필요하고, 현재 값 기준으로는 B > 128KB 읽기가 장치에서 나뉜다.
