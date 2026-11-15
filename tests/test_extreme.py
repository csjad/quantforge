# -*- coding: utf-8 -*-
"""TC-EXT-001~006 六类极端行情用例（S16-02：断言带数值边界与失败信息，容忍度为零）。
数据集由 qf.extreme 程序化生成（A3 条件：禁止手工构造），固定结构可复现。"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf import config as C                       # noqa: E402
from qf import extreme as X                      # noqa: E402
from qf.engine import BacktestEngine             # noqa: E402


class Scripted:
    def __init__(self, script):
        self.script = script
        self.warmup_windows = [0]
        self.engine = None

    def attach(self, engine):
        self.engine = engine

    def on_bar(self, t):
        return self.script.get(t, [])


class StopLossStrategy:
    """模板二式止损路径：跌破含费均价(1−stop)即清仓（只用截至当根数据）。"""
    warmup_windows = [0]
    stop = 0.05

    def attach(self, engine):
        self.engine = engine

    def on_bar(self, t):
        sigs = []
        seq = self.engine.bars["300003"]
        if t >= len(seq):
            return sigs
        bar = seq[t]
        p = self.engine._pos("300003")
        if not p.held:
            if t == 0:
                sigs.append(("BUY", "300003", self.engine.equity_now() * 0.30))
        elif bar.close < p.avg_cost * (1 - self.stop):
            sigs.append(("SELL", "300003", -1))
        return sigs


def _run(bars, calendar, strategy):
    return BacktestEngine(bars, strategy, calendar, seed_note="extreme-ut").run()


class TestEXT(unittest.TestCase):
    def test_tc_ext_001_limit_up_run_zero_buy(self):
        """连续涨停十日一字板 → 买入成交为零（若非零，失败信息给出前三笔日期价格）。"""
        bars, cal = X.scenario_limit_up_run()
        eng = _run(bars, cal, Scripted({t: [("BUY", "600001", 30000.0)]
                                       for t in range(1, 11)}))
        self.assertEqual(eng.filled_count, 0,
                         msg="一字涨停期间出现成交！" + X.failure_info(eng.trades))
        voided = [o for o in eng.orders if o.status == "已作废"]
        self.assertEqual(len(voided), 10)                      # 十日全部作废
        self.assertTrue(all("一字涨停" in o.reason for o in voided))
        self.assertEqual(eng.conservation_failures, 0)

    def test_tc_ext_002_limit_down_no_forced_sell(self):
        """连续跌停十日 → 卖出零成交（C-042 定案：不允许按跌停价强行成交）。"""
        bars, cal = X.scenario_limit_down_run()
        script = {0: [("BUY", "600001", 30000.0)]}
        # 卖出信号 t=1..10 → 执行于 t=2..11（全部一字跌停日）；t=11 的信号会在
        # 恢复正常日成交（合法），故不纳入"强拆断言"区间
        script.update({t: [("SELL", "600001", -1)] for t in range(1, 11)})
        eng = _run(bars, cal, Scripted(script))
        sells = [t for t in eng.trades if t.side == "SELL"]
        self.assertEqual(len(sells), 0,
                         msg="一字跌停出现卖出成交=强行成交！" + X.failure_info(sells))
        self.assertGreater(eng._pos("600001").held, 0)         # 持仓仍在（不强制平仓）
        voided = [o for o in eng.orders if o.status == "已作废"]
        self.assertTrue(all("一字跌停" in o.reason for o in voided))
        self.assertEqual(eng.conservation_failures, 0)

    def test_tc_ext_003_crash_stop_loss_with_slippage(self):
        """单日腰斩（合法口径：创业板−19.9%崩盘）→ 止损触发且滑点记录。"""
        bars, cal = X.scenario_crash_gem()
        eng = _run(bars, cal, StopLossStrategy())
        buys = [t for t in eng.trades if t.side == "BUY"]
        sells = [t for t in eng.trades if t.side == "SELL"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1, msg="止损未触发" + X.failure_info(sells))
        self.assertGreater(sells[0].slip_cost, 0.0)            # 滑点已记录
        self.assertLess(sells[0].realized_pnl, 0.0)            # 止损=实现亏损
        self.assertLessEqual(sells[0].exec_price,
                             sells[0].open_price * (1 - C.SLIPPAGE) + 1e-9)
        self.assertEqual(eng.conservation_failures, 0)

    def test_tc_ext_004_suspension_90d_valuation_frozen(self):
        """长期停牌九十日 → 持仓不变、估值沿用最后有效价（权益曲线停牌期恒定）。"""
        bars, cal = X.scenario_suspension_90()
        eng = _run(bars, cal, Scripted({0: [("BUY", "600001", 30000.0)]}))
        self.assertEqual(len(eng.trades), 1)                   # 仅建仓一笔，无虚假成交
        eq = [e for _, e, _m in eng.equity_curve]
        suspend = eq[3:92]                                     # d[2..91] 停牌区间
        self.assertTrue(all(abs(v - suspend[0]) < 1e-9 for v in suspend),
                        msg="停牌期权益发生变动（估值被污染）")
        self.assertEqual(eng.conservation_failures, 0)
        self.assertEqual(eng._pos("600001").held,
                         eng.trades[0].qty)                    # 持仓不变

    def test_tc_ext_005_exright_adjusted_continuous(self):
        """除权跳空 → 复权序列收益连续（无人工回撤），引擎消费复权序列守恒通过。"""
        raw, adj, cal = X.scenario_exright_gap()
        # 原始序列存在非法缺口（−50%级），属 S02 数据校验层职责——引擎不得直接消费
        closes_raw = [b.close for b in raw["600001"]]
        gaps_raw = min(closes_raw[i] / closes_raw[i - 1] - 1 for i in range(1, len(closes_raw)))
        self.assertLess(gaps_raw, -0.40)                       # 原始序列确有跳空
        # 复权序列连续
        closes_adj = [b.close for b in adj["600001"]]
        worst = min(closes_adj[i] / closes_adj[i - 1] - 1 for i in range(1, len(closes_adj)))
        self.assertGreater(worst, -0.05)                       # 复权后单日变动合法
        eng = _run(adj, cal, Scripted({}))
        self.assertEqual(eng.conservation_failures, 0)
        self.assertEqual(len(set(e for _, e, _m in eng.equity_curve)), 1)  # 无交易权益恒定

    def test_tc_ext_006_volume_dry_liquidity_filter(self):
        """量能骤降九成 → 流动性过滤生效（拦截建仓）；关闭开关则放行（对照）。"""
        bars, cal = X.scenario_volume_dry()
        old = C.MIN_VOLUME_LIMIT
        try:
            C.MIN_VOLUME_LIMIT = 50000                          # 下限=正常量1/4
            eng = _run(bars, cal, Scripted({5: [("BUY", "600001", 30000.0)],
                                            6: [("BUY", "600001", 30000.0)]}))
            self.assertEqual(eng.filled_count, 0,
                             msg="量能过滤未拦截！" + X.failure_info(eng.trades))
            self.assertEqual(eng.blocked_signals, 2)
            self.assertEqual(eng.reject_reasons.get("流动性不足(量能过滤)"), 2)
        finally:
            C.MIN_VOLUME_LIMIT = old
        # 对照：过滤关闭时正常放行
        eng2 = _run(bars, cal, Scripted({5: [("BUY", "600001", 30000.0)]}))
        self.assertEqual(eng2.filled_count, 1)
        self.assertEqual(eng2.conservation_failures, 0)


if __name__ == "__main__":
    unittest.main()
