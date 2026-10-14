"""播放与播放头位置。

后端优先级：**sounddevice → QAudioSink → 静音**。

为什么首选 sounddevice：
  - 它的回调直接给出**已消费的帧数**，播放头能和波形严格对齐，
    并且可以扣掉输出延迟（stream.latency）；
  - 本机 PySide6 是精简版，**不含 QtMultimedia**，QAudioSink 根本 import 不到；
    这里保留它作为回退，是为了在完整版 PySide6 上也能跑。

不用 QMediaPlayer 的原因：它的位置更新粗且抖，而本工具全靠"音头和波形对齐"来看因果。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

try:
    import sounddevice as sd
    _HAVE_SD = True
except Exception:                                    # pragma: no cover
    sd = None
    _HAVE_SD = False

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
    _HAVE_MM = True
except Exception:                                    # pragma: no cover
    _HAVE_MM = False


class Player(QObject):
    positionChanged = Signal(float)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._samples = None
        self._sr = 44100
        self._dur = 0.0
        self._start = 0.0
        self._data = None
        self._cursor = 0          # sounddevice：已消费到第几帧（相对 _data）
        self._latency = 0.0
        self._stream = None
        self._sink = None
        self._buf = None
        # 音头提示音（"节拍器"）：播放经过这些时刻就啪一声
        self._click_times = np.zeros(0, dtype=np.float64)
        self._click_idx = np.zeros(0, dtype=np.int64)
        self._click_wave = None
        self._click_gain = 0.70
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._tick)

    # ---------------- 元信息 ----------------
    @property
    def backend(self) -> str:
        if _HAVE_SD:
            return "sounddevice"
        if _HAVE_MM:
            return "QAudioSink"
        return "none"

    @property
    def duration(self) -> float:
        return self._dur

    def is_playing(self) -> bool:
        return self._stream is not None or self._sink is not None

    # ---------------- 载入 ----------------
    def load(self, samples, sr: int) -> None:
        self.stop()
        self._samples = np.ascontiguousarray(samples, dtype=np.float32)
        self._sr = int(sr)
        self._dur = len(self._samples) / float(self._sr)
        self._click_wave = None          # 采样率变了，波形要重建
        self._rebuild_click_idx()

    # ---------------- 音头提示音 ----------------
    def set_clicks(self, times, gain=None) -> None:
        """设置「经过这些时刻就啪一声」的时刻表（秒，绝对时间）。传空即关闭。

        用当前 ODF 变体的检出去打拍子：听到啪声的位置应当和波形上的起音一致，
        对不上就说明检测偏了 —— 比盯着曲线看快得多。
        """
        self._click_times = np.sort(np.asarray(times, dtype=np.float64))
        if gain is not None and float(gain) != self._click_gain:
            self._click_gain = float(gain)
            self._click_wave = None
        self._rebuild_click_idx()

    def click_count(self) -> int:
        return int(self._click_times.size)

    def _rebuild_click_idx(self) -> None:
        """把绝对时刻换算成 _data 里的下标（_data 从 _start 开始）。"""
        if self._click_times.size == 0:
            self._click_idx = np.zeros(0, dtype=np.int64)
            return
        idx = np.rint((self._click_times - self._start) * self._sr).astype(np.int64)
        self._click_idx = idx[idx >= 0]

    def _click_waveform(self):
        if self._click_wave is None:
            n = max(8, int(0.012 * self._sr))
            t = np.arange(n) / float(self._sr)
            w = (np.sin(2.0 * np.pi * 1800.0 * t)
                 + 0.5 * np.sin(2.0 * np.pi * 3600.0 * t)) * np.exp(-t / 0.0025)
            w /= max(1e-9, float(np.max(np.abs(w))))
            self._click_wave = (w * self._click_gain).astype(np.float32)
        return self._click_wave

    # ---------------- sounddevice 后端 ----------------
    def _sd_callback(self, outdata, frames, _time, _status):
        i = self._cursor
        n = len(self._data)
        end = i + frames
        take = frames if end <= n else max(0, n - i)
        if take:
            outdata[:take, 0] = self._data[i:i + take]
        if take < frames:
            outdata[take:, 0] = 0.0
        if self._click_idx.size:
            wave = self._click_waveform()
            lo = int(np.searchsorted(self._click_idx, i))
            hi = int(np.searchsorted(self._click_idx, i + frames))
            if hi > lo:
                for k in self._click_idx[lo:hi]:
                    s = int(k) - i
                    if 0 <= s < frames:
                        m = min(len(wave), frames - s)
                        outdata[s:s + m, 0] += wave[:m]
                np.clip(outdata, -1.0, 1.0, out=outdata)
        self._cursor = min(end, n)

    def _play_sd(self, start_s: float) -> None:
        i0 = int(max(0.0, min(start_s, self._dur)) * self._sr)
        self._data = self._samples[i0:]
        self._cursor = 0
        self._stream = sd.OutputStream(samplerate=self._sr, channels=1,
                                       dtype="float32", callback=self._sd_callback)
        self._latency = float(getattr(self._stream, "latency", 0.0) or 0.0)
        self._stream.start()

    # ---------------- Qt 回退后端 ----------------
    def _play_mm(self, start_s: float) -> None:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        i0 = int(max(0.0, min(start_s, self._dur)) * self._sr)
        self._buf = QBuffer()
        self._buf.setData(QByteArray(self._samples[i0:].tobytes()))
        self._buf.open(QIODevice.OpenModeFlag.ReadOnly)
        fmt = QAudioFormat()
        fmt.setSampleRate(self._sr)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        self._sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt, self)
        self._sink.start(self._buf)

    # ---------------- 控制 ----------------
    def play(self, start_s: float = 0.0) -> None:
        self.stop()
        if self._samples is None or len(self._samples) == 0:
            return
        self._start = max(0.0, min(start_s, max(0.0, self._dur - 0.02)))
        self._rebuild_click_idx()
        try:
            if _HAVE_SD:
                self._play_sd(self._start)
            elif _HAVE_MM:
                self._play_mm(self._start)
            else:
                return
        except Exception:
            self.stop()
            return
        self._timer.start()

    def _tick(self) -> None:
        if self._stream is not None:
            pos = self._start + self._cursor / self._sr - self._latency
            done = self._data is not None and self._cursor >= len(self._data)
        elif self._sink is not None:
            pos = self._start + self._sink.processedUSecs() / 1e6
            done = pos >= self._dur
        else:
            return
        self.positionChanged.emit(max(self._start, min(pos, self._dur)))
        if done:
            self.stop()
            self.finished.emit()

    def stop(self) -> None:
        self._timer.stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
            self._sink = None
        self._buf = None
        self._data = None
        self._cursor = 0
