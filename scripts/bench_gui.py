"""GUI 渲染路径基准：证明缩放/平移不再阻塞。

构造一个 45 秒的信号，建立概览，然后测量三层在多种缩放级别下的取数耗时。

用法： python scripts/bench_gui.py
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, os.path.join(_ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtWidgets import QApplication          # noqa: E402

from gui.analysis import Analysis                    # noqa: E402
from gui.demo import demo_signal                     # noqa: E402

W = 1360          # 画布宽度（与截图一致）


def wait_idle(app, an, timeout=180.0):
    t0 = time.perf_counter()
    while an.busy > 0 and time.perf_counter() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return time.perf_counter() - t0


def timeit(fn, n=20):
    fn()                                            # 预热
    best, worst, tot = 1e9, 0.0, 0.0
    for _ in range(n):
        t = time.perf_counter()
        fn()
        dt = (time.perf_counter() - t) * 1000.0
        best = min(best, dt)
        worst = max(worst, dt)
        tot += dt
    return tot / n, worst


def main():
    app = QApplication([])
    base, sr = demo_signal()
    reps = 15
    samples = np.tile(base, reps)
    print("信号：%.1f s @ %d Hz（%d 样本）" % (len(samples) / sr, sr, len(samples)))

    t = time.perf_counter()
    an = Analysis(samples, sr)
    build = wait_idle(app, an)
    print("整曲概览构建（波形 + 频谱 + ODF，全部后台）：%.2f s" % build)
    print()

    total = an.total
    levels = [("全景", 0.0, total), ("1/4", 0.0, total / 4),
              ("1/16", total * 0.4, total * 0.4 + total / 16),
              ("1/64", total * 0.4, total * 0.4 + total / 64)]

    print("%-8s %10s %12s %12s %12s" % ("缩放", "跨度(ms)", "波形(ms)", "频谱(ms)", "ODF(ms)"))
    print("-" * 60)
    for name, t0, t1 in levels:
        rows = 256
        env = timeit(lambda: an.envelope(t0, t1, W))
        spe = timeit(lambda: an.spectrum(t0, t1, W, rows, mode=an.spec_mode))
        odf = timeit(lambda: an.odf_curve(an.odf_variant, t0, t1))
        print("%-8s %10.0f %12.3f %12.3f %12.3f"
              % (name, (t1 - t0) * 1000, env[1], spe[1], odf[1]))

    # 平移扫描：模拟拖动时的连续重绘
    span = total / 16
    print()
    t = time.perf_counter()
    n = 120
    for i in range(n):
        a = total * 0.2 + (total * 0.6) * i / n
        an.envelope(a, a + span, W)
        an.spectrum(a, a + span, W, 256, mode=an.spec_mode)
        an.odf_curve(an.odf_variant, a, a + span)
    dt = (time.perf_counter() - t) / n * 1000.0
    print("连续平移（1/16 缩放，三层一起取数）：平均 %.2f ms/帧，等效 %.0f FPS"
          % (dt, 1000.0 / max(1e-9, dt)))

    # 高倍放大时的局部精算代价（同步测，实际在后台线程）
    t0 = total * 0.4
    t = time.perf_counter()
    out = an._seg_stft(t0, t0 + 0.5, W, rows=256)
    assert out is not None
    print("高倍放大：0.5 s 窗口的局部频谱精算 = %.0f ms（后台跑，不阻塞）"
          % ((time.perf_counter() - t) * 1000))

    # 让精算队列把最后一个请求跑完，避免退出时报错
    an._fineWorker.submit("__drain__", lambda: None)
    time.sleep(0.3)
    app.processEvents()
    an.close()
    print()
    print("结论：只要 概览 / 精算 都在后台，画布的每帧取数应在个位数毫秒。")


if __name__ == "__main__":
    main()
