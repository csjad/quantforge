# -*- coding: utf-8 -*-
"""qf.charts — 零依赖内嵌 SVG 图表（D-009：W1 形态图表用纯 stdlib 生成 SVG，
D-006 的 Matplotlib 保留给部署形态面板——离线包体积与零依赖优先）。

三张图（S10/S09 口径）：权益曲线（折线）、水下曲线（回撤面积，分母=峰值口径）、
月度收益（柱状，正负着色）。全部内嵌单文件 HTML，无 JS、无外链。
"""
from __future__ import annotations

import math
from typing import List, Tuple, Optional


def _polyline(points: List[Tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def equity_svg(curve: List[Tuple[str, float]], w: int = 720, h: int = 220,
               hover_dates: Optional[List[str]] = None) -> str:
    """权益曲线 SVG（含首尾标注与网格）。
    hover_dates（A5）：与 curve 等长的真实日期——每个采样点挂透明热区+<title>
    （悬停显示日期/净值）；自动降采样至 ≤120 个热区。"""
    if len(curve) < 2:
        return "<svg></svg><!-- 数据不足 -->"
    vals = [r[1] for r in curve]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    pad = 8
    pts = [(pad + i * (w - 2 * pad) / (len(vals) - 1),
            h - pad - (v - lo) / rng * (h - 2 * pad)) for i, v in enumerate(vals)]
    d_first, d_last = curve[0][0], curve[-1][0]
    grid = "".join(
        f'<line x1="{pad}" y1="{pad + k*(h-2*pad)/4:.0f}" x2="{w-pad}" '
        f'y2="{pad + k*(h-2*pad)/4:.0f}" stroke="#e5e7eb" stroke-width="1"/>'
        for k in range(5))
    hot = ""
    if hover_dates and len(hover_dates) == len(curve):
        step = max(1, len(pts) // 120)
        for i in range(0, len(pts), step):
            x, y = pts[i]
            hot += (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="10" fill="transparent">'
                    f'<title>{hover_dates[i]}｜{vals[i]:,.0f}</title></circle>')
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="权益曲线">{grid}'
            f'<polyline fill="none" stroke="#1d4ed8" stroke-width="1.6" '
            f'points="{_polyline(pts)}"/>{hot}'
            f'<text x="{pad}" y="{pad+10}" font-size="11" fill="#374151">'
            f'峰值 {hi:,.0f}</text>'
            f'<text x="{pad}" y="{h-4}" font-size="11" fill="#374151">'
            f'谷值 {lo:,.0f}｜起点 {d_first} → 终点 {d_last}</text></svg>')


def drawdown_svg(curve: List[Tuple[str, float]], w: int = 720, h: int = 160) -> str:
    """水下曲线（回撤面积，分母=峰值——S09 冻结口径）。"""
    if len(curve) < 2:
        return "<svg></svg><!-- 数据不足 -->"
    vals = [r[1] for r in curve]
    peak, pts = vals[0], []
    mdd = 0.0
    for i, v in enumerate(vals):
        peak = max(peak, v)
        dd = (v - peak) / peak if peak > 0 else 0.0
        mdd = min(mdd, dd)
        pts.append((8 + i * (w - 16) / (len(vals) - 1), h - 8 + dd * (h - 16)))
    area = _polyline(pts) + f" {pts[-1][0]:.2f},{h-8} {pts[0][0]:.2f},{h-8}"
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="回撤水下曲线">'
            f'<line x1="8" y1="{h-8}" x2="{w-8}" y2="{h-8}" stroke="#9ca3af"/>'
            f'<polygon points="{area}" fill="#ef4444" fill-opacity="0.25" '
            f'stroke="#dc2626" stroke-width="1"/>'
            f'<text x="8" y="14" font-size="11" fill="#374151">'
            f'最大回撤 {mdd:.2%}（分母=峰值）</text></svg>')


def monthly_svg(curve: List[Tuple[str, float]], w: int = 720, h: int = 180) -> str:
    """月度收益柱状（正绿负红；月收益=月末权益环比）。"""
    monthly: List[Tuple[str, float]] = []
    cur_month, cur_val = None, None
    for row in curve:
        d, v = row[0], row[1]
        m = d[:7]
        if cur_month is None:
            cur_month, cur_val = m, v
        elif m != cur_month:
            monthly.append((cur_month, cur_val))
            cur_month, cur_val = m, v
        else:
            cur_val = v
    if cur_month:
        monthly.append((cur_month, cur_val))
    if len(monthly) < 2:
        return "<svg></svg><!-- 数据不足 -->"
    rets = []
    prev = monthly[0][1]
    for m, v in monthly[1:]:
        rets.append((m, (v / prev - 1.0) if prev > 0 else 0.0))
        prev = v
    lo = min(r for _, r in rets)
    hi = max(r for _, r in rets)
    rng = (hi - lo) or 1.0
    zero_y = h - 20 - (0 - lo) / rng * (h - 40)
    bw = max(2.0, (w - 60) / max(1, len(rets)) - 2)
    bars = []
    for i, (m, r) in enumerate(rets):
        x = 50 + i * ((w - 60) / max(1, len(rets)))
        y0 = zero_y
        y1 = h - 20 - (r - lo) / rng * (h - 40)
        color = "#16a34a" if r >= 0 else "#dc2626"
        bars.append(f'<rect x="{x:.1f}" y="{min(y0,y1):.1f}" width="{bw:.1f}" '
                    f'height="{max(1.0, abs(y1-y0)):.1f}" fill="{color}"/>')
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="月度收益">'
            f'<line x1="40" y1="{zero_y:.0f}" x2="{w-10}" y2="{zero_y:.0f}" '
            f'stroke="#9ca3af"/>'
            + "".join(bars) +
            f'<text x="8" y="14" font-size="11" fill="#374151">'
            f'月度收益（{len(rets)} 个月，正绿负红）</text></svg>')


def sanitize_year_ticks(curve: List[Tuple[str, float]]) -> List[str]:
    """年度刻度（用于 HTML 表格页脚，非 SVG 内部）。"""
    ticks, seen = [], set()
    for d, _v in curve:
        y = d[:4]
        if y not in seen:
            seen.add(y)
            ticks.append(d)
    return ticks
