# -*- coding: utf-8 -*-
"""qf.sample_data — 自制合成样例行情生成器（S44 合规底线落实）

**样例数据须为自制或公开授权，不得使用来源不明的数据**（S44/A2 条件）。
本生成器产出的数据为**纯程序合成（几何布朗运动+机制切换），非真实行情**，
可自由分发——数据来源与授权说明随包分发（data/README_数据来源与授权.md）。

设计（覆盖引擎全部关键路径）：
  - 5 标的：600001（沪主板±10%）、000002（深主板±10%）、300003（创业板±20%）、
    688005（科创板±20%）、600006（沪主板±10%）
  - 时间：约 5 年（2021-01-04 起，跳过周末；合成数据不模拟法定节假日）
  - 注入路径：每标的一段 5 日停牌（缺口）；600001 注入 2 个一字涨停日 + 1 个
    一字跌停日（涨跌停三情形路径）
  - 固定种子（可复现：报告首页"随机种子"项）
"""
from __future__ import annotations

import math
import os
import random
from datetime import date, timedelta
from typing import Dict, List

from .engine import Bar

SYMBOLS = [
    ("600001", "样例沪主板A", 0.10),
    ("000002", "样例深主板B", 0.10),
    ("300003", "样例创业板C", 0.20),
    ("688005", "样例科创板D", 0.20),
    ("600006", "样例沪主板E", 0.10),
]
START = date(2021, 1, 4)
YEARS = 5


def gen_calendar(start: date = START, years: int = YEARS) -> List[str]:
    """交易日历=工作日（合成口径，不模拟法定节假日）。"""
    days = []
    d = start
    end = date(start.year + years, 1, 1)
    while d < end:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def gen_sample_bars(seed: int = 20261113) -> Dict[str, List[Bar]]:
    rng = random.Random(seed)
    calendar = gen_calendar()
    bars: Dict[str, List[Bar]] = {}
    n = len(calendar)
    for si, (symbol, name, pct) in enumerate(SYMBOLS):
        rng2 = random.Random(seed + si * 7919)
        price = 10.0 + si * 4.0
        seq: List[Bar] = []
        drift, vol = 0.0002, 0.018
        # 停牌窗口：第 300+i*80 根起连续 5 个交易日
        susp_start = 300 + si * 80
        susp_set = set(range(susp_start, susp_start + 5))
        for t, ds in enumerate(calendar):
            if t in susp_set:
                continue                                    # 停牌：整行缺失
            # 机制切换：缓慢趋势 + 阶段性波动放大
            if t % 480 < 240:
                drift_t, vol_t = drift, vol
            else:
                drift_t, vol_t = -drift, vol * 1.4
            shock = rng2.gauss(drift_t, vol_t)
            prev_close = price
            close = max(1.0, price * (1 + shock))
            o = price * (1 + rng2.gauss(0, vol / 6))
            hi = max(o, close) * (1 + abs(rng2.gauss(0, vol / 8)))
            lo = min(o, close) * (1 - abs(rng2.gauss(0, vol / 8)))
            o, hi, lo = round(o, 2), round(hi, 2), round(lo, 2)
            close = round(close, 2)
            volm = int(50_000 * (1 + 6 * abs(shock) / vol) * rng2.uniform(0.6, 1.4))
            # 一字板注入（600001 专属：涨跌停三情形路径）
            if symbol == "600001":
                if t == 500:                                # 一字涨停：前收×1.1
                    lu = round(prev_close * (1 + pct), 2)
                    o = hi = lo = close = lu
                    volm = int(volm * 0.3)
                elif t == 700:                              # 第二个一字涨停
                    lu = round(prev_close * (1 + pct), 2)
                    o = hi = lo = close = lu
                    volm = int(volm * 0.3)
                elif t == 900:                              # 一字跌停
                    ld = round(prev_close * (1 - pct), 2)
                    o = hi = lo = close = ld
                    volm = int(volm * 0.3)
            seq.append(Bar(ds, o, max(hi, o, close), min(lo, o, close), close, volm))
            price = close
        bars[symbol] = seq
    return bars


def write_sample_csv(bars: Dict[str, List[Bar]], out_dir: str) -> Dict[str, int]:
    """按标的+年分区写 CSV（D-002 分区键口径，W1 载体=CSV，见 D-008）。"""
    os.makedirs(out_dir, exist_ok=True)
    counts = {}
    for s, seq in bars.items():
        by_year: Dict[str, List[Bar]] = {}
        for b in seq:
            by_year.setdefault(b.date[:4], []).append(b)
        for y, rows in sorted(by_year.items()):
            path = os.path.join(out_dir, f"{s}_{y}.csv")
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write("date,open,high,low,close,volume\n")
                for b in rows:
                    f.write(f"{b.date},{b.open},{b.high},{b.low},{b.close},{b.volume}\n")
            counts[f"{s}_{y}"] = len(rows)
    return counts


def write_license_note(out_dir: str):
    """A2 条件项：样例数据附来源与授权说明（随包分发）。"""
    os.makedirs(out_dir, exist_ok=True)
    text = (
        "# 样例数据来源与授权说明\n\n"
        "- 来源：**纯程序合成**（本包自带生成器 `qf.sample_data`，几何布朗运动+机制切换），"
        "**非真实行情、非任何数据商数据**；\n"
        "- 授权：自制合成数据，**可自由使用与分发，无第三方权利限制**；\n"
        "- 用途：仅用于引擎功能验证与教学演示，合成数据的任何统计特征不构成对真实"
        "市场的判断，据此得出的结论不构成投资建议；\n"
        "- 复现：`python qf.py gen-sample`（固定种子 20261113，重跑结果逐位一致）。\n"
    )
    with open(os.path.join(out_dir, "README_数据来源与授权.md"), "w",
              encoding="utf-8") as f:
        f.write(text)
