"""合成测试信号（P0 用）。

设计原则：每个信号都带 ground truth，以及（可选）一个"误报统计区间"。
这样才能把"算法结构对不对"和"参数调没调好"分开。

所有信号都是单声道、44.1 kHz、float64，幅度大致在 [-1, 1]。
"""

from __future__ import annotations

import numpy as np

SR = 44100


def _harmonic(f0_track: np.ndarray, sr: int, n_harm: int = 10) -> np.ndarray:
    """给定逐样本 f0 轨迹，合成谐波堆。

    用 cumsum 累积相位，因此频率变化时相位连续 —— 这是"音高变化但没有瞬态"
    的关键，也是 S2 / S5 能测出"纯能量类检测器看不见音高"的原因。
    """
    n = len(f0_track)
    phase = 2.0 * np.pi * np.cumsum(f0_track) / sr
    out = np.zeros(n)
    nyq = sr / 2.0
    f_max = float(np.max(f0_track))
    for k in range(1, n_harm + 1):
        if k * f_max >= nyq:
            break
        out += np.sin(k * phase) / k
    return out


def _pack(name, samples, sr, truth_ms, expect, fp_region_ms=None, note="",
          level: float = 0.7):
    samples = np.asarray(samples, dtype=np.float64)
    peak = float(np.max(np.abs(samples))) or 1.0
    return {
        "name": name,
        "note": note,
        "samples": samples / peak * level,
        "sr": sr,
        "truth_ms": [float(v) for v in truth_ms],
        "expect": int(expect),
        "fp_region_ms": fp_region_ms,
    }


def s1_vibrato(sr: int = SR, dur: float = 2.5, f0: float = 220.0,
               rate: float = 6.0, depth_semitone: float = 0.5,
               onset: float = 0.30) -> dict:
    """S1 颤音长音：6 Hz、峰峰值 1 半音。

    稳态段（fp_region_ms）里的任何检测都是误报。
    这个信号用来证明"逐 bin 求导会把颤音变成一堆音符"。
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    vib = depth_semitone * np.sin(2.0 * np.pi * rate * t)
    f0_track = f0 * 2.0 ** (vib / 12.0)
    sig = _harmonic(f0_track, sr, n_harm=10)
    head = int(onset * sr)
    env = np.ones(n)
    env[:head] = 0.0
    ramp = max(1, int(0.06 * sr))
    env[head:head + ramp] = np.linspace(0.0, 1.0, ramp)
    tail = max(1, int(0.20 * sr))
    env[-tail:] *= np.linspace(1.0, 0.0, tail)
    sig = sig * env
    return _pack("S1 颤音长音", sig, sr, truth_ms=[onset * 1000.0], expect=1,
                 fp_region_ms=(600.0, dur * 1000.0 - 250.0),
                 note="6 Hz / 峰峰 1 半音；红区为稳态段，任何检测都是误报")


def s2_step(sr: int = SR, dur: float = 2.0, f0_a: float = 220.0,
            semitones: float = 3.0, step_s: float = 1.0) -> dict:
    """S2 音高台阶：相位连续，无任何能量瞬态。

    用来测"只靠能量的检测器能不能看见音高变化"——预期是看不见。
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    f0_b = f0_a * 2.0 ** (semitones / 12.0)
    f0_track = np.where(t < step_s, f0_a, f0_b)
    sig = _harmonic(f0_track, sr, n_harm=10)
    ramp_in = max(1, int(0.02 * sr))
    tail = max(1, int(0.20 * sr))
    env = np.ones(n)
    env[:ramp_in] = np.linspace(0.0, 1.0, ramp_in)
    env[-tail:] *= np.linspace(1.0, 0.0, tail)
    return _pack("S2 音高台阶", sig * env, sr, truth_ms=[step_s * 1000.0], expect=1,
                 note="+3 半音、相位连续、无能量瞬态")


