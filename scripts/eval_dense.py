"""在「含真正密集音符」的谱面上评估 —— 检验项目立论前提的那个实验。

背景（见 docs/05-p0-results.md F14 / F15）：
  Infinity Heaven 的谱面最小相邻间隔是 93 ms（= 16 分），
  整张谱面**没有一个间隔小于 55 ms**，所以它测不到现有检测器 55 ms 硬下限的缺陷。
  RhyDive 本地曲库里另有若干谱面确实含大量 < 55 ms 的间隔
  （World Ender 有 324 个），那些才是能真正考验前提的素材。

读法：
  「全部」这一列 A 基本都赢；「密集邻域」这一列才是重点。
  带真实真值的素材上，密集段里 E 的召回与精确都能压过 A ——
  这是本项目唯一一个目标区间内双赢的结果。

用法：
    python scripts/eval_dense.py                 # 自动挑最密的 4 张
    python scripts/eval_dense.py --top 8
    python scripts/eval_dense.py --min-count 50
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np
import soundfile as sf

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from transient_catcher import odf, osu as osumod  # noqa: E402

EXTS = (".flac", ".wav", ".ogg", ".mp3", ".m4a")
DENSE_MS = 55.0        # 现有检测器输出层的硬下限
NEAR_MS = 100.0        # 「密集邻域」的半宽
TOL_MS = 60.0


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
    for _d, i, j in cands:
        if i in ud or j in ur:
            continue
        ud.add(i)
        ur.add(j)
        out.append((i, j))
    return out


def find_audio(osu_path):
    adir = os.path.dirname(osu_path)
    hits = [f for f in glob.glob(os.path.join(adir, "*"))
            if os.path.splitext(f)[1].lower() in EXTS]
    return hits[0] if hits else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="含密集音符的谱面上的评估")
    ap.add_argument("--top", type=int, default=4)
    ap.add_argument("--min-count", type=int, default=15,
                    help="至少要有这么多 <55ms 的间隔才纳入")
    args = ap.parse_args(argv)

    cands = []
    for c in osumod.library_charts():
        t = np.asarray(osumod.parse_osu(c["path"]), dtype=np.float64) * 1000.0
        if len(t) < 50:
            continue
        n55 = int((np.diff(t) < DENSE_MS).sum())
        if n55 >= args.min_count:
            cands.append((n55, c, t))
    if not cands:
        print("曲库里没有含密集音符的谱面（需要 RhyDive 本地曲库）")
        return 1
    cands.sort(key=lambda z: -z[0])
    print("含 <%d ms 间隔的谱面 %d 张，取最密的 %d 张"
          % (DENSE_MS, len(cands), args.top))

    cache = {}
    for n55, c, t in cands[:args.top]:
        audio = find_audio(c["path"])
        if audio is None:
            print("  %s：找不到音频" % c["title"][:30])
            continue
        if audio not in cache:
            x, sr = sf.read(audio, dtype="float32", always_2d=True)
            cache[audio] = (x.mean(axis=1).astype(np.float64), sr)
        mono, sr = cache[audio]

        g = np.diff(t)
        dn = np.zeros(len(t), dtype=bool)
        dn[:-1] |= g < DENSE_MS
        dn[1:] |= g < DENSE_MS
        td = t[dn]

        print()
        print("=== %s ｜ %s ｜ %.0f s ｜ 谱面 %d ｜ 密集段 %d ｜ <%d ms 间隔 %d ==="
              % (c["title"][:30], os.path.basename(audio)[:22], len(mono) / sr,
                 len(t), int(dn.sum()), DENSE_MS, n55))
        print("  %-12s | %-24s | %-24s"
              % ("检测器", "全部 召回/精确(检出)", "密集邻域 召回/精确(检出)"))
        for name, fn in (("A 现有算法", lambda s: odf.rhydive_like(s, sr)),
                         ("E 我们的", lambda s: odf.channel_a(s, sr))):
            det = np.asarray([q for q, _ in fn(mono)["onsets"]], dtype=np.float64)
            pa = match_pairs(det, t, TOL_MS)
            ra = len(pa) / max(1, len(t))
            pra = len(pa) / max(1, len(det))
            lo = np.searchsorted(td, det - NEAR_MS)
            hi = np.searchsorted(td, det + NEAR_MS, "right")
            dd = det[hi > lo]
            pb = match_pairs(dd, td, TOL_MS)
            rb = len(pb) / max(1, len(td))
            prb = len(pb) / max(1, len(dd))
            print("  %-12s | 召回 %3.0f%%  精确 %3.0f%% (%4d) | 召回 %3.0f%%  精确 %3.0f%% (%4d)"
                  % (name, ra * 100, pra * 100, len(det), rb * 100, prb * 100, len(dd)))
    return 0


if __name__ == "__main__":
    sys.exit(main())