# -*- coding: utf-8 -*-
"""doctor 三重检测用例：合法策略零命中；注入前视的策略必被抓住。"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf import extreme as X                     # noqa: E402
from qf.doctor import (static_scan, doomsday_trap, run_doctor,  # noqa: E402
                       format_report)
from qf.strategies import MaCross               # noqa: E402


class LookaheadStrategy:
    """故意前视：用 t+1 的收盘价决定 t 日信号（未来函数典型样本）。"""
    warmup_windows = [0]

    def attach(self, engine):
        self.engine = engine

    def on_bar(self, t):
        seq = self.engine.bars["600001"]
        if t + 1 >= len(seq):                    # ← 正偏移索引：静态扫描目标
            return []
        if seq[t + 1].close > seq[t].close:
            return [("BUY", "600001", self.engine.equity_now() * 0.30)]
        return []


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.bars, self.cal = X.scenario_crash_gem(days=30)
        self.bars = {"600001": self.bars["300003"]}   # 换成沪市代码走全费率路径

    def test_static_scan_clean_on_ma_cross(self):
        self.assertEqual(static_scan(MaCross(5, 20)), [])

    def test_static_scan_catches_lookahead(self):
        hits = static_scan(LookaheadStrategy())
        self.assertGreaterEqual(len(hits), 1)
        self.assertRegex(hits[0]["code"], r"seq\[t\s*\+\s*1\]")

    def test_doomsday_trap_clean_on_causal(self):
        findings = doomsday_trap(self.bars, lambda: MaCross(5, 20), self.cal)
        self.assertEqual(findings, [], msg=f"合法策略出现截断分叉：{findings}")

    def test_doomsday_trap_catches_lookahead(self):
        findings = doomsday_trap(self.bars, LookaheadStrategy, self.cal)
        self.assertGreater(len(findings), 0, msg="前视策略未被末日陷阱抓住")

    def test_run_doctor_verdict(self):
        v = run_doctor(self.bars, lambda: MaCross(5, 20), self.cal)
        self.assertTrue(v["合格"])
        self.assertIn("✅", format_report(v))
        v2 = run_doctor(self.bars, LookaheadStrategy, self.cal)
        self.assertFalse(v2["合格"])
        self.assertIn("❌", format_report(v2))


if __name__ == "__main__":
    unittest.main()
