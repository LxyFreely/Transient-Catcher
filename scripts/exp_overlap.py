"""实验：overlap（减小 hop）到底能不能提高时间精度？

用有精确 ground truth 的合成信号直接测两件事：
  1. 检出音头的**时间误差**（经过抛物线插值之后）；
  2. 音头**峰高的波动**（同一串等间隔音符，峰高本应一致；波动来自帧栅格与音符的对齐漂移）。

为了公平地比较不同 hop，**峰值挑选的局部极大窗口固定为时间**（±8 ms），
否则 hop 变小会让 ±3 帧变成更窄的时间窗，混淆结论。
窗口长度保持不变（这是"只加 overlap、不改窗长"的对照）。
"""

from __future__ import annotations

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from transient_catcher import odf, signals  # noqa: E402

HOPS = (10.0, 5.0, 2.5, 1.25)
PEAK_HALF_MS = 8.0


def match(det, truth, tol=40.0):
    used = set()
    errs = []
    for t in truth:
        best, bd = None, 1e9
        for i, d in enumerate(det):
            if i in used:
                continue
            if abs(d - t) < bd:
                bd, best = abs(d - t), i
        if best is not None and bd <= tol:
            used.add(best)
            errs.append(det[best] - t)
    return np.asarray(errs), len(used)


def main():
    cases = [
        ("S4 32分（8 个音头，间隔 46.875ms）", signals.s4_dense32()),
        ("S3 同音反复（4 个音头，间隔 100/60/47ms）", signals.s3_rearticulation()),
    ]
    print("=" * 104)
    print("overlap 对时间精度的影响（窗口长度固定 46 ms，只改 hop；峰值窗口固定 ±8 ms）")
    print("=" * 104)
    print("%-40s %8s %8s %10s %10s %10s %10s" % (
        "信号 / 配置", "hop(ms)", "overlap", "检出/真值", "中位误差", "平均|误差|", "峰高CV"))
    print("-" * 104)

    for name, sig in cases:
        x, sr = sig["samples"], sig["sr"]
        truth = sig["truth_ms"]
        for hop in HOPS:
            half = max(1, int(round(PEAK_HALF_MS / hop)))
            r = odf.perbin(x, sr, use_reassign=False, maxfilt=17,
                           hop_ms=hop, peak_half=half)
            det = [t for t, _ in r["onsets"]]
            errs, n = match(det, truth)
            # 峰高波动：只统计匹配上的那些音头
            used = set()
            strengths = []
            for t in truth:
                best, bd = None, 1e9
                for i, (dt, ds) in enumerate(r["onsets"]):
                    if i in used:
                        continue
                    if abs(dt - t) < bd:
                        bd, best = abs(dt - t), i
                if best is not None and bd <= 40.0:
                    used.add(best)
                    strengths.append(r["onsets"][best][1])
            cv = (np.std(strengths) / np.mean(strengths)) if len(strengths) > 1 else 0.0
            ov = 1.0 - hop / r["win"] * sr / 1000.0
            print("%-40s %8.2f %7.0f%% %7d/%-3d %10s %10s %10.3f" % (
                name[:38], hop, 100.0 * (1.0 - hop * sr / 1000.0 / r["win"]),
                n, len(truth),
                ("%+.2f" % np.median(errs)) if len(errs) else "-",
                ("%.2f" % np.mean(np.abs(errs))) if len(errs) else "-",
                cv))
        print()

    # 逐样本误差分布（S4）
    print("S4 各音头的检出误差（ms，按真值顺序）：")
    x, sr = signals.s4_dense32()["samples"], signals.s4_dense32()["sr"]
    truth = signals.s4_dense32()["truth_ms"]
    for hop in HOPS:
        half = max(1, int(round(PEAK_HALF_MS / hop)))
        r = odf.perbin(x, sr, use_reassign=False, maxfilt=17, hop_ms=hop, peak_half=half)
        det = [t for t, _ in r["onsets"]]
        row = []
        for t in truth:
            near = [d for d in det if abs(d - t) <= 40.0]
            row.append("%+.2f" % (min(near, key=lambda d: abs(d - t)) - t) if near else "  -- ")
        print("  hop=%-6.2f  %s" % (hop, "  ".join(row)))


if __name__ == "__main__":
    main()
