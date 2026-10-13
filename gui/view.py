"""共享的时间视窗状态。

设计要点：**所有泳道共用同一个视窗**（t0, t1），这是"类音频编辑软件"界面的核心
—— 波形、频谱、重分配图、判定曲线必须横向严格对齐，否则看不了因果关系。
"""

from __future__ import annotations


class ViewState:
    MIN_SPAN = 0.008       # 最小可见时间跨度（8 ms），足以看清 32 分音符

    def __init__(self, total: float = 1.0):
        self.total = max(1e-3, total)
        self.t0 = 0.0
        self.t1 = self.total
        self.playhead = 0.0

    # ---- 基本 ----
    @property
    def span(self) -> float:
        return max(1e-6, self.t1 - self.t0)

    def set_total(self, total: float) -> None:
        self.total = max(1e-3, total)
        self.t0 = 0.0
        self.t1 = self.total

    def clamp(self) -> None:
        span = min(self.span, self.total)
        if span < self.MIN_SPAN:
            span = min(self.MIN_SPAN, self.total)
        if self.t0 < 0.0:
            self.t0 = 0.0
        if self.t0 + span > self.total:
            self.t0 = max(0.0, self.total - span)
        self.t1 = self.t0 + span

    # ---- 变换 ----
    def x_of(self, t: float, x0: float, w: float) -> float:
        return x0 + (t - self.t0) / self.span * w

    def t_of(self, x: float, x0: float, w: float) -> float:
        return self.t0 + (x - x0) / max(1e-9, w) * self.span

    # ---- 操作 ----
    def zoom(self, factor: float, anchor_t: float | None = None) -> None:
        if anchor_t is None:
            anchor_t = 0.5 * (self.t0 + self.t1)
        frac = (anchor_t - self.t0) / self.span
        new_span = self.span * factor
        new_span = max(min(new_span, self.total), min(self.MIN_SPAN, self.total))
        self.t0 = anchor_t - frac * new_span
        self.t1 = self.t0 + new_span
        self.clamp()

    def pan(self, dt: float) -> None:
        self.t0 += dt
        self.t1 += dt
        self.clamp()

    def zoom_to(self, a: float, b: float) -> None:
        a, b = min(a, b), max(a, b)
        if b - a < self.MIN_SPAN:
            c = 0.5 * (a + b)
            a, b = c - self.MIN_SPAN / 2, c + self.MIN_SPAN / 2
        self.t0, self.t1 = a, b
        self.clamp()
