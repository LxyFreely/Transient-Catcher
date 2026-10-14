"""生成两个示例 MIDI，用来在没有任何外部素材时验证整条链路。

    demo_piano_dense.mid  密集 32 分钢琴 + 和弦 + 弱奏 + 踏板 + 静音段
    demo_vocal_legato.mid 单声部连奏人声，音程 1-2 个半音

这两个文件刻意覆盖我们要打的两个失效模式：
  1. 密集 32 分（BPM 160 下相邻音只有 46.875 ms，低于现有检测器 55 ms 硬下限）
  2. 弱奏 / 连奏（能量不动，但音高在走）

同时放了两个"考验诚实"的片段：
  - 一整小节完全静音（真值 0 个音符）—— 这段里的任何检出都是误报
  - 一整小节踩着延音踏板的长音 —— 踏板共鸣会喂给检测器持续的谱变化

用法：
    python scripts/midi_demo.py --outdir testdata
"""

from __future__ import annotations

import argparse
import os
import sys

import mido

TPB = 480


def _write(path, events, bpm):
    """events: [(绝对 tick, Message, 次序)]；同 tick 上 note_off 先于 note_on。"""
    events = sorted(events, key=lambda e: (e[0], e[2]))
    mid = mido.MidiFile(ticks_per_beat=TPB)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    last = 0
    for tick, msg, _order in events:
        track.append(msg.copy(time=int(tick - last)))
        last = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    mid.save(path)
    return path


def _on(events, t_on, t_off, pitch, vel, ch=0):
    events.append((int(t_on), mido.Message("note_on", note=int(pitch),
                                           velocity=int(vel), channel=ch), 1))
    events.append((int(t_off), mido.Message("note_off", note=int(pitch),
                                            velocity=0, channel=ch), 0))


def _cc(events, tick, value, control=64, ch=0):
    events.append((int(tick), mido.Message("control_change", control=control,
                                           value=int(value), channel=ch), 0))


def piano_dense(path):
    """BPM 160：四分音符 375 ms，32 分音符 46.875 ms。共 8 小节 = 12 s。"""
    bpm = 160.0
    e = []
    q = TPB                     # 四分音符 tick
    s32 = q // 8                # 32 分音符 = 60 tick
    s16 = q // 4
    bar = 4 * q
    T = lambda b, x: b * bar + x        # noqa: E731

    # --- 第 0-1 小节：32 分半音阶上下行（32 音 × 2） ---
    for i in range(32):
        _on(e, T(0, i * s32), T(0, i * s32) + s32 * 2, 72 + i, 96)
    for i in range(32):
        _on(e, T(1, i * s32), T(1, i * s32) + s32 * 2, 103 - i, 92)

    # --- 第 2 小节：四分和弦（每个音都该被检出） ---
    chords = [(50, [62, 65, 69]), (55, [64, 67, 71]),
              (48, [60, 64, 67]), (57, [60, 64, 69])]
    for k, (bass, ch) in enumerate(chords):
        _on(e, T(2, k * q), T(2, k * q) + q, bass, 88)
        for p in ch:
            _on(e, T(2, k * q), T(2, k * q) + q, p, 80)

    # --- 第 3 小节：16 分同音反复 ---
    for i in range(16):
        _on(e, T(3, i * s16), T(3, i * s16) + s16, 76, 74)

    # --- 第 4 小节：同样的反复，但力度只有 25（弱奏） ---
    for i in range(16):
        _on(e, T(4, i * s16), T(4, i * s16) + s16, 76, 25)

    # --- 第 5 小节：踩满延音踏板，反复弹同一个音 ---
    _cc(e, T(5, 0), 127)
    for i in range(8):
        _on(e, T(5, i * (q // 2)), T(5, i * (q // 2)) + q // 4, 67, 80)
    _cc(e, T(5, 4 * q) - 10, 0)

    # --- 第 6 小节：完全静音（真值 0 个音符） ---
    #     故意什么都不放。

    # --- 第 7 小节：收尾和弦，长按 ---
    for p in (48, 60, 64, 67, 72):
        _on(e, T(7, 0), T(7, 0) + 2 * q, p, 85)

    return _write(path, e, bpm)


def vocal_legato(path):
    """BPM 90：四分音符 667 ms。单声部、连奏、音程不超过 5 个半音。"""
    bpm = 90.0
    e = []
    q = TPB
    base = 57                                   # A3
    steps = [0, 2, 1, 3, 2, 1, 0, -1, 0, 2, 4, 2, 1, 0, -1, 0]
    for i, s in enumerate(steps):
        t0 = i * q
        t1 = (i + 1) * q                        # 连奏：一直唱到下一个音
        _on(e, t0, t1, base + s, 72 + (i % 3) * 4)
    _on(e, len(steps) * q, (len(steps) + 2) * q, base, 78)   # 收尾长音
    return _write(path, e, bpm)


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成示例 MIDI")
    ap.add_argument("--outdir", default="testdata")
    args = ap.parse_args(argv)
    out = []
    out.append(piano_dense(os.path.join(args.outdir, "demo_piano_dense.mid")))
    out.append(vocal_legato(os.path.join(args.outdir, "demo_vocal_legato.mid")))
    for p in out:
        mid = mido.MidiFile(p)
        n = sum(1 for m in mido.merge_tracks(mid.tracks)
                if m.type == "note_on" and m.velocity > 0)
        print("写出 %s（%d 个音符，%d tick/拍）" % (p, n, mid.ticks_per_beat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
