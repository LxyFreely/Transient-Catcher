# Transient Catcher

**面向 RhyDive 的音符瞬态检测器 —— 参考实现**

> An independent, experimental reference implementation of a note / transient detector
> for **RhyDive**, a chart-less rhythm game.
> This repository is a **suggestion**, not a patch: it contains no RhyDive code.

---

## 为什么会有这个仓库

RhyDive 是一款没有谱面的娱乐性音游：玩家对着歌曲的节奏敲击，敲得越准得分越高，
音符由客户端**直接从音频里检测**出来。

这条检测链路在两类素材上会明显漏检：

1. **密集的 32 分钢琴音型** —— 快速跑动的音阶或琶音，本该敲的地方没有音符；
2. **变化幅度不大的人声** —— 连奏、平缓的旋律线，换音时检测不到。

本仓库的意图很单纯：把一套我们认为能改善这两点的检测方案，
写成**可运行的代码 + 可验证的说明**，交给 RhyDive 的作者自行取用。
合适就拿走，不合适也希望能提供一点思路。

配套有一个**类音频编辑软件布局的查看器**（PySide6）：可以把任意音频、
或者直接把自己 RhyDive 曲库里的歌载进来，在同一根时间轴上对齐查看
波形、频谱、重分配频谱与判定曲线，滚轮缩放、拖动平移。

![GUI 概览](docs/images/gui-overview.png)

## 定位与边界（请先读）

- 这是**参考实现 / 示例项目**，**不是**补丁，也不是 fork。
- 仓库**不包含、不依赖** RhyDive 的任何代码或资源。
- 对现有检测器行为的描述，仅用于说明"问题出在哪"，属于对已发布客户端的
  行为观察与独立分析，不构成对其代码的再分发。
- 全程离线：不联网、不上传任何音频。
- 许可证：**MIT**，见 [LICENSE](LICENSE)。

## 读者指引

| 你是 | 从这里开始 |
|---|---|
| **想直接拿去用 / 提 issue** | **[docs/07-proposal.md](docs/07-proposal.md)** —— 面向 RhyDive 作者的提案，可直接当 issue 正文 |
| **刚接手 / 上下文被压缩过** | **[docs/00-status.md](docs/00-status.md)** —— 现状、参数、数字、待办，**读这一份就够** |
| **要改核心结构** | **[docs/06-pitfalls.md](docs/06-pitfalls.md)** —— 先读，每条都是付过时间代价的 |
| **AI 编码助手** | [AGENTS.md](AGENTS.md) —— 阅读顺序、术语表、接口契约、硬约束 |
| 想看问题定义 | [docs/01-analysis.md](docs/01-analysis.md) |
| 想看方案 | [docs/02-design.md](docs/02-design.md) |
| 想自己验证 | [docs/03-validation.md](docs/03-validation.md) |
| 想看全部实测数据 | [docs/05-p0-results.md](docs/05-p0-results.md)（F1–F18） |
| 想看文献依据 | [docs/04-references.md](docs/04-references.md) |

## 当前状态

| 阶段 | 状态 | 产物 |
|---|---|---|
| 问题界定 | 完成 | 本文件 |
| 现有检测器行为分析 | 完成 | [docs/01-analysis.md](docs/01-analysis.md) |
| 新检测器设计 | 完成 | [docs/02-design.md](docs/02-design.md) |
| 验证与 GUI 计划 | 完成 | [docs/03-validation.md](docs/03-validation.md) |
| 参考文献 | 完成 | [docs/04-references.md](docs/04-references.md) |
| **P0 · 合成信号对照** | **完成** | [scripts/p0_synthetic.py](scripts/p0_synthetic.py) · 结果与结论 [docs/05-p0-results.md](docs/05-p0-results.md) |
| P1 · 通道 A（瞬态） | 未开始 | — |
| P2 · 通道 B（谱峰追踪） | 未开始 | — |
| P3 · 融合 | 未开始 | — |
| **P4 · 测试 GUI** | **可用** | [gui/](gui/) · 规格 [docs/03-validation.md](docs/03-validation.md) 第 4 节 |
| **真实素材回归** | **已暴露关键缺陷** | [scripts/real_infinity.py](scripts/real_infinity.py) · 结论 [docs/05-p0-results.md](docs/05-p0-results.md) 第 8 节 |
| **谱面参照评估** | 完成（召回 93%，精确率是下界） | [scripts/eval_chart.py](scripts/eval_chart.py) · 结论第 10 节 |
| **MIDI 渲染测试集** | **可用（精确率是真实数字）** | [scripts/midi_render.py](scripts/midi_render.py) · [midi_eval.py](scripts/midi_eval.py) · 结论第 12 节 |
| P1 · 通道 A | 原型可用（变体 E） | `odf.channel_a()`，安静段 0 → 2 个音头 |

## 设计一句话

> 不要试图用**一张图**同时抓住瞬态和音高。
>
> 现有检测器的两个失败，分别落在**时间轴**和**音高轴**上。
> 把频率做细去救时间轴上的问题，只会换来颤音的灾难。
> 正确做法是用**两条通道**分别解决，再在**事件层**融合。

