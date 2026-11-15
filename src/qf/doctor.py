# -*- coding: utf-8 -*-
"""qf.doctor — 未来函数三重检测（S03-01 §10 冻结口径的 CLI 落地）

| 重 | 检测 | 方法 | 执行频率（冻结） |
|---|---|---|---|
| 一 | 静态检测：扫描策略代码中对行情序列的**正偏移索引**，凡出现即报错 | 代码扫描 | 每次提交必跑（抓八成问题，成本近零） |
| 二 | 动态检测"末日陷阱"：把最后一根之后的数据全部置空（截断重跑），若前段权益分叉则存在隐性前视 | 注入测试 | 每次提交必跑（抓一成隐含问题） |
| 三 | 对照检测：同一策略跑两遍，第二遍数据整体后移一根；出现系统性偏移则存在隐性前视 | 双跑比对 | 仅里程碑版本跑（设计层错误必须重做） |

诚实边界：第一重是**文本级扫描**（正则），抓不到"通过函数间接取未来值"的写法，
所以必须与第二重配合——两层都过才算策略提交合格。
"""
from __future__ import annotations

import inspect
import re
from typing import Dict, List, Optional, Tuple

from .engine import BacktestEngine, Bar

# 正偏移索引的文本特征：[t+1] / [t + 2] / [i +1] / [idx+ n] / shift(-1) 等
POSITIVE_OFFSET_PATTERNS = [
    re.compile(r"\[\s*\w+\s*\+\s*\d+\s*\]"),        # [t + 1]
    re.compile(r"\[\s*\w+\+\d+\s*\]"),              # [t+1]
    re.compile(r"\.shift\s*\(\s*-\s*\d+"),          # .shift(-1)（pandas 前视惯用法）
    re.compile(r"\[\s*-\s*\d+\s*\]\s*\[?"),         # 负索引取"未来"（序列尾=未来值）
]


def static_scan(strategy) -> List[dict]:
    """第一重：策略源码正偏移索引扫描。返回命中列表（文件/行号/内容）。"""
    try:
        src = inspect.getsource(type(strategy))
    except (OSError, TypeError):
        return [{"error": "无法读取策略源码（动态构造类），静态扫描跳过——须以第二重兜底"}]
    findings = []
    for lineno, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("#", '"""', "'''")):
            continue                                  # 注释行不计（引用蓝图不违规）
        if "warmup" in stripped.lower():
            continue                                  # 预热窗口声明是元数据，不是行情序列访问
        for pat in POSITIVE_OFFSET_PATTERNS:
            if pat.search(stripped):
                findings.append({"line": lineno, "code": stripped.strip(),
                                 "pattern": pat.pattern})
                break
    return findings


