# AGENTS.md

> 你是 AI 编码助手，这个文件是给你的。人类读者请从 [README.md](README.md) 开始。

## 30 秒摘要

这个仓库要给 **RhyDive**（一个没有谱面的音游）提供一个**更好的音符检测器参考实现**。
现有检测器在两类素材上漏检：**密集 32 分钢琴音型**、**变化不大的连奏人声**。
原因已定位：前者是**时间轴**被硬性限死（输出层 55 ms 硬下限），后者是**音高轴**根本没被观测。
方案是**两通道**，细节在 [docs/02-design.md](docs/02-design.md)。

**当前状态**：设计与分析完成，P0 验证完成，**通道 A 有原型（变体 E/F，默认 23 ms 窗）**，
**通道 B 尚未实现**，GUI 可用（类 DAW 布局 + 谱面参考），
**有四套评估**（合成 / 真实素材 / 谱面 / MIDI 渲染测试集）。

> ⚠ **最新结论对我们不利，必须知道**：在用户提供的真实编曲 `夜航星.mid` 上，
> 现有算法 A（91%/39%）与变体 D 的 F1 **都不低于**我们的 E（74%/36%）。
> 当前实现下"我们的更好"这个前提**不成立**。见 [05-p0-results](docs/05-p0-results.md) F12.6。

> **如果你只有时间读一份文档，读 [docs/00-status.md](docs/00-status.md)。**
> 里面有：代码地图、六个变体、当前参数、四套评估的关键数字、
> 已知未解决的问题、按优先级排的下一步。

## 建议的阅读顺序

0. **[docs/00-status.md](docs/00-status.md)** —— 现状与交接（**只读一份就读它**）
1. [README.md](README.md) —— 定位与边界
2. [docs/01-analysis.md](docs/01-analysis.md) —— **问题是什么**
3. [docs/02-design.md](docs/02-design.md) —— **方案是什么**（核心）
4. **[docs/06-pitfalls.md](docs/06-pitfalls.md)** —— **改结构前必读**
5. [docs/03-validation.md](docs/03-validation.md) —— 怎么验证 + GUI 规格
6. [docs/05-p0-results.md](docs/05-p0-results.md) —— 全部实测数据 F1–F13
7. [docs/04-references.md](docs/04-references.md) —— 依据

## 六个 ODF 变体速查

GUI 工具栏的「ODF 变体」就是切这六个。

| 变体 | 实现 | 定位 |
|---|---|---|
| A | `odf.rhydive_like` | 现有检测器的独立重实现，**只作对照，不是方案** |
| B | `odf.perbin(reassign=True, maxfilt=None)` | 最初设想（1/4 半音 + 重分配 + 逐 bin 导数） |
| C | `odf.perbin(reassign=True, maxfilt=17)` | B + SuperFlux max-filter |
| D | `odf.perbin(reassign=False, maxfilt=17)` | 去掉重分配。P0 里比 B/C 都好 |
| **E** | `odf.channel_a` | **通道 A 原型**（23 ms 窗 + 能量归一化通量 + 相对峰阈值）。**默认** |
| **F** | `odf.pitch_salience` | **复音层**（谐波归组后再看变化）。**精确率最高** |

B/C/D 是过程产物，保留是为了让 P0 对照可复跑。**真正有价值的是 E 和 F。**

GUI 的变体下拉里还有 **A∩E** 与 **A∩E∩F** —— 多链路取交集（20 ms 内互相印证才保留）。它直接针对**过检**：误报与命中的强度完全相同，任何幅度阈值都分不开，但不同统计量对「衰减 / 拍频」的响应各不相同。实测 A∩E 把误报减四成、真阳性减两成（见 `docs/06-pitfalls.md` A16）。

侧栏还有两个检查工具：**音头提示音**（播放时在每个音头处啪一声，用当前变体的检出去打拍子，对不上就是检测偏了）；**音头标记上限**（超上限只显示计数，避免卡死）。

## 四套评估（改任何东西之后都要跑全）

~~~bash
python tests/test_dsp.py          # 4 项自检（重分配的符号与量纲等）
python scripts/p0_synthetic.py    # 合成信号（定结构）
python scripts/real_infinity.py   # 真实素材（定参数）
python scripts/eval_chart.py      # osu 谱面（需要 RhyDive 本地曲库；精确率是下界）
python scripts/compare_configs.py # 横跨三套比配置 —— 不知道默认值该不该改就跑它
~~~

第四套是 **MIDI 渲染测试集**（零外部素材，从零可复现）。它的价值在于：
音频是从真值渲染出来的，**每个发声的音符都在真值里**，所以精确率是**真实数字**，
不再是谱面评估里的下界。代价是音频是合成的（见 [06-pitfalls](docs/06-pitfalls.md) C5）。

~~~bash
python scripts/midi_demo.py --outdir testdata          # 生成示例 MIDI
python scripts/midi_render.py testdata/demo_piano_dense.mid --out testdata/p.wav --voice piano
python scripts/midi_eval.py  --dir testdata             # 评估
python scripts/midi_sweep.py --dir testdata             # 扫参数
~~~

