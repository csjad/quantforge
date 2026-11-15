# -*- coding: utf-8 -*-
"""引擎核心用例：S03-01 §12/§13 数字走查、T+1、涨跌停三情形、停牌作废、三守恒。"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf import config as C                     # noqa: E402
from qf.engine import BacktestEngine, Bar, max_buyable_lots  # noqa: E402
from qf.strategies import MaCross              # noqa: E402
from qf.sample_data import gen_sample_bars, gen_calendar    # noqa: E402


class Scripted:
    """脚本策略：按 {t: [signals]} 回放信号（信号只能来自 t 收盘，引擎保证次序）。"""

    def __init__(self, script):
        self.script = script
        self.warmup_windows = [0]
        self.engine = None

    def attach(self, engine):
        self.engine = engine

    def on_bar(self, t):
        return self.script.get(t, [])


def _dates(n, start=date(2021, 1, 4)):
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _bars(symbol, dates, opens, highs, lows, closes, vols=None):
    vols = vols or [100000.0] * len(dates)
    return {symbol: [Bar(d, o, h, l, c, v) for d, o, h, l, c, v in
                     zip(dates, opens, highs, lows, closes, vols)]}


class TestS03Walkthrough(unittest.TestCase):
    def test_s03_12_full_trade_walkthrough(self):
        """§12 数字走查：初始¥100,000，前收10.00，开盘10.05，仓位上限30%。"""
        d = _dates(2)
        bars = _bars("600001", d,
                     opens=[10.00, 10.05], highs=[10.02, 10.06],
                     lows=[9.98, 10.01], closes=[10.00, 10.04])
        eng = BacktestEngine(bars, Scripted({0: [("BUY", "600001", 30000.0)]}), d)
        eng.run()
        self.assertEqual(eng.filled_count, 1)
        tr = eng.trades[0]
        self.assertEqual(tr.qty, 2900)                       # 2982→整手2900（勘误口径）
        self.assertTrue(abs(tr.exec_price - 10.06005) < 1e-6)  # 开盘价×(1+滑点)
        # 成交金额 29145 元口径（原文按开盘价10.05计）；实际含滑点金额 29174.145
        self.assertTrue(abs(tr.amount - 2900 * 10.06005) < 1e-6)
        self.assertTrue(abs(tr.fees - (7.29 + 0.29)) < 1e-9)   # 佣金7.29+过户费0.29
        self.assertTrue(abs(tr.slip_cost - 29.145) < 1e-6)     # 滑点 2900×10.05×0.001
        total_out = 2900 * 10.06005 + 7.29 + 0.29              # = 29181.725 ≈ 29181.73
        self.assertTrue(abs(total_out - 29181.73) < 0.01)
        self.assertTrue(abs(eng.account.cash_available - (100000 - total_out)) < 1e-6)
        self.assertTrue(abs(eng.account.cash_available - 70818.27) < 0.01)
        self.assertEqual(eng.conservation_failures, 0)

    def test_s03_13_insufficient_funds_reject(self):
        """§13：可用¥990<一手含费 → 信号拒单并记录原因，不允许融通。"""
        d = _dates(2)
        bars = _bars("600001", d, [10.00, 10.05], [10.02, 10.06],
                     [9.98, 10.01], [10.00, 10.04])
        eng = BacktestEngine(bars, Scripted({0: [("BUY", "600001", 30000.0)]}), d)
        eng.account.cash_available = 990.0
        eng.account.cash_ledger = 990.0
        eng.run()
        self.assertEqual(eng.filled_count, 0)
        self.assertEqual(eng.rejected_count, 1)
        reject = [o for o in eng.orders if o.status == "已拒"]
        self.assertEqual(len(reject), 1)
        self.assertEqual(reject[0].reason, "资金不足一手")

    def test_s03_13_max_lots_recalc(self):
        """资金反算：一手含费=100股×10.01+佣金5+过户费0.01=1006.01（复算口径；
        蓝图§13原文过户费'0.1元'为笔误，1000×0.00001=0.01，铁律一数值优先）。
        9900÷1006.01≈9.84 → 9手，与蓝图结论的手数一致。"""
        self.assertEqual(max_buyable_lots(9900.0, 10.01, "600001"), 9)
        self.assertEqual(max_buyable_lots(990.0, 10.01, "600001"), 0)
        # 深市无过户费
        self.assertEqual(max_buyable_lots(9900.0, 10.01, "000002"), 9)


class TestT1(unittest.TestCase):
    def test_t1_overnight_unlock_and_sell(self):
        """走查口径：D日开盘买入 → D日收盘卖单（冻结）→ D+1开盘前解锁 → D+1开盘卖出。"""
        d = _dates(3)
        bars = _bars("600001", d, [10.00, 10.20, 10.40], [10.10, 10.30, 10.50],
                     [9.95, 10.10, 10.30], [10.00, 10.20, 10.40])
        eng = BacktestEngine(bars, Scripted({0: [("BUY", "600001", 30000.0)],
                                             1: [("SELL", "600001", -1)]}), d)
        eng.run()
        buys = [t for t in eng.trades if t.side == "BUY"]
        sells = [t for t in eng.trades if t.side == "SELL"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0].date, d[2])        # 次日开盘成交（D+1）
        self.assertEqual(sells[0].qty, buys[0].qty)  # 清仓
        self.assertGreater(sells[0].realized_pnl, 0)  # 10.40 卖 > 10.20 买
        self.assertEqual(eng.conservation_failures, 0)

    def test_t1_same_day_sell_blocked_without_pending(self):
        """持仓守恒拦截：无可卖数量时卖出信号被拦截（四自检量可见）。"""
        d = _dates(2)
        bars = _bars("600001", d, [10.00, 10.20], [10.10, 10.30],
                     [9.95, 10.10], [10.00, 10.20])
        # t=0 发卖出信号但无持仓 → 拦截
        eng = BacktestEngine(bars, Scripted({0: [("SELL", "600001", -1)]}), d)
        eng.run()
        self.assertEqual(eng.blocked_signals, 1)
        self.assertEqual(eng.filled_count, 0)


class TestLimitUpDown(unittest.TestCase):
    """涨跌停三情形（S03-01 §5.2）。"""

    def _setup_with_position(self):
        d = _dates(3)
        bars = _bars("600001", d,
                     opens=[10.00, 10.05, 11.00], highs=[10.10, 10.20, 11.00],
                     lows=[9.95, 9.98, 11.00], closes=[10.00, 10.00, 11.00])
        return d, bars

    def test_limit_up_sell_fills_buy_rejected(self):
        """一字涨停（开盘=涨停=最低）：卖单按开盘（=涨停价）成交；买单作废。"""
        d, bars = self._setup_with_position()
        eng = BacktestEngine(bars, Scripted({0: [("BUY", "600001", 30000.0)],
                                             1: [("SELL", "600001", -1)]}), d)
        eng.run()
        sells = [t for t in eng.trades if t.side == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0].date, d[2])
        self.assertTrue(abs(sells[0].exec_price - 11.00 * (1 - C.SLIPPAGE)) < 1e-6)

        # 买单撞一字涨停 → 作废
        eng2 = BacktestEngine(bars, Scripted({1: [("BUY", "600001", 30000.0)]}), d)
        eng2.run()
        voided = [o for o in eng2.orders if o.status == "已作废"]
        self.assertEqual(len(voided), 1)
        self.assertIn("一字涨停", voided[0].reason)
        self.assertEqual(eng2.filled_count, 0)

    def test_limit_down_buy_fills_sell_voided(self):
        """一字跌停：买单按开盘（=跌停价）成交；卖单作废。"""
        d = _dates(3)
        bars = _bars("600001", d,
                     opens=[10.00, 10.00, 9.00], highs=[10.10, 10.10, 9.00],
                     lows=[9.95, 9.95, 9.00], closes=[10.00, 10.00, 9.00])
        eng = BacktestEngine(bars, Scripted({1: [("BUY", "600001", 30000.0)]}), d)
        eng.run()
        buys = [t for t in eng.trades if t.side == "BUY"]
        self.assertEqual(len(buys), 1)
        self.assertTrue(abs(buys[0].exec_price - 9.00 * (1 + C.SLIPPAGE)) < 1e-6)

        # 持仓后卖单撞一字跌停 → 作废
        eng2 = BacktestEngine(bars, Scripted({0: [("BUY", "600001", 30000.0)],
                                              1: [("SELL", "600001", -1)]}), d)
        eng2.run()
        voided = [o for o in eng2.orders if o.status == "已作废"]
        self.assertEqual(len(voided), 1)
        self.assertIn("一字跌停", voided[0].reason)


class TestSuspension(unittest.TestCase):
    def test_pending_order_expires_after_5_days(self):
        """停牌不顺延超5个交易日，超期作废并记录（S03-01 §5.3）。"""
        d = _dates(12)
        present = [0, 1, 2, 3, 4, 11]                # d6..d11 停牌（缺K线）
        rows = {i: (10.0 + i * 0.01) for i in present}
        bars = {"600001": [Bar(d[i], rows[i], rows[i] + 0.1, rows[i] - 0.1,
                               rows[i], 100000) for i in present]}
        # t=4（d5 收盘）发出买入 → d6..d11 停牌顺延 → 第6天作废
        eng = BacktestEngine(bars, Scripted({4: [("BUY", "600001", 30000.0)]}), d)
        eng.run()
        voided = [o for o in eng.orders if o.status == "已作废"]
        self.assertEqual(len(voided), 1)
        self.assertIn("停牌顺延超5个交易日", voided[0].reason)
        self.assertEqual(eng.filled_count, 0)


class TestConservation(unittest.TestCase):
    def test_full_run_zero_failures(self):
        """合成样例全流程：三守恒逐笔零失败 + 权益恒等式（含费口径）。"""
        bars_all = gen_sample_bars()
        symbol = "600001"
        seq = bars_all[symbol]
        calendar = sorted({b.date for b in seq})
        eng = BacktestEngine({symbol: seq}, MaCross(5, 20), calendar,
                             seed_note="ut-seed")
        eng.run()
        self.assertEqual(eng.conservation_failures, 0)
        self.assertGreater(eng.filled_count, 0)
        eq_end = eng.equity_curve[-1][1]
        expect = C.INITIAL_CASH + eng.account.realized_pnl + eng.unrealized_pnl()
        self.assertTrue(abs(eq_end - expect) < 1e-6)
        # 现金层守恒终值
        self.assertTrue(abs(eng.account.cash_ledger -
                            (eng.account.cash_available + eng.account.cash_frozen)) < 1e-6)


class TestDailyLossHalt(unittest.TestCase):
    def test_halt_blocks_new_buys(self):
        """双标的各30%持仓、次日双双-10%：单日亏损≈6%≥5% → 熔断，当日禁新买入
        （不强制平仓：持仓保留）。"""
        d = _dates(4)
        bars = {}
        for s in ("600001", "000002"):
            bars[s] = [Bar(d[0], 10.00, 10.10, 9.95, 10.00, 100000),
                       Bar(d[1], 10.05, 10.15, 9.98, 10.00, 100000),
                       Bar(d[2], 9.50, 10.00, 9.00, 9.00, 100000),   # -10% 收盘
                       Bar(d[3], 9.10, 9.40, 9.00, 9.20, 100000)]
        script = {0: [("BUY", "600001", 30000.0), ("BUY", "000002", 30000.0)],
                  2: [("BUY", "600001", 30000.0)]}                   # 熔断日的买入应被拦
        eng = BacktestEngine(bars, Scripted(script), d)
        eng.run()
        self.assertEqual(eng._halt_days, 1)
        self.assertGreaterEqual(eng.blocked_signals, 1)
        # 不强制平仓：持仓仍在
        self.assertGreater(eng._pos("600001").held, 0)
        self.assertEqual(eng.conservation_failures, 0)


class TestTruncationEquiv(unittest.TestCase):
    def test_no_lookahead_by_truncation(self):
        """末日陷阱（S03-01 §10 第二重）：截断数据后重跑，前段权益逐点一致。"""
        bars_all = gen_sample_bars()
        symbol = "600001"
        seq = bars_all[symbol][:120]
        calendar = sorted({b.date for b in seq})
        cut = 90
        e1 = BacktestEngine({symbol: seq}, MaCross(5, 20), calendar).run()
        seq_cut = seq[:cut]
        cal_cut = sorted({b.date for b in seq_cut})
        e2 = BacktestEngine({symbol: seq_cut}, MaCross(5, 20), cal_cut).run()
        for i in range(cut):
            self.assertTrue(abs(e1.equity_curve[i][1] - e2.equity_curve[i][1]) < 1e-9,
                            f"权益在 {i} 处分叉——存在隐性前视")


if __name__ == "__main__":
    unittest.main()
