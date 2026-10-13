"""泳道（layer）。

每个泳道是画布上一条横向全宽的行，共享同一条时间轴 —— 这正是音频编辑软件的布局。
"按需改动 UI 内容" 在这里落地为两点：
  1. 泳道可见性由侧栏控制，不可见就完全不计算；
  2. 每层只为**当前可见时间窗**计算（见 gui/analysis.py 的缓存）。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRect
from PySide6.QtGui import QColor, QImage, QPen, QPolygonF

BG = QColor("#0b1220")
BG_DARK = QColor("#080d16")

# 音头标记的显示上限。超过就**不画**标记，只显示计数 —— 否则一屏几万条竖线会卡死。
# 由侧栏的组合框修改（见 gui/window.py）。
MARKER_LIMIT = 1000


def _build_lut(n: int = 256) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n)
    r = np.clip(255.0 * (t * 1.55 - 0.12), 0, 255)
    g = np.clip(255.0 * (t ** 2.4), 0, 255)
    b = np.clip(255.0 * (0.30 + 0.70 * np.sin(np.pi * np.clip(t, 0, 1) ** 0.85)), 0, 255)
    return np.stack([r, g, b], axis=1).astype(np.uint8)


LUT = _build_lut()


class Layer:
    id = "layer"
    title = "Layer"
    default_height = 100
    color = "#94a3b8"
    flex = 0          # >0 表示视窗有富余高度时可以分给它（频谱图受益最大）
    marker_color = "#f43f5e"   # 该泳道的音头在其它泳道上投射时的颜色

    def __init__(self, height: int | None = None):
        self.visible = True
        self.base_height = int(height if height is not None else self.default_height)
        self.height = self.base_height

    def paint(self, p, rect: QRect, view, an) -> None:      # pragma: no cover
        pass

    def events(self, view, an) -> list:
        return []


class WaveformLayer(Layer):
    id, title, default_height, color = "wave", "波形", 92, "#4ade80"

    def paint(self, p, rect, view, an):
        p.fillRect(rect, BG)
        if an is None:
            return
        got = an.envelope(view.t0, view.t1, int(rect.width()))
        if got is None:
            return
        mn, mx = got
        mid = rect.center().y()
        half = rect.height() * 0.46
        p.setPen(QPen(QColor(self.color), 1))
        x0 = rect.left()
        for i in range(len(mn)):
            y0 = mid - float(mx[i]) * half
            y1 = mid - float(mn[i]) * half
            if y1 < y0:
                y0, y1 = y1, y0
            p.drawLine(x0 + i, int(y0), x0 + i, int(y1))


class SpectrogramLayer(Layer):
    id, title, default_height, flex = "spec", "频谱（对数频率）", 168, 1
    mode = "linear"

    def paint(self, p, rect, view, an):
        p.fillRect(rect, BG_DARK)
        if an is None:
            return
        db = an.spectrum(view.t0, view.t1, int(rect.width()), int(rect.height()),
                         mode=self.mode)
        if db is None:
            p.setPen(QPen(QColor("#64748b"), 1))
            p.drawText(rect.left() + 6, rect.top() + 14, "无数据（后台计算中）")
            return False
        # analysis 的输出约定是 (频率, 时间)；这里只把频率轴翻过来，
        # 让行 0 = 最高频。**不要再转置** —— 之前按 (时间, 频率) 处理过一次，
        # 结果整张图被转置，只在左边画出一条 256px 宽的竖条。
        arr = db[::-1, :]
        idx = np.clip((arr * 255.0).astype(np.int64), 0, 255)
        rgb = np.ascontiguousarray(LUT[idx])
        h, w, _ = rgb.shape
        if w < 1 or h < 1:
            return
        p.drawImage(rect, QImage(rgb.data, w, h, w * 3,
                                 QImage.Format.Format_RGB888))


class ReassignedLayer(SpectrogramLayer):
    id, title, default_height, flex = "reassign", "重分配频谱", 168, 1
    mode = "squeezed"


class ChartLayer(Layer):
    """人工谱面参考：把 .osu 的音符画成蓝色竖线，用来判断"多出来的"是不是真音头。"""

    id, title, default_height, color = "chart", "谱面参考", 64, "#38bdf8"
    marker_color = "#38bdf8"

    def __init__(self, notes=None, label=""):
        super().__init__()
        self.notes = np.asarray(notes if notes is not None else [], dtype=np.float64)
        self.label = label

    def visible_notes(self, view):
        if self.notes.size == 0:
            return np.asarray([])
        a = int(np.searchsorted(self.notes, view.t0))
        b = int(np.searchsorted(self.notes, view.t1, side="right"))
        return self.notes[a:b]

    def paint(self, p, rect, view, an):
        p.fillRect(rect, BG)
        if self.notes.size == 0:
            p.setPen(QPen(QColor("#64748b"), 1))
            p.drawText(rect.left() + 6, rect.top() + 14,
                       "未载入谱面（工具栏「载入谱面…」，或载入音频时自动匹配曲库）")
            return
        vis = self.visible_notes(view)
        p.setPen(QPen(QColor(self.color), 1))
        w = max(1, rect.width())
        for t in vis:
            x = int(view.x_of(float(t), rect.left(), w))
            p.drawLine(x, rect.top() + 6, x, rect.bottom() - 6)
        p.setPen(QPen(QColor("#94a3b8"), 1))
        p.drawText(rect.left() + 6, rect.top() + 14,
                   "谱面 %d 个音符（可见 %d）  %s" % (self.notes.size, len(vis), self.label))

    def events(self, view, an):
        return list(self.visible_notes(view))


class OdfLayer(Layer):
    id, title, default_height, color = "odf", "判定曲线", 116, "#f59e0b"
    marker_color = "#f43f5e"
    variant = "E"

    def _data(self, view, an):
        if an is None:
            return None
        return an.odf_curve(self.variant, view.t0, view.t1)

    def paint(self, p, rect, view, an):
        p.fillRect(rect, BG)
        d = self._data(view, an)
        if d is None:
            p.setPen(QPen(QColor("#64748b"), 1))
            p.drawText(rect.left() + 6, rect.top() + 14, "无数据（后台计算中）")
            return False
        ts, env, ev = d
        m = (ts >= view.t0) & (ts <= view.t1)
        tt = ts[m]
        ee = env[m]
        if len(tt) < 2:
            return
        w = max(1, rect.width())
        xs = view.x_of(np.asarray(tt), rect.left(), w)
        ys = rect.bottom() - np.clip(ee / 2.0, 0.0, 1.0) * (rect.height() - 8) - 4

        p.setPen(QPen(QColor(self.color), 1.1))
        if len(xs) <= w:
            # 点不比像素多：直接画折线，光滑且快
            p.drawPolyline(QPolygonF([QPointF(float(a), float(b))
                                      for a, b in zip(xs, ys)]))
        else:
            # 点数远多于像素时，**折线在 Qt 里是病态的**：
            # 15381 点的曲线实测要 2752 ms，因为每个像素列挤了十来个近乎垂直的大跳变。
            # 改成"逐列上下包络竖线 + 列顶折线"，图元数有上界（<= 2*width），实测 11 ms。
            self._draw_envelope(p, xs, ys, rect, w)

        # ev 是**时间的一维数组**（不是 (时刻, 强度) 元组）。
        # 之前按元组解包，异常被画布吞掉 -> 一直显示"后台计算中"，实测踩到。
        vis = np.atleast_1d(ev)
        vis = vis[(vis >= view.t0) & (vis <= view.t1)]
        if len(vis) > MARKER_LIMIT:
            p.setPen(QPen(QColor("#fbbf24"), 1))
            p.drawText(rect.left() + 6, rect.bottom() - 8,
                       "音头过多（%d > 上限 %d），已隐藏标记" % (len(vis), MARKER_LIMIT))
        else:
            p.setPen(QPen(QColor("#f8fafc"), 1))
            for t in vis:
                x = view.x_of(float(t), rect.left(), w)
                p.drawLine(int(x), rect.bottom() - 5, int(x), rect.bottom() - 16)

        p.setPen(QPen(QColor("#64748b"), 1))
        p.drawText(rect.left() + 6, rect.top() + 14, "%s 变体" % self.variant)

    @staticmethod
    def _draw_envelope(p, xs, ys, rect, w):
        """把曲线压成每个像素列一根上下包络竖线，再补一条列顶折线。"""
        # 先粗块抽样，把 ufunc.at 的规模压到 ~4*width（at 是逐元素派发，很慢）
        k = max(1, len(xs) // (4 * w))
        if k > 1:
            m = len(xs) // k
            idx = np.arange(0, m * k, k)
            seg_t = np.maximum.reduceat(ys[:m * k], idx)
            seg_b = np.minimum.reduceat(ys[:m * k], idx)
            seg_c = xs[idx]
        else:
            seg_t = seg_b = ys
            seg_c = xs
        cols = np.clip((seg_c - rect.left()).astype(np.int64), 0, w - 1)
        top = np.full(w, -np.inf)
        bot = np.full(w, np.inf)
        np.maximum.at(top, cols, seg_t)
        np.minimum.at(bot, cols, seg_b)
        ok = np.isfinite(top)
        cx = np.nonzero(ok)[0].astype(np.float64) + rect.left()
        tx, bx = top[ok], bot[ok]
        p.drawLines([QLineF(float(c), float(b), float(c), float(t))
                     for c, b, t in zip(cx, bx, tx)])
        p.drawPolyline(QPolygonF([QPointF(float(a), float(b)) for a, b in zip(cx, tx)]))

    def events(self, view, an):
        d = self._data(view, an)
        if d is None:
            return []
        return list(d[2])
