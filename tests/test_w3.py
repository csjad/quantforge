# -*- coding: utf-8 -*-
"""W3 用例：S05 沙箱逃逸（表15）/ S11 复盘五判据（TC-REP）/ S13 告警降噪（TC-MON）。"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf.alerts import AlertEngine, evaluate_backtest          # noqa: E402
from qf.metrics import compute_metrics                        # noqa: E402
from qf.review import run_review, format_review               # noqa: E402
from qf.sandbox import (check_imports, verify_strategy,       # noqa: E402
                        format_report)
from qf import extreme as X                                   # noqa: E402

CLEAN_STRATEGY = '''
import math
from collections import deque

class Strategy:
    name = "clean"
    warmup_windows = [5]

    def attach(self, engine):
        self.engine = engine

    def on_bar(self, t):
        li = self._li(t)
        if li is None or li < 5:
            return []
        seq = self.engine.bars["S"]
        closes = [b.close for b in seq]
        m5 = sum(closes[li - 5:li]) / 5
        if closes[li] > m5:
            return [("BUY", "S", self.engine.equity_now() * 0.30)]
        if self.engine._pos("S").held:
            return [("SELL", "S", -1)]
        return []

    def _li(self, t):
        d = self.engine.calendar[t]
        return self.local_idx.get(d)

    def attach(self, engine):
        self.engine = engine
        self.local_idx = {b.date: i for i, b in enumerate(engine.bars["S"])}
'''

BAD_IMPORT_STRATEGY = '''
import socket            # 网络外联：导入白名单外

class Strategy:
    def attach(self, engine):
        self.engine = engine
    def on_bar(self, t):
        return []
'''

RUNTIME_NET_STRATEGY = '''
class Strategy:
    def attach(self, engine):
        self.engine = engine
    def on_bar(self, t):
        __import__("socket")      # 运行期动态导入：审计挂钩应拦截
        return []
'''

WRITE_OUT_STRATEGY = '''
class Strategy:
    def attach(self, engine):
        self.engine = engine
    def on_bar(self, t):
        f = open("../evil.txt", "w")   # 文件越权写（白名单根之外）
        f.write("x")
        f.close()
        return []
'''

READ_OUT_STRATEGY = '''
class Strategy:
    def attach(self, engine):
        self.engine = engine
    def on_bar(self, t):
        f = open("../../secret.txt", "r")   # 文件越权读（../ 上级目录）
        f.close()
        return []
'''

INFINITE_LOOP_STRATEGY = '''
class Strategy:
    def attach(self, engine):
        self.engine = engine
    def on_bar(self, t):
        while True:               # 无限循环 → 资源边界超时终止
            pass
        return []
'''


def _write_tmp(src: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(src)
    return path


def _bars(n=60):
    return X.scenario_crash_gem(days=n)[0]["300003"]


class TestSandbox(unittest.TestCase):
    """表15 沙箱逃逸用例（S05-01 §7）：拒绝并记录/终止并标记。"""

    def test_clean_strategy_passes(self):
        path = _write_tmp(CLEAN_STRATEGY)
        try:
            self.assertEqual(check_imports(path), [])
            v = verify_strategy(path, _bars(), timeout=25)
            self.assertTrue(v["合格"],
                            msg=f"干净策略被误杀：{v['steps']['沙箱试跑'].get('violations')}")
        finally:
            os.remove(path)

    def test_import_whitelist_blocks_socket(self):
        path = _write_tmp(BAD_IMPORT_STRATEGY)
        try:
            hits = check_imports(path)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["type"], "网络外联")
            v = verify_strategy(path, _bars())
            self.assertFalse(v["合格"])
        finally:
            os.remove(path)

    def test_runtime_import_blocked_by_hook(self):
        path = _write_tmp(RUNTIME_NET_STRATEGY)
        try:
            v = verify_strategy(path, _bars(), timeout=25)
            self.assertFalse(v["steps"]["沙箱试跑"].get("ok"), msg="运行期动态导入未被拦截")
            types = [x["type"] for x in v["steps"]["沙箱试跑"].get("violations", [])]
            self.assertTrue(any("网络外联" in t or "白名单" in t for t in types),
                            msg=f"拦截类型不符：{types}")
        finally:
            os.remove(path)

    def test_file_write_outside_blocked(self):
        path = _write_tmp(WRITE_OUT_STRATEGY)
        try:
            v = verify_strategy(path, _bars(), timeout=25)
            self.assertFalse(v["steps"]["沙箱试跑"].get("ok"), msg="越权写未被拦截")
            types = [x["type"] for x in v["steps"]["沙箱试跑"].get("violations", [])]
            self.assertTrue(any("文件越权写" in t for t in types),
                            msg=f"拦截类型不符：{types}")
        finally:
            os.remove(path)

    def test_file_read_outside_blocked(self):
        path = _write_tmp(READ_OUT_STRATEGY)
        try:
            v = verify_strategy(path, _bars(), timeout=25)
            types = [x["type"] for x in v["steps"]["沙箱试跑"].get("violations", [])]
            self.assertTrue(any("文件越权读" in t for t in types),
                            msg=f"越权读未被拦截：{types}")
        finally:
            os.remove(path)

    def test_infinite_loop_timeout(self):
        path = _write_tmp(INFINITE_LOOP_STRATEGY)
        try:
            v = verify_strategy(path, _bars(), timeout=5)
            self.assertFalse(v["steps"]["沙箱试跑"].get("ok"))
            types = [x["type"] for x in v["steps"]["沙箱试跑"].get("violations", [])]
            self.assertTrue(any("超时" in t for t in types), msg=f"超时未标记：{types}")
        finally:
            os.remove(path)

    def test_report_format_target_statement(self):
        path = _write_tmp(CLEAN_STRATEGY)
        try:
            v = verify_strategy(path, _bars(), timeout=25)
            text = format_report(v)
            self.assertIn("沙箱目标=防越权操作", text)     # §8 目标声明必须出现
        finally:
            os.remove(path)


# ------------------------------------------------------------ S11 复盘

class FakeEngine:
    """轻量引擎替身： trades/equity_curve/events/_halt_days/bars。"""

    def __init__(self, trades, curve, halt_days=0, events=None):
        self.trades = trades
        self.equity_curve = curve
        self._halt_days = halt_days
        self.events = events or []
        self.bars = {"S": []}

    def unrealized_pnl(self):
        return 0.0


def _metrics(**kw):
    base = dict(累计收益=0.1, 年化收益=0.1, 年化波动=0.1, 夏普=0.5, 索提诺=0.5,
                最大回撤=-0.1, 回撤持续期=10, 卡玛=1.0, 胜率=0.5, 盈亏比=1.5,
                换手率=100.0, 成交笔数=50)
    base.update(kw)
    from qf.metrics import MetricResult
    return MetricResult(values=base, notes={}, short_period=False)


def _trade(pnl, side="SELL"):
    from qf.engine import Trade
    return Trade("2022-01-04", "S", side, 10.0, 10.0, 100, 1000, 5.0, 1.0, pnl, 0)


def _curve(n=150, start=100000.0):
    return [(f"2022-01-{(i % 28) + 1:02d}", start * (1 + 0.001 * i), 0.0)
            for i in range(n)]


class TestReview(unittest.TestCase):
    def test_tc_rep_001_risk_reward(self):
        eng = FakeEngine([_trade(10.0)], _curve())
        r = run_review(eng, _metrics(最大回撤=-0.30, 卡玛=0.3))
        self.assertTrue(any("风险收益比不足" in v.text and v.level == "中"
                            for v in r.verdicts))

    def test_tc_rep_002_sample_too_small(self):
        eng = FakeEngine([_trade(10.0)], _curve())
        r = run_review(eng, _metrics(成交笔数=10))
        self.assertTrue(any("样本不足" in v.text and v.level == "高"
                            for v in r.verdicts))

    def test_tc_rep_003_overfit_gap(self):
        eng = FakeEngine([_trade(10.0)], _curve())
        r = run_review(eng, _metrics(),
                       train_m={"年化收益": 0.50}, val_m={"年化收益": -0.10})
        self.assertTrue(any("显著过拟合" in v.text and v.level == "高"
                            for v in r.verdicts))

    def test_tc_rep_004_event_dependence(self):
        trades = [_trade(-5.0)] * 20 + [_trade(200.0)]   # 净利100，单笔200贡献≥60%
        eng = FakeEngine(trades, _curve())
        r = run_review(eng, _metrics())
        self.assertTrue(any("极少数事件" in v.text and v.level == "高"
                            for v in r.verdicts))

    def test_tc_rep_005_val_trades_lt_20(self):
        eng = FakeEngine([_trade(10.0)], _curve())
        r = run_review(eng, _metrics(), val_trades=10)
        self.assertTrue(any("结论不作数" in v.text for v in r.verdicts))

    def test_tc_rep_006_unfinished_not_silent(self):
        eng = FakeEngine([_trade(10.0)], _curve())
        r = run_review(eng, _metrics())
        self.assertFalse(r.complete)
        self.assertIn("问3", r.unfinished)
        self.assertIn("问4", r.unfinished)
        self.assertIn("未完成", format_review(r))

    def test_tc_rep_007_circuit_breaker_section(self):
        eng = FakeEngine([_trade(10.0)], _curve(), halt_days=2,
                         events=["[熔断@2022-01-04] 单日亏损≥5%"])
        r = run_review(eng, _metrics())
        self.assertTrue(any("不剔除" in c for c in r.circuit_breaker))

    def test_tc_rep_008_stats_disclaimer(self):
        eng = FakeEngine([_trade(10.0)], _curve())
        self.assertIn("仅为统计描述", format_review(run_review(eng, _metrics())))


# ------------------------------------------------------------ S13 告警

class TestAlerts(unittest.TestCase):
    def test_tc_mon_001_merge_window(self):
        ae = AlertEngine(now=datetime(2022, 1, 4, 9, 0, 0))
        for _ in range(5):
            ae.emit("k/守恒失败", "严重", "守恒未过")
        self.assertEqual(len(ae.active), 1)                    # 同类合并
        a = ae.active["k/守恒失败"]
        self.assertEqual(a.count, 5)
        self.assertTrue(a.escalated)                           # 第5次升级
        lines = ae.notify_lines()
        self.assertEqual(len(lines), 1)                        # 一条而非五条轰炸
        self.assertIn("首次", lines[0])

    def test_tc_mon_002_escalate_at_five(self):
        ae = AlertEngine()
        for i in range(5):
            a = ae.emit("k", "警告", "失败")
            if i < 4:
                self.assertFalse(a.escalated)
        self.assertTrue(ae.active["k"].escalated)

    def test_tc_mon_003_confirm_silence_with_record(self):
        ae = AlertEngine()
        ae.emit("k", "严重", "故障")
        self.assertTrue(ae.confirm("k", who="op1"))
        self.assertEqual(ae.notify_lines(), [])                # 静默
        self.assertIn("op1", ae.active["k"].impact)            # 确认留痕

    def test_tc_mon_004_recovery_pairing(self):
        ae = AlertEngine()
        ae.emit("k", "严重", "故障")
        a = ae.resolve("k")
        self.assertIsNotNone(a)
        self.assertIn("已恢复", a.message)                     # 恢复通知配对
        self.assertNotIn("k", ae.active)

    def test_tc_mon_007_data_freshness(self):
        eng = FakeEngine([], _curve())
        mr = _metrics()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "600001_2025.csv")
            open(p, "w").write("date,open,high,low,close,volume\n")
            old = datetime.now() - timedelta(days=4)
            os.utime(p, (old.timestamp(), old.timestamp()))
            ae = evaluate_backtest(eng, mr, data_dir=td)
            self.assertTrue(any("滞后 4 日" in x and "严重" in x
                                for x in ae.notify_lines()))
            mid = datetime.now() - timedelta(days=1, hours=2)
            os.utime(p, (mid.timestamp(), mid.timestamp()))
            ae2 = evaluate_backtest(eng, mr, data_dir=td)
            self.assertTrue(any("警告" in x and "滞后 1 日" in x
                                for x in ae2.notify_lines()))

    def test_tc_mon_009_context_triple(self):
        eng = FakeEngine([], _curve())
        eng.conservation_failures = 2
        mr = _metrics()
        ae = evaluate_backtest(eng, mr, run_id="R9")
        a = ae.active.get("R9/守恒失败")
        self.assertIsNotNone(a)
        self.assertEqual(a.task_id, "R9")                      # 三件套之一
        self.assertTrue(a.error)                               # 之二
        self.assertTrue(a.impact)                              # 之三


if __name__ == "__main__":
    unittest.main()
