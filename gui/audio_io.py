"""音频载入，以及从 RhyDive 本地曲库导入。

导入是本工具的第一步使用路径，所以这里同时提供：
  - 任意音频文件（soundfile 能读的格式）
  - RhyDive 已经下载到本地的曲目（直接扫它的曲库目录）
"""

from __future__ import annotations

import glob
import json
import os

import numpy as np
import soundfile as sf

AUDIO_EXT = (".wav", ".flac", ".ogg", ".aiff", ".aif", ".mp3", ".m4a")


def load_audio(path: str):
    """返回 (单声道 float64, 采样率)。"""
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1).astype(np.float64)
    return mono, int(sr)


def rhydive_library_dirs() -> list:
    """RhyDive 可能的曲库根目录。"""
    roots = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            roots.append(os.path.join(base, "RhyDive", "Library", "songs"))
    return [r for r in roots if os.path.isdir(r)]


def scan_rhydive_library() -> list:
    """扫描 RhyDive 本地曲库，返回 [{title, artist, path}, ...]。"""
    out = []
    seen = set()
    for base in rhydive_library_dirs():
        for sj in glob.glob(os.path.join(base, "*", "song.json")):
            try:
                with open(sj, "r", encoding="utf-8") as fh:
                    info = json.load(fh)
            except Exception:
                continue
            song = info.get("song", {}) or {}
            asset = song.get("audio") or ""
            if not asset:
                continue
            cand = os.path.join(os.path.dirname(sj), "assets", os.path.basename(asset))
            if not os.path.isfile(cand):
                hits = glob.glob(os.path.join(os.path.dirname(sj), "assets", "audio.*"))
                if not hits:
                    continue
                cand = hits[0]
            if cand in seen:
                continue
            seen.add(cand)
            out.append({
                "title": song.get("title") or os.path.basename(cand),
                "artist": song.get("artist") or "",
                "path": cand,
            })
    out.sort(key=lambda d: (d["artist"], d["title"]))
    return out
