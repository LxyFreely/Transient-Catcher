# AGENTS.md

> 你是 AI 编码助手，这个文件是给你的。
> 人类读者请从 [README.md](README.md) 开始。

## 30 秒摘要

这个仓库要给 **RhyDive**（一个没有谱面的音游）提供一个**更好的音符检测器参考实现**。
现有检测器在两类素材上漏检：**密集 32 分钢琴音型**、**变化不大的连奏人声**。
原因已经定位清楚：前者是**时间轴**被硬性限死，后者是**音高轴**根本没被观测。
方案是**两通道 + 事件层融合**，细节在 [docs/02-design.md](docs/02-design.md)。

**当前代码尚未实现。你现在读到的是设计与分析文档。**

## 建议的阅读顺序

1. [README.md](README.md) —— 定位与边界
2. [docs/01-analysis.md](docs/01-analysis.md) —— **问题是什么**（先读这个，否则会看不懂设计里的取舍）
3. [docs/02-design.md](docs/02-design.md) —— **方案是什么**（核心）
4. [docs/03-validation.md](docs/03-validation.md) —— **怎么证明它有用**
5. [docs/04-references.md](docs/04-references.md) —— 依据

## 术语表

| 术语 | 含义 |
|---|---|
| **onset** | 音符/声音事件的起始时刻 |
| **offset** | 音符事件的结束时刻 |
| **ODF** | Onset Detection Function，起音检测函数；一条随时间变化的曲线，峰值即候选起音 |
| **novelty** | 新颖度函数，常与 ODF 混用 |
| **spectral flux** | 谱通量：相邻帧幅度谱的正向差分之和，最经典的 ODF |
| **reassignment** | 重分配：把时频点的能量搬到由相位导出的"真实"位置，锐化时频图 |
| **synchrosqueezing** | 同步压缩：**只重分配频率**、保留均匀时间栅格的可逆版本 |
| **partial** | 部分音/分音：时频图里一条连续的能量脊线，通常对应一个谐波 |
| **partial tracking** | 部分音跟踪：跨帧把分音关联成轨迹，处理出生/消亡/迁移 |
| **blob** | 团块：重分配后能量聚集的连通域，自带质心、总能量、频率跨度 |
| **CQT / VQT** | 常数 Q 变换 / 可变 Q 变换：对数频率、每个频带用不同窗长 |
| **max-filter** | 沿频率轴的最大值滤波，用于抑制颤音造成的假通量 |
| **vibrato** | 颤音：f0 的准周期摆动，典型 4–8 Hz、峰峰值 0.5–1 个半音 |
| **NMS** | 非极大抑制：合并时间上过近的重复事件 |

## 接口契约

检测器的对外接口设计如下（实现时以此为准，改动请同步更新本节）：

~~~ts
// 最小可用形态：与 RhyDive 现有消费方兼容
detect(audio: Float32Array, sampleRate: number, options?: DetectOptions): DetectResult;

interface DetectOptions {
  hopMs?: number;            // 默认 10
  computeNotes?: boolean;    // 是否做音符级分割（较慢）
  computeCurves?: boolean;   // 是否返回各通道曲线（GUI 用）
  sensitivity?: number;      // 单一全局灵敏度旋钮，内部映射到各通道阈值
}

interface DetectResult {
  hopMs: number;
  // 核心输出：RhyDive 只需要这一项
  onsets: { timeMs: number; strength: number }[];
  // 可选：音符级结果（含音高）
  notes?: { startMs: number; endMs: number; f0Hz: number; strength: number }[];
  // 可选：可视化用
  curves?: {
    transient: Float32Array;   // 通道 A
    spectral: Float32Array;    // 通道 B
    fused: Float32Array;       // 融合后
  };
}
~~~

命令行壳：

~~~bash
node bin/detect.mjs <audio.wav> [-o notes.json] [--notes] [--curves]
~~~

输出 JSON 顶层结构必须与 `DetectResult` 一致，便于脚本断言。

## 硬约束

设计或实现时请遵守：

1. **不要引入 RhyDive 的任何代码或资源。** 本仓库必须是独立实现。
2. **不要联网。** 分析全部在本地完成。
3. **不要上传音频。** 任何形式的遥测都不允许。
4. **依赖越少越好。** 理想情况下 GUI 是单个 HTML 文件，实现无第三方运行时依赖。
5. **必须能离线运行**，且在普通笔记本上对一首 3–5 分钟的歌在几十秒内跑完。
6. **保持可解释性。** 每个输出事件都应能追溯到产生它的通道与原因（瞬态 / 出生 / 迁移 / 再击）。
   这是本项目相对"黑箱模型"的主要卖点之一，不要在实现中丢掉标签。
7. **不要为了跑分牺牲诚实。** 已知局限必须写在文档里，不要在演示里回避
   （例如"同音连奏无间隙"在物理上不可分）。

## 实现时的注意事项

- [docs/02-design.md](docs/02-design.md) 里有一节 **"被否决的方案"**。
  那些不是随手写的，是踩过的坑。**在改动核心结构前请先读那一节。**
- **颤音是本项目最大的敌人。** 任何引入更高频率分辨率的改动，都会同时放大颤音误报。
  做这类改动前，请先用 [docs/03-validation.md](docs/03-validation.md) 里的合成颤音信号验证。
- **先做合成信号验证，再上真实素材。** 合成信号有 ground truth，能把"算法对不对"
  和"参数调没调好"分开。跳过这一步会浪费大量时间。
- 现有检测器的**输出层有一个 55 ms 的硬下限**，这是 32 分漏检的直接原因。
  任何方案都必须先确认自己能输出 46.9 ms 间隔的独立事件。

## 与 RhyDive 的关系

- 本仓库**不会**修改 RhyDive，也不提供补丁。
- 目标是提供一个可以被**移植**的算法与接口，由 RhyDive 作者自行决定是否采用。
- 如果最终要接进去，最可能的落点是客户端里那条"从音频检测音符"的链路。
