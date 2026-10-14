"""主窗口：类音频编辑软件的布局。

结构：
    ┌─ 工具栏：打开 / 曲库 / 播放 / 缩放 / ODF 变体 / 时间 ─────────────┐
    ├──────────────── 画布（泳道纵向堆叠，共享时间轴）────────┬─ 视图 ─┤
    │  波形                                                  │ 泳道开关│
    │  频谱                                                  │ 重分配  │
    │  重分配频谱                                            │ 模式    │
    │  判定曲线 + 音头标记                                    │ 曲库    │
    └──────────────── 时间轴 ────────────────────────────────┴─────────┘
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDockWidget, QFileDialog,
                               QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QPushButton, QToolBar, QVBoxLayout, QWidget)

from gui.analysis import Analysis
from gui.audio_io import AUDIO_EXT, load_audio, scan_rhydive_library
from gui.canvas import Canvas
from gui.demo import demo_signal
from gui.chart import auto_match, load_notes
from gui.layers import (ChartLayer, OdfLayer, ReassignedLayer, SpectrogramLayer,
                        WaveformLayer)
from gui.player import Player
from gui.view import ViewState


class LibraryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("从 RhyDive 本地曲库导入")
        self.resize(620, 420)
        self.chosen = None
        v = QVBoxLayout(self)
        self.list = QListWidget()
        v.addWidget(self.list)
        songs = scan_rhydive_library()
        if not songs:
            v.addWidget(QLabel("没有找到本地曲库。请先用 RhyDive 下载一首歌，或改用「打开音频…」。"))
        for s in songs:
            it = QListWidgetItem("%s — %s" % (s["artist"], s["title"]))
            it.setData(Qt.ItemDataRole.UserRole, s["path"])
            self.list.addItem(it)
        btn = QPushButton("载入所选")
        btn.clicked.connect(self._accept)
        v.addWidget(btn)
        self.list.itemDoubleClicked.connect(lambda _i: self._accept())

    def _accept(self):
        it = self.list.currentItem()
        if it is not None:
            self.chosen = it.data(Qt.ItemDataRole.UserRole)
            self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Transient Catcher — 音符检测器查看器")
        self.resize(1600, 950)

        self.view = ViewState()
        self.analysis = None
        self.cur_label = "(未载入)"

        self.player = Player(self)
        self.player.positionChanged.connect(self._on_position)
        self.player.finished.connect(self._on_finished)

        self.chart_layer = ChartLayer()
        self.layers = [WaveformLayer(), SpectrogramLayer(), ReassignedLayer(),
                       self.chart_layer, OdfLayer()]
        self.canvas = Canvas(self.view, self.layers)
        self.canvas.seekRequested.connect(self._seek)
        self.canvas.cursorMoved.connect(self._on_cursor)
        self.setCentralWidget(self.canvas)

        self._build_toolbar()
        self._build_dock()
        self.statusBar().showMessage("滚轮缩放 · 拖动平移 · 双击或点时间轴跳转 · 空格播放")
        self._refresh_time()

    # ---------------- 工具栏 ----------------
    def _build_toolbar(self):
        tb = QToolBar("主")
        tb.setMovable(False)
        self.addToolBar(tb)

        def add(text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            return a

        add("打开音频…", self.open_file, "Ctrl+O")
        add("RhyDive 曲库", self.open_library)
        add("载入谱面…", self.open_chart, "Ctrl+L")
        tb.addSeparator()
        add("▶ / ⏸", self.toggle_play, "Space")
        add("⏹", self.stop_playback)
        tb.addSeparator()
        add("放大", lambda: self._zoom(0.8))
        add("缩小", lambda: self._zoom(1.25))
        add("适应全曲", self.fit_all)
        tb.addSeparator()

        tb.addWidget(QLabel(" ODF 变体 "))
        self.variant_box = QComboBox()
        self.variant_box.addItems(["A", "B", "C", "D", "E", "F", "A∩E", "A∩E∩F"])
        self.variant_box.setCurrentText("E")
        self.variant_box.setToolTip(
            "A=现有算法  B=逐bin导数(含重分配)  C=B+maxfilter  "
            "D=不重分配+maxfilter  E=通道A原型(短窗+能量归一)  "
            "F=音高显著性(谐波归组后只跟最响的复音)\n"
            "A∩E / A∩E∩F = 多链路取交集：20 ms 内互相印证的峰才保留，"
            "可大幅压掉过检（见 docs/06-pitfalls.md A16）")
        self.variant_box.currentTextChanged.connect(self._set_variant)
        tb.addWidget(self.variant_box)

        tb.addSeparator()
        self.time_label = QLabel("")
        tb.addWidget(self.time_label)

    # ---------------- 侧栏 ----------------
    def _build_dock(self):
        dock = QDockWidget("视图", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        w = QWidget()
        v = QVBoxLayout(w)

        v.addWidget(QLabel("<b>泳道</b>"))
        for L in self.layers:
            cb = QCheckBox(L.title)
            cb.setChecked(L.visible)
            cb.toggled.connect(lambda on, L=L: self._toggle_layer(L, on))
            v.addWidget(cb)

        v.addSpacing(8)
        v.addWidget(QLabel("<b>重分配频谱模式</b>"))
        self.spec_mode = QComboBox()
        self.spec_mode.addItems(["同步压缩（只搬频率）", "对数重 bin（不重分配）"])
        self.spec_mode.currentIndexChanged.connect(self._set_spec_mode)
        v.addWidget(self.spec_mode)
        v.addWidget(QLabel("两者对比可以看出重分配把能量收得多紧"))

        v.addSpacing(8)
        v.addWidget(QLabel("<b>峰相对阈值（通道 A）</b>"))
        self.rel_gate = QComboBox()
        self.rel_gate.addItems(["关", "0.20", "0.30", "0.40", "0.50"])
        self.rel_gate.setCurrentText("0.30")
        self.rel_gate.currentTextChanged.connect(self._set_rel_gate)
        v.addWidget(self.rel_gate)
        v.addWidget(QLabel("峰值须达到±600ms内最大峰的该比例。\n"
                           "调高可压掉延音的假音头，代价是弱音头会漏"))

        v.addSpacing(8)
        v.addWidget(QLabel("<b>多链路取交集</b>"))
        v.addWidget(QLabel("变体下拉里的 A∩E / A∩E∩F 就是它。\n"
                           "不同统计量对真起音响应一致、对衰减拍频各不相同，\n"
                           "所以 20 ms 内互相印证的峰才保留。\n"
                           "实测误报能少四成，代价是真阳性少两成"))

        v.addSpacing(8)
        v.addWidget(QLabel("<b>音头提示音</b>"))
        self.click_box = QCheckBox("播放时在每个音头处啪一声")
        self.click_box.setChecked(False)
        self.click_box.toggled.connect(self._set_click_sound)
        v.addWidget(self.click_box)
        v.addWidget(QLabel("用**当前变体**的检出去打拍子。听到啪声的时刻\n"
                           "应当和波形上的起音一致；对不上就是检测偏了。\n"
                           "切成 A∩E 再听，能直接听出少了哪些过检"))

        v.addSpacing(8)
        v.addWidget(QLabel("<b>复音数上限（变体 F）</b>"))
        self.top_notes = QComboBox()
        self.top_notes.addItems(["5", "8", "10", "15", "20", "不限"])
        self.top_notes.setCurrentText("10")
        self.top_notes.currentTextChanged.connect(self._set_top_notes)
        v.addWidget(self.top_notes)
        v.addWidget(QLabel("只跟最响的这几个复音。实测 5–10 最稳，\n"
                           "20 以上选择本身会抖（颤音误报飙升）"))

        v.addSpacing(8)
        v.addWidget(QLabel("<b>音头标记上限</b>"))
        self.marker_limit = QComboBox()
        self.marker_limit.addItems(["200", "500", "1000", "2000", "5000"])
        self.marker_limit.setCurrentText("1000")
        self.marker_limit.currentTextChanged.connect(self._set_marker_limit)
        v.addWidget(self.marker_limit)
        v.addWidget(QLabel("超过上限时只显示计数、不画标记，避免卡死"))

        v.addSpacing(8)
        v.addWidget(QLabel("<b>当前音频</b>"))
        self.info = QLabel("(未载入)")
        self.info.setWordWrap(True)
        v.addWidget(self.info)

        v.addSpacing(8)
        v.addWidget(QLabel("<b>提示</b>"))
        tip = QLabel("判定曲线泳道下方的白色短刻度就是检测到的音头；\n"
                     "红色竖线是所有可见泳道的音头投射，用于和波形/频谱对齐。（红线）")
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)

        dock.setWidget(w)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    # ---------------- 载入 ----------------
    def load_path(self, path: str) -> None:
        try:
            samples, sr = load_audio(path)
        except Exception as exc:                     # pragma: no cover
            self.statusBar().showMessage("载入失败：%s" % exc)
            return
        self._adopt(samples, sr, os.path.basename(path))

    def load_demo(self) -> None:
        samples, sr = demo_signal()
        self._adopt(samples, sr, "内置演示（32分 + 颤音 + 连奏）")

    def _adopt(self, samples, sr, label) -> None:
        self.player.load(samples, sr)
        self.analysis = Analysis(samples, sr)
        self.analysis.updated.connect(self._on_analysis_updated)
        self.view.set_total(len(samples) / float(sr))
        self.cur_label = label
        self.canvas.set_analysis(self.analysis)
        # 试图从 RhyDive 曲库自动匹配谱面，作为人工 ground truth 参考
        m = auto_match(label)
        if m is not None:
            try:
                self._set_chart(load_notes(m["path"]), m["name"])
            except Exception:
                pass
        self.statusBar().showMessage("正在后台建立整曲概览…")
        self.info.setText("%s\n采样率 %d Hz\n时长 %.2f s\n样本数 %d"
                          % (label, sr, len(samples) / sr, len(samples)))
        self.canvas.update()
        self._refresh_time()
        self._sync_clicks()
        self.statusBar().showMessage("已载入：%s" % label)

    def open_file(self):
        exts = " ".join("*" + e for e in AUDIO_EXT)
        path, _ = QFileDialog.getOpenFileName(self, "打开音频", "", "音频 (%s)" % exts)
        if path:
            self.load_path(path)

    def _set_chart(self, notes, label):
        self.chart_layer.notes = notes
        self.chart_layer.label = label
        self.canvas.invalidate_lanes()
        self.statusBar().showMessage("已载入谱面参考：%s（%d 个音符）" % (label, len(notes)))

    def open_chart(self):
        path, _ = QFileDialog.getOpenFileName(self, "载入 osu 谱面", "", "osu 谱面 (*.osu)")
        if not path:
            return
        try:
            self._set_chart(load_notes(path), os.path.basename(path))
        except Exception as exc:
            self.statusBar().showMessage("谱面解析失败：%s" % exc)

    def open_library(self):
        dlg = LibraryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen:
            self.load_path(dlg.chosen)

    # ---------------- 播放 ----------------
    def toggle_play(self):
        if self.player.is_playing():
            self.player.stop()
            self.statusBar().showMessage("已暂停")
        else:
            self.player.play(self.view.playhead)
            self.statusBar().showMessage("播放中")

    def stop_playback(self):
        self.player.stop()
        self.view.playhead = 0.0
        self.canvas.update()
        self._refresh_time()

    def _seek(self, t: float):
        self.view.playhead = max(0.0, min(t, self.view.total))
        if self.player.is_playing():
            self.player.play(self.view.playhead)
        self.canvas.update()
        self._refresh_time()

    def _on_position(self, t: float):
        self.view.playhead = t
        self.canvas.update()
        self._refresh_time()

    def _on_finished(self):
        self.statusBar().showMessage("播放结束")

    # ---------------- 视图 ----------------
    def closeEvent(self, ev):
        try:
            if self.analysis is not None:
                self.analysis.close()
            self.player.stop()
        except Exception:
            pass
        super().closeEvent(ev)

    def _on_analysis_updated(self):
        self.canvas.invalidate_lanes()
        self._sync_clicks()          # 音头算完了才推给播放器
        if self.analysis is not None and self.analysis.busy:
            self.statusBar().showMessage("后台计算中… 剩余 %d 项" % self.analysis.busy)
        else:
            self.statusBar().showMessage("就绪：%s" % self.cur_label)

    def refresh_view(self):
        """外部改过 view 之后调用，保证泳道高度与标签同步。"""
        self.canvas._relayout()
        self._refresh_time()

    def _zoom(self, factor):
        self.view.zoom(factor, self.view.playhead)
        self.refresh_view()

    def fit_all(self):
        self.view.t0, self.view.t1 = 0.0, max(1e-3, self.view.total)
        self.refresh_view()

    def _toggle_layer(self, layer, on: bool):
        layer.visible = bool(on)
        self.canvas._relayout()

    def _set_variant(self, text: str):
        for L in self.layers:
            if isinstance(L, OdfLayer):
                L.variant = text
        if self.analysis is not None:
            self.analysis.set_odf_variant(text)
        self.canvas.invalidate_lanes()
        self._sync_clicks()

    # ---------------- 音头提示音 ----------------
    def _set_click_sound(self, on: bool):
        self._sync_clicks()
        if on:
            self.statusBar().showMessage(
                "音头提示音：开（变体 %s，%d 个音头）"
                % (self.variant_box.currentText(), self.player.click_count()))
        else:
            self.statusBar().showMessage("音头提示音：关")

    def _sync_clicks(self):
        """把当前变体的音头时刻推给播放器。

        没算完就推空，等 updated 信号触发时再推一次 —— 不能在这里不推，
        否则切到还没算过的变体会一直沿用上一个变体的拍子。
        """
        if self.analysis is None or not self.click_box.isChecked():
            self.player.set_clicks(())
            return
        t = self.analysis.onset_times(self.variant_box.currentText())
        self.player.set_clicks(() if t is None else t)

    def _set_rel_gate(self, text: str):
        if self.analysis is None:
            return
        try:
            self.analysis.set_a_gate(0.0 if text == "关" else float(text))
        except ValueError:
            pass
        self.canvas.invalidate_lanes()

    def _set_top_notes(self, text: str):
        if self.analysis is None:
            return
        self.analysis.set_f_notes(0 if text == "不限" else int(text))
        self.canvas.invalidate_lanes()

    def _set_marker_limit(self, text: str):
        import gui.layers as L
        try:
            L.MARKER_LIMIT = int(text)
        except ValueError:
            L.MARKER_LIMIT = 1000
        self.canvas.invalidate_lanes()

    def _set_spec_mode(self, idx: int):
        mode = "squeezed" if idx == 0 else "linear"
        for L in self.layers:
            if isinstance(L, ReassignedLayer):
                L.mode = mode
        if self.analysis is not None:
            self.analysis.set_spec_mode(mode)
        self.canvas.invalidate_lanes()

    def _on_cursor(self, t: float):
        self.statusBar().showMessage("t = %.4f s" % t)

    def _refresh_time(self):
        self.time_label.setText("视窗 %.3f – %.3f s ｜ 播放头 %.3f s"
                                % (self.view.t0, self.view.t1, self.view.playhead))
