# -*- coding: utf-8 -*-
"""删除保留名 nul（字面 UNC，绕过 Windows 设备名规范化）。"""
import os

target = "\\\\?\\E:\\AI_Projects\\Zcode\\quantforge\\nul"
try:
    os.remove(target)
    print("removed via UNC")
except OSError as exc:
    print("unc remove failed:", exc)

still = [f for f in os.listdir(r"E:\AI_Projects\Zcode\quantforge") if f.lower() == "nul"]
print("still there:", still)
