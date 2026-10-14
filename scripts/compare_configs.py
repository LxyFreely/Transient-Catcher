"""横跨三套评估，比较若干组 channel_a 配置。

为什么需要它：这个项目现在有了三套互补的 ground truth，
而它们**对参数的要求是互相矛盾的**（见 docs/06-pitfalls.md A12/A13/A14）。

  合成信号 S0-S5   定结构：能不能看见音高 / 会不会把颤音当音符
  真实歌曲 + 谱面   定参数：真实混音下的召回/精确（精确率是下界）
  MIDI 渲染 + 真值  定真假：精确率第一次是真实数字（见 C2 / C5）

只看其中任何一套都会得出偏激的结论。改默认参数前必须跑这个。

用法：
    python scripts/compare_configs.py
    python scripts/compare_configs.py --real "C:/path/song.flac" --chart "C:/path/x.osu"
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np
import soundfile as sf

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from transient_catcher import odf, osu as osumod, signals  # noqa: E402

# 每项要么是 channel_a 的关键字参数，要么是 ("variant", 名字)
CONFIGS = [
    ("E 现状      (16, 0.990, 0.30)", dict(win_ms=16.0, norm_q=0.99, rel_gamma=0.30)),
    ("E 候选      (23, 0.990, 0.30)", dict(win_ms=23.0, norm_q=0.99, rel_gamma=0.30)),
    ("E 候选      (23, 0.999, 0.30)", dict(win_ms=23.0, norm_q=0.999, rel_gamma=0.30)),
    ("E 长窗      (46, 0.999, 0.30)", dict(win_ms=46.0, norm_q=0.999, rel_gamma=0.30)),
    ("变体 D（对照）", ("variant", "D")),
    ("变体 A（对照）", ("variant", "A")),
]

REAL_FLAC = r"C:\Users\11952\Downloads\HyuN - Infinity Heaven.flac"
REAL_CHART = (r"C:\Users\11952\AppData\Roaming\RhyDive\Library\songs"
              r"\hyun-infinity-heaven-891345-f12b4c27\assets"
              r"\HyuN - Infinity Heaven (Niva) [Ad Infinitum].osu")


def run(cfg, mono, sr):
    if isinstance(cfg, tuple) and cfg[0] == "variant":
        name = cfg[1]
        fn = {"A": lambda: odf.rhydive_like(mono, sr),
              "D": lambda: odf.perbin(mono, sr, use_reassign=False, maxfilt=17)}[name]
        r = fn()
    else:
        r = odf.channel_a(mono, sr, **cfg)
    return np.asarray(r["onsets"], dtype=np.float64).reshape(-1, 2)


def match_pairs(det, ref, tol):
    det = np.asarray(det, dtype=np.float64)
    ref = np.asarray(ref, dtype=np.float64)
    if det.size == 0 or ref.size == 0:
        return []
    lo = np.searchsorted(ref, det - tol, "left")
    hi = np.searchsorted(ref, det + tol, "right")
    cands = []
    for i in range(len(det)):
        for j in range(int(lo[i]), int(hi[i])):
            cands.append((abs(ref[j] - det[i]), i, j))
    cands.sort()
    ud, ur, out = set(), set(), []
    for d, i, j in cands:
        if i in ud or j in ur:
            continue
        ud.add(i)
        ur.add(j)
        out.append((i, j, d))
    return out


def score(det_times, ref, tol):
    h = len(match_pairs(det_times, ref, tol))
    rec = h / max(1, len(ref))
    pre = h / max(1, len(det_times))
    return rec, pre, h


def main(argv=None):
    ap = argparse.ArgumentParser(description="三套评估上的配置对比")
    ap.add_argument("--real", default=REAL_FLAC)
    ap.add_argument("--chart", default=REAL_CHART)
    ap.add_argument("--dir", default="testdata")
    ap.add_argument("--pattern", default="*.gt.json")
    args = ap.parse_args(argv)

    # ---- 1) 合成信号 ----
    syn = []
    for s in signals.all_signals():
        syn.append((s["name"], s["samples"], s["sr"], s["truth_ms"],
                    s["expect"], s["fp_region_ms"]))

    # ---- 2) 真实歌曲 ----
    real = None
    if os.path.exists(args.real) and os.path.exists(args.chart):
        x, sr = sf.read(args.real, dtype="float32", always_2d=True)
        ref = np.asarray(osumod.parse_osu(args.chart), dtype=np.float64) * 1000.0
        real = (os.path.basename(args.real), x.mean(axis=1).astype(np.float64), sr, ref)

    # ---- 3) MIDI 测试集 ----
    midi = []
    for p in sorted(glob.glob(os.path.join(args.dir, args.pattern))):
        wav = p[:-len(".gt.json")] + ".wav"
        if not os.path.exists(wav):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            gt = json.load(fh)
        times = []
        for v in sorted(n["onset_ms"] for n in gt["notes"]):
            if not times or v - times[-1] > 5.0:
                times.append(v)
        ref = np.asarray(times, dtype=np.float64)
        ioi = np.diff(ref)
        tol = max(3.0, min(25.0, 0.45 * float(np.median(ioi)))) if ioi.size else 25.0
        x, sr = sf.read(wav, dtype="float32", always_2d=True)
        midi.append((os.path.basename(wav), x.mean(axis=1).astype(np.float64),
                     sr, ref, tol))

    print("=" * 88)
    print("1) 合成信号：检出数 / 预期数（括号里是误报区内的检出数，应为 0）")
    print("   %-30s %s" % ("配置", "  ".join("%-9s" % s[0].split()[0] for s in syn)))
    for label, cfg in CONFIGS:
        cells = []
        for name, samp, sr, truth, expect, fpreg in syn:
            det = run(cfg, samp, sr)[:, 0]
            n = len(det)
            if fpreg is not None:
                a, b = fpreg
                fp = int(((det >= a) & (det <= b)).sum())
                cells.append("%-9s" % ("%d(%d)" % (n, fp)))
            else:
                cells.append("%-9s" % n)
        print("   %-30s %s" % (label, "  ".join(cells)))

    if real:
        name, mono, sr, ref = real
        print()
        print("2) 真实素材 %s（谱面 %d 个音符，精确率是下界）" % (name, len(ref)))
        for label, cfg in CONFIGS:
            det = run(cfg, mono, sr)[:, 0]
            rec, pre, h = score(det, ref, 60.0)
            print("   %-30s 检出 %5d  命中 %4d  召回 %3.0f%%  精确 %3.0f%%"
                  % (label, len(det), h, rec * 100, pre * 100))

    if midi:
        print()
        print("3) MIDI 渲染测试集（真值精确，精确率是真实数字）")
        for label, cfg in CONFIGS:
            print("   %s" % label)
            for name, mono, sr, ref, tol in midi:
                det = run(cfg, mono, sr)[:, 0]
                rec, pre, h = score(det, ref, tol)
                print("      %-34s 目标 %4d  检出 %5d  召回 %3.0f%%  精确 %3.0f%%"
                      % (name[:34], len(ref), len(det), rec * 100, pre * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
