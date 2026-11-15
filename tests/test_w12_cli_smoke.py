# -*- coding: utf-8 -*-
"""CLI 冒烟：cmd_show 路径回归（--user 补丁曾引入未定义变量）。"""
import argparse
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf.cli import cmd_show  # noqa: E402


class TestCmdShow(unittest.TestCase):
    def test_no_user_runs(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cmd_show(argparse.Namespace(run_id=None))
        self.assertIn("回测", out.getvalue())

    def test_user_runs(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cmd_show(argparse.Namespace(run_id=None, user="alice"))
        self.assertIn("尚无回测记录", out.getvalue())


if __name__ == "__main__":
    unittest.main()
