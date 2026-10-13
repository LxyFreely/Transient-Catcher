"""内置演示信号：把 S4（32 分钢琴）+ S1（颤音）+ S5（弱人声连奏）叠在一起。

用途是让 GUI 不需要任何外部文件就能展示"密集音型 + 颤音 + 连奏"三种难点同时出现。
"""

from __future__ import annotations

import numpy as np

from transient_catcher import signals


def demo_signal():
    parts = [signals.s4_dense32(), signals.s1_vibrato(), signals.s5_legato()]
    n = max(len(p["samples"]) for p in parts)
    sr = parts[0]["sr"]
    mix = np.zeros(n)
    for p, gain in zip(parts, (0.85, 0.5, 0.5)):
        x = p["samples"]
        mix[:len(x)] += gain * x
    peak = float(np.max(np.abs(mix))) or 1.0
    if peak > 0.95:
        mix *= 0.95 / peak
    return mix, sr
