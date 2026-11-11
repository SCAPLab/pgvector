"""몰린 삽입 실험 그래프 (summarize.py 산출을 읽는다). 초안: 최종 형식은 사용자가 정한다.

  plots.py            -> plots/burst/fig2_*.{png,pdf}, fig3_*, fig4_*, fig6_*

그림 2 (RQ1): hot list마다 LBA 기준 조각 수 대 같은 list 새 페이지 간 LBA 거리
그림 3 (RQ2): 읽기 단위 B에 따른 list당 요청 수
그림 4 (RQ2): 읽기 단위 B에 따른 읽기 증폭
그림 6 (RQ2): list 종류별 큰 I/O 이득 (8KB -> 가장 빠른 B), 점 = 중앙값, 막대 = p10~p90
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.ticker import FixedLocator, NullLocator, FuncFormatter
from common import RESULTS

OUT = RESULTS / "burst"
FIG = ROOT / "plots" / "burst"
fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
GRAY = "#888780"
plt.rcParams.update({"font.family": "Noto Sans CJK KR", "font.size": 9.5, "axes.unicode_minus": False,
                     "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
                     "savefig.facecolor": "white", "figure.facecolor": "white"})
# 기본 팔레트 앞 세 색 (모든 쌍 색각 검증 통과 조합) + 보조 구분(선 모양·표식)
SC = {"모음": dict(color="#2a78d6", marker="o", ls="-"),
      "16개씩 섞음": dict(color="#eb6834", marker="s", ls=(0, (5, 2.5))),
      "섞음": dict(color="#1baf7a", marker="^", ls=(0, (1.2, 1.6)))}
BS = [4, 8, 16, 32, 64, 128, 256, 512, 1024]
blab = lambda b: f"{b // 1024}MB" if b >= 1024 else f"{b}KB"
kblab = lambda v: (f"{v / 1024:.1f}MB" if v < 10240 else f"{v / 1024:.0f}MB") if v >= 1024 else f"{v:.0f}KB"


def save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(FIG / f"{name}.{ext}", dpi=220, bbox_inches="tight")
    plt.close(fig)
    print("saved", FIG / f"{name}.png")


def title(ax, main, sub):
    ax.set_title(main, loc="left", fontsize=11.5, color=INK, pad=22, fontweight="bold")
    ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom")


def b_axis(ax):
    ax.set_xscale("log", base=2)
    ax.xaxis.set_major_locator(FixedLocator(BS))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: blab(int(round(v)))))
    ax.set_xlim(3.3, 1300)
    ax.set_xlabel("읽기 단위 B (로그 축)")
    ax.grid(axis="x", visible=False)


def place_end_labels(ax, labels, gap=0.05):
    """오른쪽 끝 라벨을 y 순서대로 두고, 서로 gap(축 비율)보다 가까우면 위로 밀어 겹치지 않게 한다."""
    from matplotlib.transforms import blended_transform_factory
    lo, hi = np.log10(ax.get_ylim())
    items = sorted(((np.log10(v) - lo) / (hi - lo), t) for v, t in labels)
    pos = []
    for f, t in items:
        pos.append(max(f, pos[-1] + gap) if pos else f)
    over = pos[-1] - 0.98
    if over > 0:
        pos = [p - over for p in pos]
    tr = blended_transform_factory(ax.transData, ax.transAxes)
    for p, (_, t) in zip(pos, items):
        ax.text(BS[-1] * 1.1, p, t, transform=tr, va="center", fontsize=8.5, color=INK2)


def end_label(ax, x, y, text, color, dy=0):
    ax.annotate(text, (x, y), xytext=(6, dy), textcoords="offset points", va="center", fontsize=8.5, color=INK2)


def fig2(r1, plan):
    d = r1[(r1.cls == "hot") & (r1.tag != "burst_base")]
    cold = r1[(r1.cls == "cold") & (r1.tag == "burst_s512")]
    rng = np.random.default_rng(1)
    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    for name, st in SC.items():
        x = d[d.scenario == name]
        jx = x.lba_runs * np.exp(rng.uniform(-0.05, 0.05, len(x)))
        jy = x.gap_lba_kb_median * np.exp(rng.uniform(-0.06, 0.06, len(x)))
        ax.scatter(jx, jy, s=12, color=st["color"], marker=st["marker"], alpha=0.35, linewidths=0, label=name)
        mx, my = x.lba_runs.median(), x.gap_lba_kb_median.median()
        ax.scatter([mx], [my], s=70, color=st["color"], marker=st["marker"], edgecolors="white", linewidths=1.5, zorder=3)
        ax.annotate(f"{name}: 조각 {mx:.0f}개 · 간격 {kblab(my)}", (mx, my), xytext=(12, 12), textcoords="offset points",
                    va="bottom", fontsize=8.5, color=INK)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.xaxis.set_major_locator(FixedLocator([1, 2, 5, 10, 20, 50, 100]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_locator(NullLocator())
    yt = [8, 32, 128, 512, 2048, 8192]
    ax.yaxis.set_major_locator(FixedLocator(yt)); ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: kblab(v)))
    ax.set_xlim(1.4, 400); ax.set_ylim(5, 14000)
    ax.set_xlabel("list당 조각 수 (LBA 기준, 로그 축)")
    ax.set_ylabel("같은 list 새 페이지 사이 거리 (LBA, 중앙값)")
    g = d.groupby("scenario").gap_lba_kb_median.median()
    title(ax, f"조각 수가 같아도 새 페이지 사이 거리는 삽입 순서에 따라 {g['섞음'] / g['16개씩 섞음']:.0f}배 다르다",
          f"hot list {plan['n_hot']}개 · 같은 벡터를 순서만 바꿔 삽입 · cold list(삽입 없음 {len(cold):,}개)는 "
          f"조각 수 평균 {cold.lba_runs.mean():.2f}, 새 페이지 없음")
    ax.legend(loc="upper left", frameon=False, fontsize=8.5, markerscale=1.6)
    save(fig, "fig2_runs_vs_gap")


def curve_fig(s2, col, name, ylab, ttl, sub, ref=None, ref_label=None, legend_loc="lower left"):
    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    cold = s2[(s2.tag == "burst_base") & (s2.cls == "cold")].set_index("Bk").loc[BS]
    labels = []
    ax.plot(BS, cold[col], color=GRAY, lw=1.4, marker="D", ms=3.5, label="cold list (삽입 없음)")
    labels.append((cold[col].iloc[-1], "cold (삽입 없음)"))
    if ref is not None:
        ax.plot(BS, ref, color=GRAY, lw=1.1, ls=(0, (4, 3)), label=ref_label)
        labels.append((ref[-1], ref_label))
    for sc, st in SC.items():
        tag = {"모음": "burst_s1", "16개씩 섞음": "burst_s16", "섞음": "burst_s512"}[sc]
        x = s2[(s2.tag == tag) & (s2.cls == "hot")].set_index("Bk").loc[BS]
        ax.plot(BS, x[col], color=st["color"], ls=st["ls"], marker=st["marker"], ms=4.5, lw=1.8,
                markeredgecolor="white", markeredgewidth=0.8, label=f"{sc} (hot)")
        labels.append((x[col].iloc[-1], f"{sc} {x[col].iloc[-1]:.1f}"))
    ax.axvline(128, color=AXIS, lw=1, ls=(0, (2, 2)), zorder=1)
    ax.text(128, 0.97, " 128KB = 16페이지", transform=ax.get_xaxis_transform(), fontsize=8, color=MUTED, va="top")
    b_axis(ax)
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(FixedLocator([0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500]))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_ylabel(ylab)
    title(ax, ttl, sub)
    place_end_labels(ax, labels)
    ax.legend(loc=legend_loc, frameon=False, fontsize=8.2)
    save(fig, name)


def fig6(gain):
    order = [("burst_base", "hot", "삽입 전 (같은 hot list, 연속)", GRAY),
             ("burst_base", "cold", "cold list (삽입 없음)", GRAY),
             ("burst_s1", "hot", "모음", SC["모음"]["color"]),
             ("burst_s16", "hot", "16개씩 섞음", SC["16개씩 섞음"]["color"]),
             ("burst_s512", "hot", "섞음", SC["섞음"]["color"])]
    fig, ax = plt.subplots(figsize=(6.6, 3.6))
    for i, (tag, cls, lab, c) in enumerate(order):
        g = gain[(gain.tag == tag) & (gain.cls == cls)]
        p10, p50, p90 = np.percentile(g.gain, [10, 50, 90])
        bm = int(g.best_B_kb.mode().iloc[0])
        y = len(order) - 1 - i
        ax.plot([p10, p90], [y, y], color=c, lw=3, alpha=0.45, solid_capstyle="round")
        ax.scatter([p50], [y], s=60, color=c, edgecolors="white", linewidths=1.5, zorder=3)
        ax.annotate(f"{p50:.2f}배 (최적 B {blab(bm)}, n={len(g)})", (p90, y), xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=INK2)
    ax.axvline(1.0, color=AXIS, lw=1, ls=(0, (2, 2)))
    ax.text(1.0, 0.985, " 1배 = 이득 없음", transform=ax.get_xaxis_transform(), fontsize=8, color=MUTED, va="top")
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([o[2] for o in order][::-1], color=INK2)
    ax.set_xlabel("큰 I/O 이득: 8KB 읽기 시간 ÷ 가장 빠른 B의 읽기 시간 (점 = 중앙값, 막대 = p10~p90)")
    xmax = gain.groupby(["tag", "cls"]).gain.quantile(0.9).max()
    ax.set_xlim(0.8, xmax * 1.55)
    ax.grid(axis="y", visible=False)
    title(ax, "같은 양을 삽입해도 섞여 들어오면 큰 I/O 이득이 사라진다",
          "list 하나를 O_DIRECT·QD1로 읽을 때, 2회 반복 평균")
    save(fig, "fig6_large_io_gain")


def main():
    import json
    plan = json.loads((OUT / "plan.json").read_text())
    r1 = pd.read_csv(OUT / "perlist_rq1.csv")
    s2 = pd.read_csv(OUT / "summary_rq2.csv")
    gain = pd.read_csv(OUT / "gain_per_list.csv")
    fig2(r1, plan)
    ideal = s2[(s2.tag == "burst_s1") & (s2.cls == "hot")].set_index("Bk").loc[BS, "ideal"].to_numpy()
    curve_fig(s2, "requests", "fig3_requests_vs_B", "list당 요청 수 (로그 축)",
              "섞여 들어온 list는 B를 키워도 요청 수가 줄지 않는다",
              "hot list 512개 평균 · 점선 = 같은 list가 연속이라면 필요한 요청 수",
              ref=ideal, ref_label="연속이라면")
    curve_fig(s2, "read_amp", "fig4_read_amp_vs_B", "읽기 증폭 = 읽은 바이트 ÷ list 크기 (로그 축)",
              "섞여 들어온 list는 B에 비례해 낭비가 커진다",
              "hot list 512개 평균 · 점선 = 낭비 없음(1배)",
              ref=np.ones(len(BS)), ref_label="낭비 없음", legend_loc="upper left")
    fig6(gain)


if __name__ == "__main__":
    main()
