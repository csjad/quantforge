# -*- coding: utf-8 -*-
"""qf.report_html — HTML 单文件报告（D-009：零依赖、内嵌 SVG、无 JS 无外链）。

页序冻结口径（S09-01 §5）：**首页九项配置 + 四自检量在前，收益在后**——
首屏决定可信度反应。HTML 版在 Markdown 版基础上增加三张内嵌图表
（权益/水下/月度，charts.py 生成，stdlib 纯手写 SVG）。
"""
from __future__ import annotations

import html as _html
from datetime import datetime
from typing import List

from . import config as C
from .charts import equity_svg, drawdown_svg, monthly_svg

_CSS = """
body{font-family:"Microsoft YaHei",system-ui,sans-serif;margin:24px auto;max-width:820px;
     color:#111827;line-height:1.55;padding:0 16px;background:#f8fafc}
h1{font-size:22px;border-bottom:2px solid #1d4ed8;padding-bottom:6px}
h2{font-size:16px;margin-top:26px;color:#1e3a8a}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
td,th{border:1px solid #d1d5db;padding:4px 8px;text-align:left}
th{background:#eff6ff}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:10px 14px;margin:10px 0}
.warn{background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:10px 14px;font-size:13px}
.ok{color:#16a34a;font-weight:600}.bad{color:#dc2626;font-weight:600}
small{color:#6b7280}
"""


def gen_html_report(engine, run_id: str, strategy_name: str, params: dict,
                    metrics_result, snapshot_id: str, out_dir: str,
                    symbol_pool_note: str) -> str:
    """生成单文件 HTML 报告；返回写入路径。与 report.gen_report 同源数据。"""
    eq = engine.equity_curve
    e = _html.escape

    def table(rows: List[List[str]], header: List[str]) -> str:
        th = "".join(f"<th>{e(c)}</th>" for c in header)
        trs = "".join("<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in r) + "</tr>"
                      for r in rows)
        return f"<table><tr>{th}</tr>{trs}</table>"

    cfg_rows = [
        ["1", "数据快照号", snapshot_id],
        ["2", "策略标识", strategy_name],
        ["3", "参数全量", "; ".join(f"{k}={v}" for k, v in params.items()) or "（默认参数）"],
        ["4", "费率全组", f"佣金{C.COMMISSION_RATE*1000:g}‰双边最低{C.COMMISSION_MIN:g}元/"
                     f"印花税{C.STAMP_RATE*1000:g}‰卖出/"
                     f"过户费{C.TRANSFER_RATE*1000:g}‰仅沪市"],
        ["5", "标的池口径", symbol_pool_note],
        ["6", "时间区间", f"{eq[0][0]} ~ {eq[-1][0]}（{len(eq)-1} 个交易日）" if len(eq) > 1 else "—"],
        ["7", "复权模式", "不复权（合成样例数据，无除权事件）"],
        ["8", "滑点模型", f"{C.SLIPPAGE_MODEL}（每边{C.SLIPPAGE*1000:g}‰，D-004 方案甲）"],
        ["9", "随机种子", engine.seed_note or "—"],
    ]
    selfchk = [["成交笔数", engine.filled_count],
               ["拒单笔数", engine.rejected_count],
               ["被拦截信号数", engine.blocked_signals],
               ["守恒式失败次数", engine.conservation_failures],
               ["费用恒等式失败", engine.fee_check_failures],
               ["熔断触发天数", engine._halt_days]]
    if engine.reject_reasons:
        for k, v in sorted(engine.reject_reasons.items()):
            selfchk.append([f"拒单原因·{k}", v])
    metric_rows = []
    for k, v in metrics_result.values.items():
        note = metrics_result.notes.get(k, "")
        if v is None:
            shown = "—（空值：无意义，S09 空值规则）"
        elif isinstance(v, float):
            shown = f"{v:.4f}"
        else:
            shown = str(v)
        metric_rows.append([k, shown, note])

    cons_ok = engine.conservation_failures == 0 and engine.fee_check_failures == 0
    parts: List[str] = [
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
        f"<title>回测报告 {e(run_id)}</title><style>{_CSS}</style></head><body>",
        f"<h1>回测报告 {e(run_id)}</h1>",
        f"<small>生成时间 {datetime.now().isoformat(sep=' ', timespec='seconds')}"
        f"｜W1 引擎（D-008 嵌入式形态）｜图表=内嵌 SVG（D-009 零依赖）</small>",
        "<h2>① 首页九项配置（缺一不可复现，S09-01 §5）</h2>",
        table(cfg_rows, ["#", "配置项", "取值"]),
        "<h2>② 自检量（S03-01 §11 + 费用恒等式）</h2>",
        table(selfchk, ["量", "值"]),
        f"<p class=\"{'ok' if cons_ok else 'bad'}\">"
        f"{'三守恒+费用恒等全部通过——钱没有凭空出现' if cons_ok else '守恒校验存在失败——结果不可采信，先定位再谈收益'}</p>",
        "<h2>③ 十二项绩效指标（S09-01 冻结口径）</h2>",
        table(metric_rows, ["指标", "值", "口径备注"]),
        ("<p><b>短区间硬规则：</b>区间不足 100 个交易日，年化类指标标注「参考」，"
         "不参与排序。</p>") if metrics_result.short_period else "",
        "<h2>④ 权益曲线（内嵌 SVG）</h2><div class='card'>",
        equity_svg([(d, v) for d, v, _m in eq]),
        "</div><h2>⑤ 回撤水下曲线（分母=峰值）</h2><div class='card'>",
        drawdown_svg([(d, v) for d, v, _m in eq]),
        "</div><h2>⑥ 月度收益</h2><div class='card'>",
        monthly_svg([(d, v) for d, v, _m in eq]),
        "</div>",
        "<h2>⑦ 风险声明（合规底线，S51 白皮书口径）</h2>",
        "<div class='warn'>本报告基于<b>合成样例数据</b>（非真实行情），仅用于引擎"
        "功能验证与教学；历史回测不代表未来表现，受滑点假设、费率口径、流动性约束、"
        "幸存者偏差与参数过拟合影响；本平台不接自动下单、不承诺收益、不隐藏配置；"
        "本报告不构成投资建议。</div>",
        "</body></html>",
    ]
    import os
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"report_{run_id}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(p for p in parts if p))
    return path