**合成信号定结构，真实素材定参数 —— 两者都不能省**（见 06-pitfalls C1）。

性能相关的另外三个（改 GUI 时跑）：

~~~bash
python scripts/bench_gui.py         # 渲染基准（合成信号）
python scripts/bench_gui_real.py    # 逐档压测（真实歌曲）
python scripts/smoke_gui.py         # 冒烟（不弹窗、不发声）
~~~

## 仓库地图

~~~text
src/transient_catcher/   dsp / odf / signals / osu
tests/                   自检
scripts/                 P0 / 真实素材 / 谱面评估 / 配置对比 / MIDI 测试集 / 基准 / 冒烟
gui/                     类 DAW 查看器（PySide6 + sounddevice）
docs/                    00-status 到 06-pitfalls
testdata/                生成的测试集（可随时删掉重建，不在 git 里）
~~~

## 术语表

| 术语 | 含义 |
|---|---|
| **onset / offset** | 音符事件的起始 / 结束时刻 |
| **ODF** | Onset Detection Function，起音检测函数；峰即候选起音 |
| **spectral flux** | 谱通量：相邻帧幅度谱的正向差分之和，最经典的 ODF |
| **reassignment** | 重分配：把时频点能量搬到相位导出的"真实"位置，锐化时频图 |
| **synchrosqueezing** | 同步压缩：**只重分配频率**，保留均匀时间栅格 |
| **max-filter** | 沿频率轴的最大值滤波，抑制颤音造成的假通量（只滤前一帧！） |
| **partial** | 分音：时频图里一条能量脊线，通常对应一个谐波 |
| **partial tracking** | 部分音跟踪：跨帧关联分音成轨迹（通道 B 的核心，**未实现**） |
| **salience** | 音高显著性：`Σ_k spec[f0 的第 k 次谐波]`。变体 F 用的就是它 |
| **vibrato** | 颤音：f0 的准周期摆动，典型 4–8 Hz、峰峰值 0.5–1 个半音 |
| **拍频 (beating)** | 相邻高次分音的幅度调制，会产生假的"音头"（见 06-pitfalls A9） |

## 接口契约

对外接口（Python）。改动请同步更新本节与 [docs/00-status.md](docs/00-status.md)。

~~~python
from transient_catcher import odf

# RhyDive 只需要 onsets 这一项
r = odf.channel_a(audio, sample_rate)          # 变体 E：默认
# 或
r = odf.pitch_salience(audio, sample_rate)     # 变体 F：精确率更高

r["onsets"]   # [(time_ms, strength), ...]   —— 核心输出
r["env"]      # np.ndarray                    —— 归一化后的判定曲线（可视化用）
r["hop_ms"]   # 帧移
r["win"]      # 窗长（采样点）
~~~

命令行壳与 JSON 输出**尚未实现**；GUI 的 `--shot` 无头模式是目前唯一的批量出口。

## 硬约束

1. **不要引入 RhyDive 的任何代码或资源。** 本仓库必须是独立实现。
2. **不要联网。** 分析全部在本地完成。
3. **不要上传音频。** 任何形式的遥测都不允许。
4. **不要引入 librosa。** STFT / 重分配 / 对数重 bin 全部自己用 numpy 实现，
   为的是让算法在仓库里完全可见、可移植。环境依赖见 `environment.yml`。
5. **必须能离线运行**，且一首 3–5 分钟的歌在几十秒内跑完
   （实测：154 s 的歌，概览构建 1.7 s）。
6. **保持可解释性。** 每个输出事件都应能追溯到原因。这是相对"黑箱模型"的主要卖点。
7. **不要为了跑分牺牲诚实。** 已知局限必须写在文档里。
   本项目的惯例是**把对我们不利的数据也记进 [docs/05-p0-results.md](docs/05-p0-results.md)**，照做。

## 实现时的注意事项

- **[docs/06-pitfalls.md](docs/06-pitfalls.md) 是必读的**（DSP 14 条 + GUI 9 条 + 方法论 6 条）。
  那些不是理论顾虑，是实际付了时间代价的。
- [docs/02-design.md](docs/02-design.md) 里还有一节 **"被否决的方案"（D1–D7）**，同样要读。
- **颤音、拍频、延音涟漪是同一类敌人**（持续音的周期性频谱波动）。
  任何引入更高频率分辨率的改动都会同时放大它们。
  做这类改动前先用 `scripts/p0_synthetic.py` 的 S1 验证。
- **先合成信号，再真实素材。** 跳过这一步会浪费大量时间。
- **改 GUI 前注意**：`analysis` 的输出约定是 **（频率, 时间）**；
  画布 `try/except` 会吞异常，排查时看无头截图模式打印的 `缺数据的泳道=[...]`。

## 与 RhyDive 的关系

- 本仓库**不会**修改 RhyDive，也不提供补丁。
- 目标是提供一个可以被**移植**的算法与接口，由作者自行决定是否采用。
- 最可能的落点是客户端里那条"从音频检测音符"的链路。
