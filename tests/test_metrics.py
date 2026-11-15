# -*- coding: utf-8 -*-
"""绩效指标用例（S09-01：三日权益手算、空值规则、峰值分母回撤、√250、短区间标注）。"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf.metrics import compute_metrics, compute_turnover  # noqa: E402
from qf.engine import Trade                # noqa: E402


def _curve(values, start_day=1):
    return [(f"2021-01-{start_day+i:02d}", v, 0.0) for i, v in enumerate(values)]


class TestS09(unittest.TestCase):
    def test_three_day_hand_calc(self):
        """工程正文三日权益手算：10.0万/10.1万/9.9万 → 累计 −1%；短区间年化标参考。"""
        mr = compute_metrics(_curve([100000.0, 101000.0, 99000.0]), [])
        self.assertTrue(abs(mr.values["累计收益"] - (-0.01)) < 1e-9)
        self.assertTrue(mr.short_period)               # 2个交易日 <100 → 参考
        ann = mr.values["年化收益"]
        expect = (1 - 0.01) ** (250 / 2) - 1
        self.assertTrue(abs(ann - expect) < 1e-9)
        self.assertIn("参考", mr.notes["年化收益"])

    def test_null_rules(self):
        """三类空值规则：无交易→胜率/盈亏比空值；权益恒定→夏普空值。"""
        mr = compute_metrics(_curve([100000.0] * 150), [])
        self.assertIsNone(mr.values["胜率"])
        self.assertIsNone(mr.values["盈亏比"])
        self.assertIsNone(mr.values["夏普"])           # 波动为零 → 空值而非无穷
        self.assertTrue(abs((mr.values["年化波动"] or 0.0)) < 1e-12)

    def test_null_rules_with_trades(self):
        """有交易但零亏损 → 盈亏比空值；全胜 → 胜率1.0。"""
        t = Trade("d", "600001", "SELL", 11.0, 11.0, 100, 1100.0, 5.5, 0.5, 100.0, 0)
        mr = compute_metrics(_curve([100000.0] + [101000.0] * 150), [t])
        self.assertTrue(abs(mr.values["胜率"] - 1.0) < 1e-9)
        self.assertIsNone(mr.values["盈亏比"])          # 平均亏损为0 → 空值

    def test_max_drawdown_peak_denominator(self):
        """最大回撤分母=峰值（不是初始资金）：[100,120,90,95] → −25%，起止与持续期。"""
        eq = _curve([100000.0, 120000.0, 90000.0, 95000.0])
        mr = compute_metrics(eq, [])
        self.assertTrue(abs(mr.values["最大回撤"] - (-0.25)) < 1e-9)
        self.assertEqual(mr.notes["最大回撤"], "分母=峰值；2021-01-02 → 2021-01-03")
        self.assertEqual(mr.values["回撤持续期(交易日)"], 1)

    def test_sharp_sqrt250(self):
        """夏普年化系数=√250（写成250会夸张数倍——冻结易错点）。"""
        # 交替 ±1%：mean=0，夏普=0
        vals = [100000.0]
        for i in range(1, 121):
            vals.append(vals[-1] * (1.01 if i % 2 else 0.99))
        mr = compute_metrics(_curve(vals), [])
        self.assertTrue(abs(mr.values["夏普"]) < 1e-9)
        # 全+1%: std=0 → 空值；全+1%与+2%交替可手算
        vals2 = [100000.0]
        for i in range(120):
            vals2.append(vals2[-1] * (1.02 if i % 2 == 0 else 1.01))
        mr2 = compute_metrics(_curve(vals2), [])
        rets = [vals2[i] / vals2[i - 1] - 1 for i in range(1, len(vals2))]
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / len(rets)
        expect = m / math.sqrt(var) * math.sqrt(250)
        # 与手算（√250 系数）逐位一致——若实现写成 250，此断言必失败
        self.assertTrue(abs(mr2.values["夏普"] - expect) < 1e-9)

    def test_win_rate_and_payoff(self):
        sells = [
            Trade("d1", "s", "SELL", 11.0, 11.0, 100, 1100, 5.5, 0.5, 100.0, 0),
            Trade("d2", "s", "SELL", 9.0, 9.0, 100, 900, 5.0, 0.5, -50.0, 0),
            Trade("d3", "s", "SELL", 10.0, 10.0, 100, 1000, 5.0, 0.5, 20.0, 0),
        ]
        mr = compute_metrics(_curve([100000.0] + [100500.0] * 150), sells)
        self.assertTrue(abs(mr.values["胜率"] - 2 / 3) < 1e-9)
        avg_win = (100.0 + 20.0) / 2
        self.assertTrue(abs(mr.values["盈亏比"] - avg_win / 50.0) < 1e-9)

    def test_turnover_double_sided(self):
        """换手率双边口径年化：Σ金额÷日均持仓市值÷交易日数×250×100%。"""
        v = compute_turnover(100000.0, [50000.0] * 11, 10)
        self.assertTrue(abs(v - 0.2 * 250 * 100.0) < 1e-9)
        self.assertIsNone(compute_turnover(1000.0, [0.0] * 10, 10))   # 全程空仓→空值

    def test_trade_count(self):
        t = Trade("d", "s", "BUY", 10.0, 10.0, 100, 1000, 5.0, 1.0, 0.0, 0)
        mr = compute_metrics(_curve([100000.0] * 150), [t])
        self.assertEqual(mr.values["成交笔数"], 1)


if __name__ == "__main__":
    unittest.main()
