"""그래프 2 (RQ1): 삽입 시나리오(k)별 hot posting list의 LBA 간격 (논문용 초안).

데이터: results/burst/rq1_gap_by_k.csv (burst/paper/fig2_data.py가 만든다). 행 하나 = 시나리오 하나.
  k             동시에 삽입이 interleave되는 posting list 수 (1 / 16 / 512)
  gap_kb_mean   LBA 간격의 평균 (KB): posting list마다 평균을 구한 뒤 hot 512개에 대해 평균
  gap_kb_p10/90 posting list별 평균의 p10 / p90
  gap_kb_median LBA 간격의 중앙값 (KB): report 본문의 8KB / 128KB / 4.4MB
LBA 간격 = 같은 posting list에 연이어 할당된 두 새 페이지 사이의 디스크 LBA 거리 (8KB = 연속).
이전 판(extent 수 vs LBA 간격 산점도)은 fig2_extent_vs_gap.py에 그대로 있다.
실행: .venv/bin/python -I burst/paper/fig2_lba_gap_by_k.py  ->  plots/burst/paper/fig2_lba_gap_by_k.{png,pdf}
"""
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator, NullLocator

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "results/burst/rq1_gap_by_k.csv"
OUT = ROOT / "plots/burst/paper/fig2_lba_gap_by_k"

# ===== 다듬을 부분: 크기, 글꼴, 색, 라벨 =====
FIGSIZE = (4.2, 3.2)
FONT = 13
plt.rcParams.update({
    "font.family": "DejaVu Sans",   # 한글 라벨을 쓰려면 "Noto Sans CJK KR" + "axes.unicode_minus": False
    "font.size": FONT, "font.weight": "bold",
    "axes.labelweight": "bold", "axes.labelsize": FONT + 1,
    "axes.linewidth": 1.5, "axes.edgecolor": "black",
    "xtick.labelsize": FONT, "ytick.labelsize": FONT,
    "xtick.major.width": 1.5, "ytick.major.width": 1.5,
    "grid.color": "#c8c8c8", "grid.linestyle": "--", "grid.linewidth": 0.8,
    "legend.fontsize": FONT - 1, "legend.frameon": False,
    "savefig.dpi": 600, "savefig.bbox": "tight",
})
STAT = "mean"                    # "mean" = 평균, "median" = 중앙값 (report 본문 수치). 바꾸면 YLABEL도 같이 바꾼다.
BARS = [("s1", "1", "#2a78d6"), ("s16", "16", "#eb6834"), ("s512", "512", "#1baf7a")]   # (slug, x축 라벨, 색)
BAR_WIDTH = 0.6
XLABEL = "Posting List Unit (K)"
YLABEL = "Average LBA Gap"
SHOW_VALUES = True               # 막대 위에 값
SHOW_RANGE = False               # posting list별 평균의 p10~p90 오차 막대 (STAT = "mean"일 때만)
SHOW_CONTIGUOUS_LINE = False     # 8KB = 연속 기준선 (켜면 점선 + 위쪽 범례 "Contiguous (8KB)")
YTICKS_KB = [8, 32, 128, 512, 2048, 8192]
YLIM_KB = (4, 16384)
# =============================================


def kb_label(v):
    return f"{v // 1024:g}MB" if v >= 1024 else f"{v:g}KB"


def value_label(kb):
    if kb >= 1024:
        return f"{kb / 1024:.1f}MB"
    return f"{kb:.0f}KB" if kb >= 10 else f"{kb:.1f}KB"


df = pd.read_csv(CSV).set_index("slug")
fig, ax = plt.subplots(figsize=FIGSIZE)
col = f"gap_kb_{STAT}"
for x, (slug, name, color) in enumerate(BARS):
    v = df.at[slug, col]
    ax.bar(x, v, width=BAR_WIDTH, color=color, edgecolor="black", linewidth=1.2, zorder=2)
    top = v
    if SHOW_RANGE and STAT == "mean":
        lo, hi = df.at[slug, "gap_kb_p10"], df.at[slug, "gap_kb_p90"]
        ax.errorbar(x, v, yerr=[[v - lo], [hi - v]], fmt="none", ecolor="black",
                    elinewidth=1.5, capsize=4, capthick=1.5, zorder=3)
        top = hi
    if SHOW_VALUES:
        ax.text(x, top * 1.15, value_label(v), ha="center", va="bottom", fontsize=FONT - 2)

if SHOW_CONTIGUOUS_LINE:   # 기준선 이름은 다른 그래프처럼 위쪽 범례로
    ax.axhline(8, color="black", ls=":", lw=1.5, zorder=1, label="Contiguous (8KB)")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), handlelength=1.8, handletextpad=0.4)

ax.set_xticks(range(len(BARS)))
ax.set_xticklabels([name for _, name, _ in BARS])
ax.tick_params(axis="x", length=0)
ax.set_xlim(-0.5, len(BARS) - 0.5)
ax.set_yscale("log", base=2)
ax.yaxis.set_major_locator(FixedLocator(YTICKS_KB))
ax.yaxis.set_minor_locator(NullLocator())
ax.set_yticklabels([kb_label(v) for v in YTICKS_KB])
ax.set_ylim(*YLIM_KB)
ax.set_xlabel(XLABEL)
ax.set_ylabel(YLABEL)
ax.grid(True, axis="y")
ax.set_axisbelow(True)

OUT.parent.mkdir(parents=True, exist_ok=True)
for ext in ("png", "pdf", "svg"):
    fig.savefig(f"{OUT}.{ext}")
print("saved", f"{OUT}.png")
