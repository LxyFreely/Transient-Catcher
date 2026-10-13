"""DSP 基础件：STFT、频率重分配（同步压缩）、对数重 bin、沿频率 max-filter。

全部用 numpy 实现，不依赖 librosa —— 目的是让算法在仓库里完全可见、可移植。
"""

from __future__ import annotations

import numpy as np


def frame(x: np.ndarray, win: int, hop: int) -> np.ndarray:
    """切帧，返回 (n_frames, win)。"""
    n = 1 + max(0, (len(x) - win) // hop)
    if n <= 0:
        raise ValueError("信号太短，切不出一帧")
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def hann(win: int) -> np.ndarray:
    return np.hanning(win)


def hann_derivative(win: int) -> np.ndarray:
    """Hann 窗对样本索引的解析导数。

        w[n]  = 0.5 - 0.5 * cos(2*pi*n/(N-1))
        w'[n] = (pi/(N-1)) * sin(2*pi*n/(N-1))
    """
    n = np.arange(win)
    return (np.pi / (win - 1)) * np.sin(2.0 * np.pi * n / (win - 1))


def stft(x: np.ndarray, win: int, hop: int):
    w = hann(win)
    frames = frame(x, win, hop)
    return np.fft.rfft(frames * w, axis=1), w


def freq_reassigned(x: np.ndarray, sr: int, win: int, hop: int, eps: float = 1e-10):
    """频率重分配（同步压缩）。

    返回 (X, f_hat, bin_freqs)：
      X         复数 STFT，(n_frames, n_bins)
      f_hat     每个时频点的重分配频率（Hz）
      bin_freqs rfft 的原始频率轴

    推导：
      对纯音，STFT 相位满足 d(phase)/dt = -(w - w0)，故瞬时频率 w0 = w + d(phase)/dt。
      又 dX/dt = -X_g'（g' 为窗的导数），于是 d(phase)/dt = -Im(X_g' / X)。
      因此 f_hat = f_bin - Im(X_g' / X) * sr / (2*pi)。

    注意：这里**只重分配频率**，时间仍保持在均匀的帧栅格上。
    全重分配（时间也搬）会破坏帧栅格，无法再做逐帧跟踪 —— 见设计文档的决策 D2。
    """
    w = hann(win)
    dw = hann_derivative(win)
    frames = frame(x, win, hop)
    X = np.fft.rfft(frames * w, axis=1)
    Xd = np.fft.rfft(frames * dw, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = Xd / X
    ratio = np.where(np.abs(X) > eps, ratio, 0.0)
    bin_freqs = np.fft.rfftfreq(win, 1.0 / sr)
    f_hat = bin_freqs[None, :] - np.imag(ratio) * sr / (2.0 * np.pi)
    return X, f_hat, bin_freqs


def log_freq_grid(f_min: float = 55.0, f_max: float = 10000.0,
                  per_semitone: int = 4) -> np.ndarray:
    """对数频率栅格。per_semitone=4 即 1/4 半音。"""
    n_sem = 12.0 * np.log2(f_max / f_min)
    n = int(np.ceil(n_sem * per_semitone)) + 1
    cents = np.arange(n) * 100.0 / per_semitone
    return f_min * 2.0 ** (cents / 1200.0)


def squeeze(X: np.ndarray, f_hat: np.ndarray, out_freqs: np.ndarray,
            rel_threshold: float = 0.0) -> np.ndarray:
    """同步压缩：把每个时频点的能量搬到 f_hat 最近的输出频点上。

    rel_threshold：每帧只保留幅度超过"该帧峰值 * rel_threshold"的 bin。
    这是同步压缩里的标准"压缩阈值"，用来丢掉幅度过小、相位导数不可靠的点
    —— 那些点的 f_hat 基本是噪声，会把能量随机撒到栅格各处，产生假的逐帧抖动。
    """
    mag = np.abs(X)
    n_frames, _ = mag.shape
    n_out = len(out_freqs)
    raw = np.searchsorted(out_freqs, f_hat)
    # 落在栅格之外的能量**直接丢弃**，不要 clip 到首尾 bin。
    # 否则所有低于 f_min（直流、低频噪声）的能量会堆到第 0 个 bin 上，
    # 在频谱图上表现为边缘一条假的亮线 —— 这是实测踩到的。
    keep = (raw > 0) & (raw < n_out)
    idx = np.clip(raw, 0, n_out - 1)
    if rel_threshold > 0.0:
        keep = keep & (mag >= mag.max(axis=1, keepdims=True) * rel_threshold)
    # 一次性 bincount，替代逐帧 np.add.at（实测快一个数量级）
    flat = (np.arange(n_frames, dtype=np.int64)[:, None] * n_out + idx)[keep]
    weights = mag[keep]
    out = np.bincount(flat, weights=weights, minlength=n_frames * n_out)
    return out.reshape(n_frames, n_out)


def rebin_log(spec: np.ndarray, bin_freqs: np.ndarray,
              out_freqs: np.ndarray) -> np.ndarray:
    """把频谱沿对数频率插值到 out_freqs（范围外记 0）。"""
    lo, hi = float(out_freqs[0]), float(out_freqs[-1])
    mask = (bin_freqs >= lo) & (bin_freqs <= hi)
    sub = spec[:, mask]
    lf = np.log2(bin_freqs[mask])
    lg = np.log2(out_freqs)
    # 插值的位置与权重只依赖频率轴，算一次即可，然后整块广播 —— 不再逐帧 np.interp
    pos = np.interp(lg, lf, np.arange(len(lf), dtype=np.float64))
    valid = pos <= (len(lf) - 1)
    i0 = np.clip(np.floor(pos).astype(np.int64), 0, len(lf) - 1)
    i1 = np.clip(i0 + 1, 0, len(lf) - 1)
    w = (pos - i0)[None, :]
    out = sub[:, i0] * (1.0 - w) + sub[:, i1] * w
    if not valid.all():
        out = out.copy()
        out[:, ~valid] = 0.0
    return out


def max_filter_freq(spec: np.ndarray, width: int) -> np.ndarray:
    """沿频率轴做最大值滤波（width 为奇数 bin 数）。

    这是 SuperFlux 的颤音抑制机制：偏分音在自己邻域内游走时不产生通量，
    而宽带瞬态因为能量本来就铺满邻域，几乎不受影响。
    """
    if width <= 1:
        return spec
    half = width // 2
    pad = np.pad(spec, ((0, 0), (half, half)), mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, width, axis=1)
    return win.max(axis=-1)
