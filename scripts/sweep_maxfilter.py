"""诊断 3：重分配的符号/量纲自检 + max-filter 宽度扫描。"""

from __future__ import annotations

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from transient_catcher import dsp, odf, signals  # noqa: E402


def main():
    sr, win, hop = 44100, 2048, 441
    print("=" * 96)
    print("一、重分配自检：纯音的 f_hat 应等于真实频率")
    print("=" * 96)
    print("%10s %12s %14s %12s %12s" % ("真实 f0", "峰值 bin", "f_hat", "误差 Hz", "误差 音分"))
    for f0 in (110.0, 220.0, 440.0, 880.0, 1760.0):
        t = np.arange(sr) / sr
        x = np.sin(2 * np.pi * f0 * t)
        X, f_hat, fb = dsp.freq_reassigned(x, sr, win, hop)
        row = 20
        k = int(np.argmax(np.abs(X[row])))
        est = float(f_hat[row, k])
        err_c = 1200.0 * np.log2(est / f0) if est > 0 else float("inf")
        print("%10.1f %12.1f %14.2f %12.2f %12.1f" % (f0, fb[k], est, est - f0, err_c))

    print()
    print("=" * 96)
    print("二、max-filter 宽度扫描（不重分配 + 重分配），看能否把颤音压下去")
    print("=" * 96)
    s0 = signals.s1_vibrato(rate=0.0)
    s1 = signals.s1_vibrato()
    print("%-40s %10s %10s %7s %9s" % ("配置", "flux均值", "p90", "事件", "事件/秒"))
    print("-" * 96)
    for reassign in (False, True):
        for w in (None, 3, 5, 9, 17, 33, 65):
            tag = "reassign=%-5s maxf=%-4s" % (reassign, w)
            for label, s in (("S0", s0), ("S1", s1)):
                r = odf.perbin(s["samples"], s["sr"], use_reassign=reassign, maxfilt=w)
                f = np.asarray(r["flux"])
                pos = f[f > 0]
                p90 = float(np.quantile(pos, 0.9)) if pos.size else 0.0
                dur = len(r["env"]) * odf.HOP_MS / 1000.0
                print("%-40s %10.3f %10.3f %7d %9.1f" % (
                    label + " " + tag, float(f.mean()), p90, len(r["onsets"]),
                    len(r["onsets"]) / dur))
        print()


if __name__ == "__main__":
    main()
