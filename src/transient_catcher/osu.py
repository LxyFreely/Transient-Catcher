"""osu! 谱面解析 —— 当作人工 ground truth 用。

谱面是"人挑出来的可玩音符"，不等于音频里的全部音头（用户上传曲往往更密）。
所以它给的是**下界**：谱面里的音头必须被检出；多出来的需要另行判断。
"""

from __future__ import annotations

import glob
import os

import numpy as np

RHYDIVE_SONGS = os.path.join("RhyDive", "Library", "songs")


def parse_osu(path: str) -> np.ndarray:
    """返回按时间排序的音符时刻（秒）。转盘不计。"""
    sec, out = None, []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("[") and line.endswith("]"):
                sec = line[1:-1]
                continue
            if sec != "HitObjects":
                continue
            p = line.split(",")
            try:
                t, typ = float(p[2]) / 1000.0, int(p[3])
            except Exception:
                continue
            if typ & 8:                    # spinner
                continue
            out.append(t)
    return np.asarray(sorted(out), dtype=np.float64)


def library_charts():
    """RhyDive 本地曲库里的所有 .osu，返回 [{title, artist, path}, ...]。"""
    base = os.environ.get("APPDATA")
    if not base:
        return []
    root = os.path.join(base, RHYDIVE_SONGS)
    out = []
    for sj in glob.glob(os.path.join(root, "*", "song.json")):
        asset_dir = os.path.join(os.path.dirname(sj), "assets")
        fmts = glob.glob(os.path.join(asset_dir, "*.osu"))
        if not fmts:
            continue
        title = os.path.basename(os.path.dirname(sj))
        try:
            import json
            with open(sj, "r", encoding="utf-8") as fh:
                song = json.load(fh).get("song", {})
            title = song.get("title") or title
        except Exception:
            pass
        for f in fmts:
            out.append({"title": title, "path": f, "name": os.path.basename(f),
                        "dir": os.path.basename(os.path.dirname(sj))})
    return out


def match_chart(audio_name: str):
    """按音频文件名猜一首曲库谱面。

    文件名通常是「艺术家 - 标题.flac」，而曲库里的 song.json 只有标题，
    目录名却是「艺术家-标题-编号-哈希」。所以用**多关键词打分**，
    而不是拿单个词去子串匹配（只取" - "前面那截会匹配不到）。
    同分时取音符最多的那张（通常是最难难度）。
    """
    stem = os.path.splitext(os.path.basename(audio_name))[0].lower()
    toks = [t.strip() for t in stem.replace("_", " ").split(" - ")]
    toks = [t for t in toks if len(t) >= 3]
    if not toks:
        return None
    best, best_score, best_n = None, 0, -1
    for c in library_charts():
        hay = (c["title"] + " " + c.get("dir", "")).lower()
        score = sum(1 for t in toks if t in hay)
        if score == 0:
            continue
        n = len(parse_osu(c["path"]))
        if score > best_score or (score == best_score and n > best_n):
            best, best_score, best_n = c, score, n
    return best
