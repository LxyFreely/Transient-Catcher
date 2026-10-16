# AGENTS.md

> 你是 AI 编码助手，这个文件是给你的。人类读者请从 [README.md](README.md) 开始。

## 30 秒摘要

这个仓库要给 **RhyDive**（一个没有谱面的音游）提供一个**更好的音符检测器参考实现**。
现有检测器在两类素材上漏检：**密集 32 分钢琴音型**、**变化不大的连奏人声**。
原因已定位：后者是**音高轴**根本没被观测；前者原以为是**输出层的 55 ms 硬下限**，
**但 F22 把这个诊断否掉了** —— 放宽那个间距完全不能恢复召回，真正的瓶颈在频谱分辨率与差分方式。
方案是**两通道**，细节在 [docs/02-design.md](docs/02-design.md)。

**当前状态**：设计与分析完成，P0 验证完成，**通道 A 有原型（变体 E/F，默认 23 ms 窗）**，
**通道 B 尚未实现**（但 F22 已给它定了可量化的目标），
GUI 可用（默认变体 **带2/3∩A** + 谱面参考 + 音头提示音 + 静音抑制），
**有四套评估**（合成 / 真实素材 / 谱面 / MIDI 渲染测试集），外加用户提供的完整 MIDI 真值（F21/F22）。

> ⚠ **必须知道的负面结论**：我们自己的检测器 E 在**所有**有完整真值的素材上都**打不过现有算法 A**
> （完整 MIDI 真值下 F0.5：A 0.693 vs E 0.663）。**我们的贡献不是「更准的检测器」**，
> 而是**「更干净的复核层」**：`带2/3 ∩ A` 把精确率从 65% 提到 **90%**（F0.5 0.849），
> 代价是召回 94% → 69%。见 [05-p0-results](docs/05-p0-results.md) F21 / F22。

> **只关心「能给对方什么」，读 [docs/07-proposal.md](docs/07-proposal.md)。**
> **其余情况读 [docs/00-status.md](docs/00-status.md)。**
> 里面有：代码地图、10 个变体、当前参数、四套评估的关键数字、
> 已知未解决的问题、按优先级排的下一步。

## 建议的阅读顺序

0. **[docs/00-status.md](docs/00-status.md)** —— 现状与交接（**只读一份就读它**）
1. [README.md](README.md) —— 定位与边界
2. [docs/01-analysis.md](docs/01-analysis.md) —— **问题是什么**
3. [docs/02-design.md](docs/02-design.md) —— **方案是什么**（核心）
4. **[docs/06-pitfalls.md](docs/06-pitfalls.md)** —— **改结构前必读**
5. [docs/03-validation.md](docs/03-validation.md) —— 怎么验证 + GUI 规格
6. [docs/05-p0-results.md](docs/05-p0-results.md) —— 全部实测数据 F1–F23
7. **[docs/07-proposal.md](docs/07-proposal.md)** —— **对外的那一份**：给 RhyDive 的提案，
   可直接当 GitHub issue 正文（不含对方任何代码，纯行为描述 + 独立实现）
8. [docs/04-references.md](docs/04-references.md) —— 依据

## 变体速查（GUI 下拉里的 10 项）

6 个基础变体：`A B C D E F`；4 个组合变体：`A∩E`、`A∩E∩F`、`带2/3`、`带2/3∩A`。

| 变体 | 实现 | 定位 |
|---|---|---|
| A | `odf.rhydive_like` | 现有检测器的独立重实现，**只作对照，不是方案** |
| B | `odf.perbin(reassign=True, maxfilt=None)` | 最初设想（1/4 半音 + 重分配 + 逐 bin 导数） |
| C | `odf.perbin(reassign=True, maxfilt=17)` | B + SuperFlux max-filter |
| D | `odf.perbin(reassign=False, maxfilt=17)` | 去掉重分配。P0 里比 B/C 都好 |
| **E** | `odf.channel_a` | **通道 A 原型**（23 ms 窗 + 能量归一化通量 + 相对峰阈值） |
| **F** | `odf.pitch_salience` | **复音层**（谐波归组后再看变化）。**精确率最高** |

B/C/D 是过程产物，保留是为了让 P0 对照可复跑。**真正有价值的是 E 和 F。**

GUI 的变体下拉里还有 **A∩E** 与 **A∩E∩F** —— 多链路取交集（20 ms 内互相印证才保留）。它直接针对**过检**：误报与命中的强度完全相同，任何幅度阈值都分不开，但不同统计量对「衰减 / 拍频」的响应各不相同。实测 A∩E 把误报减四成、真阳性减两成（见 `docs/06-pitfalls.md` A16）。

还有 **带2/3** 与 **带2/3∩A** —— **按频带特化**：同一条 `channel_a` 用低/中/高三条带
各跑一遍，两条以上同意才保留。**这是目前表现最好的一组。**
按 F0.5 选「带2/3 ∩ A」（过检比漏检致命，所以精确权重 4×）：完整 MIDI 真值下
**90% 精确 / 69% 召回，F0.5 = 0.849**（带2/3 单独是 0.806，A 是 0.693）。
试过把 A 调灵敏来换回召回，六个旋钮全试了，**没有一个能超过默认**（F22）。
中频带（600–2500 Hz）单独一个就比全带 E 精确率高 9–25 个点。
**权威率不要调高** —— 3/3 全一致反而更差。

侧栏还有**静音抑制**（默认 0.10）：局部能量低于全曲中位数时按比例扣判定曲线，
专治「音量小、音符稀疏」段落的过检。**别调过 0.15** —— 纯钢琴曲的安静段会掉（F19）。

另外两个检查工具：**音头提示音**（播放时在每个音头处啪一声，用当前变体的检出去打拍子，对不上就是检测偏了）；**音头标记上限**（超上限只显示计数，避免卡死）。

> ⚠ **谱面评估的分辨力很有限**（C8 / F20）：本曲谱面 4.6 音/秒、检测器输出 11.3 个/秒，
> **同密度随机撒点就能拿 73% 召回、30% 精确**。A 与 E 的谱面精确率完全相同（38%）。
> 引用谱面数字时必须连同随机基线一起报（`eval_chart.py` 已自动打印「有效空间」）。

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
docs/                    00-status 到 07-proposal
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
r = odf.channel_a(audio, sample_rate)          # 变体 E
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

- **[docs/06-pitfalls.md](docs/06-pitfalls.md) 是必读的**（DSP 18 条 + GUI 11 条 + 方法论 8 条）。
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
