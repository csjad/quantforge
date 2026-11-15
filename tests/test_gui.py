# -*- coding: utf-8 -*-
"""GUI 模块导入级测试（不实例化 Tk——headless 环境安全）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


class TestGui(unittest.TestCase):
    def test_module_imports_zero_dependency(self):
        """无 tkinter 的解释器也应可导入（惰性导入设计）。"""
        import qf.gui as g
        self.assertTrue(hasattr(g, "QuantForgeApp"))
        self.assertTrue(hasattr(g, "main"))
        self.assertNotIn("import tkinter", open(g.__file__, encoding="utf-8")
                         .read().split("def _build")[0])

    def test_strategy_choices_match_registry(self):
        from qf.gui import STRATEGY_CHOICES
        from qf.strategies import STRATEGY_REGISTRY
        self.assertEqual({c for c, _n in STRATEGY_CHOICES},
                         set(STRATEGY_REGISTRY.keys()))

    def test_build_cmd(self):
        from qf.gui import build_cmd
        cmd = build_cmd("ma_cross", "600001", "3", "15")
        self.assertIn("--strategy", cmd)
        self.assertEqual(cmd[cmd.index("--strategy") + 1], "ma_cross")
        self.assertIn("600001", cmd)
        self.assertIn("--fast", cmd)
        cmd2 = build_cmd("rsi_reversal", None)     # 全部标的 → 无 --symbol
        self.assertNotIn("--symbol", cmd2)

    def test_latest_report_none_on_empty(self):
        import qf.gui as g
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            old = g.OUT_DIR
            g.OUT_DIR = td
            try:
                self.assertIsNone(g.latest_report())
                open(os.path.join(td, "report_x.html"), "w").write("<svg></svg>")
                self.assertIsNotNone(g.latest_report())
            finally:
                g.OUT_DIR = old


if __name__ == "__main__":
    unittest.main()
