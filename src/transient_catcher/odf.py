"""三种起音检测函数（ODF），用于 P0 对照。

1. rhydive_like : 按 docs/01-analysis.md 的描述**独立重实现**的现有检测器。
                  它的作用是提供可比对照，不是"打擂台"，也不是原项目代码。
2. perbin       : 1/4 半音对数重 bin + 逐 bin 正导数（最初的想法）。
3. perbin_maxf  : 同上，但在求导前先沿频率做 max-filter。
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import maximum_filter1d

from . import dsp

HOP_MS = 10.0
WIN_MS = 23.0
BANDS = 24
F_MIN, F_MAX = 55.0, 10000.0
REGISTERS = [(55.0, 180.0), (180.0, 2500.0), (2500.0, 10000.0)]
REG_GAP_MS = 55.0
MERGE_MS = 30.0
PEAK_FLOOR = 0.15
PEAK_HALF = 3
QUANTILE = 0.9
LOCAL_MS = 500.0


def _win_hop(sr: int, win_ms: float = WIN_MS, hop_ms: float = HOP_MS):
    win = int(2 ** int(round(float(np.log2(sr * win_ms / 1000.0)))))
    hop = int(round(sr * hop_ms / 1000.0))
    return win, hop


def moving_average(x: np.ndarray, half: int) -> np.ndarray:
    """中心滑动平均（窗口 2*half+1，边缘用 edge 补齐）。"""
    n = len(x)
    if half <= 0:
        return x.copy()
    pad = np.pad(x, (half, half), mode="edge")
    c = np.cumsum(np.concatenate([[0.0], pad]))
    win = 2 * half + 1
    return (c[win:win + n] - c[:n]) / win


def adaptive_threshold(flux: np.ndarray, hop_ms: float, floor: float = 1.0,
                       quantile: float = QUANTILE, local_ms: float = LOCAL_MS):
    """自适应阈值：减局部均值 → 取正部 → 分位数归一化。

    与现有检测器一致：分位数低于 floor 时整段清零（全局灵敏度闸门）。
    """
    half = int(round(local_ms / hop_ms))
    env = np.maximum(0.0, flux - moving_average(flux, half))
    pos = env[env > 0.0]
    if pos.size == 0:
        return np.zeros_like(env), 0.0
    a = float(np.quantile(pos, quantile))
    if a < floor:
        return np.zeros_like(env), a
    return np.minimum(2.0, env / a), a


def peak_pick(env: np.ndarray, hop_ms: float, centre_ms: float,
              floor: float = PEAK_FLOOR, half: int = PEAK_HALF,
              min_gap_ms: float = REG_GAP_MS):
    """局部极大值 + 抛物线插值 + 最小间距合并。"""
    out = []
    for i in range(half, len(env) - half):
        v = float(env[i])
        if v < floor:
            continue
        seg = env[i - half:i + half + 1]
        if v < float(seg.max()):
            continue
        if v == float(seg.max()) and int(np.argmax(seg)) != half:
            continue
        a, b, c = float(env[i - 1]), float(env[i]), float(env[i + 1])
        den = a + c - 2.0 * b
        d = 0.5 * (a - c) / den if den != 0.0 else 0.0
        d = min(0.5, max(-0.5, d))
        t = (i + d) * hop_ms + centre_ms
        s = min(1.0, v)
        if out and t - out[-1][0] < min_gap_ms:
            if s > out[-1][1]:
                out[-1] = (t, s)
        else:
            out.append((t, s))
    return out


# --------------------------------------------------------------------------
# A) 现有检测器（独立重实现）
# --------------------------------------------------------------------------
def rhydive_like(x: np.ndarray, sr: int, hop_ms: float = HOP_MS) -> dict:
    win, hop = _win_hop(sr, hop_ms=hop_ms)
    X, _ = dsp.stft(x, win, hop)
    mag = np.abs(X)
    bin_freqs = np.fft.rfftfreq(win, 1.0 / sr)
    n_frames = mag.shape[0]

    edges = F_MIN * (F_MAX / F_MIN) ** (np.arange(BANDS + 1) / BANDS)
    band = np.zeros((n_frames, BANDS))
    counts = np.zeros(BANDS)
    for b in range(BANDS):
        lo = int(np.clip(np.searchsorted(bin_freqs, edges[b]), 0, len(bin_freqs) - 1))
        hi = int(np.clip(np.searchsorted(bin_freqs, edges[b + 1]), lo + 1, len(bin_freqs)))
        band[:, b] = mag[:, lo:hi].sum(axis=1)
        counts[b] = hi - lo
    band = np.log1p(40.0 * band / counts[None, :] / (win / 4.0))

    centers = F_MIN * (F_MAX / F_MIN) ** ((np.arange(BANDS) + 0.5) / BANDS)
    reg = np.full(BANDS, -1, dtype=int)
    for r, (lo_f, hi_f) in enumerate(REGISTERS):
        reg[(centers >= lo_f) & (centers < hi_f)] = r

    flux = np.zeros(n_frames)
    flux[2:] = np.maximum(0.0, band[2:] - band[:-2]).sum(axis=1)

    centre_ms = win / 2.0 / sr * 1000.0
    env_total, _ = adaptive_threshold(flux, hop_ms, floor=1.0)

    onsets = []
    for r in range(len(REGISTERS)):
        cols = np.where(reg == r)[0]
        if cols.size == 0:
            continue
        f = np.zeros(n_frames)
        f[2:] = np.maximum(0.0, band[2:][:, cols] - band[:-2][:, cols]).sum(axis=1)
        env_r, _ = adaptive_threshold(f, hop_ms, floor=float(cols.size) / BANDS)
        onsets.extend(peak_pick(env_r, hop_ms, centre_ms, min_gap_ms=REG_GAP_MS))

    onsets.sort(key=lambda p: (p[0], -p[1]))
    merged = []
    for t, s in onsets:
        if merged and t - merged[-1][0] < MERGE_MS:
            if s > merged[-1][1]:
                merged[-1] = (t, s)
        else:
            merged.append((t, s))

    return {"name": "A 现有算法(重实现)", "env": env_total, "onsets": merged,
            "hop_ms": hop_ms, "win": win}


# --------------------------------------------------------------------------
# E) 通道 A 原型：短窗 + 窄 max-filter + 能量归一化通量 + 全局归一化
# --------------------------------------------------------------------------
def channel_a(x: np.ndarray, sr: int, win_ms: float = 23.0, hop_ms: float = HOP_MS,
              maxfilt: int = 5, per_semitone: int = 4, energy_win_ms: float = 250.0,
              norm_q: float = 0.99, floor: float = 0.20, peak_half: int = 3,
              f_min: float = 65.0, f_max: float = 10000.0,
              rel_win_ms: float = 600.0, rel_gamma: float = 0.30) -> dict:
    """通道 A 原型（P1 的骨干）。

    四个要点，都是拿真实素材（Infinity Heaven 25–35 s 的 32 分琶音）实测出来的：

    1. **窗要短，但不要最短：23 ms**（原来是 16 ms，见 docs/05-p0-results.md F13）。
       46 ms 的窗和 32 分音符（46.875 ms）同量级，相邻音符在频谱上被抹平；
       但 16 ms 又太短 —— 512 点窗的 bin 间距有 86 Hz，低音区谐波的旁瓣互相重叠，
       会让**完全稳态**的长音也逐帧起伏（06-pitfalls A12）。
       实测 23 ms 在三套评估上**全面不劣于** 16 ms，而且把 S2 音高台阶
       从"检出 8 个"修正到"检出 1 个"（正确答案）。
    2. **max-filter 要窄（5 格 ≈ 1.25 半音）**。原来 17 格 = 4.25 半音，
       琶音的音程落在窗口内，会被当成"能量本来就在那儿"而抑制掉。
       实测：安静段通量从 98 涨到 432（4.4 倍）。
    3. **用能量归一化通量**：flux / 局部能量。起音强度应当是"相对当前能量变化了多少"，
       这样响亮段与安静段才可比 —— 原方案只在这里失守（安静段通量 98 vs 活跃段 982）。
    4. **全局归一化，不要局部拉伸**。这一条最关键：
       局部归一化是尺度无关的，会把稳定长音的数值涟漪也拉成"信号"（实测炸出 38 个事件）。
       而 flux/能量 的**绝对水平**在真实音乐（中位 1.4e-1）和稳定长音（1.2e-2）
       之间差一个数量级 —— 全局归一化正好保留了这个区别。

    5. **相对峰阈值（rel_gamma）**：峰值还要达到"局部最大峰"的 rel_gamma 倍。
       动因是真实歌曲的 0.70–1.10 s：一个延音被检成 6 个音头。
       诊断显示根因是**钢琴高次分音的拍频**（3–8 kHz 包络只有 ±30% 的涟漪，
       没有真正的回落），而 ODF 把它放大了。六个峰的强度是
       3.83 / 0.98 / 0.72 / 0.64 / 0.79 / 0.67 —— **真起音比其余大 4–6 倍**。
       绝对下限分不开它们（A 段与 B 段的中位数几乎相同），
       但"相对 ±600 ms 内的最大峰"可以：A 段 6 → 1，同时 S1/S0 不退化。
       代价是 B 段的召回从 3 掉到 2 —— 这个取舍是本质的，不是调参能绕开的。
    """
    win = max(128, int(2 ** int(round(float(np.log2(sr * win_ms / 1000.0))))))
    hop = max(1, int(round(sr * hop_ms / 1000.0)))
    X, _ = dsp.stft(x, win, hop)
    bin_freqs = np.fft.rfftfreq(win, 1.0 / sr)
    grid = dsp.log_freq_grid(f_min, f_max, per_semitone)
    spec = dsp.rebin_log(np.abs(X), bin_freqs, grid)
    if maxfilt and maxfilt > 1:
        spec = dsp.max_filter_freq(spec, maxfilt)

    d = np.zeros_like(spec)
    d[1:] = np.maximum(0.0, spec[1:] - spec[:-1])
    flux = d.sum(axis=1)
    energy = spec.sum(axis=1)

    half = max(1, int(round(energy_win_ms / hop_ms)))
    e_local = np.maximum(moving_average(energy, half),
                         float(energy.max()) * 1e-4 if energy.size else 1.0)
    rel = flux / np.maximum(1e-12, e_local)
    scale = float(np.quantile(rel, norm_q)) if rel.size else 1.0
    env = rel / max(1e-12, scale)

    if rel_gamma > 0.0 and env.size:
        w = max(1, int(round(rel_win_ms / hop_ms)))
        ref = maximum_filter1d(env, size=2 * w + 1, mode="nearest")
        env = np.where(env > rel_gamma * ref, env, 0.0)

    onsets = peak_pick(env, hop_ms, win / 2.0 / sr * 1000.0, floor=floor,
                       half=peak_half, min_gap_ms=0.0)
    return {"name": "E 通道A(短窗+能量归一)", "env": env, "onsets": onsets,
            "hop_ms": hop_ms, "win": win, "flux": flux, "rel": rel}


# --------------------------------------------------------------------------
# F) 音高显著性通量：先把谐波归组成"复音"，再在复音层看变化
# --------------------------------------------------------------------------
def pitch_salience(x: np.ndarray, sr: int, win_ms: float = 16.0, hop_ms: float = HOP_MS,
                   n_harm: int = 8, top_notes: int = 10, per_semitone: int = 4,
                   maxfilt: int = 5, f0_lo: float = 55.0, f0_hi: float = 1200.0,
                   rel_win_ms: float = 600.0, rel_gamma: float = 0.30,
                   floor: float = 0.20, peak_half: int = 3,
                   energy_win_ms: float = 250.0, norm_q: float = 0.99) -> dict:
    """通道 A 的"复音层"变体。

    动因（实测）：**只对主要分量做分析**这个直觉本身是对的，但直接"取前 K 个 bin"
    是错的 —— 那是**谐波**不是**复音**（一个钢琴音就有十几个谐波），
    而且硬性 top-K 的集合每帧进出，会凭空造出通量（实测 top-20 把 S1 颤音误报顶到 9.7/s）。
    三种"只取显著分量"的写法（top-K / 相对门限 / 谱峰掩码）**全部让精确率变差**，
    因为它们丢掉的正是那个在起平均作用的"底"。

    正确的形式是**先做谐波归组**：
        salience[f0] = Σ_{k=1..n_harm} spec[f0 的第 k 次谐波]
    这样单个分音的拍频会在求和里互相抵消，而真正的换音会让 salience 的峰整体移动。

    实测（谱面精确率，下界）：
        频谱通量基线                召回 93%  精确 36%
        显著性（8 谐波）            召回 88%  精确 **45%**
        显著性 + 取最响 10 个复音    召回 92%  精确 40%   （B 段召回 2 → 4）
        显著性 + 取最响 20 个复音    召回 90%  精确 40%   （S1 崩溃到 12.7/s）

    **top_notes 不要超过 ~10**：再往上纳入的都是弱分量，选择本身开始不稳定。
    """
    win = max(128, int(2 ** int(round(float(np.log2(sr * win_ms / 1000.0))))))
    hop = max(1, int(round(sr * hop_ms / 1000.0)))
    X, _ = dsp.stft(x, win, hop)
    bin_freqs = np.fft.rfftfreq(win, 1.0 / sr)
    grid = dsp.log_freq_grid(65.0, 10000.0, per_semitone)
    spec = dsp.rebin_log(np.abs(X), bin_freqs, grid)
    if maxfilt and maxfilt > 1:
        spec = dsp.max_filter_freq(spec, maxfilt)

    # --- 谐波归组 ---
    i0 = int(np.searchsorted(grid, f0_lo))
    i1 = int(np.searchsorted(grid, f0_hi))
    n_f0 = max(1, i1 - i0)
    sal = np.zeros((spec.shape[0], n_f0))
    for k in range(1, max(1, n_harm) + 1):
        off = int(round(per_semitone * 12 * np.log2(k)))
        lo = i0 + off
        if lo >= spec.shape[1]:
            break
        hi = min(i1 + off, spec.shape[1])
        sal[:, :hi - lo] += spec[:, lo:hi]

    # --- 只保留最响的若干个复音 ---
    if top_notes and top_notes > 0:
        k = min(top_notes, sal.shape[1])
        idx = np.argpartition(-sal, k - 1, axis=1)[:, :k]
        keep = np.zeros_like(sal, dtype=bool)
        np.put_along_axis(keep, idx, True, axis=1)
        sal = np.where(keep, sal, 0.0)

    d = np.zeros_like(sal)
    d[1:] = np.maximum(0.0, sal[1:] - sal[:-1])
    flux = d.sum(axis=1)
    energy = sal.sum(axis=1)
    hl = max(1, int(round(energy_win_ms / hop_ms)))
    el = np.maximum(moving_average(energy, hl),
                    float(energy.max()) * 1e-4 if energy.size else 1.0)
    rel = flux / np.maximum(1e-12, el)
    scale = float(np.quantile(rel, norm_q)) if rel.size else 1.0
    env = rel / max(1e-12, scale)
    if rel_gamma > 0.0 and env.size:
        w = max(1, int(round(rel_win_ms / hop_ms)))
        env = np.where(env > rel_gamma * maximum_filter1d(env, 2 * w + 1, mode="nearest"),
                       env, 0.0)

    onsets = peak_pick(env, hop_ms, win / 2.0 / sr * 1000.0, floor=floor,
                       half=peak_half, min_gap_ms=0.0)
    return {"name": "F 音高显著性(%d谐波/取%d复音)" % (n_harm, top_notes),
            "env": env, "onsets": onsets, "hop_ms": hop_ms, "win": win,
            "flux": flux, "rel": rel, "f0_grid": grid[i0:i1]}


# --------------------------------------------------------------------------
# B/C) 1/4 半音对数重 bin + 逐 bin 正导数
# --------------------------------------------------------------------------
def perbin(x: np.ndarray, sr: int, per_semitone: int = 4, win_ms: float = 46.0,
           use_reassign: bool = True, maxfilt: int | None = None,
           sq_thresh: float = 0.0, hop_ms: float = HOP_MS,
           peak_half: int | None = None) -> dict:
    win, hop = _win_hop(sr, win_ms=win_ms, hop_ms=hop_ms)
    grid = dsp.log_freq_grid(F_MIN, F_MAX, per_semitone)
    X, _ = dsp.stft(x, win, hop)
    bin_freqs = np.fft.rfftfreq(win, 1.0 / sr)
    if use_reassign:
        X, f_hat, bin_freqs = dsp.freq_reassigned(x, sr, win, hop)
        spec = dsp.squeeze(X, f_hat, grid, rel_threshold=sq_thresh)
    else:
        spec = dsp.rebin_log(np.abs(X), bin_freqs, grid)

    label = "B 逐 bin 导数(1/4 半音)"
    d = np.zeros_like(spec)
    if maxfilt:
        # SuperFlux 的正确形式：**只对前一帧**沿频率做最大值滤波，当前帧保持原样。
        # 如果两帧都滤波，移动中的脊线会在"膨胀边界"上产生假的通量 —— 这是实测踩到的坑。
        prev_max = dsp.max_filter_freq(spec, maxfilt)[:-1]
        d[1:] = np.maximum(0.0, spec[1:] - prev_max)
        label = "C B + SuperFlux max-filter(%d)" % maxfilt
    else:
        d[1:] = np.maximum(0.0, spec[1:] - spec[:-1])
    flux = d.sum(axis=1)

    centre_ms = win / 2.0 / sr * 1000.0
    env, _ = adaptive_threshold(flux, hop_ms, floor=1.0)
    onsets = peak_pick(env, hop_ms, centre_ms, min_gap_ms=0.0,
                       half=(peak_half if peak_half else PEAK_HALF))

    return {"name": label, "env": env, "onsets": onsets, "hop_ms": hop_ms,
            "win": win, "grid": grid, "spec": spec, "flux": flux}
