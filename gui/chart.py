"""GUI 侧的谱面装载。"""

from __future__ import annotations

import numpy as np

from transient_catcher import osu


def load_notes(path: str) -> np.ndarray:
    return osu.parse_osu(path)


def auto_match(audio_path: str):
    try:
        return osu.match_chart(audio_path)
    except Exception:
        return None
