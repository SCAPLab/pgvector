"""그래프 4 (RQ2): block size B에 따른 hot posting list의 read amplification (논문용 초안).

데이터: results/burst/rq2_hot_curves.csv (burst/summarize.py가 만든다). 행 하나 = 시나리오 x block size B.
  slug      base / s1 / s16 / s512  (삽입 전 / k=1 모음 / k=16 16개씩 섞음 / k=512 섞음)
  block_kb  block size B (KB)
  read_amp  읽은 바이트 / posting list 실제 크기 (hot 512개 평균, 1 = 낭비 없음)
실행: .venv/bin/python burst/paper/fig4_read_amp_vs_B.py  ->  plots/burst/paper/fig4_read_amp_vs_B.{png,pdf}
"""
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator, NullLocator, NullFormatter

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "results/burst/rq2_hot_curves.csv"
OUT = ROOT / "plots/burst/paper/fig4_read_amp_vs_B"

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
    "lines.linewidth": 2, "lines.markersize": 6,
    "savefig.dpi": 600, "savefig.bbox": "tight",
})
SERIES = {   # slug: (범례 이름, 선 모양). k=16과 k=512는 B <= 64KB에서 값이 같아 선이 겹친다.
    # "base": ("Before insert", dict(color="#888780", marker="D", ls="-")),   # 삽입 전 선: 줄 앞 #을 지우면 다시 그린다
    "s1":   ("K=1",           dict(color="#2a78d6", marker="o", ls="-")),
    "s16":  ("K=16",          dict(color="#eb6834", marker="s", ls="-")),
    "s512": ("K=512",         dict(color="#1baf7a", marker="^", ls="-")),
}
SHOW_128K_LINE = True         # B = 128KB (장치 max_sectors_kb, k=16의 LBA 간격)
XLABEL = "Block Size"
YLABEL = "Read Amplification"
XTICKS_KB = [4, 16, 64, 256, 1024]
YTICKS = [1, 2, 4, 8, 16, 32, 64]
YLIM = (0.85, 64)
# =============================================


def kb_label(v):
    return f"{v // 1024:g}MB" if v >= 1024 else f"{v:g}KB"


df = pd.read_csv(CSV)
fig, ax = plt.subplots(figsize=FIGSIZE)

for slug, (label, style) in SERIES.items():
    d = df[df.slug == slug].sort_values("block_kb")
    ax.plot(d.block_kb, d.read_amp, label=label, **style)

if SHOW_128K_LINE:
    ax.axvline(128, color="gray", ls=":", lw=1.2, zorder=0)

ax.set_xscale("log", base=2)
ax.xaxis.set_major_locator(FixedLocator(XTICKS_KB))
ax.xaxis.set_minor_locator(FixedLocator(sorted(df.block_kb.unique())))   # 라벨 없는 눈금: 측정한 B 9개
ax.xaxis.set_minor_formatter(NullFormatter())
ax.set_xticklabels([kb_label(v) for v in XTICKS_KB])
ax.set_yscale("log", base=2)
ax.yaxis.set_major_locator(FixedLocator(YTICKS))
ax.yaxis.set_minor_locator(NullLocator())
ax.set_yticklabels([f"{v:g}" for v in YTICKS])
ax.set_ylim(*YLIM)
ax.set_xlabel(XLABEL)
ax.set_ylabel(YLABEL)
ax.grid(True)
ax.set_axisbelow(True)
ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3,
          handlelength=1.8, handletextpad=0.4, columnspacing=1.0)

OUT.parent.mkdir(parents=True, exist_ok=True)
for ext in ("png", "pdf", "svg"):
    fig.savefig(f"{OUT}.{ext}")
print("saved", f"{OUT}.png")
