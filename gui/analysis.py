"""按需分析：整曲概览（后台算一次）+ 视窗重采样 + 高倍放大的局部精算。

卡顿的根因与对策：
  1. 重计算在 GUI 线程同步跑
     -> 概览交给 QThreadPool（一次性任务），画布只读缓存，永不阻塞；
  2. 缓存键精确到 0.1 ms，平移时永不命中
     -> 改为"整曲概览 + 重采样"，只有放大到超过概览分辨率才真算；
  3. 拖动时每一帧都排一个精算任务，堆积上百个
     -> 精算走**单线程"最新者胜"**队列，做过期的直接丢；
  4. 精算按"含 padding 的整段"定 hop，导致 98% overlap、上千帧
     -> **窗长与 hop 都跟着缩放走**：放大时用 8 ms 短窗，
        又快又对（46 ms 的窗在 0.5 ms/px 的缩放下毫无意义）。
"""

from __future__ import annotations

from collections import OrderedDict

import numpy as np
from PySide6.QtCore import (QMutex, QObject, QRunnable, QThread, QThreadPool,
                            QWaitCondition, Signal)

from transient_catcher import dsp, odf

PAD_MAX_S = 0.35
OV_SPEC_COLS = 6000      # 概览列数：调大可以减少"中等缩放"回退到精算的次数
# 按频带特化用的三个频带（Hz）。见 docs/05-p0-results.md F18：
# 中频带单独一个就比全带 E 精确率高 23/11/14 个点，低频带与高频带贡献的几乎都是误报。
BAND_EDGES = ((65.0, 600.0), (600.0, 2500.0), (2500.0, 10000.0))
OV_SPEC_ROWS = 256
OV_ENV_BUCKETS = 4000
ODF_HOP_MS = 10.0
FINE_CACHE_MAX = 24
WIN_MS_MIN, WIN_MS_MAX = 8.0, 46.0
WIN_COLS = 8.0          # 窗长大约覆盖显示上的这么多列


class _Sig(QObject):
    done = Signal(int, str, object)


class _Task(QRunnable):
    """一次性任务（概览）。"""

    def __init__(self, tid: int, key: str, fn, sig: _Sig):
        super().__init__()
        self.tid, self.key, self.fn, self.sig = tid, key, fn, sig
        self.setAutoDelete(True)

    def run(self):
        try:
            res = self.fn()
        except Exception:
            res = None
        try:
            self.sig.done.emit(self.tid, self.key, res)
        except RuntimeError:
            pass          # 程序退出时信号源可能已回收


class _FineWorker(QThread):
    """局部精算：单线程 + **最新者胜**。

    拖动会产生大量过期请求，排队执行毫无意义；只保留最后一个。
    """

    done = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._job = None
        self._abort = False

    def submit(self, key: str, fn):
        self._mutex.lock()
        self._job = (key, fn)
        self._cond.wakeAll()
        self._mutex.unlock()

    def run(self):
        while True:
            self._mutex.lock()
            while self._job is None and not self._abort:
                self._cond.wait(self._mutex)
            if self._abort:
                self._mutex.unlock()
                return
            key, fn = self._job
            self._job = None
            self._mutex.unlock()
            try:
                res = fn()
            except Exception:
                res = None
            if res is not None:
                try:
                    self.done.emit(key, res)
                except RuntimeError:
                    return

    def stop(self):
        self._mutex.lock()
        self._abort = True
        self._cond.wakeAll()
        self._mutex.unlock()
        self.wait(3000)