def s3_rearticulation(sr: int = SR, f0: float = 220.0,
                      gaps_ms=(100.0, 60.0, 47.0), start_s: float = 0.25) -> dict:
    """S3 同音反复：间隔 100 / 60 / 47 ms。

    47 ms 这一档直接低于现有检测器 55 ms 的最小间距。
    """
    total = start_s + sum(gaps_ms) / 1000.0 + 0.6
    n = int(total * sr)
    sig = np.zeros(n)
    times = []
    t_cur = start_s
    for g in gaps_ms:
        times.append(t_cur)
        g_s = g / 1000.0
        dur = g_s * 0.72
        m = max(2, int(dur * sr))
        tt = np.arange(m) / sr
        env = np.exp(-tt / (g_s * 0.20))
        atk = max(1, int(0.002 * sr))
        env[:atk] *= np.linspace(0.0, 1.0, atk)
        seg = _harmonic(np.full(m, f0), sr, n_harm=8) * env
        i0 = int(t_cur * sr)
        sig[i0:i0 + m] += seg
        t_cur += g_s
    times.append(t_cur)
    return _pack("S3 同音反复", sig, sr, truth_ms=[v * 1000.0 for v in times],
                 expect=len(times), note="音高相同，间隔 100 / 60 / 47 ms")


def s4_dense32(sr: int = SR, bpm: float = 160.0, count: int = 8,
               f0_base: float = 261.63) -> dict:
    """S4 密集 32 分钢琴音型：BPM 160 → 间隔 46.875 ms。

    现有检测器在 55 ms 处合并，这一档必然被吃掉。
    """
    step = 60.0 / bpm / 8.0
    lead = 0.25
    n = int((lead + step * count + 0.6) * sr)
    sig = np.zeros(n)
    truth = []
    for i in range(count):
        t0 = lead + i * step
        truth.append(t0 * 1000.0)
        f0 = f0_base * 2.0 ** (i / 12.0)
        m = max(2, int(step * 2.4 * sr))
        tt = np.arange(m) / sr
        env = np.exp(-tt / (step * 0.45))
        seg = _harmonic(np.full(m, f0), sr, n_harm=12) * env
        i0 = int(t0 * sr)
        sig[i0:i0 + m] += seg
    return _pack("S4 32 分钢琴音型", sig, sr, truth_ms=truth, expect=count,
                 note="BPM 160，间隔 46.875 ms，半音阶上行")


def s5_legato(sr: int = SR, dur: float = 3.0, melody=(0, 1, 0, 2),
              t0: float = 0.35, note_len: float = 0.62,
              glide: float = 0.20, level: float = 0.22) -> dict:
    """S5 弱人声式连奏：慢滑音（0.2 s）、小音程（1–2 半音）、低幅度。

    关键设计：**既没有能量凹陷，滑音也慢到不产生任何瞬态**。
    这对应"变化幅度不大的人声"——它的难点不是能量，而是音高在走。

    ground truth 取每个滑音的**中点**作为换音时刻。
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    base = 196.0
    targets = [base * 2.0 ** (s / 12.0) for s in melody]
    f0_track = np.full(n, targets[0])
    truth = [t0 * 1000.0]
    for i in range(1, len(targets)):
        mid = t0 + i * note_len
        lo = mid - glide / 2.0
        hi = mid + glide / 2.0
        prev, f = targets[i - 1], targets[i]
        m = (t >= lo) & (t < hi)
        f0_track[m] = prev + (f - prev) * ((t[m] - lo) / glide)
        f0_track[t >= hi] = f
        truth.append(mid * 1000.0)
    sig = _harmonic(f0_track, sr, n_harm=6)
    env = np.zeros(n)
    head = int(t0 * sr)
    ramp = max(1, int(0.10 * sr))
    env[head:head + ramp] = np.linspace(0.0, 1.0, ramp)
    end = min(n, int((t0 + (len(targets) - 1) * note_len + 0.30) * sr))
    env[head + ramp:end] = 1.0
    if end < n:
        env[end:] = np.linspace(1.0, 0.0, n - end)
    sig = sig * env
    return _pack("S5 弱人声式连奏", sig, sr, truth_ms=truth, expect=len(truth),
                 level=level,
                 note="慢滑音 0.2 s、小音程、无能量凹陷、低幅度")


def all_signals():
    return [s1_vibrato(), s2_step(), s3_rearticulation(), s4_dense32(), s5_legato()]
