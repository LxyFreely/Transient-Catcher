"""压测真实 GUI 渲染路径：从全景一路缩放到 8 ms，找出卡死的档位。"""

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

from PySide6.QtWidgets import QApplication      # noqa: E402

from gui.window import MainWindow                # noqa: E402

FLAC = r"C:\Users\11952\Downloads\HyuN - Infinity Heaven.flac"


def idle(app, win, timeout=60.0):
    t0 = time.perf_counter()
    while win.analysis.busy > 0 or win.analysis.pending_fine:
        app.processEvents()
        time.sleep(0.01)
        if time.perf_counter() - t0 > timeout:
            return False
    app.processEvents()
    return True


def main():
    app = QApplication([])
    win = MainWindow()
    win.resize(1600, 950)
    win.show()
    app.processEvents()
    t0 = time.perf_counter()
    win.load_path(FLAC)
    idle(app, win)
    print("载入 + 概览：%.2f s" % (time.perf_counter() - t0))
    print()

    total = win.view.total
    spans = [total, total / 4, total / 16, total / 64, total / 256, 1.0, 0.2, 0.05, 0.008]
    print("%-14s %-12s %10s %10s %10s" % ("跨度", "中心(s)", "取数(ms)", "渲染(ms)", "音头数"))
    print("-" * 62)
    for span in spans:
        c = min(total * 0.5, max(span, total * 0.25))
        win.view.zoom_to(c - span / 2, c + span / 2)
        win.canvas._fit_heights()
        idle(app, win)
        t = time.perf_counter()
        win.canvas.invalidate_lanes()
        app.processEvents()
        dt = (time.perf_counter() - t) * 1000

        evs = []
        for L in win.layers:
            if L.visible:
                try:
                    evs.extend(L.events(win.view, win.analysis))
                except Exception:
                    pass
        print("%-14s %-12.2f %10s %10.0f %10d"
              % ("%.3g s" % span, c, "-", dt, len(evs)))

    print()
    print("连续平移（当前缩放）：")
    for span in (total / 16, 1.0, 0.05):
        win.view.zoom_to(60.0, 60.0 + span)
        win.canvas._fit_heights()
        idle(app, win)
        t = time.perf_counter()
        n = 20
        for i in range(n):
            win.view.t0 = 60.0 + i * span * 0.05
            win.view.t1 = win.view.t0 + span
            win.canvas.invalidate_lanes()
            app.processEvents()
        dt = (time.perf_counter() - t) / n * 1000
        print("  span=%-8.3g  平均 %.0f ms/帧" % (span, dt))

    win.analysis.close()


if __name__ == "__main__":
    main()
