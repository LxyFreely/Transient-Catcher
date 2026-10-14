"""用 osu 谱面当 ground truth，对全曲做评估，找出所有失守的区段。

注意：谱面是"人工选出来的可玩音符"，不等于音频里的全部音头
（用户上传曲比谱面更密），所以：
  低召回  -> 可能正常（谱面没记那么多）
  低精确  -> 一定是问题（检出的东西谱面没有）
重点看**精确率**。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from transient_catcher import odf  # noqa: E402

FLAC = r"C:\Users\11952\Downloads\HyuN - Infinity Heaven.flac"
CHART = (r"C:\Users\11952\AppData\Roaming\RhyDive\Library\songs"
         r"\hyun-infinity-heaven-891345-f12b4c27\assets"
         r"\HyuN - Infinity Heaven (Niva) [Ad Infinitum].osu")
# 匹配容差。注意这里的 det / ref 都是**秒**（因为按 10 s 分桶），
# 所以容差必须也是秒。原来写 TOL = 60.0 而注释说是 ms，实际等于 60 秒 ——
# 于是"任何检出都能匹配任何音符"，召回率永远是 100%。这是个假数字。
TOL = 0.060         # 60 ms


def parse_osu(path):
    sec, objs = None, []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("[") and line.endswith("]"):
                sec = line[1:-1]
                continue
            if sec == "HitObjects":
                p = line.split(",")
                try:
                    t, typ = float(p[2]), int(p[3])
                except Exception:
                    continue
                if typ & 8:                    # 转盘（spinner）不算音头
                    continue
                objs.append(t)
    return np.asarray(sorted(objs), dtype=np.float64)


def match(det, ref, tol=TOL):
    used = set()
    hit = 0
    for t in det:
        best, bd = None, 1e9
        for i, r in enumerate(ref):
            if i in used:
                continue
            d = abs(r - t)
            if d < bd:
                bd, best = d, i
        if best is not None and bd <= tol:
            used.add(best)
            hit += 1
    return hit


def main():
    ref = parse_osu(CHART)
    x, sr = sf.read(FLAC, dtype="float32", always_2d=True)
    mono = x.mean(axis=1).astype(np.float64)
    dur = len(mono) / sr
    r = odf.channel_a(mono, sr, hop_ms=10.0)
    det = np.asarray([t / 1000.0 for t, _ in r["onsets"]], dtype=np.float64)
    ref_s = ref / 1000.0

    print("谱面音符 %d 个 ｜ 检出 %d 个 ｜ 时长 %.1f s ｜ 容差 %.0f ms"
          % (len(ref_s), len(det), dur, TOL * 1000.0))
    print("=" * 88)
    print("%-12s %8s %8s %8s %8s %8s" % ("区间(s)", "谱面", "检出", "命中", "召回", "精确"))
    print("-" * 88)
    rows = []
    B = 10.0
    for a in np.arange(0, dur, B):
        b = a + B
        rd = ref_s[(ref_s >= a) & (ref_s < b)]
        dd = det[(det >= a) & (det < b)]
        if len(rd) == 0 and len(dd) == 0:
            continue
        h = match(dd, rd)
        rec = h / max(1, len(rd))
        pre = h / max(1, len(dd))
        rows.append((a, len(rd), len(dd), h, rec, pre))
    for a, nr, nd, h, rec, pre in rows:
        flag = ""
        if nd > 0 and pre < 0.5:
            flag = "   <== 精确率低"
        print("%-12s %8d %8d %8d %7.0f%% %7.0f%%%s"
              % ("%.0f-%.0f" % (a, a + B), nr, nd, h, rec * 100, pre * 100, flag))

    nref, ndet = sum(r[1] for r in rows), sum(r[2] for r in rows)
    nhit = sum(r[3] for r in rows)
    print("-" * 88)
    print("合计：谱面 %d ｜ 检出 %d ｜ 命中 %d ｜ 召回 %.0f%% ｜ 精确 %.0f%%"
          % (nref, ndet, nhit, 100 * nhit / max(1, nref), 100 * nhit / max(1, ndet)))

    worst = sorted([r for r in rows if r[2] >= 5], key=lambda r: r[5])[:6]
    print("\n精确率最低的 6 个 10 秒区间：")
    for a, nr, nd, h, rec, pre in worst:
        print("  %.0f–%.0f s: 谱面 %3d, 检出 %3d, 命中 %3d, 召回 %3.0f%%, 精确 %3.0f%%"
              % (a, a + B, nr, nd, h, rec * 100, pre * 100))


if __name__ == "__main__":
    main()
