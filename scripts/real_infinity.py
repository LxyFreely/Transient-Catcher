"""真实素材回归：Infinity Heaven 25–35 s 的 32 分琶音。

这是"安静段"问题的验收脚本 —— 合成信号测不出来，
因为它要的是"同一首歌里，安静段与活跃段的通量差一个数量级"。

参考事实：
  BPM 160 -> 32 分 = 46.875 ms
  安静段 25.45–25.65（200 ms）若真有 32 分，应有约 4 个音头
  该段谱面里只有 16 分（94 ms）对象，说明音频比谱面更密

用法： python scripts/real_infinity.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from transient_catcher import odf, signals  # noqa: E402

FLAC = r"C:\Users\11952\Downloads\HyuN - Infinity Heaven.flac"
QUIET = (25.45, 25.65)
LO, HI = 25.0, 35.0


def count(onsets, t_base, lo, hi):
    return sum(1 for t, _ in onsets if lo <= t_base + t / 1000.0 <= hi)


def main():
    if not os.path.isfile(FLAC):
        print("找不到素材：", FLAC)
        return
    x, sr = sf.read(FLAC, dtype="float32", always_2d=True)
    mono = x.mean(axis=1).astype(np.float64)
    seg = mono[int(22.0 * sr):int(38.0 * sr)]

    s0 = signals.s1_vibrato(rate=0.0)
    s1 = signals.s1_vibrato()

    rows = []
    for tag, fn in (
        ("A 现有算法（重实现）", lambda a, s: odf.rhydive_like(a, s, hop_ms=10.0)),
        ("D 逐bin导数 不重分配+maxfilt17", lambda a, s: odf.perbin(
            a, s, use_reassign=False, maxfilt=17, hop_ms=10.0)),
        ("E 通道A原型（短窗+能量归一）", lambda a, s: odf.channel_a(a, s, hop_ms=10.0)),
    ):
        r = fn(seg, sr)
        nq = count(r["onsets"], 22.0, *QUIET)
        n35 = count(r["onsets"], 22.0, LO, HI)

        r1 = fn(s1["samples"], s1["sr"])
        fp = count(r1["onsets"], 0.0, 600.0, 2250.0) / 1.65
        r0 = fn(s0["samples"], s0["sr"])
        n0 = len(r0["onsets"])
        rows.append((tag, nq, n35, fp, n0))

    print("真实素材：HyuN - Infinity Heaven，BPM 160（32 分 = 46.875 ms）")
    print("=" * 88)
    print("%-34s %8s %8s | %10s %8s" % ("配置", "安静段", "25-35", "S1误报/秒", "S0事件"))
    print("-" * 88)
    for tag, nq, n35, fp, n0 in rows:
        print("%-34s %8d %8d | %10.1f %8d" % (tag, nq, n35, fp, n0))
    print("-" * 88)
    print("目标：安静段 ≈ 4（32 分密度）｜ S1 误报 ≈ 0 ｜ S0 事件 = 1（只有起音）")


if __name__ == "__main__":
    main()
