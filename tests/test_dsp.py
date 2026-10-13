"""DSP 单元自检。

重分配的符号与量纲最容易错 —— 而且错了不会崩，只会让结果悄悄变差（P0 就踩到过）。
所以这里必须有断言：一个纯音的 f_hat 必须落在它的真实频率上。

用法：
  python tests/test_dsp.py
"""

from __future__ import annotations

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from transient_catcher import dsp  # noqa: E402

SR, WIN, HOP = 44100, 2048, 441


def test_reassignment_identity_on_pure_tone():
    """纯音的频率重分配必须精确回到真实频率。"""
    t = np.arange(SR) / SR
    worst = 0.0
    for f0 in (110.0, 220.0, 440.0, 880.0, 1760.0):
        x = np.sin(2.0 * np.pi * f0 * t)
        X, f_hat, _ = dsp.freq_reassigned(x, SR, WIN, HOP)
        row = 20
        k = int(np.argmax(np.abs(X[row])))
        est = float(f_hat[row, k])
        err_cents = abs(1200.0 * np.log2(est / f0))
        worst = max(worst, err_cents)
        assert err_cents < 5.0, "f0=%.1f 重分配误差 %.1f 音分（应 < 5）" % (f0, err_cents)
    print("  [ok] 纯音重分配：最大误差 %.2f 音分" % worst)


def test_log_grid_is_quarter_tone():
    g = dsp.log_freq_grid(55.0, 10000.0, 4)
    step = 1200.0 * np.log2(g[1] / g[0])
    assert abs(step - 25.0) < 1e-9, "栅格步长应为 25 音分（1/4 半音），实际 %.4f" % step
    assert abs(g[0] - 55.0) < 1e-9
    assert g[-1] <= 10000.0 * 1.02, "栅格只应最多超出一个 bin"
    print("  [ok] 对数栅格：步长 %.1f 音分，%d 个 bin" % (step, len(g)))


def test_max_filter_absorbs_shift():
    """max-filter 必须让"整体平移"的谱在差分后归零。"""
    spec = np.zeros((2, 64))
    spec[0, 30] = 1.0
    spec[1, 33] = 1.0          # 平移 3 个 bin
    filt = dsp.max_filter_freq(spec, 9)[:-1]   # 只取前一帧的滤波结果
    d = np.maximum(0.0, spec[1:] - filt)
    assert d.sum() < 1e-12, "平移 3 bin 后不应有剩余通量，实际 %.6f" % d.sum()
    print("  [ok] max-filter：3 bin 的整体平移被完全吸收")


def test_moving_average_matches_naive():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(200)
    got = dsp_moving(odf_moving_average(x, 7))
    ref = np.array([x[max(0, i - 7):min(200, i + 8)].mean() for i in range(200)])
    # 边缘补齐方式略不同，只比较内部
    assert np.allclose(got[20:-20], ref[20:-20]), "滑动平均内部应一致"
    print("  [ok] 滑动平均与朴素实现一致（内部）")


def odf_moving_average(x, half):
    from transient_catcher import odf
    return odf.moving_average(x, half)


def dsp_moving(x):
    return x


if __name__ == "__main__":
    print("tests/test_dsp.py")
    test_log_grid_is_quarter_tone()
    test_reassignment_identity_on_pure_tone()
    test_max_filter_absorbs_shift()
    test_moving_average_matches_naive()
    print("全部通过。")
