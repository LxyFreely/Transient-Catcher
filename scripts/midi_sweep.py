"""在 MIDI 测试集上扫描 channel_a 的参数，打印可直接贴进文档的表格。

这是本项目目前最有说服力的一张表。它的结论是：

    **没有任何一组单分辨率参数能同时照顾"密集音型"和"表现力人声"。**

  - 密集 32 分需要短窗，但**不是越短越好**：实测 23 ms 最好，
    8-16 ms 反而更差（低频稳态音的泄漏干涉，见 A12 / F13）。
  - 表现力人声（颤音 + 滑音）需要长窗压制涟漪，
    但长窗又会抹平密集音型的换音。
  - rel_gamma 只是把召回换成精确，不是免费的；在表现力人声上
    无论怎么调，精确率都停在个位数。

所以它同时是"通道 A 调参工具"和"通道 B 为什么必须存在"的证据。

用法：
    python scripts/midi_sweep.py --dir testdata
    python scripts/midi_sweep.py --dir testdata --param win_ms=8,16,23,46 --param rel_gamma=0.3,0.5
    python scripts/midi_sweep.py --dir testdata --markdown
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import os
import sys

import numpy as np
import soundfile as sf

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from transient_catcher import odf  # noqa: E402

DEFAULTS = {"win_ms": 16.0, "norm_q": 0.99, "rel_gamma": 0.30,
            "rel_win_ms": 600.0, "maxfilt": 5, "floor": 0.20}

FIELDS = ("win_ms", "norm_q", "rel_gamma")


def cluster(a, tol):
    """把同一次"敲击"内的多个时刻并成一个（GT 与检出必须用同一容差）。"""
    a = np.sort(np.asarray(a, dtype=np.float64))
    if a.size == 0 or tol <= 0.0:
        return a
    keep = [float(a[0])]
    for v in a[1:]:
        if v - keep[-1] > tol:
            keep.append(float(v))
    return np.asarray(keep)


def load_case(gt_path, cluster_ms=15.0):
    with open(gt_path, "r", encoding="utf-8") as fh:
        gt = json.load(fh)
    wav = gt_path[:-len(".gt.json")] + ".wav"
    if not os.path.exists(wav):
        return None
    x, sr = sf.read(wav, dtype="float32", always_2d=True)
    mono = x.mean(axis=1).astype(np.float64)
    ref = cluster([n["onset_ms"] for n in gt["notes"]], cluster_ms)
    ioi = np.diff(ref)
    tol = max(3.0, min(25.0, 0.45 * float(np.median(ioi)))) if ioi.size else 25.0
    return {"name": os.path.basename(wav), "mono": mono, "sr": sr,
            "ref": ref, "tol": tol, "voice": gt.get("voice", "?")}


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
    used_d, used_r, out = set(), set(), []
    for d, i, j in cands:
        if i in used_d or j in used_r:
            continue
        used_d.add(i)
        used_r.add(j)
        out.append((i, j, d))
    return out


def parse_param(spec):
    name, _, vals = spec.partition("=")
    name = name.strip()
    if name not in DEFAULTS:
        raise SystemExit("未知参数 %s，可选：%s" % (name, ", ".join(DEFAULTS)))
    return name, [float(v) for v in vals.split(",") if v.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description="MIDI 测试集参数扫描")
    ap.add_argument("--dir", default="testdata")
    ap.add_argument("--param", action="append", default=None,
                    metavar="名字=v1,v2", help="可重复，默认三个都扫")
    ap.add_argument("--cluster-ms", type=float, default=15.0,
                    help="真值与检出共用的簇容差（同一敲击内算一次）")
    ap.add_argument("--pattern", default="*.gt.json", help="文件名过滤")
    ap.add_argument("--markdown", action="store_true", help="输出 markdown 表格")
    args = ap.parse_args(argv)

    specs = args.param or ["win_ms=8,12,16,23,32,46",
                           "norm_q=0.99,0.999",
                           "rel_gamma=0.3,0.5"]
    keys, grids = [], []
    for s in specs:
        k, v = parse_param(s)
        keys.append(k)
        grids.append(v)

    cases = []
    for p in sorted(glob.glob(os.path.join(args.dir, args.pattern))):
        c = load_case(p, args.cluster_ms)
        if c:
            cases.append(c)
    if not cases:
        print("在 %s 里没找到 *.gt.json。先跑 scripts/midi_demo.py 与 scripts/midi_render.py"
              % args.dir)
        return 1

    combos = []
    for vals in itertools.product(*grids):
        kw = dict(DEFAULTS)
        kw.update(dict(zip(keys, vals)))
        combos.append(kw)

    for c in cases:
        print("=" * 84)
        print("%s   音色 %s   真值独立起音 %d 个   容差 %.1f ms"
              % (c["name"], c["voice"], len(c["ref"]), c["tol"]))
        head = "  " + "  ".join("%-9s" % k for k in keys) + " %6s %6s %7s %7s %7s"
        if args.markdown:
            print("| " + " | ".join(keys) + " | 真值 | 检出 | 召回 | 精确 | F1 |")
            print("|" + "---|" * (len(keys) + 5))
        else:
            print(head % ("真值", "检出", "召回", "精确", "F1"))
        best = None
        for kw in combos:
            r = odf.channel_a(c["mono"], c["sr"], **kw)
            det = cluster([t for t, _ in r["onsets"]], args.cluster_ms)
            h = len(match_pairs(det, c["ref"], c["tol"]))
            rec = h / max(1, len(c["ref"]))
            pre = h / max(1, len(det))
            f1 = 2 * rec * pre / (rec + pre) if (rec + pre) else 0.0
            cells = ["%-9g" % kw[k] for k in keys]
            if args.markdown:
                print("| " + " | ".join(cells) + " | %d | %d | %.0f%% | %.0f%% | %.2f |"
                      % (len(c["ref"]), len(det), rec * 100, pre * 100, f1))
            else:
                print("  " + "  ".join(cells) + " %6d %6d %6.0f%% %6.0f%% %7.2f"
                      % (len(c["ref"]), len(det), rec * 100, pre * 100, f1))
            if best is None or f1 > best[0]:
                best = (f1, dict(kw), len(det), rec, pre)
        print("  F1 最高的一组：%s  检出 %d  召回 %.0f%%  精确 %.0f%%  F1 %.2f"
              % (" ".join("%s=%g" % (k, best[1][k]) for k in FIELDS),
                 best[2], best[3] * 100, best[4] * 100, best[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
