#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""QuantForge W1 启动器：python qf.py init|gen-sample|backtest|show|demo"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from qf.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
