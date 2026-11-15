# -*- coding: utf-8 -*-
"""指标教科书对照用例（S04-02 TC-IND 关键子集，容差 1e-6）。"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf import indicators as I  # noqa: E402

TOL = 1e-6


def close(a, b, tol=TOL):
    return a is not None and abs(a - b) <= tol


class TestMA(unittest.TestCase):
    def test_tc001_textbook(self):
        out = I.sma([10, 11, 12, 13, 14], 3)
        self.assertTrue(close(out[2], 11))
        self.assertTrue(close(out[3], 12))
        self.assertTrue(close(out[4], 13))

    def test_tc002_boundary_and_gap(self):
        out = I.sma([10, 11, 12, 13, 14], 3)
        self.assertIsNone(out[0])            # 窗口未满不得填充首值
        self.assertIsNone(out[1])
        # 缺口注入（前向填充陷阱回归）：缺口日无值，缺口后满窗按真实数据计算
        gapped = I.sma([10, None, 12, 13, 14], 3)
        self.assertIsNone(gapped[1])
        self.assertIsNone(gapped[2])         # 窗口含缺口 → 中断（不跳过计数）
        self.assertIsNone(gapped[3])
        self.assertTrue(close(gapped[4], 13))  # 缺口后满窗恢复

    def test_tc003_monotonic(self):
        a = I.sma([1, 2, 3, 4, 5], 3)
        b = I.sma([11, 12, 13, 14, 15], 3)
        for x, y in zip(a, b):
            if x is not None:                  # 未满窗的 None 跳过
                self.assertTrue(close(y, x + 10))


class TestEMA(unittest.TestCase):
    def test_tc004_textbook_seed_simple_mean(self):
        out = I.ema([10, 11, 12, 13, 14], 3)   # α=0.5
        self.assertTrue(close(out[2], 11.0))   # 初值=前3根简单均值
        self.assertTrue(close(out[3], 12.0))
        self.assertTrue(close(out[4], 13.0))

    def test_tc005_wrong_seed_differs(self):
        # 首值初值法（错误）：seed=10 → 10.5 → 11.25 ≠ 正确值 11
        wrong = 10.0
        for c in [11, 12]:
            wrong = wrong + 0.5 * (c - wrong)
        self.assertGreater(abs(wrong - 11.0), 1e-6)


class TestWMA(unittest.TestCase):
    def test_tc007_textbook(self):
        out = I.wma([10, 11, 12], 3)
        self.assertTrue(close(out[2], 64.0 / 6.0))


class TestStd(unittest.TestCase):
    def test_tc010_textbook_population(self):
        out = I.stddev([1, 2, 3, 4, 5], 5)
        self.assertTrue(close(out[4], math.sqrt(2)))

    def test_tc011_all_same_zero(self):
        out = I.stddev([5, 5, 5], 3)
        self.assertTrue(close(out[2], 0.0))
        mid, up, lo, bw = I.boll([5, 5, 5, 5, 5], 5, 2.0)
        self.assertTrue(close(bw[4], 0.0))     # 带宽=0
        self.assertTrue(close(up[4], 5.0))     # 上下轨=中轨
        self.assertTrue(close(lo[4], 5.0))


class TestATR(unittest.TestCase):
    def test_tc013_first_tr_hl(self):
        tr = I.true_range([11], [9], [10])
        self.assertTrue(close(tr[0], 2.0))     # 首根无前收 TR=H−L

    def test_tc014_wilder_seeded(self):
        highs = [11, 12, 11.5, 11.8, 12.2]
        lows = [9, 10, 10.5, 10.8, 11.0]
        closes = [10, 11, 11.2, 11.5, 11.9]
        out = I.atr(highs, lows, closes, 3)
        self.assertIsNone(out[1])              # 窗口未满空值
        self.assertIsNotNone(out[2])           # 第 N 根起有值
        trs = I.true_range(highs, lows, closes)
        self.assertTrue(close(out[2], sum(trs[:3]) / 3))  # Wilder 初值=前N根均值


class TestROC(unittest.TestCase):
    def test_tc016_textbook(self):
        out = I.roc([10, 11], 1)
        self.assertTrue(close(out[1], 10.0))
        self.assertIsNone(out[0])

    def test_tc017_div_zero(self):
        out = I.roc([0, 10], 1)
        self.assertIsNone(out[1])              # C(t−N)=0 → 空值

    def test_tc018_symmetry(self):
        a = I.roc([100, 110], 1)[1]
        b = I.roc([100, 90], 1)[1]
        self.assertTrue(close(a, -b))


class TestRSI(unittest.TestCase):
    def test_tc019_all_up_is_100(self):
        closes = [10 + i for i in range(20)]
        out = I.rsi(closes, 14)
        self.assertTrue(close(out[14], 100.0))

    def test_tc020_all_same_none(self):
        closes = [10.0] * 20
        out = I.rsi(closes, 14)
        self.assertIsNone(out[14])             # U=D=0 → RS 未定义 → 空值
        self.assertIsNone(out[19])

    def test_tc021_symmetry_mirror(self):
        """对称性（镜像）：符号翻转 → RSI' = 100 − RSI（RSI=100×U/(U+D)，翻转交换
        U/D）。"分母写成U−D"的半对公式不满足镜像，即被此检查暴露。"""
        up = [100.0, 102.0, 101.0, 103.0, 102.0, 104.0, 103.0, 105.0,
              104.0, 106.0, 105.0, 107.0, 106.0, 108.0, 107.0, 109.0]
        dn = [200.0 - v for v in up]                     # 收益精确取反
        ru = I.rsi(up, 14)
        rd = I.rsi(dn, 14)
        for t in range(14, 16):
            self.assertIsNotNone(ru[t])
            self.assertTrue(close(ru[t] + rd[t], 100.0))
            self.assertTrue(0.0 <= ru[t] <= 100.0)


class TestBOLL(unittest.TestCase):
    def test_tc022_bands(self):
        closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
        mid, up, lo, bw = I.boll(closes, 5, 2.0)
        s = I.stddev(closes, 5)[4]
        self.assertTrue(close(up[4], mid[4] + 2 * s))
        self.assertTrue(close(lo[4], mid[4] - 2 * s))

    def test_tc023_zero_band(self):
        closes = [10.0] * 6
        mid, up, lo, bw = I.boll(closes, 5, 2.0)
        self.assertTrue(close(bw[5], 0.0))
        self.assertTrue(close(up[5], mid[5]))


class TestVolume(unittest.TestCase):
    def test_tc025_vol_ma(self):
        out = I.vol_ma([100, 200, 300], 2)
        self.assertTrue(close(out[1], 150.0))
        self.assertTrue(close(out[2], 250.0))

    def test_tc028_029_volume_ratio(self):
        vols = [100, 200, 100, 100, 400, 400, 400]
        out = I.volume_ratio(vols, 3)
        self.assertIsNone(out[4])            # t<2n−1 → 窗口未满
        self.assertIsNotNone(out[5])
        self.assertTrue(close(out[5], (sum(vols[3:6]) / 3) / (sum(vols[2:5]) / 3)))
        zeros = [0, 0, 0, 0, 0, 100, 100]
        self.assertIsNone(I.volume_ratio(zeros, 3)[5])   # 过去均值为0 → 空值

    def test_tc030_scale_invariant(self):
        vols = [100, 200, 100, 100, 400, 400, 400]
        r1 = I.volume_ratio(vols, 3)[5]
        r2 = I.volume_ratio([v * 1000 for v in vols], 3)[5]
        self.assertTrue(close(r1, r2))         # 量纲：整体乘常数，量比不变


class TestMACD(unittest.TestCase):
    def test_reference_cross_check(self):
        """与测试内独立参考实现交叉核对（同参数同序列）。"""
        closes = [10 + 0.2 * i + (0.3 if i % 5 == 0 else 0) for i in range(40)]

        def ref_ema(vals, n):
            a = 2.0 / (n + 1)
            out = [None] * len(vals)
            prev = None
            for t, v in enumerate(vals):
                if prev is None:
                    if t + 1 >= n:
                        prev = sum(vals[:n]) / n
                        out[t] = prev
                else:
                    prev = prev + a * (v - prev)
                    out[t] = prev
            return out

        dif_ref = [None if (a is None or b is None) else a - b
                   for a, b in zip(ref_ema(closes, 12), ref_ema(closes, 26))]
        dif, dea, hist = I.macd(closes)
        for t in range(35, 40):
            self.assertTrue(close(dif[t], dif_ref[t]))              # DIF 交叉核对
            self.assertTrue(close(hist[t], 2.0 * (dif[t] - dea[t])))  # 柱=2×(DIF−DEA)
        self.assertIsNotNone(dea[39])


class TestKDJ(unittest.TestCase):
    def test_tc034_init50_recursion(self):
        highs = [12.0] * 10
        lows = [10.0] * 10
        closes = [10.0, 10.5, 11.0, 11.5, 12.0, 11.0, 10.8, 10.2, 10.9, 11.4]
        k, d, j = I.kdj(highs, lows, closes, 9, 3)
        rsv8 = (closes[8] - 10.0) / 2.0 * 100.0
        k8 = 2 / 3 * 50 + 1 / 3 * rsv8
        self.assertTrue(close(k[8], k8))
        d8 = 2 / 3 * 50 + 1 / 3 * k8
        self.assertTrue(close(d[8], d8))
        self.assertTrue(close(j[8], 3 * k8 - 2 * d8))

    def test_tc035_flat_range_none(self):
        highs = [10.0] * 10
        lows = [10.0] * 10
        closes = [10.0] * 10
        k, d, j = I.kdj(highs, lows, closes)
        self.assertIsNone(k[9])                # H9=L9 → RSV 未定义 → 空值


class TestCCI(unittest.TestCase):
    def test_tc040_hand_calc(self):
        highs = [12, 12.4, 12.8]
        lows = [10, 10.4, 10.8]
        closes = [11, 11.4, 11.8]
        out = I.cci(highs, lows, closes, 3)
        tps = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        m = sum(tps) / 3
        md = sum(abs(v - m) for v in tps) / 3
        self.assertTrue(close(out[2], (tps[2] - m) / (0.015 * md)))

    def test_tc041_md_zero(self):
        out = I.cci([10, 10, 10], [10, 10, 10], [10, 10, 10], 3)
        self.assertIsNone(out[2])              # MD=0 → 空值


class TestOBV(unittest.TestCase):
    def test_tc043_044_hand_calc(self):
        closes = [10, 11, 12, 11, 11]
        vols = [100, 200, 300, 400, 500]
        out = I.obv(closes, vols)
        self.assertEqual(out[0], 0.0)          # 首根 OBV=0
        self.assertTrue(close(out[1], 200))
        self.assertTrue(close(out[2], 500))
        self.assertTrue(close(out[3], 100))    # 跑减
        self.assertTrue(close(out[4], 100))    # 平盘不变


if __name__ == "__main__":
    unittest.main()
