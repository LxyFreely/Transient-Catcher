"""GUI 冒烟测试：不弹窗，验证真实（非 offscreen）路径。

检查项：
  1. Qt 原生平台
  2. 播放后端（sounddevice 优先）
  3. 音频输出设备的格式是否受支持（**用静音流测试，不会发出声音**）
  4. MainWindow 能构造、演示信号能载入
  5. RhyDive 本地曲库能扫到多少首

用法： python scripts/smoke_gui.py
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

app = QApplication([])
print("1. Qt 平台插件      :", app.platformName())

# ---- 播放后端 ----
try:
    import sounddevice as sd
    print("2. 播放后端         : sounddevice", sd.__version__)
    try:
        dev = sd.query_devices(kind="output")
        print("   默认输出设备      :", dev["name"])
    except Exception as exc:
        print("   默认输出设备      : 查询失败 ->", exc)
    ok = True
    try:
        sd.check_output_settings(samplerate=44100, channels=1, dtype="float32")
    except Exception as exc:
        ok = False
        print("   格式检查          : 不支持 ->", exc)
    if ok:
        # 静音流：只验证 open/start/stop 全链路，不发出声音
        try:
            silence = np.zeros((4410, 1), dtype="float32")
            st = sd.OutputStream(samplerate=44100, channels=1, dtype="float32",
                                 callback=lambda out, f, t, s: out.fill(0.0))
            st.start()
            time.sleep(0.15)
            st.stop()
            st.close()
            print("   静音流 open/start : ok（未发声）")
        except Exception as exc:
            print("   静音流 open/start : 失败 ->", exc)
except Exception as exc:
    print("2. 播放后端         : sounddevice 不可用 ->", exc, "（会静音，其余功能不受影响）")

from gui.audio_io import scan_rhydive_library      # noqa: E402
from gui.window import MainWindow                   # noqa: E402

win = MainWindow()
print("3. MainWindow       : 构造成功，%d 条泳道，播放后端 = %s"
      % (len(win.layers), win.player.backend))
win.load_demo()
print("   演示信号          : 时长 %.2f s，视窗 %.2f–%.2f s"
      % (win.view.total, win.view.t0, win.view.t1))

songs = scan_rhydive_library()
print("4. RhyDive 本地曲库 : 扫到 %d 首" % len(songs))
for s in songs[:8]:
    print("     %s — %s" % (s["artist"], s["title"]))
if len(songs) > 8:
    print("     ... 还有 %d 首" % (len(songs) - 8))

print()
print("冒烟测试通过。窗口未显示（没有调用 show()），也没有发出声音。")
