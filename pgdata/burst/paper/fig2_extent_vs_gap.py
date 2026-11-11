"""그래프 2 (RQ1): hot posting list별 extent 수 vs LBA 간격 (논문용 초안).

데이터: results/burst/rq1_hot_perlist.csv (burst/summarize.py가 만든다). 행 하나 = hot posting list 하나.
  slug     s1 / s16 / s512  (k = 삽입이 한꺼번에 섞여 들어온 posting list 수: 모음 / 16개씩 섞음 / 섞음)
  extents  posting list 페이지 중 LBA가 연속인 구간의 수
  gap_kb   같은 posting list에 연달아 추가된 두 페이지의 LBA 간격 중앙값 (KB, 8KB = 연속)
x축은 비어 있는 구간(20~60)을 잘라 왼쪽·오른쪽 두 패널로 그리고, 잘린 자리에 물결 표시를 넣는다.
실행: .venv/bin/python burst/paper/fig2_extent_vs_gap.py  ->  plots/burst/paper/fig2_extent_vs_gap.{png,pdf}
"""
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, NullLocator

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "results/burst/rq1_hot_perlist.csv"
OUT = ROOT / "plots/burst/paper/fig2_extent_vs_gap"

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
    "legend.fontsize": FONT, "legend.frameon": False,
    "savefig.dpi": 300, "savefig.bbox": "tight",
})
SERIES = {   # slug: (범례 이름, 점 모양)
    "s1":   ("k=1",   dict(color="#2a78d6", marker="o")),
    "s16":  ("k=16",  dict(color="#eb6834", marker="s")),
    "s512": ("k=512", dict(color="#1baf7a", marker="^")),
}
POINT_SIZE = 18
POINT_ALPHA = 0.6
XLABEL = "# of Extents"
YLABEL = "LBA Gap (Bytes)"
XLABEL_PAD = 24                  # x축 라벨과 축 사이 거리 (pt). 글꼴을 키우면 같이 키운다.
# x축 끊기: 왼쪽 패널 (k=1) / 오른쪽 패널 (k=16, k=512)
XLIM_LEFT, XTICKS_LEFT = (0, 20), [0, 10, 20]
XLIM_RIGHT, XTICKS_RIGHT = (60, 82), [60, 70, 80]
WIDTH_RATIOS = (XLIM_LEFT[1] - XLIM_LEFT[0], XLIM_RIGHT[1] - XLIM_RIGHT[0])   # 1 extent의 폭을 두 패널에서 같게
GAP = 0.2                        # 두 패널 사이 간격 (축 폭 대비). 좁히면 경계의 눈금 숫자(20, 60)가 겹친다.
WAVE_HEIGHT = 0.07               # 물결 길이 (그림 높이 대비)
WAVE_AMP = 0.007                 # 물결 진폭 (그림 폭 대비)
YTICKS_KB = [8, 32, 128, 512, 2048, 8192]
YLIM_KB = (4, 16384)
SHOW_CONTIGUOUS_LINE = True      # 8KB = 연속 기준선
# =============================================


def kb_label(v):
    return f"{v // 1024:g}M" if v >= 1024 else f"{v:g}K"


def wave(fig, x, y):
    """그림 좌표 (x, y)를 지나는 세로 물결 하나 (축이 잘린 자리 표시)."""
    t = np.linspace(-1, 1, 60)
    xs = x + WAVE_AMP * np.sin(np.pi * t)
    ys = y + WAVE_HEIGHT / 2 * t
    fig.add_artist(Line2D(xs, ys, transform=fig.transFigure, color="black", lw=1.5, clip_on=False))


df = pd.read_csv(CSV)
fig, (axl, axr) = plt.subplots(1, 2, sharey=True, figsize=FIGSIZE,
                               gridspec_kw=dict(width_ratios=WIDTH_RATIOS, wspace=GAP))
fig.subplots_adjust(left=0.2, right=0.97, bottom=0.2, top=0.82)   # 위치를 고정해야 물결·라벨 좌표가 맞는다

for ax in (axl, axr):
    for slug, (label, style) in SERIES.items():
        d = df[df.slug == slug]
        ax.scatter(d.extents, d.gap_kb, s=POINT_SIZE, alpha=POINT_ALPHA, edgecolors="none", label=label, **style)
    if SHOW_CONTIGUOUS_LINE:
        ax.axhline(8, color="black", ls=":", lw=1.2, zorder=0)
    ax.grid(True)
    ax.set_axisbelow(True)

if SHOW_CONTIGUOUS_LINE:
    axr.text(XLIM_RIGHT[1] - 0.5, 8 * 1.25, "Contiguous", ha="right", va="bottom", fontsize=FONT - 2)

axl.set_xlim(*XLIM_LEFT)
axl.xaxis.set_major_locator(FixedLocator(XTICKS_LEFT))
axr.set_xlim(*XLIM_RIGHT)
axr.xaxis.set_major_locator(FixedLocator(XTICKS_RIGHT))
axl.spines["right"].set_visible(False)
axr.spines["left"].set_visible(False)
axr.tick_params(axis="y", left=False, labelleft=False)

axl.set_yscale("log", base=2)
axl.yaxis.set_major_locator(FixedLocator(YTICKS_KB))
axl.yaxis.set_minor_locator(NullLocator())
axl.set_yticklabels([kb_label(v) for v in YTICKS_KB])
axl.set_ylim(*YLIM_KB)
axl.set_ylabel(YLABEL)

pl, pr = axl.get_position(), axr.get_position()
for x in (pl.x1, pr.x0):          # 잘린 자리: 아래·위 축선마다 물결 두 줄
    for y in (pl.y0, pl.y1):
        wave(fig, x, y)

xc = (pl.x0 + pr.x1) / 2           # 두 패널 전체의 가운데
axl.annotate(XLABEL, xy=(xc, pl.y0), xycoords="figure fraction", xytext=(0, -XLABEL_PAD),
             textcoords="offset points", ha="center", va="top", fontsize=FONT + 1, fontweight="bold")

handles, labels = [], []
for ax in (axl, axr):
    for h, l in zip(*ax.get_legend_handles_labels()):
        if l not in labels:
            handles.append(h)
            labels.append(l)
leg = fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(xc, pl.y1 + 0.02), ncol=len(labels),
                 markerscale=1.6, handletextpad=0.2, columnspacing=1.0)
for h in leg.legend_handles:   # 범례 표식은 투명도 없이
    h.set_alpha(1)

OUT.parent.mkdir(parents=True, exist_ok=True)
for ext in ("png", "pdf"):
    fig.savefig(f"{OUT}.{ext}")
print("saved", f"{OUT}.png")