class Analysis(QObject):
    updated = Signal()

    def __init__(self, samples, sr: int, parent=None):
        super().__init__(parent)
        self.samples = np.ascontiguousarray(samples, dtype=np.float64)
        self.sr = int(sr)
        self.total = len(self.samples) / float(self.sr)
        self.spec_mode = "squeezed"
        self.odf_variant = "D"
        # 通道 A 的可调项（侧栏"峰相对阈值"）——改它只重算 ODF 概览，不碰频谱
        self.a_opts = {"rel_gamma": 0.30}
        # 变体 F（音高显著性）：复音数上限。实测 >10 会让选择本身不稳定
        self.f_opts = {"n_harm": 8, "top_notes": 10, "rel_gamma": 0.30}
        # 多链路取交集（变体下拉里的 A∩E / A∩E∩F）的匹配容差。
        # 注意：这里的 **ms** 只是给人看的，_ov_odf 里存的时刻是**秒**
        # （_build_ov_odf 把 onsets 的 ms 除了 1000），用之前必须 /1000。
        # 忘了除会变成"20 秒容差"，交集退化成空操作 —— 实测就是这么踩到的。
        self.intersect_tol_ms = 20.0

        self._sig = _Sig()
        self._sig.done.connect(self._on_ov_done)
        self._pool = QThreadPool.globalInstance()
        self._tid = 0
        self._inflight: dict = {}

        self._fineWorker = _FineWorker(self)
        self._fineWorker.done.connect(self._on_fine_done)
        self._fineWorker.start()
        self._fine_want = None
        self._fine_pending = None

        self._ov_env = None
        self._ov_spec: dict = {}
        self._ov_odf: dict = {}
        self._fine: OrderedDict = OrderedDict()
        self.busy = 0

        self.start_overview()

    # ---------------- 概览 ----------------
    def start_overview(self):
        self._submit("ov_env", self._build_ov_env)
        self._submit("ov_spec", lambda: (self.spec_mode, self._build_ov_spec()))

    def set_spec_mode(self, mode: str):
        if mode == self.spec_mode:
            return
        self.spec_mode = mode
        self._fine.clear()
        self._ov_spec.pop(mode, None)
        self._submit("ov_spec", lambda: (mode, self._build_ov_spec()))

    def set_a_gate(self, gamma: float):
        """改通道 A 的相对峰阈值；只丢掉 ODF 概览缓存，重新后台计算。"""
        self.a_opts["rel_gamma"] = float(gamma)
        self._ov_odf.pop("E", None)
        self._submit("ov_odf", lambda: ("E", self._build_ov_odf("E")))

    def set_f_notes(self, top_notes: int):
        self.f_opts["top_notes"] = int(top_notes)
        self._ov_odf.pop("F", None)
        self._submit("ov_odf", lambda: ("F", self._build_ov_odf("F")))

    def set_odf_variant(self, variant: str):
        if variant == self.odf_variant:
            return
        self.odf_variant = variant
        if variant not in self._ov_odf:
            self._submit("ov_odf", lambda: (variant, self._build_ov_odf(variant)))

    def _submit(self, key: str, fn):
        if key in self._inflight:
            return
        self._tid += 1
        self._inflight[key] = self._tid
        self.busy += 1
        self._pool.start(_Task(self._tid, key, fn, self._sig))

    def _on_ov_done(self, tid: int, key: str, res):
        if self._inflight.get(key) != tid:
            self.busy = max(0, self.busy - 1)
            return
        del self._inflight[key]
        self.busy = max(0, self.busy - 1)
        if res is not None:
            if key == "ov_env":
                self._ov_env = res
            elif key == "ov_spec":
                mode, arr = res
                if arr is not None:
                    self._ov_spec[mode] = arr
            elif key == "ov_odf":
                variant, data = res
                if data is not None:
                    self._ov_odf[variant] = data
        self.updated.emit()

    @property
    def pending_fine(self) -> bool:
        return self._fine_pending is not None

    def _on_fine_done(self, key: str, res):
        if self._fine_pending == key:
            self._fine_pending = None
        self._fine[key] = res
        self._fine.move_to_end(key)
        while len(self._fine) > FINE_CACHE_MAX:
            self._fine.popitem(last=False)
        self.updated.emit()

    # ---------------- 计算 ----------------
    def _build_ov_env(self):
        n = len(self.samples)
        b = OV_ENV_BUCKETS
        if n < b * 2:
            idx = np.linspace(0, n - 1, b).astype(np.int64)
            v = self.samples[idx]
            return (v, v)
        edges = np.clip(np.linspace(0, n, b + 1).astype(np.int64), 0, n - 1)
        return (np.minimum.reduceat(self.samples, edges[:-1]),
                np.maximum.reduceat(self.samples, edges[:-1]))

    def _seg_stft(self, t0, t1, cols, win_ms=None, per_semitone=4, rows=OV_SPEC_ROWS):
        sr = self.sr
        span = max(1e-4, t1 - t0)
        pad = min(PAD_MAX_S, max(0.05, span * 0.25))
        i0 = max(0, int((t0 - pad) * sr))
        i1 = min(len(self.samples), int((t1 + pad) * sr))
        seg = self.samples[i0:i1]
        if len(seg) < 4096:
            return None
        # 窗长与 hop 都跟着缩放走
        if win_ms is None:
            win_ms = float(np.clip(WIN_COLS * span / max(1, cols) * 1000.0,
                                   WIN_MS_MIN, WIN_MS_MAX))
        win = int(2 ** int(round(float(np.log2(sr * win_ms / 1000.0)))))
        win = max(128, min(win, 1 << 15))
        hop = max(1, int(span * sr / max(1, cols)))
        X, _ = dsp.stft(seg, win, hop)
        bin_freqs = np.fft.rfftfreq(win, 1.0 / sr)
        grid = dsp.log_freq_grid(65.0, 10000.0, per_semitone)
        if len(grid) != rows:
            grid = grid[np.linspace(0, len(grid) - 1, rows).astype(np.int64)]
        if self.spec_mode == "squeezed":
            _, f_hat, bin_freqs = dsp.freq_reassigned(seg, sr, win, hop)
            spec = dsp.squeeze(X, f_hat, grid)
        else:
            spec = dsp.rebin_log(np.abs(X), bin_freqs, grid)
        # 返回 (rows, frames) + 起始时刻 + 帧间隔，**由调用方裁到 [t0,t1] 并重采样到 cols**。
        # 不这样做就会出现"图只画了左边一块、而且和坐标轴错位" —— 实测踩到。
        return np.log1p(6.0 * spec).T, i0 / sr, hop / sr

    @staticmethod
    def _normalize(arr):
        lo, hi = np.percentile(arr, [5.0, 99.5])
        return np.clip((arr - lo) / max(1e-9, hi - lo), 0.0, 1.0) ** 1.2

    @staticmethod
    def _crop_to_view(arr, seg_t0, hop_s, t0, t1, cols):
        """按真实时间裁到 [t0,t1] 并重采样到恰好 cols 列。"""
        n = arr.shape[1]
        if n < 2 or cols < 1:
            return None
        pos = np.clip((np.linspace(t0, t1, cols) - seg_t0) / max(1e-12, hop_s),
                      0.0, n - 1.0)
        if cols >= n:
            return np.take(arr, np.rint(pos).astype(np.int64), axis=1)
        i0 = np.floor(pos).astype(np.int64)
        i1 = np.minimum(i0 + 1, n - 1)
        w = (pos - i0)[None, :]
        return arr[:, i0] * (1.0 - w) + arr[:, i1] * w

    def _build_ov_spec(self):
        out = self._seg_stft(0.0, self.total, OV_SPEC_COLS, rows=OV_SPEC_ROWS)
        if out is None:
            return None
        arr, st, hop_s = out
        img = self._crop_to_view(arr, st, hop_s, 0.0, self.total, OV_SPEC_COLS)
        return None if img is None else self._normalize(img)

    def _build_ov_odf(self, variant: str):
        if variant in ("带2/3", "带3/3"):
            return self._build_band_vote(need=2 if variant == "带2/3" else 3)
        if "∩" in variant:
            return self._build_intersection(variant)
        sr = self.sr
        if variant.startswith("E:"):
            # "E:下限:上限" —— 同一套参数只换频带范围
            _tag, lo, hi = variant.split(":")
            r = odf.channel_a(self.samples, sr, hop_ms=ODF_HOP_MS,
                              f_min=float(lo), f_max=float(hi), **self.a_opts)
        elif variant == "A":
            r = odf.rhydive_like(self.samples, sr, hop_ms=ODF_HOP_MS)
        elif variant == "B":
            r = odf.perbin(self.samples, sr, use_reassign=True, maxfilt=None,
                           hop_ms=ODF_HOP_MS)
        elif variant == "C":
            r = odf.perbin(self.samples, sr, use_reassign=True, maxfilt=17,
                           hop_ms=ODF_HOP_MS)
        elif variant == "E":
            r = odf.channel_a(self.samples, sr, hop_ms=ODF_HOP_MS, **self.a_opts)
        elif variant == "F":
            r = odf.pitch_salience(self.samples, sr, hop_ms=ODF_HOP_MS, **self.f_opts)
        else:
            r = odf.perbin(self.samples, sr, use_reassign=False, maxfilt=17,
                           hop_ms=ODF_HOP_MS)
        ts = np.arange(len(r["env"])) * (r["hop_ms"] / 1000.0)
        ev = np.asarray([t / 1000.0 for t, _ in r["onsets"]], dtype=np.float64)
        return (ts, np.asarray(r["env"], dtype=np.float64), ev)

    def _build_intersection(self, variant: str):
        """多链路取交集（变体下拉里的 A∩E / A∩E∩F）。

        动因（docs/06-pitfalls.md A16）：误报与命中的**强度完全一样**，
        所以任何幅度阈值都分不开；82% 的误报只是音符自身衰减 / 拍频 / 共鸣
        产生的谱变化。不同统计量对「真起音」的响应一致、对「衰减涟漪」的
        响应各不相同，所以取交集正好滤掉后者。

        实测（20 ms 容差）：A∩E 在 夜航星 上把误报从 2014 降到 1119（-44%），
        只损失 21% 的真阳性；F1 在 夜航星 与 Infinity Heaven 上都高于 A 单独。
        """
        parts = [p for p in variant.split("∩") if p]
        dets, env_ts, env = [], None, None
        for p in parts:
            ts, e, d = self._build_ov_odf(p)
            dets.append(d)
            if env is None:
                env_ts, env = ts, e      # 曲线用第一条链路的，标记才是交集
        keep = dets[0]
        for d in dets[1:]:
            if keep.size == 0 or d.size == 0:
                keep = np.zeros(0)
                break
            keep = keep[self._near(d, keep, self.intersect_tol_ms / 1000.0)]
        return (env_ts, env, keep)

    def _build_band_vote(self, need: int = 2):
        """按频带特化 + 多数投票（变体「带2/3」「带3/3」）。

        同一条 channel_a 用三个频带各跑一遍再取多数。数据见
        docs/05-p0-results.md F18：三带 2/3 再和 A 取交集，
        把真实音乐上的 F1 从 0.54-0.55 推到 0.66-0.68。

        **权威率不要调高**：3/3 全一致会把召回砍到 20-50%，平均 F1 反而更低。
        """
        dets, env_ts, env = [], None, None
        for lo, hi in BAND_EDGES:
            ts, e, d = self._build_ov_odf("E:%g:%g" % (lo, hi))
            dets.append(d)
            if env is None:
                env_ts, env = ts, e
        tol = self.intersect_tol_ms / 1000.0
        out = []
        for d in dets:
            for t in d:
                cnt = 0
                for b in dets:
                    if b.size and (np.searchsorted(b, t + tol, "right")
                                   > np.searchsorted(b, t - tol)):
                        cnt += 1
                if cnt >= need:
                    out.append(float(t))
        return (env_ts, env, self._cluster_times(out, tol))

    @staticmethod
    def _cluster_times(times, tol):
        a = np.sort(np.asarray(times, dtype=np.float64))
        if a.size == 0 or tol <= 0.0:
            return a
        keep = [float(a[0])]
        for v in a[1:]:
            if v - keep[-1] > tol:
                keep.append(float(v))
        return np.asarray(keep)

    @staticmethod
    def _near(ref, a, tol):
        """a 中每个元素在 ref 的 ±tol 内是否有邻居。"""
        ref = np.asarray(ref, dtype=np.float64)
        a = np.asarray(a, dtype=np.float64)
        if ref.size == 0 or a.size == 0:
            return np.zeros(a.size, dtype=bool)
        lo = np.searchsorted(ref, a - tol)
        hi = np.searchsorted(ref, a + tol, side="right")
        return hi > lo

    # ---------------- 画布接口（永不阻塞） ----------------
    def envelope(self, t0, t1, cols):
        n = len(self.samples)
        view_n = int(max(0.0, t1 - t0) * self.sr)
        if self._ov_env is not None and view_n > 16 * cols * 8:
            mn, mx = self._ov_env
            b = len(mn)
            a = int(np.clip(t0 / self.total * b, 0, b - 1))
            z = int(np.clip(t1 / self.total * b, a + 1, b))
            take = np.clip(np.searchsorted(np.arange(a, z) / b * self.total,
                                           np.linspace(t0, t1, cols)), 0, z - a - 1)
            return (mn[a:z][take], mx[a:z][take])
        i0 = max(0, int(t0 * self.sr))
        i1 = min(n, int(t1 * self.sr))
        if i1 - i0 < 2:
            return None
        seg = self.samples[i0:i1]
        if len(seg) <= cols:
            idx = np.linspace(0, len(seg) - 1, cols).astype(np.int64)
            v = seg[idx]
            return (v, v)
        edges = np.clip(np.linspace(0, len(seg), cols + 1).astype(np.int64),
                        0, len(seg) - 1)
        return (np.minimum.reduceat(seg, edges[:-1]),
                np.maximum.reduceat(seg, edges[:-1]))

    def _ov_view(self, ov, t0, t1, cols, rows):
        """从整曲概览里裁出 [t0,t1] 再重采样到 (rows, cols)。

        注意：概览覆盖的是**整首歌**，直接返回它会让画面从 0 秒开始左对齐 ——
        这是实测踩到的 bug。任何返回概览的地方都必须先经过这里。
        """
        n = ov.shape[1]
        c0 = int(np.clip(t0 / self.total * n, 0, n - 1))
        c1 = int(np.clip(t1 / self.total * n, c0 + 1, n))
        sub = ov[:, c0:c1]
        m = sub.shape[1]
        if m != cols:
            if cols >= m:                            # 放大：最近邻足够（这只是临时兜底）
                sub = np.take(sub, np.rint(np.linspace(0, m - 1, cols)).astype(np.int64),
                              axis=1)
            else:                                    # 缩小：线性插值，避免混叠
                pos = np.linspace(0, m - 1, cols)
                i0 = np.floor(pos).astype(np.int64)
                i1 = np.minimum(i0 + 1, m - 1)
                w = (pos - i0)[None, :]
                sub = sub[:, i0] * (1.0 - w) + sub[:, i1] * w
        if sub.shape[0] != rows:
            sub = sub[np.linspace(0, sub.shape[0] - 1, rows).astype(np.int64)]
        return sub

    def spectrum(self, t0, t1, cols, rows, mode="squeezed"):
        ov = self._ov_spec.get(mode)
        span = max(1e-6, t1 - t0)
        have = OV_SPEC_COLS / max(1e-6, self.total)
        # 容差 2.5 倍：放大到 2.5 倍以内时概览重采样看起来一样，但快两个数量级
        if ov is not None and (cols / span) <= have * 2.5:
            return self._ov_view(ov, t0, t1, cols, rows)

        key = "fine|%s|%.3f|%.3f|%d|%d" % (mode, t0, t1, cols, rows)
        if key in self._fine:
            return self._fine[key]

        fallback = None if ov is None else self._ov_view(ov, t0, t1, cols, rows)
        if self._fine_want != key and self._fine_pending != key:
            self._fine_want = key
            self._fine_pending = key

            def job(_k=key, _a=t0, _b=t1, _c=cols, _r=rows):
                out = self._seg_stft(_a, _b, _c, rows=_r)
                if out is None:
                    return None
                arr, st, hop_s = out
                img = self._crop_to_view(arr, st, hop_s, _a, _b, _c)
                return None if img is None else self._normalize(img)

            self._fineWorker.submit(key, job)
        return fallback

    def odf_curve(self, variant, t0, t1):
        d = self._ov_odf.get(variant)
        if d is None:
            if variant not in self._inflight:
                self._submit("ov_odf", lambda: (variant, self._build_ov_odf(variant)))
            return None
        ts, env, ev = d
        a = max(0, int(np.searchsorted(ts, t0)) - 1)
        b = min(len(ts), int(np.searchsorted(ts, t1)) + 2)
        ea = int(np.searchsorted(ev, t0 - 0.05))
        eb = int(np.searchsorted(ev, t1 + 0.05))
        return (ts[a:b], env[a:b], ev[ea:eb])

    def onset_times(self, variant: str):
        """当前变体已算好的音头时刻（秒）；还没算完返回 None。"""
        d = self._ov_odf.get(variant)
        if d is None:
            return None
        return np.asarray(d[2], dtype=np.float64)

    def close(self):
        self._fineWorker.stop()
