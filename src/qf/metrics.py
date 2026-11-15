# -*- coding: utf-8 -*-
"""qf.metrics — 十二项绩效指标（S09-01 冻结公式，W1 实现）

冻结口径（改动须走 C-XXX）：
  - 年化基准 = 250 交易日（非 365 自然日——用自然日会系统性低估收益）
  - 夏普 = 超额收益均值 ÷ 超额收益标准差 × √250；无风险利率默认 0（报告显式写出）；
    分母必须用超额收益标准差（rf=0 时与总收益标准差相同）
  - 最大回撤：分母用**峰值**（不是初始资金），同时输出回撤开始与结束日期；
    逐日累计权益→维护历史峰值→逐日计算当前回撤→取全局最小（不许月末采样）
  - 换手率：期间累计成交金额 ÷ 期间日均持仓市值（**双边口径**），年化 = 日均换手 × 250
  - 三类空值规则：无交易→胜率/盈亏比返回 None（不是 0）；权益恒定→夏普 None；
    单笔亏损为零→盈亏比 None —— 零会被下游误读为"极差表现"，None 才表示"无意义"
  - 短区间硬规则：区间 <100 交易日时年化类指标标注"参考"，不参与排序
  - 指标计算以成交记录为唯一来源，不得读取策略内部变量（A3 条件项）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import config as C


@dataclass
class MetricResult:
    values: dict = field(default_factory=dict)          # 名称 → 值或 None
    notes: dict = field(default_factory=dict)           # 名称 → 口径备注
    short_period: bool = False                          # 区间<100交易日：年化标"参考"

    def as_lines(self) -> List[str]:
        ref = "（参考，区间<100交易日）" if self.short_period else ""
        rows = []
        for k, v in self.values.items():
            note = self.notes.get(k, "")
            if v is None:
                rows.append(f"| {k} | —（无意义，空值规则） | {note} |")
            elif isinstance(v, float):
                rows.append(f"| {k} | {v:.4f} | {note} |")
            else:
                rows.append(f"| {k} | {v} | {note} |")
        return rows


def _daily_returns(equity: List[float]) -> List[float]:
    """日收益率（首日无收益，不参与统计）。"""
    out = []
    for i in range(1, len(equity)):
        if equity[i - 1] > 0:
            out.append(equity[i] / equity[i - 1] - 1.0)
    return out


def _pstd(vals: List[float]) -> float:
    """总体标准差（÷N，与 S04 σ 口径一致）。"""
    n = len(vals)
    if n == 0:
        return 0.0
    m = sum(vals) / n
    return math.sqrt(sum((v - m) ** 2 for v in vals) / n)


def compute_metrics(equity_curve: List[Tuple[str, float]],
                    trades: List, initial_cash: float = C.INITIAL_CASH,
                    rf: float = 0.0) -> MetricResult:
    """equity_curve: [(date, equity)...]；trades: qf.engine.Trade 列表。"""
    res = MetricResult()
    dates = [r[0] for r in equity_curve]
    eq = [r[1] for r in equity_curve]
    days = max(0, len(eq) - 1)
    res.short_period = days < C.ANNUALIZE_MIN_DAYS

    # 1 累计收益（净值口径：末值净值÷初值净值−1；初始资金=净值1.0）
    total_ret = (eq[-1] / initial_cash - 1.0) if eq and initial_cash > 0 else None
    res.values["累计收益"] = total_ret

    # 2 年化收益 (1+累计)^(250/交易日数)−1
    if total_ret is not None and days > 0 and (1 + total_ret) > 0:
        res.values["年化收益"] = (1.0 + total_ret) ** (C.TRADING_DAYS_PER_YEAR / days) - 1.0
        res.notes["年化收益"] = "250交易日基准" + ("（参考）" if res.short_period else "")
    else:
        res.values["年化收益"] = None
        res.notes["年化收益"] = "250交易日基准"

    rets = _daily_returns(eq)

    # 3 年化波动 = 日收益标准差 × √250
    vol = _pstd(rets) * math.sqrt(C.TRADING_DAYS_PER_YEAR) if rets else None
    res.values["年化波动"] = vol

    # 4 夏普 = 超额收益均值 ÷ 超额收益标准差 × √250（rf 默认 0，显式写出）
    res.notes["夏普"] = f"无风险利率={rf}；年化系数√250"
    if rets and _pstd(rets) > 0:
        excess = [r - rf for r in rets]
        m = sum(excess) / len(excess)
        res.values["夏普"] = m / _pstd(excess) * math.sqrt(C.TRADING_DAYS_PER_YEAR)
    else:
        res.values["夏普"] = None          # 权益恒定 → 空值而非无穷

    # 5 索提诺：分母只计下行超额收益标准差
    res.notes["索提诺"] = "分母=下行超额收益标准差"
    if rets:
        downside = [r - rf for r in rets if r < rf]
        if downside and _pstd(downside) > 0:
            m = sum(r - rf for r in rets) / len(rets)
            res.values["索提诺"] = m / _pstd(downside) * math.sqrt(C.TRADING_DAYS_PER_YEAR)
        else:
            res.values["索提诺"] = None     # 无下行波动 → 空值
    else:
        res.values["索提诺"] = None

    # 6/7 最大回撤（峰值分母）+ 回撤持续期（峰值到谷底历经交易日数）
    peak = eq[0] if eq else 0.0
    peak_i = 0
    mdd = 0.0
    mdd_start = mdd_end = ""
    mdd_days = 0
    for i, v in enumerate(eq):
        if v > peak:
            peak, peak_i = v, i
        if peak > 0:
            dd = (v - peak) / peak          # 分母=峰值（冻结口径）
            if dd < mdd:
                mdd = dd
                mdd_start = dates[peak_i] if peak_i < len(dates) else ""
                mdd_end = dates[i]
                mdd_days = i - peak_i
    res.values["最大回撤"] = mdd
    res.notes["最大回撤"] = f"分母=峰值；{mdd_start} → {mdd_end}"
    res.values["回撤持续期(交易日)"] = mdd_days

    # 8 卡玛 = 年化收益 ÷ |最大回撤|
    ann = res.values.get("年化收益")
    res.values["卡玛"] = (ann / abs(mdd)) if (ann is not None and mdd != 0) else None

    # 9/10 胜率 / 盈亏比（逐笔；无交易→空值而非零）
    sells = [t for t in trades if t.side == "SELL"]
    wins = [t for t in sells if t.realized_pnl > 0]
    losses = [t for t in sells if t.realized_pnl < 0]
    res.values["胜率"] = (len(wins) / len(sells)) if sells else None
    res.notes["胜率"] = "盈利笔数÷总卖出笔数；无交易=空值"
    if sells:
        avg_win = sum(t.realized_pnl for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.realized_pnl for t in losses) / len(losses) if losses else 0.0
        res.values["盈亏比"] = (avg_win / abs(avg_loss)) if avg_loss != 0 else None
        res.notes["盈亏比"] = "平均盈利÷|平均亏损|；零亏损=空值"
    else:
        res.values["盈亏比"] = None

    # 11 换手率（双边口径，年化）：引擎逐日记录持仓市值（equity_curve 三元组第三位）
    total_amt = sum(t.amount for t in trades)
    mv_curve = [m for _, _, m in equity_curve] if equity_curve and \
        len(equity_curve[0]) == 3 else []
    res.values["换手率(年化双边)"] = compute_turnover(total_amt, mv_curve, days)
    res.notes["换手率(年化双边)"] = "Σ成交金额÷日均持仓市值÷交易日数×250；空仓=空值"

    # 12 成交笔数
    res.values["成交笔数"] = len(trades)
    return res


def compute_turnover(total_trade_amount: float, holding_mv_curve: List[float],
                     days: int) -> Optional[float]:
    """年化双边换手率 = Σ成交金额 ÷ 期间日均持仓市值 ÷ 交易日数 × 250 × 100%。
    持仓市值曲线全零 → 空值（空仓策略换手无意义）。"""
    if not holding_mv_curve or days <= 0:
        return None
    avg_mv = sum(holding_mv_curve) / len(holding_mv_curve)
    if avg_mv <= 0:
        return None
    daily = total_trade_amount / avg_mv / days
    return daily * C.TRADING_DAYS_PER_YEAR * 100.0
