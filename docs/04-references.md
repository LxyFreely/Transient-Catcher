# 04 · 参考文献

> 标注说明：
> - **【核心】** = 直接决定了本方案某处设计的取舍，改结构前应该读。
> - **【参考】** = 背景知识或有用的实现参考。

---

## 1. 起音检测函数（ODF）与颤音抑制

| 文献 | 说明 | 标记 |
|---|---|---|
| [Maximum Filter Vibrato Suppression for Onset Detection](https://research.jku.at/en/publications/maximum-filter-vibrato-suppression-for-onset-detection/)（Böck & Widmer, DAFx 2013） | **SuperFlux**。沿频率做最大值滤波再求差分，专门抑制颤音造成的假通量。本方案通道 A 的第 3 步直接采用这个机制。 | **【核心】** |
| [DAFx 2013 论文存档](https://dafx.de/paper-archive/2013/papers/09.dafx2013_submission_12.pdf) | 上述论文的 PDF（含阈值设定细节）。 | **【核心】** |
| [aubio 的 specdesc.h](https://browse.dgit.debian.org/aubio.git/plain/src/spectral/specdesc.h) | 一批经典 ODF 的开源实现合集（spectral flux、complex domain、HFC 等），适合对照实现。 | 【参考】 |

## 2. 重分配与同步压缩

| 文献 | 说明 | 标记 |
|---|---|---|
| [Improved Detection of Semi-Percussive Onsets in Audio Using Temporal Reassignment](http://dihana.cps.unizar.es/proceedings/ICASSP/2018/pdfs/0000611.pdf)（ICASSP 2018） | 用**时间重分配**改善半打击性（如钢琴）起音检测。注意：它走的是"竖直能量检测"路线，与本方案的逐帧跟踪路线不同——对应 [02-design.md](02-design.md) 的决策 **D2**。 | **【核心】** |
| [synchrosqueezing 参考实现（MATLAB）](https://raw.githubusercontent.com/ebrevdo/synchrosqueezing/master/synchrosqueezing/synsq_cwt_squeeze.m) | 同步压缩（只重分配频率）的最小实现，适合理解算法骨架。 | **【核心】** |

## 3. 对数频率表示（CQT / VQT）

| 文献 | 说明 | 标记 |
|---|---|---|
| [librosa.vqt](https://librosa.org/doc/latest/api/generated/librosa.vqt.html) | 可变 Q 变换。参数 gamma 用于放松 Q 值，在频率分辨率与时间分辨率之间调和——纯 CQT 在低频会因窗长过长而无法做瞬态检测。 | **【核心】** |
| [Constant-Q Transform for Sound](https://github.com/BrendanJamesLynskey/Constant_Q_Transform_for_Sound) | CQT 的交互式讲解，包含与 FFT 的对比、chromagram、起音检测。适合快速建立直觉。 | 【参考】 |

## 4. 部分音跟踪与正弦建模

| 文献 | 说明 | 标记 |
|---|---|---|
| [Enhancing the Tracking of Partials for the Sinusoidal Modeling of Polyphonic Sounds](https://ieeexplore.ieee.org/document/4244523/) | 多声部信号下的分音跟踪改进。对应本方案通道 B 的**轨迹关联**与**轨道交换**问题。 | **【核心】** |
| [Spectral Modeling Synthesis 工具箱](https://people.csail.mit.edu/kimo/publications/masters/spectralmodelingtoolbox.pdf) | SMS / 正弦建模的经典材料，含峰延续（peak continuation）与出生/消亡处理。 | **【核心】** |
| [SMS 用于语音合成的论文](https://www3.pd.istc.cnr.it/piero/FESTIVAL/docs/G.Sommavilla-SMS_Speech_synth_Thesis.pdf) | 面向语音的 SMS 实践，与人声场景相关。 | 【参考】 |

## 5. 起音与偏移检测

| 文献 | 说明 | 标记 |
|---|---|---|
| [A new method of onset and offset detection in ensemble singing](https://www.tandfonline.com/doi/full/10.1080/14015439.2018.1452977) | **合唱（人声）的起音/偏移检测**——与本项目的弱人声痛点直接相关。 | **【核心】** |
| [Nagi Ahmed, Master Thesis (2010)](https://zenodo.org/records/3752963/files/2010-Nagi-Ahmed-Master-Thesis.pdf) | 起音检测的系统性综述，包含各类特征的对比。 | 【参考】 |

## 6. 神经方法（对照基线，暂不采用）

这些是当前 SOTA，对应 [02-design.md](02-design.md) 的决策 **D5**。
第一版不采用，但如果纯 DSP 方案在效果上不够，它们是明确的退路。

| 方法 | 说明 |
|---|---|
| [madmom](https://github.com/CPJKU/madmom) | 含基于 RNN/CNN 的起音检测函数，是用作**离线对照基线**最方便的选择。 |
| [Onsets and Frames](https://magenta.tensorflow.org/onsets-frames)（Magenta） | 钢琴转录的经典两阶段模型，对"密集钢琴"这个痛点针对性强。 |
| [basic-pitch](https://github.com/spotify/basic-pitch) | 轻量多乐器转录模型，有可部署的产物。 |
| [librosa](https://librosa.org) | 不是模型，但提供 onset_strength、vqt、pyin 等参考实现，适合做算法原型与对照。 |
| [aubio](https://aubio.org) | 轻量 C 实现的起音检测与音高检测，可作为性能与效果的参照。 |

---

## 7. 数值依据速查

本文档与 [02-design.md](02-design.md) 中用到的数字，出处与推导如下，便于复核：

| 数字 | 来源 |
|---|---|
| 颤音速率 4–8 Hz、深度 0.5–1 半音 | 声乐/弦乐颤音的常见取值范围（见第 5 节文献） |
| 1/4 半音 → Q ≈ 69 | 由 Q = 1/(2^(1/48) − 1) 直接计算 |
| 55 Hz 处 1/4 半音 ≈ 0.8 Hz | 55 × (2^(1/48) − 1) |
| 1 kHz 处 1/4 半音 ≈ 14.5 Hz | 1000 × (2^(1/48) − 1) |
| 23 ms 窗 → 频率分辨率 ≈ 43 Hz | 1 / 0.023 |
| 1 kHz 处 43 Hz ≈ 73 音分 | 1200 × log2(1 + 43/1000) |
| 55 Hz–10 kHz 对数等分 24 带 ≈ 3.75 半音/带 | 12 × log2(10000/55) / 24 |
| 160 BPM 的 32 分音符 ≈ 46.9 ms | 60000 / 160 / 8 |
