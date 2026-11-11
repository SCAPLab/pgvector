"""그래프 6 (RQ2): posting list 종류별 block size 튜닝 이득 (논문용 초안).

데이터: results/burst/gain_summary.csv (burst/summarize.py가 만든다). 행 하나 = posting list 종류 하나.
  slug         base_hot / base_cold / s1 / s16 / s512  (삽입 전 hot / cold (삽입 없음) / k=1 / k=16 / k=512)
  gain_*       block size 튜닝 이득 = 8KB로 읽은 시간 / 가장 빠른 B로 읽은 시간. posting list마다 구한 값의 p10, 중앙값, p90
  best_b_kb    가장 빠른 B의 최빈값 (KB)
  lists        posting list 수 (hot 512개, cold 표본 128개)
막대 = 중앙값, 수염 = p10~p90, 막대 위 숫자 = 중앙값, x축 이름 아래 줄 = 가장 빠른 B.
실행: .venv/bin/python burst/paper/fig6_tuning_gain.py  ->  plots/burst/paper/fig6_tuning_gain.{png,pdf}
"""
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MultipleLocator

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "results/burst/gain_summary.csv"
OUT = ROOT / "plots/burst/paper/fig6_tuning_gain"

# ===== 다듬을 부분: 크기, 글꼴, 색, 라벨 =====
FIGSIZE = (4.2, 3.2)
FONT = 13
plt.rcParams.update({
    "font.family": "DejaVu Sans",   # 한글 라벨을 쓰려면 "Noto Sans CJK KR" + "axes.unicode_minus": False
    "font.size": FONT, "font.weight": "bold",
    "axes.labelweight": "bold", "axes.labelsize": FONT + 1,
    "axes.linewidth": 1.5, "axes.edgecolor": "black",
    "xtick.labelsize": FONT - 2, "ytick.labelsize": FONT,
    "xtick.major.width": 1.5, "ytick.major.width": 1.5,
    "grid.color": "#c8c8c8", "grid.linestyle": "--", "grid.linewidth": 0.8,
    "legend.fontsize": FONT - 1, "legend.frameon": False,
    "savefig.dpi": 300, "savefig.bbox": "tight",
})
# (slug, x축 이름, 막대 색). 그래프 3·4와 같은 색: 삽입 전은 회색, 시나리오는 파랑·주황·초록.
BARS = [
    ("base_hot",  "Hot",   "#888780"),
    ("base_cold", "Cold",  "#c3c2b7"),
    ("s1",        "k=1",   "#2a78d6"),
    ("s16",       "k=16",  "#eb6834"),
    ("s512",      "k=512", "#1baf7a"),
]
X = [0, 1, 2.5, 3.5, 4.5]        # 막대 위치: 삽입 전 두 개와 삽입 후 세 개 사이를 조금 띄운다
BAR_WIDTH = 0.65
GROUPS = [((0, 1), "Before insert"), ((2.5, 4.5), "After insert (+50%)")]   # (막대 x 범위, 묶음 이름)
SHOW_GROUPS = True               # 위쪽에 묶음 이름과 구분선
SHOW_VALUES = True               # 막대 위에 중앙값
SHOW_BEST_B = True               # x축 이름 아래 줄에 가장 빠른 B
BEST_B_HEADER = "Best B:"
BEST_B_PAD = 20                  # x축에서 그 줄까지 거리 (pt). x축 글꼴을 키우면 같이 키운다.
SHOW_NO_GAIN_LINE = True         # 1x = 튜닝해도 이득 없음
YLABEL = "Tuning Gain (x)"
YLIM = (0, 6)
YSTEP = 1
# =============================================


def kb_label(v):
    return f"{v // 1024:g}M" if v >= 1024 else f"{v:g}K"


df = pd.read_csv(CSV).set_index("slug")
fig, ax = plt.subplots(figsize=FIGSIZE)

for x, (slug, name, color) in zip(X, BARS):
    r = df.loc[slug]
    ax.bar(x, r.gain_median, width=BAR_WIDTH, color=color, edgecolor="black", linewidth=1.2, zorder=2)
    whisker = ax.errorbar(x, r.gain_median, yerr=[[r.gain_median - r.gain_p10], [r.gain_p90 - r.gain_median]],
                          fmt="none", ecolor="black", elinewidth=1.5, capsize=4, capthick=1.5, zorder=3)
    if SHOW_VALUES:
        ax.text(x, r.gain_p90 + 0.12, f"{r.gain_median:.2f}", ha="center", va="bottom", fontsize=FONT - 3)
    if SHOW_BEST_B:
        ax.annotate(kb_label(r.best_b_kb), xy=(x, 0), xycoords=("data", "axes fraction"),
                    xytext=(0, -BEST_B_PAD), textcoords="offset points",
                    ha="center", va="top", fontsize=FONT - 3, color="#444444")

ax.set_xticks(X)
ax.set_xticklabels([name for _, name, _ in BARS])
ax.tick_params(axis="x", length=0)
ax.set_xlim(X[0] - 0.5, X[-1] + 0.5)
if SHOW_BEST_B:
    ax.annotate(BEST_B_HEADER, xy=(0, 0), xycoords="axes fraction", xytext=(-4, -BEST_B_PAD),
                textcoords="offset points", ha="right", va="top", fontsize=FONT - 3, color="#444444")

if SHOW_NO_GAIN_LINE:
    ax.axhline(1, color="black", ls=":", lw=1.5, zorder=4)

if SHOW_GROUPS:
    ax.axvline((X[1] + X[2]) / 2, color="black", ls="--", lw=1.0, zorder=1)
    for (x0, x1), name in GROUPS:
        ax.text((x0 + x1) / 2, YLIM[1] * 0.97, name, ha="center", va="top", fontsize=FONT - 3)

ax.set_ylim(*YLIM)
ax.yaxis.set_major_locator(MultipleLocator(YSTEP))
ax.set_ylabel(YLABEL)
ax.grid(True, axis="y")
ax.set_axisbelow(True)

whisker.set_label("p10-p90")
legend = [whisker]
if SHOW_NO_GAIN_LINE:
    legend.append(Line2D([], [], color="black", ls=":", lw=1.5, label="No gain (1x)"))
ax.legend(handles=legend, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=len(legend),
          handlelength=1.8, handletextpad=0.4, columnspacing=1.2)

OUT.parent.mkdir(parents=True, exist_ok=True)
for ext in ("png", "pdf"):
    fig.savefig(f"{OUT}.{ext}")
print("saved", f"{OUT}.png")
