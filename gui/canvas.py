"""画布：泳道纵向堆叠 + 共享时间轴 + **位图缓存**。

性能要点：
  - 泳道内容渲染进一张 QPixmap 并复用；只有"视图/数据变了"才重建。
  - 播放头是唯一每帧变化的东西，单独画在位图之上 —— 播放时不再重建泳道。
  - 绘制过程**永不阻塞**：数据没算好就画已有的（可能是粗概览），
    后台算完由 analysis.updated 触发重建。
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

AXIS_H = 26
BG = QColor("#05080f")
LANE_HINT = QColor("#64748b")


class Canvas(QWidget):
    seekRequested = Signal(float)
    cursorMoved = Signal(float)

    def __init__(self, view, layers, parent=None):
        super().__init__(parent)
        self.view = view
        self.layers = layers
        self.analysis = None
        self.setMouseTracking(True)
        self.setMinimumHeight(self.total_height())
        self._drag_x = None
        self._drag_t0 = 0.0
        self._pix = None
        self._lanes_dirty = True
        self.last_pending = []

    # ---------------- 布局 ----------------
    def total_height(self) -> int:
        return sum(L.base_height for L in self.layers if L.visible) + AXIS_H

    def _lanes_bottom(self) -> int:
        return sum(L.height for L in self.layers if L.visible)

    def set_analysis(self, an) -> None:
        self.analysis = an
        self.invalidate_lanes()

    def invalidate_lanes(self) -> None:
        self._lanes_dirty = True
        self.update()

    def resizeEvent(self, ev):
        self._fit_heights()
        self._lanes_dirty = True
        super().resizeEvent(ev)

    def _fit_heights(self) -> None:
        vis = [L for L in self.layers if L.visible]
        if not vis:
            return
        base = sum(L.base_height for L in vis)
        extra = (self.height() - AXIS_H) - base
        if extra <= 0:
            for L in vis:
                L.height = L.base_height
            return
        fx = sum(max(0, L.flex) for L in vis)
        if fx <= 0:
            for L in vis:
                L.height = L.base_height + extra // len(vis)
        else:
            for L in vis:
                L.height = L.base_height + int(extra * max(0, L.flex) / fx)

    def _relayout(self) -> None:
        self._fit_heights()
        self.setMinimumHeight(self.total_height())
        self.invalidate_lanes()

    # ---------------- 绘制 ----------------
    def _render_lanes(self) -> None:
        w = max(1, self.width())
        h = max(1, self.height())
        dpr = float(self.devicePixelRatioF())
        if (self._pix is None or self._pix.width() != int(w * dpr)
                or self._pix.height() != int(h * dpr)):
            self._pix = QPixmap(int(w * dpr), int(h * dpr))
            self._pix.setDevicePixelRatio(dpr)
        self._pix.fill(BG)

        p = QPainter(self._pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        y = 0
        pending = False
        pend_titles = []
        for L in self.layers:
            if not L.visible:
                continue
            rect = QRect(0, y, w, L.height)
            p.save()
            p.setClipRect(rect)
            try:
                if L.paint(p, rect, self.view, self.analysis) is False:
                    pending = True
                    pend_titles.append(L.title)
            except Exception as exc:
                pending = True
                pend_titles.append("%s(%s)" % (L.title, exc))
            p.restore()
            p.setPen(QPen(QColor("#1e293b"), 1))
            p.drawLine(0, y + L.height - 1, w, y + L.height - 1)
            y += L.height

        import gui.layers as _L
        groups = []
        total = 0
        for L in self.layers:
            if not L.visible:
                continue
            try:
                ev = [t for t in L.events(self.view, self.analysis)
                      if self.view.t0 <= t <= self.view.t1]
            except Exception:
                ev = []
            if ev:
                groups.append((L, ev))
                total += len(ev)
        if total > _L.MARKER_LIMIT:
            # 超过上限就整层不画：一屏上万条竖线会让界面直接卡死
            p.setPen(LANE_HINT)
            p.drawText(8, y - 8, "音头过多（%d 个，超过上限 %d），已隐藏投射线"
                       % (total, _L.MARKER_LIMIT))
        else:
            for L, ev in groups:
                p.setPen(QPen(QColor(L.marker_color), 1))
                for t in ev:
                    x = int(self.view.x_of(t, 0, w))
                    p.drawLine(x, 0, x, y)

        self._draw_axis(p, QRect(0, y, w, AXIS_H))
        if pending:
            p.setPen(LANE_HINT)
            p.drawText(8, max(14, y - 6), "后台计算中…")
        p.end()
        self._lanes_dirty = False
        self.last_pending = pend_titles

    def _draw_axis(self, p, rect: QRect):
        p.fillRect(rect, QColor("#0b1220"))
        span = self.view.span
        step = 10.0 ** np.floor(np.log10(max(1e-5, span / 8.0)))
        for mult in (1, 2, 5, 10):
            if span / (step * mult) <= 12:
                step *= mult
                break
        p.setPen(QPen(QColor("#475569"), 1))
        t = np.ceil(self.view.t0 / step) * step
        guard = 0
        while t <= self.view.t1 and guard < 200:
            x = int(self.view.x_of(t, rect.left(), rect.width()))
            p.drawLine(x, rect.top(), x, rect.top() + 5)
            label = ("%.3f" % t) if step < 0.1 else ("%.2f" % t if step < 1 else "%.1f" % t)
            p.drawText(x + 3, rect.top() + 17, label)
            t += step
            guard += 1

    def _draw_playhead(self, p) -> None:
        if not (self.view.t0 <= self.view.playhead <= self.view.t1):
            return
        x = int(self.view.x_of(self.view.playhead, 0, self.width()))
        p.setPen(QPen(QColor("#22d3ee"), 1.5))
        p.drawLine(x, 0, x, self._lanes_bottom())

    def paintEvent(self, _ev):
        if self._lanes_dirty or self._pix is None:
            self._render_lanes()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pix)
        self._draw_playhead(p)

    # ---------------- 交互 ----------------
    def wheelEvent(self, ev):
        dy = ev.angleDelta().y()
        if dy == 0:
            return
        anchor = self.view.t_of(ev.position().x(), 0, self.width())
        self.view.zoom(0.84 if dy > 0 else 1.0 / 0.84, anchor)
        self.invalidate_lanes()

    def mousePressEvent(self, ev):
        if ev.position().y() > self._lanes_bottom():
            self.seekRequested.emit(self.view.t_of(ev.position().x(), 0, self.width()))
            return
        if ev.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._drag_x = ev.position().x()
            self._drag_t0 = self.view.t0

    def mouseMoveEvent(self, ev):
        self.cursorMoved.emit(self.view.t_of(ev.position().x(), 0, self.width()))
        if self._drag_x is None:
            return
        span = self.view.t1 - self.view.t0
        dt = -(ev.position().x() - self._drag_x) / max(1, self.width()) * span
        self.view.t0 = self._drag_t0 + dt
        self.view.t1 = self.view.t0 + span
        self.view.clamp()
        self.invalidate_lanes()

    def mouseReleaseEvent(self, _ev):
        self._drag_x = None

    def mouseDoubleClickEvent(self, ev):
        self.seekRequested.emit(self.view.t_of(ev.position().x(), 0, self.width()))
