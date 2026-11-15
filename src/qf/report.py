# -*- coding: utf-8 -*-
"""qf.report — Markdown 报告生成（S09-01 §5 可复现设计 + S03-01 §11 四自检量）

报告页序冻结口径（S09-01 §5）：**首页九项配置 + 四自检量在前，收益在后**——
首屏决定可信度反应，顺序反常识但对应真实场景。
首页九项配置缺一，复现即不可能。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List

from . import config as C


def gen_report(engine, run_id: str, strategy_name: str, params: dict,
               metrics_result, snapshot_id: str, out_dir: str,
               symbol_pool_note: str) -> str:
    eq = engine.equity_curve
    total_fees = sum(t.fees for t in engine.trades)
    total_slip = sum(t.slip_cost for t in engine.trades)
    buy_amt = sum(t.amount for t in engine.trades if t.side == "BUY")
    sell_amt = sum(t.amount for t in engine.trades if t.side == "SELL")

    L: List[str] = []
    L.append(f"# 回测报告 {run_id}")
    L.append("")
    L.append(f"生成时间：{datetime.now().isoformat(sep=' ', timespec='seconds')}"
             f"｜W1 引擎（D-008 嵌入式形态）")
    L.append("")
    L.append("## 首页九项配置（缺一不可复现，S09-01 §5）")
    L.append("")
    L.append("| # | 配置项 | 取值 |")
    L.append("|---|---|---|")
    cfg = [
        ("1", "数据快照号", snapshot_id),
        ("2", "策略标识", strategy_name),
        ("3", "参数全量", "; ".join(f"{k}={v}" for k, v in params.items()) or "（默认参数）"),
        ("4", "费率全组", f"佣金{C.COMMISSION_RATE*1000:g}‰双边最低{C.COMMISSION_MIN:g}元/"
                     f"印花税{C.STAMP_RATE*1000:g}‰卖出/"
                     f"过户费{C.TRANSFER_RATE*1000:g}‰仅沪市"),
        ("5", "标的池口径", symbol_pool_note),
        ("6", "时间区间", f"{eq[0][0]} ~ {eq[-1][0]}（{len(eq)-1} 个交易日）" if len(eq) > 1 else "—"),
        ("7", "复权模式", "不复权（合成样例数据，无除权事件）"),
        ("8", "滑点模型", f"{C.SLIPPAGE_MODEL}（每边{C.SLIPPAGE*1000:g}‰，D-004 方案甲）"),
        ("9", "随机种子", engine.seed_note or "—"),
    ]
    for a, b, c in cfg:
        L.append(f"| {a} | {b} | {c} |")

    L.append("")
    L.append("## 四自检量（S03-01 §11：出现'成交为零但收益为正'时靠这四个量定位）")
    L.append("")
    L.append("| 量 | 值 |")
    L.append("|---|---|")
    L.append(f"| 成交笔数 | {engine.filled_count} |")
    L.append(f"| 拒单笔数 | {engine.rejected_count} |")
    L.append(f"| 被拦截信号数 | {engine.blocked_signals} |")
    L.append(f"| 守恒式失败次数 | {engine.conservation_failures} |")
    L.append(f"| 熔断触发天数 | {engine._halt_days} |")

    L.append("")
    L.append("## 十二项绩效指标（S09-01 冻结口径）")
    L.append("")
    L.append("| 指标 | 值 | 口径备注 |")
    L.append("|---|---|---|")
    L.extend(metrics_result.as_lines())
    if metrics_result.short_period:
        L.append("")
        L.append("> 短区间硬规则：区间不足 100 个交易日，年化类指标一律标注"
                 "「参考」，不参与排序（三日年化≈荒谬放大）。")

    L.append("")
    L.append("## 成本明细（S03-01 §6：逐项打印，允许一键关闭对照）")
    L.append("")
    L.append("| 项 | 金额(元) |")
    L.append("|---|---|")
    L.append(f"| 买入成交金额（含滑点价） | {buy_amt:.2f} |")
    L.append(f"| 卖出成交金额（含滑点价） | {sell_amt:.2f} |")
    L.append(f"| 佣金+印花税+过户费合计 | {total_fees:.2f} |")
    L.append(f"| 滑点成本合计 | {total_slip:.2f} |")

    L.append("")
    L.append("## 权益曲线（逐日快照，前 20 行 + 月度汇总）")
    L.append("")
    L.append("| 日期 | 权益(元) | 持仓市值(元) |")
    L.append("|---|---|---|")
    for d, e, m in eq[:20]:
        L.append(f"| {d} | {e:.2f} | {m:.2f} |")
    if len(eq) > 20:
        L.append(f"| …（其余 {len(eq)-20} 行见 SQLite runs/equity 表） | | |")

    # 月度收益汇总
    monthly = {}
    last_eq = C.INITIAL_CASH
    for d, e, _m in eq:
        key = d[:7]
        monthly[key] = e
    L.append("")
    L.append("| 月 | 月末权益(元) |")
    L.append("|---|---|")
    prev = C.INITIAL_CASH
    for k, v in monthly.items():
        L.append(f"| {k} | {v:.2f}（较上月 {v/prev-1:+.2%}） |")
        prev = v

    L.append("")
    L.append("## 风险声明（合规底线，S51 白皮书口径）")
    L.append("")
    L.append("- 本报告基于**合成样例数据**（非真实行情），仅用于引擎功能验证与教学；")
    L.append("- 历史回测不代表未来表现，受滑点假设、费率口径、流动性约束、幸存者偏差"
             "与参数过拟合影响；")
    L.append("- 本平台不接自动下单、不承诺收益、不隐藏配置；本报告不构成投资建议。")

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"report_{run_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return path
