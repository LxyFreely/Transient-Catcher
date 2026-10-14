"""把 MIDI 渲染成 WAV，并导出逐音符的精确真值。

为什么不用 FluidSynth / SoundFont 渲染
--------------------------------------
1. 本机没有 fluidsynth、没有 soundfont；ffmpeg 这个 build 的 MIDI 支持只有
   Sample Dump，不带合成器。为了这个去装二进制依赖不值得。
2. 更关键：评估音符检测器需要的是**真值**。SoundFont 只给你音频；
   自己渲染可以同时给出每个音符的起止时刻、音高、力度，且与音频严格对齐。
3. 全程 numpy，读 MIDI 用 mido（纯 Python），零二进制依赖。

代价（必须记住）
----------------
这是**合成音频**。它能测出"结构对不对"（漏了什么、凭空多出什么），
测不出真实录音里的踏板共鸣、房间混响、演奏噪声、多轨混音、有损编码。
真实素材仍然要用 scripts/real_infinity.py 交叉验证 —— 见 docs/06-pitfalls.md C1。

比 osu 谱面强在哪
-----------------
谱面是"人工选出来的可玩音符"，音频里还有大量没被记谱的音头
（见 docs/05-p0-results.md F10）。所以谱面评估里 **精确率只是下界**。
MIDI 渲染出来的音频里，**每一个发声的音符都在真值里**，
因此精确率第一次是**真实数字**，不再是下界。这是 MIDI 测试集最大的价值。

用法
----
    python scripts/midi_render.py song.mid --out testdata/song_piano.wav --voice piano
    python scripts/midi_render.py song.mid --out v.wav --voice vocal --legato
    python scripts/midi_render.py song.mid --list        # 只打印音符表
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import soundfile as sf

try:
    import mido
except ImportError:                                          # pragma: no cover
    mido = None

# 元音 /a/ 的前三个共振峰：(中心 Hz, 带宽 Hz, 增益)
FORMANTS = ((720.0, 90.0, 1.00), (1240.0, 110.0, 0.55),
            (2600.0, 170.0, 0.28), (3400.0, 220.0, 0.12))

TWO_PI = 2.0 * np.pi


# --------------------------------------------------------------------------
# 读 MIDI
# --------------------------------------------------------------------------
def read_notes(path: str):
    """把 MIDI 读成 (notes, info)。

    notes: [{'onset': s, 'offset': s, 'pitch': int, 'velocity': int,
             'channel': int}, ...]，按 (onset, pitch) 排序。

    自己维护 tempo map 与延音踏板，不依赖 mido 的秒转换
    （不同版本对 MidiFile 迭代时的 time 单位处理不一致）。
    """
    if mido is None:
        raise RuntimeError("需要 mido：pip install mido")

    mf = mido.MidiFile(path)
    tpb = mf.ticks_per_beat
    merged = mido.merge_tracks(mf.tracks)

    tempo = 500000.0                 # 微秒 / 四分音符，默认 120 BPM
    now = 0.0
    open_notes = {}                  # (channel, pitch) -> note
    sustained = []                   # 被踏板延长、等抬起才关的音
    pedal_down = {}
    notes = []

    def close(note, t):
        note["offset"] = max(float(t), note["onset"] + 1e-3)
        notes.append(note)

    for msg in merged:
        now += mido.tick2second(msg.time, tpb, tempo)
        mt = msg.type

        if mt == "set_tempo":
            tempo = float(msg.tempo)

        elif mt == "note_on" and msg.velocity > 0:
            key = (msg.channel, msg.note)
            old = open_notes.pop(key, None)     # 同键重复触发
            if old is not None:
                close(old, now)
            open_notes[key] = {"onset": float(now), "pitch": int(msg.note),
                               "velocity": int(msg.velocity),
                               "channel": int(msg.channel)}

        elif mt in ("note_off", "note_on"):
            key = (msg.channel, msg.note)
            n = open_notes.pop(key, None)
            if n is None:
                continue
            if pedal_down.get(msg.channel, False):
                sustained.append(n)
            else:
                close(n, now)

        elif mt == "control_change" and msg.control == 64:
            ch = msg.channel
            if msg.value >= 64:
                pedal_down[ch] = True
            else:
                pedal_down[ch] = False
                left = [s for s in sustained if s["channel"] == ch]
                for n in left:
                    sustained.remove(n)
                    close(n, now)

    for n in list(open_notes.values()) + list(sustained):
        close(n, now)

    notes.sort(key=lambda n: (n["onset"], n["pitch"]))
    info = {"ticks_per_beat": int(tpb), "duration_s": float(now),
            "track_count": len(mf.tracks)}
    return notes, info


def estimate_bpm(path: str) -> float:
    """取第一个 set_tempo 当作粗略 BPM（只用于显示）。"""
    if mido is None:
        return 0.0
    try:
        for msg in mido.merge_tracks(mido.MidiFile(path).tracks):
            if msg.type == "set_tempo":
                return 60.0e6 / float(msg.tempo)
    except Exception:
        pass
    return 120.0


# --------------------------------------------------------------------------
# 三种音色
# --------------------------------------------------------------------------
def _f0(pitch: int) -> float:
    return 440.0 * 2.0 ** ((pitch - 69) / 12.0)


def _piano_note(pitch, velocity, dur_s, sr, rng, partial_cap=24, hammer_gain=0.06,
                f_max=8000.0, beat=1.0):
    """钢琴：非谐分音 + 双弦拍频 + 双段衰减 + 制音器 + 击槌噪声。

    拍频不是装饰。相邻分音的幅度调制会产生假的"音头"，
    这正是 docs/06-pitfalls.md A9 那条坑的来源；
    测试集里必须包含它，否则我们等于在骗自己。
    """
    f0 = _f0(pitch) * 2.0 ** (rng.normal(0.0, 1.2) / 1200.0)
    B = float(np.clip(0.0004 * 2.0 ** ((pitch - 60) / 24.0), 1e-4, 0.02))
    amp0 = (velocity / 127.0) ** 1.4

    # 尾巴只要覆盖制音器衰减：exp(-0.22/0.045) = -48 dB，足够。
    # 原来写 0.40 s，短音上有一半以上的采样点都花在这条尾巴上。
    m = int((dur_s + 0.22) * sr) + 1
    t = np.arange(m) / sr
    sig = np.zeros(m)

    atk = np.minimum(1.0, t / 0.0035)
    rel = np.where(t > dur_s, np.exp(-(t - dur_s) / 0.045), 1.0)
    common = atk * rel
    slow = 0.12 * np.exp(-t / 2.6)

    tf0 = float(np.clip(1.5 * 2.0 ** (-(pitch - 60) / 26.0), 0.22, 3.0))
    for k in range(1, partial_cap + 1):
        fk = k * f0 * np.sqrt(1.0 + B * k * k)
        if fk >= min(f_max, 0.45 * sr):
            break
        ak = amp0 / (k ** 1.15)
        ak *= float(np.clip(1.0 + 0.30 * rng.normal(), 0.15, 2.0))

        tfk = tf0 / (1.0 + 0.45 * (k - 1))
        env = 0.88 * np.exp(-t / tfk) + slow

        cents = 0.9 + 0.9 * rng.random()            # 双弦失谐
        dbeat = fk * (2.0 * cents) * 0.6931472 / 1200.0
        if dbeat > 1.5 and beat > 0.0:
            am = 1.0 - beat + beat * (0.78 + 0.22 * np.cos(
                TWO_PI * dbeat * t + rng.random() * 6.2832))
        else:
            # 拍频周期远长于音符本身，cos 白算；但均值要保持 0.78
            am = 1.0 - beat + beat * 0.78

        phi = rng.random() * 6.2832
        sig += ak * np.sin(TWO_PI * fk * t + phi) * env * common * am

    if hammer_gain > 0.0:
        # 击槌噪声是真实钢琴起音的一部分，但它必须**远低于**基音。
        # 第一版写成 0.22*amp0（和基音一样响），等于给每个音塞了一个宽带瞬态，
        # 把密集音型的检测变成送分题 —— 实测窗长怎么变都不影响结果，就是征兆。
        # 真实钢琴大约在 -25 dB，这里默认 0.06。
        hammer = np.diff(rng.normal(0.0, 1.0, m), prepend=0.0)
        hammer = np.convolve(hammer, np.ones(3) / 3.0, mode="same")
        sig += hammer_gain * amp0 * hammer * np.exp(-t / 0.0035)
    return sig


def _pure_note(pitch, velocity, dur_s, sr, rng, partial_cap=16):
    """纯净谐波堆：没有拍频、没有噪声。作为"结构对不对"的基线。"""
    f0 = _f0(pitch)
    amp0 = (velocity / 127.0) ** 1.2
    m = int((dur_s + 0.06) * sr) + 1
    t = np.arange(m) / sr
    sig = np.zeros(m)
    atk = np.minimum(1.0, t / 0.005)
    rel = np.where(t > dur_s, np.exp(-(t - dur_s) / 0.020), 1.0)
    for k in range(1, partial_cap + 1):
        fk = k * f0
        if fk >= 0.45 * sr:
            break
        sig += (amp0 / k) * np.sin(TWO_PI * fk * t) * atk * rel
    return sig


def _vocal_track(notes, sr, rng, legato=True, glide_s=0.070,
                 vib_rate=5.5, vib_depth=0.55, vib_delay=0.35,
                 n_harm=24, breath=0.012):
    """人声：单声部假设 + 滑音 + 起振渐入的颤音 + 共振峰包络 + 气声噪声。

    单声部是有意的：人声的难点是"音高在走而能量不动"，
    复音人声会被 testdata 里另外的钢琴文件覆盖。
    """
    notes = sorted(notes, key=lambda z: z["onset"])
    if not notes:
        return np.zeros(1)

    last_end = max(notes[-1]["offset"], notes[-1]["onset"] + 0.35)
    t_end = last_end + 0.35
    n = int(t_end * sr) + 1
    t = np.arange(n) / sr

    f0 = np.full(n, _f0(notes[0]["pitch"]))
    env = np.zeros(n)
    prev_f = _f0(notes[0]["pitch"])
    prev_t_end = notes[0]["onset"]

    for i, nt in enumerate(notes):
        t0 = float(nt["onset"])
        if i + 1 < len(notes):
            nxt = float(notes[i + 1]["onset"])
            t1 = nxt if legato else min(float(nt["offset"]), nxt)
        else:
            t1 = max(float(nt["offset"]), t0 + 0.35)
        if t1 <= t0 + 1e-4:
            t1 = t0 + 1e-4
        i0 = int(t0 * sr)
        i1 = min(n, int(t1 * sr))
        if i1 <= i0:
            continue
        f = _f0(nt["pitch"])
        seg = np.full(i1 - i0, f)
        if i > 0 and glide_s > 0.0:
            g = int(min(glide_s * sr, i1 - i0))
            if g > 1:
                seg[:g] = np.linspace(prev_f, f, g)
        f0[i0:i1] = seg
        env[i0:i1] = 1.0
        prev_f = f
        prev_t_end = t1

    env *= np.exp(-np.maximum(0.0, t - prev_t_end) / 0.060)
    i0 = int(notes[0]["onset"] * sr)
    a = max(1, int(0.045 * sr))
    if i0 > 0:
        env[:i0] = 0.0
    if i0 + a <= n:
        env[i0:i0 + a] *= np.linspace(0.0, 1.0, a)

    ramp = np.clip((t - notes[0]["onset"]) / max(1e-6, vib_delay), 0.0, 1.0)
    vib = vib_depth * ramp * np.sin(TWO_PI * vib_rate * t)
    f0m = f0 * 2.0 ** (vib / 12.0)
    phase = TWO_PI * np.cumsum(f0m) / sr

    f_med = float(np.median(f0[f0 > 0.0])) if np.any(f0 > 0.0) else 220.0
    sig = np.zeros(n)
    for k in range(1, n_harm + 1):
        fk = k * f_med
        if fk >= 0.45 * sr:
            break
        resp = sum(g * bw * bw / ((fk - fc) ** 2 + bw * bw) for fc, bw, g in FORMANTS)
        a_k = resp / k
        if a_k < 1e-4:
            continue
        sig += a_k * np.sin(k * phase + rng.random() * 6.2832)

    if breath > 0.0:
        nz = np.diff(rng.normal(0.0, 1.0, n), prepend=0.0)
        nz = np.convolve(nz, np.ones(3) / 3.0, mode="same")
        sig += breath * nz

    return sig * env


# --------------------------------------------------------------------------
# GM 打击乐（通道 9）
# --------------------------------------------------------------------------
def _drum_kind(pitch):
    if pitch in (35, 36):
        return "kick"
    if pitch in (38, 40):
        return "snare"
    if pitch in (37, 39):
        return "clap"
    if pitch in (42, 44):
        return "hat"
    if pitch == 46:
        return "hatopen"
    if pitch in (49, 52, 55, 57):
        return "crash"
    if pitch in (51, 53, 59):
        return "ride"
    if pitch in (41, 43, 45, 47, 48, 50):
        return "tom"
    return "perc"


def _drum_hit(pitch, velocity, sr, rng):
    """把 GM 打击乐音高渲染成真正的打击乐。

    这一条不是锦上添花：真实编曲里**鼓占了音头的一大部分**（用户的
    夜航星.mid 里 ch9 有 6792 个音符，占全部 53%）。
    如果照音高去合成，得到的是完全错误的声音，测出来的东西也就没有意义。
    """
    kind = _drum_kind(pitch)
    amp = (velocity / 127.0) ** 1.3

    if kind in ("kick", "tom"):
        if kind == "kick":
            f_hi, f_lo, tau, dur = 130.0, 46.0, 0.075, 0.45
        else:
            base = 250.0 * 2.0 ** ((pitch - 45) / 12.0)
            f_hi, f_lo, tau, dur = base * 1.6, base * 0.75, 0.16, 0.55
        m = int(dur * sr) + 1
        t = np.arange(m) / sr
        f = f_lo + (f_hi - f_lo) * np.exp(-t / 0.018)
        body = np.sin(TWO_PI * np.cumsum(f) / sr) * np.exp(-t / tau)
        click = np.diff(rng.normal(0.0, 1.0, m), prepend=0.0) * np.exp(-t / 0.004)
        return amp * (body + 0.22 * click)

    if kind in ("snare", "clap"):
        tau = 0.055 if kind == "snare" else 0.028
        dur = 0.30 if kind == "snare" else 0.35
        m = int(dur * sr) + 1
        t = np.arange(m) / sr
        nz = np.diff(rng.normal(0.0, 1.0, m), prepend=0.0)
        nz = np.convolve(nz, np.ones(2) / 2.0, mode="same")
        out = 0.9 * nz * np.exp(-t / tau)
        if kind == "snare":
            out = out + 0.45 * np.sin(TWO_PI * 185.0 * t) * np.exp(-t / 0.045)
        return amp * out

    table = {"hat": (0.10, 0.018), "hatopen": (0.45, 0.10),
             "ride": (0.70, 0.22), "crash": (1.30, 0.42),
             "perc": (0.18, 0.045)}
    dur, tau = table.get(kind, table["perc"])
    m = int(dur * sr) + 1
    t = np.arange(m) / sr
    nz = np.diff(rng.normal(0.0, 1.0, m), prepend=0.0)
    if kind == "perc":
        nz = np.convolve(nz, np.ones(3) / 3.0, mode="same")
    return amp * nz * np.exp(-t / tau) * 0.7


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
VOICES = ("piano", "pure", "vocal")

# 人声音色的可消融参数。把它们暴露出来，是为了能回答
# "检测器到底怕颤音、怕滑音，还是怕气声" 这种问题 ——
# 光看一个总体数字是答不出来的。
DEFAULT_VOICE_OPTS = {"vib_rate": 5.5, "vib_depth": 0.55, "vib_delay": 0.35,
                      "n_harm": 24, "breath": 0.012, "glide_s": 0.070}


def voice_ops(overrides=None):
    o = dict(DEFAULT_VOICE_OPTS)
    if overrides:
        o.update({k: v for k, v in overrides.items() if v is not None})
    return o


def render(notes, sr, voice="piano", seed=0, legato=True, peak=0.89,
           voice_opts=None, hammer_gain=0.06, drums=True, beat=1.0):
    """把音符表渲染成波形。channel 9 的音符走打击乐合成，不走音高合成。"""
    rng = np.random.default_rng(seed)
    ops = voice_ops(voice_opts)

    drum_notes = [n for n in notes if n["channel"] == 9] if drums else []
    if drum_notes:
        notes = [n for n in notes if n["channel"] != 9]

    if voice == "vocal" and notes:
        sig = _vocal_track(notes, sr, rng, legato=legato,
                           glide_s=ops["glide_s"], vib_rate=ops["vib_rate"],
                           vib_depth=ops["vib_depth"], vib_delay=ops["vib_delay"],
                           n_harm=int(ops["n_harm"]), breath=ops["breath"])
    elif notes:
        t_end = max(float(n["offset"]) for n in notes) + 0.5
        sig = np.zeros(int(t_end * sr) + 1)
        for nt in notes:
            dur = max(0.02, float(nt["offset"] - nt["onset"]))
            if voice == "piano":
                seg = _piano_note(nt["pitch"], nt["velocity"], dur, sr, rng,
                                  hammer_gain=hammer_gain, beat=beat)
            else:
                seg = _pure_note(nt["pitch"], nt["velocity"], dur, sr, rng)
            i0 = int(nt["onset"] * sr)
            i1 = min(len(sig), i0 + len(seg))
            if i1 > i0:
                sig[i0:i1] += seg[:i1 - i0]
    else:
        sig = np.zeros(1)

    if drum_notes:
        need = int((max(n["offset"] for n in drum_notes) + 1.5) * sr) + 1
        if need > len(sig):
            sig = np.concatenate([sig, np.zeros(need - len(sig))])
        for nt in drum_notes:
            seg = _drum_hit(nt["pitch"], nt["velocity"], sr, rng)
            i0 = int(nt["onset"] * sr)
            i1 = min(len(sig), i0 + len(seg))
            if i1 > i0:
                sig[i0:i1] += seg[:i1 - i0]

    p = float(np.max(np.abs(sig))) if sig.size else 0.0
    if p > 0.0:
        sig = sig / p * peak
    return sig.astype(np.float64)


def parse_label_spec(spec):
    """'名字:起-止'（单位秒）-> {'name', 'start_ms', 'end_ms'}"""
    name, _, rng = spec.partition(":")
    a, _, b = rng.partition("-")
    return {"name": name.strip(),
            "start_ms": float(a) * 1000.0, "end_ms": float(b) * 1000.0}


def ground_truth(notes, src, voice, sr, info, legato, labels=None):
    return {
        "source_midi": os.path.abspath(src),
        "labels": list(labels or []),
        "voice": voice,
        "legato": bool(legato),
        "sample_rate": int(sr),
        "duration_s": float(len(notes) and max(n["offset"] for n in notes) or 0.0),
        "bpm": round(float(estimate_bpm(src)), 3),
        "note_count": len(notes),
        "midi_info": info,
        "notes": [
            {"onset_ms": round(n["onset"] * 1000.0, 4),
             "offset_ms": round(n["offset"] * 1000.0, 4),
             "pitch": n["pitch"],
             "freq_hz": round(_f0(n["pitch"]), 3),
             "velocity": n["velocity"],
             "channel": n["channel"]}
            for n in notes
        ],
    }


def render_file(path, out_wav, voice="piano", sr=44100, seed=0,
                legato=True, peak=0.89, subtype="PCM_16", out_gt=None,
                labels=None, voice_opts=None, hammer_gain=0.06,
                drums=True, max_seconds=None, quiet=False, beat=1.0):
    notes, info = read_notes(path)
    if not notes:
        raise RuntimeError("这个 MIDI 里没有音符：" + path)
    if max_seconds:
        notes = [n for n in notes if n["onset"] < max_seconds]
        if not notes:
            raise RuntimeError("--duration %.1f s 内没有音符" % max_seconds)
    t0 = time.time()
    sig = render(notes, sr, voice=voice, seed=seed, legato=legato, peak=peak,
                 voice_opts=voice_opts, hammer_gain=hammer_gain, drums=drums,
                 beat=beat)
    if max_seconds:
        sig = sig[:int(max_seconds * sr)]
    if not quiet:
        print("  渲染 %d 个音符 ｜ 含鼓 %s ｜ 耗时 %.1f s"
              % (len(notes), "是" if drums else "否", time.time() - t0))
    os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
    sf.write(out_wav, sig, sr, subtype=subtype)
    gt = ground_truth(notes, path, voice, sr, info, legato, labels=labels)
    gt["wav"] = os.path.abspath(out_wav)
    gt["voice_opts"] = voice_ops(voice_opts)
    gt["drums"] = bool(drums)
    gt["excerpt_s"] = float(max_seconds) if max_seconds else None
    gt["duration_s"] = round(len(sig) / float(sr), 4)
    if out_gt is None:
        out_gt = os.path.splitext(out_wav)[0] + ".gt.json"
    with open(out_gt, "w", encoding="utf-8") as fh:
        json.dump(gt, fh, ensure_ascii=False, indent=2)
    return gt, out_wav, out_gt


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="MIDI -> WAV + 精确真值")
    ap.add_argument("midi")
    ap.add_argument("--out", "-o", default=None, help="输出 WAV（默认与 MIDI 同名）")
    ap.add_argument("--voice", choices=VOICES, default="piano")
    ap.add_argument("--gt", default=None, help="真值 JSON 路径")
    ap.add_argument("--sr", type=int, default=44100)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--peak", type=float, default=0.89)
    ap.add_argument("--subtype", default="PCM_16")
    ap.add_argument("--no-drums", action="store_true",
                    help="忽略通道 9（GM 打击乐）。默认按打击乐合成")
    ap.add_argument("--beat", type=float, default=1.0,
                    help="钢琴双弦拍频深度，0 = 关掉（用来判断测试集是否对某算法不公）")
    ap.add_argument("--duration", type=float, default=None,
                    help="只渲染前 N 秒（快速试听 / 迭代用）")
    ap.add_argument("--no-legato", action="store_true", help="人声不要连奏")
    ap.add_argument("--vib-depth", type=float, default=0.55, help="颤音峰峰值（半音）")
    ap.add_argument("--vib-rate", type=float, default=5.5, help="颤音频率 Hz")
    ap.add_argument("--vib-delay", type=float, default=0.35, help="颤音渐入时长 s")
    ap.add_argument("--breath", type=float, default=0.012, help="气声噪声幅度")
    ap.add_argument("--glide-ms", type=float, default=70.0, help="滑音时长 ms")
    ap.add_argument("--n-harm", type=int, default=24, help="最大谐波数")
    ap.add_argument("--hammer", type=float, default=0.06,
                    help="击槌噪声幅度（相对基音）。真实钢琴约 0.06")
    ap.add_argument("--label", action="append", default=None,
                    metavar="名字:起-止",
                    help="给真值打区间标签（秒），可重复。评估时按区间出表")
    ap.add_argument("--list", action="store_true", help="只打印音符表，不渲染")
    args = ap.parse_args(argv)

    notes, info = read_notes(args.midi)
    bpm = estimate_bpm(args.midi)
    print("MIDI: %s" % args.midi)
    print("  轨道 %d ｜ 每拍 %d tick ｜ 时长 %.2f s ｜ 约 %.1f BPM ｜ 音符 %d 个"
          % (info["track_count"], info["ticks_per_beat"], info["duration_s"],
             bpm, len(notes)))
    if not notes:
        print("  这个文件里没有音符。")
        return 1

    if args.list:
        print("  %-10s %-10s %6s %5s %5s" % ("起(ms)", "止(ms)", "音高", "力度", "声道"))
        for n in notes[:80]:
            print("  %-10.1f %-10.1f %6d %5d %5d"
                  % (n["onset"] * 1000, n["offset"] * 1000,
                     n["pitch"], n["velocity"], n["channel"]))
        if len(notes) > 80:
            print("  ... 还有 %d 个" % (len(notes) - 80))
        return 0

    out = args.out or os.path.splitext(args.midi)[0] + "_" + args.voice + ".wav"
    gt, wav, gtp = render_file(args.midi, out, voice=args.voice, sr=args.sr,
                               seed=args.seed, legato=not args.no_legato,
                               peak=args.peak, subtype=args.subtype,
                               out_gt=args.gt,
                               drums=not args.no_drums,
                               max_seconds=args.duration, beat=args.beat,
                               labels=[parse_label_spec(s) for s in (args.label or [])],
                               voice_opts={"vib_depth": args.vib_depth,
                                           "vib_rate": args.vib_rate,
                                           "vib_delay": args.vib_delay,
                                           "breath": args.breath,
                                           "glide_s": args.glide_ms / 1000.0,
                                           "n_harm": args.n_harm},
                               hammer_gain=args.hammer)
    print("  音色 %s ｜ 采样率 %d ｜ 峰值 %.2f" % (args.voice, args.sr, args.peak))
    print("  写出 %s" % wav)
    print("  真值 %s（%d 个音符）" % (gtp, gt["note_count"]))
    io = np.diff([n["onset_ms"] for n in gt["notes"]])
    if io.size:
        print("  相邻音起始间隔：中位 %.1f ms，最小 %.1f ms"
              % (float(np.median(io)), float(io.min())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