推导过程见 [docs/01-analysis.md](docs/01-analysis.md)，
方案见 [docs/02-design.md](docs/02-design.md)。

## 目录结构

~~~text
Transient Catcher/
├── README.md                    # 本文件
├── AGENTS.md                    # 给 AI 助手的入口
├── LICENSE                      # MIT
├── environment.yml              # conda 环境
├── docs/
│   ├── 00-status.md             # ★ 现状与交接（读这一份就够）
│   ├── 01-analysis.md           # 现有检测器行为分析
│   ├── 02-design.md             # 新检测器设计（核心）
│   ├── 03-validation.md         # 验证计划与 GUI 规格
│   ├── 04-references.md         # 参考文献
│   ├── 05-p0-results.md         # 全部实测结果 F1–F18（含对我们不利的数据）
│   ├── 06-pitfalls.md           # ★ 踩过的坑（DSP 17 条 + GUI 9 条 + 方法论 7 条）
│   └── 07-proposal.md           # ★ 给 RhyDive 的提案（可直接当 issue 正文）
├── src/transient_catcher/       # 检测器
│   ├── signals.py               # 合成测试信号（P0 用）
│   ├── dsp.py                   # STFT / 频率重分配 / 对数重 bin / max-filter
│   └── odf.py                   # 四种 ODF + 峰值挑选
├── scripts/
│   ├── p0_synthetic.py          # P0：合成信号四曲线对照
│   ├── real_infinity.py         # 真实素材回归（32 分琶音漏检）
│   ├── eval_chart.py            # 用 osu 谱面当人工 ground truth 做全曲评估
│   ├── eval_dense.py            # ★ 在含真正密集音符的谱面上评估（检验项目前提）
│   ├── compare_configs.py       # 横跨三套评估比较配置（改默认值前跑）
│   ├── midi_demo.py             # 生成示例 MIDI（零外部素材）
│   ├── midi_render.py           # MIDI -> WAV + 精确真值（三种音色 + GM 打击乐）
│   ├── midi_eval.py             # 用 MIDI 真值评估（精确率是真实数字）
│   ├── midi_sweep.py            # 在 MIDI 测试集上扫 channel_a 参数
│   ├── exp_overlap.py           # overlap 与时间精度实验
│   ├── bench_gui.py             # GUI 渲染路径基准（合成信号）
│   ├── bench_gui_real.py        # GUI 逐档压测（真实歌曲，含全曲视野）
│   ├── smoke_gui.py             # GUI 冒烟测试（不弹窗）
│   └── sweep_maxfilter.py       # max-filter 宽度扫描 / 重分配对照
├── gui/                         # 类 DAW 布局的查看器（PySide6）
│   ├── main.py                  # 入口（含无头截图模式）
│   ├── window.py                # 主窗口 / 工具栏 / 侧栏 / 曲库导入
│   ├── canvas.py                # 泳道堆叠 + 共享时间轴 + 交互
│   ├── layers.py                # 波形 / 频谱 / 重分配频谱 / 判定曲线
│   ├── analysis.py              # 按需分析 + 视窗缓存
│   ├── player.py                # sounddevice 播放与播放头
│   └── audio_io.py              # 音频载入 + RhyDive 曲库扫描
├── tests/
│   └── test_dsp.py              # DSP 自检（重分配符号与量纲）
├── testdata/                    # MIDI 测试集（可随时删掉重建，不入库）
└── out/                         # 跑出来的图与指标（不入库）
~~~

## 用法

~~~bash
# 一次性准备环境。
# 如果 base 里已经有 numpy / scipy / matplotlib / numba / PySide6，可以跳过这步。
conda env create -f environment.yml
conda activate transient-catcher

# P0：合成信号对照。产出 out/p0_synthetic.png 和一张指标表。
python scripts/p0_synthetic.py

# 真实素材回归（需要 Downloads 里那首 flac；没有会提示）
python scripts/real_infinity.py

# 打开查看器（类音频编辑软件布局）
python gui/main.py
python gui/main.py --demo                       # 载入内置演示信号
python gui/main.py --audio "某首歌.mp3"          # 直接载入音频
python gui/main.py --demo --shot out.png --t0 0.22 --t1 0.42   # 无头出图（回归自检）
~~~

## 技术路线

主实现用 **Python**（numpy / scipy），GUI 用 **PySide6**。理由见
[docs/02-design.md](docs/02-design.md) 的"技术栈"一节：

- **分析是全离线的，性能不是瓶颈。** numpy / scipy 的 FFT 本身就是预编译的
  C / Fortran，我们真正自己写的只剩"轨迹关联"这类循环，而 numba 可以把它 JIT 掉。
- **重分配频谱的显示**是本项目 GUI 的核心，Python 侧做这种自定义渲染更直接，
  而且能按可见时间窗**按需计算**，不必一次性算完整首歌。
- RhyDive 侧是 Electron + Web Audio，所以本仓库**不以"可直接 drop-in"为目标**，
  而是以**算法可读、可复现、可移植**为目标。移植说明见
  [docs/02-design.md](docs/02-design.md) 第 10 节。
