"""用 MIDI 渲染出来的测试集评估检测器。

与 scripts/eval_chart.py 的关键区别（决定了它值不值得存在）
--------------------------------------------------------
谱面是人工挑过的"可玩音符"，音频里还有大量没进谱子的音头
（见 docs/05-p0-results.md F10），所以谱面评估里**精确率只是下界**。
MIDI 渲染的音频里，**每一个发声的音符都在真值里**，
因此这里的精确率第一次是**真实数字**，不再是下界。

代价：音频是合成的。它测不出真实录音的混响、踏板共鸣、演奏噪声、
有损编码。所以两套必须一起看 —— 见 docs/06-pitfalls.md C1。

两种评估口径
------------
  时间层：把真值里同一时刻的和弦并成一个"独立起音时刻"。
          音游真正要的是"这个时刻有东西可敲"，这是主口径。
  音符层：每个音符各自算一个目标。和弦里三个音只检出一个就记 2/3。
          它衡量的是"复音还原度"，比时间层苛刻得多。

用法
----
    python scripts/midi_eval.py testdata/demo_piano_dense_piano.wav
    python scripts/midi_eval.py --dir testdata
    python scripts/midi_eval.py testdata/x.wav --variant A B D E F
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

from transient_catcher import odf  # noqa: E402

VARIANTS = {
    "A": lambda x, sr: odf.rhydive_like(x, sr),
    "B": lambda x, sr: odf.perbin(x, sr, use_reassign=True, maxfilt=None),
    "C": lambda x, sr: odf.perbin(x, sr, use_reassign=True, maxfilt=17),
    "D": lambda x, sr: odf.perbin(x, sr, use_reassign=False, maxfilt=17),
    "E": lambda x, sr: odf.channel_a(x, sr),
    "F": lambda x, sr: odf.pitch_salience(x, sr),
}


# --------------------------------------------------------------------------
def run_variant(name, mono, sr):
    r = VARIANTS[name](mono, sr)
    arr = np.asarray(r["onsets"], dtype=np.float64).reshape(-1, 2)
    return arr[:, 0], arr[:, 1], r


def match_pairs(det, ref, tol):
    """一对一最近匹配。返回 [(i, j, 距离), ...]。

    先枚举所有在容差内的候选对，再按距离从小到大贪心 —— 比"按时间序抓最近的"
    更稳，尤其在密集段。候选量正比于检出数，不会爆内存。
    """
    det = np.asarray(det, dtype=np.float64)
    ref = np.asarray(ref, dtype=np.float64)
    if det.size == 0 or ref.size == 0:
        return []
    lo = np.searchsorted(ref, det - tol, side="left")
    hi = np.searchsorted(ref, det + tol, side="right")
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


def cluster_times(times, tol):
    """把容差内的时刻并成一个（同一时刻的和弦只算一次起音）。"""
    times = np.sort(np.asarray(times, dtype=np.float64))
    if times.size == 0:
        return times
    keep = [times[0]]
    for t in times[1:]:
        if t - keep[-1] > tol:
            keep.append(t)
    return np.asarray(keep)


def cluster_det(det, strength, tol):
    """把同一"敲击"内的多个检出并成一个（保留最强的那个）。

    必须和 GT 用**同一个**簇容差，否则不公平：
    真实编曲里为了做冲击力，常把两个鼓音符放在相隔十几毫秒内
    （用户的 夜航星.mid 里就有约 750 处），人耳和音游都只当一个敲击。
    GT 已经把它并成一个目标，检出如果不并，就会因为"一个动作报了两次"
    被记成误报。两边要么都并，要么都不并。
    """
    det = np.asarray(det, dtype=np.float64)
    strength = np.asarray(strength, dtype=np.float64)
    if det.size == 0 or tol <= 0.0:
        return det, strength
    order = np.argsort(det)
    det, strength = det[order], strength[order]
    kt, ks = [float(det[0])], [float(strength[0])]
    for i in range(1, len(det)):
        if det[i] - kt[-1] > tol:
            kt.append(float(det[i]))
            ks.append(float(strength[i]))
        elif strength[i] > ks[-1]:
            kt[-1] = float(det[i])
            ks[-1] = float(strength[i])
    return np.asarray(kt), np.asarray(ks)


def scores(n_target, n_det, n_hit):
    rec = n_hit / n_target if n_target else float("nan")
    pre = n_hit / n_det if n_det else float("nan")
    if n_target and n_det and n_hit:
        f1 = 2.0 * rec * pre / (rec + pre)
    else:
        f1 = 0.0
    return rec, pre, f1


def pct(v):
    return "  --  " if not np.isfinite(v) else "%5.0f%%" % (v * 100.0)


# --------------------------------------------------------------------------
def evaluate_one(wav, gt_path, variants, args):
    with open(gt_path, "r", encoding="utf-8") as fh:
        gt = json.load(fh)
    notes = gt["notes"]
    gt_note = np.asarray([n["onset_ms"] for n in notes], dtype=np.float64)
    gt_uni = cluster_times(gt_note, args.cluster_ms)

    x, sr = sf.read(wav, dtype="float32", always_2d=True)
    mono = x.mean(axis=1).astype(np.float64)
    dur_s = len(mono) / float(sr)

    ioi = np.diff(gt_uni)
    med_ioi = float(np.median(ioi)) if ioi.size else 1e9
    tol = float(max(3.0, min(args.tol_ms, 0.45 * med_ioi)))

    print("=" * 78)
    print("%s" % wav)
    print("  音色 %-6s ｜ 音频 %.2f s ｜ 真值 %d 个音符 / %d 个独立起音时刻 ｜ 约 %.1f BPM"
          % (gt.get("voice", "?"), dur_s, len(gt_note), len(gt_uni),
             gt.get("bpm", 0.0)))
    n_merge = len(gt_note) - len(gt_uni)
    if n_merge:
        print("  （%d 个音符与其他音符同时起音，已并入同一时刻；和弦用音符层看）"
              % n_merge)
    print("  容差 %.1f ms  （中位起音间隔 %.1f ms 决定上限 %.1f ms）"
          % (tol, med_ioi, 0.45 * med_ioi))

    summary = {}
    for v in variants:
        det, strength, _r = run_variant(v, mono, sr)
        n_raw = len(det)
        det, strength = cluster_det(det, strength, args.cluster_det_ms)
        pairs_u = match_pairs(det, gt_uni, tol)
        pairs_n = match_pairs(det, gt_note, tol)
        hit_u, hit_n = len(pairs_u), len(pairs_n)
        rec_u, pre_u, f1_u = scores(len(gt_uni), len(det), hit_u)
        rec_n, pre_n, f1_n = scores(len(gt_note), len(det), hit_n)

        print("-" * 78)
        extra = ("" if n_raw == len(det)
                 else "（%.0f ms 簇后 %d）" % (args.cluster_det_ms, len(det)))
        print("  变体 %s ｜ 检出 %d 个%s ｜ 密度 %.2f/s"
              % (v, n_raw, extra, len(det) / max(1e-9, dur_s)))
        print("  %-18s %6s %6s %6s %7s %7s %7s"
              % ("口径", "目标", "检出", "命中", "召回", "精确", "F1"))
        print("  %-18s %6d %6d %6d %7s %7s %7.2f"
              % ("时间层(独立时刻)", len(gt_uni), len(det), hit_u,
                 pct(rec_u), pct(pre_u), f1_u))
        print("  %-18s %6d %6d %6d %7s %7s %7.2f"
              % ("音符层(每个音符)", len(gt_note), len(det), hit_n,
                 pct(rec_n), pct(pre_n), f1_n))

        if pairs_u:
            bias = np.asarray([det[i] - gt_uni[j] for i, j, _d in pairs_u])
            q1, q2, q3 = np.percentile(bias, [25, 50, 75])
            print("  时间偏差(检出-真值)：中位 %+.1f ms ｜ 四分位 %+.1f ~ %+.1f ms ｜ 最大 %+.1f ms"
                  % (q2, q1, q3, float(np.max(np.abs(bias)))))
            hs = strength[[i for i, _j, _d in pairs_u]]
            fs = strength[[i for i in range(len(det))
                           if i not in {p[0] for p in pairs_u}]]
            if fs.size:
                print("  检出强度：命中中位 %.2f ｜ 未命中中位 %.2f"
                      % (float(np.median(hs)), float(np.median(fs))))

        # --- 按标签 ---
        labels = gt.get("labels") or []
        if labels:
            print("  按标签：")
            print("    %-22s %6s %6s %6s %7s %7s"
                  % ("标签(秒)", "目标", "检出", "命中", "召回", "精确"))
            for L in labels:
                a, b = L["start_ms"], L["end_ms"]
                ru = gt_uni[(gt_uni >= a) & (gt_uni < b)]
                dd = det[(det >= a) & (det < b)]
                if ru.size == 0 and dd.size == 0:
                    continue
                h = len(match_pairs(dd, ru, tol))
                r_, p_, _f = scores(len(ru), len(dd), h)
                note = ""
                if ru.size == 0 and dd.size:
                    note = "  <== 真值 0 个，全是误报"
                print("    %-22s %6d %6d %6d %7s %7s%s"
                      % ("%s(%.1f-%.1f)" % (L["name"], a / 1000.0, b / 1000.0),
                         len(ru), len(dd), h, pct(r_), pct(p_), note))

        if args.show_fp:
            used = {p[0] for p in pairs_u}
            fp = [i for i in range(len(det)) if i not in used]
            if fp:
                fp = sorted(fp, key=lambda i: det[i])
                print("  未匹配的检出（前 %d 个，共 %d 个）：" % (min(args.max_fp, len(fp)), len(fp)))
                for i in fp[:args.max_fp]:
                    t = det[i] / 1000.0
                    tag = ""
                    for L in labels:
                        if L["start_ms"] / 1000.0 <= t < L["end_ms"] / 1000.0:
                            tag = " [" + L["name"] + "]"
                            break
                    print("    %8.3f s  强度 %.2f%s" % (t, strength[i], tag))

        if args.sweep:
            print("  容差敏感性：", end="")
            for tt in (5.0, 10.0, 20.0, 30.0, 50.0):
                h = len(match_pairs(det, gt_uni, tt))
                r_ = h / max(1, len(gt_uni))
                p_ = h / max(1, len(det))
                print("  %dms %.0f%%/%.0f%%" % (tt, r_ * 100, p_ * 100), end="")
            print()

        summary[v] = (len(det), hit_u, rec_u, pre_u, f1_u, rec_n, pre_n, bias_med(pairs_u, det, gt_uni))
    return summary


def bias_med(pairs, det, ref):
    if not pairs:
        return 0.0
    return float(np.median([det[i] - ref[j] for i, j, _d in pairs]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="MIDI 测试集评估")
    ap.add_argument("wav", nargs="*", help="渲染出来的 WAV（真值取同名的 .gt.json）")
    ap.add_argument("--dir", default=None, help="扫描目录下所有 *.gt.json")
    ap.add_argument("--variant", "-v", nargs="+", default=["E"],
                    choices=list(VARIANTS), help="默认 E")
    ap.add_argument("--tol-ms", type=float, default=25.0)
    ap.add_argument("--cluster-ms", type=float, default=10.0,
                    help="真值里同一次敲击的合并容差（和弦、鼓双击）")
    ap.add_argument("--cluster-det-ms", type=float, default=None,
                    help="检出的合并容差，默认与 --cluster-ms 相同（必须对称）")
    ap.add_argument("--show-fp", action="store_true", help="列出未匹配的检出")
    ap.add_argument("--max-fp", type=int, default=30)
    ap.add_argument("--sweep", action="store_true", help="打印容差敏感性")
    args = ap.parse_args(argv)
    if args.cluster_det_ms is None:
        args.cluster_det_ms = args.cluster_ms

    wavs = list(args.wav)
    if args.dir:
        suffix = ".gt.json"
        wavs += [p[:-len(suffix)] + ".wav"
                 for p in sorted(glob.glob(os.path.join(args.dir, "*" + suffix)))]
    wavs = [w for w in wavs if os.path.exists(w)]
    if not wavs:
        print("没找到 WAV。先跑 scripts/midi_render.py，或用 --dir testdata")
        return 1

    grand = {}
    for w in wavs:
        gtp = os.path.splitext(w)[0] + ".gt.json"
        if not os.path.exists(gtp):
            print("跳过 %s：找不到 %s" % (w, gtp))
            continue
        s = evaluate_one(w, gtp, args.variant, args)
        for v, row in s.items():
            grand.setdefault(v, []).append((os.path.basename(w), row))

    if len(wavs) > 1 or len(args.variant) > 1:
        print("=" * 78)
        print("汇总（时间层召回 / 时间层精确）")
        print("  %-34s %s" % ("文件", "  ".join("%-13s" % v for v in args.variant)))
        for idx in range(len(grand[args.variant[0]])):
            name = grand[args.variant[0]][idx][0]
            cells = []
            for v in args.variant:
                _n, _h, ru, pu, _f1, _rn, _pn, _b = grand[v][idx][1]
                cells.append("%-13s" % ("%.0f%% / %.0f%%" % (ru * 100, pu * 100)))
            print("  %-34s %s" % (name[:34], "  ".join(cells)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
