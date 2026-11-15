# -*- coding: utf-8 -*-
"""W2 新增模块用例：SVG 图表、HTML 单文件报告、费用恒等式、性能预算。"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf import config as C                       # noqa: E402
from qf import extreme as X                      # noqa: E402
from qf.charts import equity_svg, drawdown_svg, monthly_svg  # noqa: E402
from qf.engine import BacktestEngine             # noqa: E402
from qf.metrics import compute_metrics           # noqa: E402
from qf.report_html import gen_html_report       # noqa: E402
from qf.strategies import MaCross                # noqa: E402


def _curve(n=300, base=100000.0):
    from datetime import date, timedelta
    out, d, v = [], date(2022, 1, 4), base
    for i in range(n):
        out.append((d.isoformat(), v, v * 0.3))
        d += timedelta(days=1)
        v *= (1 + (0.004 if i % 9 else -0.006))
    return out


class TestCharts(unittest.TestCase):
    def setUp(self):
        self.curve = _curve()

    def test_equity_svg_polyline(self):
        s = equity_svg(self.curve)
        self.assertIn("<svg", s)
        self.assertIn("polyline", s)
        self.assertGreater(s.count(","), 100)      # 点数充足（≈300 点）

    def test_drawdown_svg_area(self):
        s = drawdown_svg(self.curve)
        self.assertIn("polygon", s)
        self.assertIn("最大回撤", s)
        self.assertIn("分母=峰值", s)               # S09 冻结口径标注

    def test_monthly_svg_bars(self):
        s = monthly_svg(self.curve)
        self.assertIn("rect", s)
        self.assertGreaterEqual(s.count("<rect"), 8)    # 300天≈11个月边界→≥9根柱

    def test_short_series_no_crash(self):
        self.assertIn("数据不足", equity_svg([(d, v) for d, v, _ in self.curve[:1]]))


class TestHtmlReport(unittest.TestCase):
    def test_single_file_no_external_deps(self):
        bars, cal = X.scenario_crash_gem(days=60)
        eng = BacktestEngine({"300003": bars["300003"]}, MaCross(5, 20), cal).run()
        mr = compute_metrics(eng.equity_curve, eng.trades)
        with tempfile.TemporaryDirectory() as td:
            path = gen_html_report(eng, "ut1", "ma_cross", {}, mr,
                                   snapshot_id="UT", out_dir=td,
                                   symbol_pool_note="单标的 300003")
            html = open(path, encoding="utf-8").read()
            self.assertTrue(html.startswith("<!DOCTYPE html>"))
            self.assertEqual(html.count("<svg"), 3)            # 三张图
            self.assertNotIn("<script", html)                  # 无 JS（零依赖纪律）
            self.assertNotIn("src=", html)                     # 无外链资源
            self.assertNotIn("<link", html)                    # 无外链样式
            self.assertIn("九项配置", html)
            self.assertIn("费用恒等式失败", html)
            self.assertIn("不构成投资建议", html)
            self.assertLess(os.path.getsize(path), 900_000)    # 单文件体积可控


class TestFeeIdentity(unittest.TestCase):
    def test_fee_identity_zero_failures(self):
        """第5自检量：全流程费用双记账恒等（留痕 vs 独立复算）。"""
        from qf.sample_data import gen_sample_bars, gen_calendar
        bars = gen_sample_bars()
        seq = bars["600001"]
        cal = sorted({b.date for b in seq})
        eng = BacktestEngine({"600001": seq}, MaCross(5, 20), cal).run()
        self.assertEqual(eng.fee_check_failures, 0)
        self.assertEqual(eng.conservation_failures, 0)
        self.assertGreater(eng.filled_count, 0)


class TestBenchBudget(unittest.TestCase):
    def test_small_pool_budget(self):
        """5标的×5年全池回测 ≤15 秒（S06 表17 口径的 W1 对应：100组×3秒/组→单组≤3秒
        的1/5子集抽样预算）。"""
        from qf.sample_data import gen_sample_bars, gen_calendar
        bars = gen_sample_bars()
        cal = gen_calendar()
        t0 = time.perf_counter()
        eng = BacktestEngine(bars, MaCross(5, 20), cal).run()
        dt = time.perf_counter() - t0
        self.assertLess(dt, 15.0, msg=f"全池回测超预算：{dt:.1f}s")
        self.assertEqual(eng.conservation_failures, 0)

    def test_many_symbols_budget(self):
        """300标的×250日（7.5万根K线）单次回测 ≤90 秒（500标的×1年缩放口径）。"""
        from qf.sample_data import gen_calendar
        cal = gen_calendar()[:250]
        bars = {}
        for i in range(300):
            seq, price = [], 10.0 + (i % 40)
            for j, ds in enumerate(cal):
                price = round(price * (1 + (0.003 if j % 3 else -0.002)), 3)
                seq.append(X._bar(ds, price, price * 1.01, price * 0.99, price))
            bars[f"6001{i:03d}"[:6] + str(i % 10)] = seq
        t0 = time.perf_counter()
        eng = BacktestEngine(bars, MaCross(5, 20), cal).run()
        dt = time.perf_counter() - t0
        self.assertLess(dt, 90.0, msg=f"300标的回测超预算：{dt:.1f}s")
        self.assertEqual(eng.conservation_failures, 0)


if __name__ == "__main__":
    unittest.main()
