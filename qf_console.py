#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""QuantForge Web 控制台启动器（新默认入口；tkinter 经典版=python qf_gui.py）"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from qf.webconsole import main  # noqa: E402

if __name__ == "__main__":
    main()
