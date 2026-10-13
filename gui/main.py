"""GUI 入口。

用法：
    python gui/main.py                 # 空窗口，自己打开文件
    python gui/main.py --demo          # 载入内置演示信号
    python gui/main.py --audio x.mp3
    python gui/main.py --demo --shot out.png --t0 0.2 --t1 0.9   # 无头出图（自检用）
"""

from __future__ import annotations

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, os.path.join(_ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtCore import QTimer                      # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase         # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

from gui.window import MainWindow                      # noqa: E402


def _ensure_fonts() -> None:
    """无头（offscreen）运行时 Qt 找不到系统字体，整片会画成豆腐块。

    这里手动把系统字体挂上去；正常桌面运行时 families() 非空，直接跳过。
    """
    fams = set(QFontDatabase.families())
    if "Microsoft YaHei" in fams:
        return
    windir = os.environ.get("WINDIR", "C:" + os.sep + "Windows")
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc",
                 "segoeui.ttf", "arial.ttf"):
        p = os.path.join(windir, "Fonts", name)
        if os.path.isfile(p):
            QFontDatabase.addApplicationFont(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", nargs="?", help="要载入的音频文件")
    ap.add_argument("--audio", dest="audio_opt", default=None,
                    help="同位置参数；两种写法都接受（文档里用的是 --audio）")
    ap.add_argument("--demo", action="store_true", help="载入内置演示信号")
    ap.add_argument("--shot", help="无头模式：渲染后保存 PNG 并退出")
    ap.add_argument("--t0", type=float, help="视窗起点（秒）")
    ap.add_argument("--t1", type=float, help="视窗终点（秒）")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    _ensure_fonts()
    app.setFont(QFont("Microsoft YaHei", 9))
    win = MainWindow()

    src_audio = args.audio or args.audio_opt
    if src_audio:
        win.load_path(src_audio)
    elif args.demo:
        win.load_demo()

    if args.t0 is not None and args.t1 is not None:
        win.view.zoom_to(args.t0, args.t1)

    win.show()
    app.processEvents()
    win.refresh_view()
    app.processEvents()

    if args.shot:
        def shoot():
            # 等概览与局部精算都结束，否则截图会抓到"还没算好"的中间态
            deadline = time.time() + 30.0
            while time.time() < deadline:
                app.processEvents()
                a = win.analysis
                if a is not None and a.busy == 0 and not a.pending_fine:
                    break
                time.sleep(0.02)
            for _ in range(4):          # 让最后到达的结果完成重绘
                app.processEvents()
                time.sleep(0.05)
            win.canvas.invalidate_lanes()   # 强制一次干净重绘
            app.processEvents()
            print("busy=%s pending_fine=%s 缺数据的泳道=%s"
                  % (win.analysis.busy if win.analysis else 0,
                     win.analysis.pending_fine if win.analysis else False,
                     win.canvas.last_pending))
            img = win.grab()
            img.save(args.shot)
            print("已保存截图：", args.shot, img.width(), "x", img.height())
            app.quit()
        QTimer.singleShot(300, shoot)
        return app.exec()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
