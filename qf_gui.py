#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""QuantForge 图形界面启动器（双击 启动QuantForge界面.bat 即开）"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from qf.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