def doomsday_trap(bars: Dict[str, List[Bar]], strategy_factory, calendar: List[str],
                  cuts: Optional[List[int]] = None, tol: float = 1e-9) -> List[dict]:
    """第二重：末日陷阱——在多个截断点重跑，比对**截断点最后一根K线上产生的订单**：
    完整数据下 t=k−1 产生了订单、截断数据下没有 → 该信号依赖了 k−1 之后的数据
    （蓝图口径："若策略仍能在末日产生依赖末日的信号则报错"）。
    前段权益曲线逐点一致一并比对（捕捉已成交路径的分叉）。"""
    all_dates = sorted({b.date for seq in bars.values() for b in seq})
    n = len(all_dates)
    cuts = cuts or [max(2, n // 2), max(3, int(n * 0.75)), n - 1]
    findings = []
    full = BacktestEngine(bars, strategy_factory(), calendar, seed_note="doctor")
    full.run()
    for k in cuts:
        if k >= n or k < 2:
            continue
        sub_bars = {s: [b for b in seq if b.date <= all_dates[k - 1]]
                    for s, seq in bars.items()}
        sub_cal = [d for d in calendar if d <= all_dates[k - 1]]
        part = BacktestEngine(sub_bars, strategy_factory(), sub_cal,
                              seed_note="doctor").run()
        # ① 末日信号比对（核心判据，信号层——不被去重/风控拦截掩盖）：
        # 完整数据在 t=k−1 收到信号、截断数据没收到 → 信号依赖了未来数据
        full_sig = full.signals_by_bar.get(k - 1, 0)
        part_sig = part.signals_by_bar.get(k - 1, 0)
        if full_sig != part_sig:
            findings.append({"cut": k, "kind": "末日信号依赖未来数据",
                             "full_signals": full_sig, "trunc_signals": part_sig})
            continue
        # ② 末日订单比对（信号同数但订单异 → 风控路径依赖未来账户状态）
        full_last = len([o for o in full.orders if o.created_bar == k - 1
                         and o.status != "已拒"])
        part_last = len([o for o in part.orders if o.created_bar == k - 1
                         and o.status != "已拒"])
        if full_last != part_last:
            findings.append({"cut": k, "kind": "末日订单依赖未来数据",
                             "full_orders": full_last, "trunc_orders": part_last})
            continue
        # ② 前段权益逐点一致（含费口径完全可复现）
        for i in range(k):
            if i < len(part.equity_curve) and i < len(full.equity_curve):
                d1 = full.equity_curve[i][1]
                d2 = part.equity_curve[i][1]
                if abs(d1 - d2) > tol:
                    findings.append({"cut": k, "kind": "权益分叉", "bar": i,
                                     "full": d1, "trunc": d2})
                    break
    return findings


def shift_compare(bars: Dict[str, List[Bar]], strategy_factory, calendar: List[str]) -> dict:
    """第三重：双跑比对——数据整体后移一根（丢弃首根），对比成交笔数。
    系统性多出/缺少成交 → 存在隐性前视的信号（信息性报告，不硬失败）。"""
    full = BacktestEngine(bars, strategy_factory(), calendar, seed_note="doctor")
    full.run()
    shifted_bars = {s: seq[1:] for s, seq in bars.items()}
    shifted_cal = calendar[1:]
    shifted = BacktestEngine(shifted_bars, strategy_factory(), shifted_cal,
                             seed_note="doctor")
    shifted.run()
    return {"original_fills": full.filled_count,
            "shifted_fills": shifted.filled_count,
            "diff_ratio": (abs(shifted.filled_count - full.filled_count)
                           / max(1, full.filled_count))}


def run_doctor(bars: Dict[str, List[Bar]], strategy_factory, calendar: List[str]) -> dict:
    """三重检测总入口：返回结构化报告（gate.py / CLI 共用）。"""
    probe = strategy_factory()
    scan = static_scan(probe)
    trap = doomsday_trap(bars, strategy_factory, calendar)
    shift = shift_compare(bars, strategy_factory, calendar)
    verdict = {
        "静态扫描": {"hits": len([h for h in scan if "line" in h]),
                    "detail": scan},
        "末日陷阱": {"diffs": len(trap), "detail": trap},
        "双跑比对": {"shift": shift,
                    "note": "信息性：成交笔数差异>50% 提示隐性前视嫌疑，须人工复核"},
    }
    ok = verdict["静态扫描"]["hits"] == 0 and verdict["末日陷阱"]["diffs"] == 0
    verdict["合格"] = ok
    return verdict


def format_report(v: dict) -> str:
    L = ["== 未来函数三重检测（S03-01 §10）=="]
    s = v["静态扫描"]
    L.append(f"  [第一重 静态扫描] 命中 {s['hits']} 处正偏移索引")
    for h in s["detail"]:
        if "line" in h:
            L.append(f"      ↳ L{h['line']}: {h['code']}  (pattern={h['pattern']})")
        else:
            L.append(f"      ↳ {h.get('error')}")
    t = v["末日陷阱"]
    L.append(f"  [第二重 末日陷阱] 截断重跑异常 {t['diffs']} 处")
    for d in t["detail"]:
        if d.get("kind") == "末日信号依赖未来数据":
            L.append(f"      ↳ cut={d['cut']} 末日信号：完整={d['full_signals']} "
                     f"截断={d['trunc_signals']} → 信号依赖了末日之后的数据")
        elif d.get("kind") == "末日订单依赖未来数据":
            L.append(f"      ↳ cut={d['cut']} 末日订单：完整={d['full_orders']} "
                     f"截断={d['trunc_orders']} → 信号依赖了末日之后的数据")
        else:
            L.append(f"      ↳ cut={d['cut']} 权益分叉 bar={d['bar']} "
                     f"full={d['full']:.4f} trunc={d['trunc']:.4f}")
    sh = v["双跑比对"]["shift"]
    L.append(f"  [第三重 双跑比对] 原始成交 {sh['original_fills']} 笔 vs "
             f"后移重跑 {sh['shifted_fills']} 笔（差异率 {sh['diff_ratio']:.0%}，信息性）")
    L.append(f"  判定：{'✅ 合格（前两重零命中）' if v['合格'] else '❌ 不合格——存在未来函数嫌疑'}")
    return "\n".join(L)
