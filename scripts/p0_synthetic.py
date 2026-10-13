"""P0：合成信号对照。

四种 ODF：
  A  现有算法（按 docs/01-analysis.md 独立重实现）
  B  1/4 半音对数重 bin + 逐 bin 正导数，**含频率重分配**（最初的想法）
  C  B + SuperFlux max-filter
  D  同 B/C，但**不做重分配** + SuperFlux max-filter

外加 S0 稳定长音作为 S1 的严格对照：它必须几乎不产生事件。

产出：
  out/p0_synthetic.png
  stdout 指标表

用法：
  python scripts/p0_synthetic.py
"""

from __future__ import annotations

import os
import sys

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from transient_catcher import odf, signals  # noqa: E402

OUT = os.path.join(_ROOT, "out")
MAXF = 17          # SuperFlux max-filter 宽度（1/4 半音 bin 数；17 bin ≈ 4.25 半音）


def norm(a):
    m = float(np.max(a)) if len(a) else 0.0
    return a / m if m > 0 else a


def fp_per_sec(onsets, region_ms):
    if not region_ms:
        return None
    lo, hi = region_ms
    n = sum(1 for t, _ in onsets if lo <= t <= hi)
    span = (hi - lo) / 1000.0
    return n / span if span > 0 else None


def fmt(v):
    return "-" if v is None else ("%.1f" % v)


def build_signals():
    s0 = signals.s1_vibrato(rate=0.0)
    s0["name"] = "S0 稳定长音（S1 对照）"
    s0["note"] = "与 S1 完全相同，但去掉颤音；它的事件应当只有起音那一个"
    s0["fp_region_ms"] = (600.0, 2250.0)
    s0["expect"] = 1
    return [s0] + signals.all_signals()


def main():
    ranks = []
    for s in build_signals():
        x, sr = s["samples"], s["sr"]
        r_a = odf.rhydive_like(x, sr)
        r_b = odf.perbin(x, sr, use_reassign=True, maxfilt=None)
        r_c = odf.perbin(x, sr, use_reassign=True, maxfilt=MAXF)
        r_d = odf.perbin(x, sr, use_reassign=False, maxfilt=MAXF)
        r_e = odf.channel_a(x, sr)
        ranks.append((s, r_a, r_b, r_c, r_d, r_e))

    print("=" * 104)
    print("P0 指标表   A=现有算法  B=逐bin导数(含重分配)  C=B+maxfilter  D=不重分配+maxfilter")
    print("=" * 104)
    print("%-22s %5s %4s %4s %4s %4s %4s %10s %10s %10s %10s" % (
        "信号", "预期", "A", "B", "C", "D", "E",
        "A误报/秒", "B误报/秒", "C误报/秒", "D误报/秒"))
    print("-" * 104)
    for s, ra, rb, rc, rd, re_ in ranks:
        print("%-22s %5d %4d %4d %4d %4d %4d %10s %10s %10s %10s" % (
            s["name"], s["expect"], len(ra["onsets"]), len(rb["onsets"]),
            len(rc["onsets"]), len(rd["onsets"]), len(re_["onsets"]),
            fmt(fp_per_sec(ra["onsets"], s["fp_region_ms"])),
            fmt(fp_per_sec(rb["onsets"], s["fp_region_ms"])),
            fmt(fp_per_sec(rc["onsets"], s["fp_region_ms"])),
            fmt(fp_per_sec(rd["onsets"], s["fp_region_ms"]))))
    print("=" * 104)
    for s, ra, rb, rc, rd, re_ in ranks:
        print("%-22s D=%s" % (s["name"], [round(t) for t, _ in rd["onsets"]][:10]))
        print("%-22s E=%s" % ("", [round(t) for t, _ in re_["onsets"]][:12]))
    print("\n真值：S0/S1 起音 300ms；S2 台阶 1000ms；S3 同音反复 250/350/410/457ms；"
          "S4 32分 250…578ms；S5 连奏 350/970/1590/2210ms")

    colors = {"A": "#2563eb", "B": "#ea580c", "C": "#a855f7", "D": "#16a34a",
              "E": "#0ea5e9"}
    fig, axes = plt.subplots(len(ranks), 1, figsize=(14, 17))
    for ax, (s, ra, rb, rc, rd, re_) in zip(axes, ranks):
        ta = np.arange(len(ra["env"])) * ra["hop_ms"] / 1000.0
        tb = np.arange(len(rb["env"])) * rb["hop_ms"] / 1000.0
        ax.plot(ta, norm(ra["env"]), lw=1.6, color=colors["A"], label=ra["name"])
        ax.plot(tb, norm(rb["env"]), lw=0.9, color=colors["B"], alpha=0.85, label=rb["name"])
        ax.plot(tb, norm(rc["env"]), lw=1.2, color=colors["C"], alpha=0.9, label=rc["name"])
        ax.plot(tb, norm(rd["env"]), lw=1.2, color=colors["D"], label=rd["name"])
        ax.plot(np.arange(len(re_["env"])) * re_["hop_ms"] / 1000.0,
                norm(re_["env"]), lw=1.6, color="#0ea5e9", label=re_["name"])
        for gt in s["truth_ms"]:
            ax.axvline(gt / 1000.0, color="#111827", ls="--", lw=0.9, alpha=0.5)
        if s["fp_region_ms"]:
            ax.axvspan(s["fp_region_ms"][0] / 1000.0, s["fp_region_ms"][1] / 1000.0,
                       color="#ef4444", alpha=0.06)
        ax.set_title("%s —— %s" % (s["name"], s["note"]), fontsize=10, loc="left")
        ax.set_ylim(-0.06, 1.18)
        ax.set_ylabel("归一化 ODF")
        ax.grid(alpha=0.2)
    axes[0].legend(loc="upper right", fontsize=8.5)
    axes[-1].set_xlabel("时间 (s)")
    fig.suptitle("P0 对照：四种 ODF 在合成信号上（各自归一化到峰值 1；虚线 = ground truth）", fontsize=12)
    fig.tight_layout()
    os.makedirs(OUT, exist_ok=True)
    png = os.path.join(OUT, "p0_synthetic.png")
    fig.savefig(png, dpi=120)
    print("\n图已保存：", png)


if __name__ == "__main__":
    main()
