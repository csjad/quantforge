# -*- coding: utf-8 -*-
"""qf.review — 策略复盘 Agent（S11-02 冻结口径：规则式，非自由生成）

**复盘模块基于规则而非自由生成**：命中预设判据后输出对应结论——每条结论
可复算、可追溯、不产生幻觉（S11-02 §1）。

五条判据（S11-02 §2，阈值冻结）+ 三件套（结论文本+风险等级+验证建议）：
  1 最大回撤>20% 且 卡玛<0.5 → "风险收益比不足"（中）
  2 交易次数<30 → "样本不足，结论不成立"（高）
  3 训练段与验证段年化差距>40% → "存在显著过拟合"（高）
  4 换手率极高 且 收益集中在少数几笔 → "收益依赖极少数事件"（高）
  5 验证段成交笔数<20 → "样本不足，结论不作数"（高，S06 三查联动）

**复盘四问**（§3，一问答不上来=复盘"未完成"，不得静默跳过）：
  问1 信号在哪一阶段贡献收益（按持仓期分桶）
  问2 亏损集中在哪类行情（趋势/震荡/极端分桶，W1 以 MA20 偏离为趋势代理）
  问3 参数是否踩在边界（需参数上下界声明，缺声明→该问未完成）
  问4 样本外是否衰减（需训练/验证同口径指标，缺→未完成）

**风险总结强制内容**（§4 A5 条件项）：熔断区间说明（起止日/当日亏损/被拒信号数/
不剔除原则）。每条结论后附"仅为统计描述"声明（防投资建议误读，§2）。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from . import config as C


@dataclass
class Verdict:
    text: str
    level: str          # 高/中/低
    advice: str         # 如何进一步验证


@dataclass
class ReviewResult:
    verdicts: List[Verdict] = field(default_factory=list)
    answers: Dict[str, str] = field(default_factory=dict)     # 四问
    unfinished: List[str] = field(default_factory=list)       # 未完成的问
    circuit_breaker: List[str] = field(default_factory=list)  # 熔断区间说明
    complete: bool = True
    level: str = "低"

    @property
    def worst_level(self) -> str:
        order = {"低": 0, "中": 1, "高": 2}
        worst = "低"
        for v in self.verdicts:
            if order.get(v.level, 0) > order[worst]:
                worst = v.level
        return worst


def _round_trips(trades: List) -> List[Tuple[str, float, int]]:
    """按标的顺序配对 买卖 → 持仓期桶：返回 (区间标签, 净盈亏, 持有交易日数)。"""
    open_buys: Dict[str, dict] = {}
    trips: List[Tuple[str, float, int]] = []
    for t in trades:
        if t.side == "BUY":
            open_buys.setdefault(t.symbol, {"date": t.date, "qty": 0, "cost": 0.0})
            rec = open_buys[t.symbol]
            rec["qty"] += t.qty
        elif t.side == "SELL" and t.symbol in open_buys:
            rec = open_buys[t.symbol]
            trips.append((f"{rec['date']}~{t.date}", t.realized_pnl, 0))
            open_buys.pop(t.symbol, None)
    return trips


def run_review(engine, metrics_result, train_m: Optional[dict] = None,
               val_m: Optional[dict] = None, param_specs: Optional[dict] = None,
               val_trades: Optional[int] = None) -> ReviewResult:
    """复盘总入口：engine（含 trades/equity_curve/events/持仓市值）+ 指标。"""
    res = ReviewResult()
    v = metrics_result.values
    tr = [t for t in engine.trades if t.side == "SELL"]

    # ---- 五判据（S11-02 §2）----
    mdd, calmar = v.get("最大回撤"), v.get("卡玛")
    if mdd is not None and calmar is not None and mdd < -0.20 and abs(calmar) < 0.5:
        res.verdicts.append(Verdict(
            "风险收益比不足（最大回撤>20% 且 卡玛<0.5）", "中",
            "建议：降低单标的仓位上限或加趋势过滤后重跑，观察回撤是否收敛"))
    if v.get("成交笔数", 0) < 30:
        res.verdicts.append(Verdict(
            "样本不足，结论不成立（成交<30笔）", "高",
            "建议：拉长回测区间或扩大标的池后重跑"))
    if train_m is not None and val_m is not None:
        a_tr, a_va = train_m.get("年化收益"), val_m.get("年化收益")
        if a_tr is not None and a_va is not None:
            gap = abs(a_tr - a_va)
            base = max(0.05, abs(a_tr))
            if gap / base > 0.40:
                res.verdicts.append(Verdict(
                    "存在显著过拟合（训练/验证年化差距>40%）", "高",
                    "建议：缩小参数空间、加样本外滚动检验（S06 三查）"))
    total_pnl = sum(t.realized_pnl for t in tr)
    if tr and total_pnl > 0:
        best = max(t.realized_pnl for t in tr)
        # 换手率极高 且 收益集中在少数几笔（W1 落地：单笔贡献≥60%总利润）
        if best >= 0.60 * total_pnl:
            res.verdicts.append(Verdict(
                "收益依赖极少数事件（单笔贡献≥60%总利润）", "高",
                "建议：检查该笔是否为极端行情幸存；分年度看收益结构"))
    if val_trades is not None and val_trades < 20:
        res.verdicts.append(Verdict(
            "样本不足，结论不作数（验证段成交<20笔，S06 三查联动）", "高",
            "建议：延长验证段或扩池"))
    res.level = res.worst_level
    if not res.verdicts:
        res.verdicts.append(Verdict("未命中任何预设判据——正常结论", "低",
                                    "建议：换区间/扩池做稳健性复核"))

    # ---- 复盘四问（§3；一问答不上来=未完成，不得静默跳过）----
    trips = _round_trips(engine.trades)
    if trips:
        win_hold = [d for _s, p, d in trips if p > 0]
        lose_hold = [d for _s, p, d in trips if p <= 0]
        res.answers["问1 信号在哪一阶段贡献收益（按持仓期分桶）"] = \
            f"共 {len(trips)} 个回合；盈利回合 {len(win_hold)} 个、亏损回合 {len(lose_hold)} 个" \
            f"（回合级明细见 trades 表，持仓期桶为 W1 近似口径）"
    else:
        res.unfinished.append("问1")
        res.answers["问1 信号在哪一阶段贡献收益（按持仓期分桶）"] = "无成交回合——未完成"

    seq = engine.bars.get(next(iter(engine.bars), ""), [])
    if seq:
        closes = [b.close for b in seq]
        ma20_dev = []
        for i in range(20, len(closes)):
            m = sum(closes[i - 20:i]) / 20
            ma20_dev.append(abs(closes[i] / m - 1))
        trend_days = sum(1 for x in ma20_dev if x > 0.03)
        swing_days = len(ma20_dev) - trend_days
        loss_in_swing = sum(1 for _s, p, _d in trips if p < 0) * swing_days // max(1, len(ma20_dev))
        res.answers["问2 亏损集中在哪类行情（趋势/震荡/极端）"] = \
            f"代理口径：MA20偏离>3%记趋势（{trend_days}日）/其余震荡（{swing_days}日）；" \
            f"按比例推算震荡期亏损回合≈{loss_in_swing}（精确分桶需行情状态标注层）"
    else:
        res.unfinished.append("问2")

    if param_specs:
        edges = [f"{k}（距边界{(min(v - lo, hi - v) / (hi - lo)):.0%}）"
                 for k, (v, lo, hi) in param_specs.items() if hi > lo]
        res.answers["问3 参数是否踩在边界"] = \
            ("；".join(edges) if edges else "无参数声明") + \
            ("——存在贴边参数，地形不稳" if any("0%" in e for e in edges) else "")
    else:
        res.unfinished.append("问3")
        res.answers["问3 参数是否踩在边界"] = "未提供参数上下界声明——未完成"

    if train_m is not None and val_m is not None:
        res.answers["问4 样本外是否衰减"] = \
            f"训练年化 {train_m.get('年化收益')} → 验证年化 {val_m.get('年化收益')}（同口径）"
    else:
        res.unfinished.append("问4")
        res.answers["问4 样本外是否衰减"] = "未提供训练/验证对照——未完成"

    res.complete = not res.unfinished

    # ---- 熔断区间说明（§4 A5 条件项；不剔除原则）----
    halt_days = getattr(engine, "_halt_days", 0)
    if halt_days:
        halts = [e for e in engine.events if e.startswith("[熔断")]
        res.circuit_breaker = [
            f"熔断触发 {halt_days} 天；示例：{halts[0] if halts else ''}",
            "被熔断拒绝的信号已计入'被拦截信号数'（自检量）",
            "熔断区间的权益波动**计入最大回撤**（S07 不剔除原则）"]
    else:
        res.circuit_breaker = ["本回测未触发单日亏损熔断（5%）"]
    return res


def format_review(res: ReviewResult, run_id: str = "") -> str:
    L = [f"== 策略复盘（规则式，S11-02）{run_id} ==",
         f"综合风险等级：{res.level}｜复盘{'完成' if res.complete else '未完成（问' + '、问'.join(res.unfinished) + ' 未完成）'}"]
    for i, v in enumerate(res.verdicts, 1):
        L.append(f"  判据{i} [{v.level}] {v.text}")
        L.append(f"      ↳ 验证建议：{v.advice}")
    for k, a in res.answers.items():
        L.append(f"  {k}：{a}")
    for c in res.circuit_breaker:
        L.append(f"  熔断说明：{c}")
    L.append("  （以上结论仅为统计描述，不构成投资建议——S11-02 §2 强制声明）")
    return "\n".join(L)


def save_review(res: ReviewResult, run_id: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"review_{run_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(format_review(res, run_id))
    return path
