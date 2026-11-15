# -*- coding: utf-8 -*-
"""qf.sweep — 参数遍历寻优（S06-01/D-005 冻结口径的 W1 实现）

冻结口径（改动须走 C-XXX）：
  - **D-005：随机搜索为主、网格遍历为对照（仅两参数以内）、贝叶斯留 V3**；
  - 网格步长默认=区间/10（B-12 步长裁剪）；
  - 任务标识 = 策略标识 + 参数哈希 + 数据快照号（三段绑定）；
  - **断点续跑三状态**：已完成=结果记录存在（W1 以"checkpoint 记录+内容校验和"
    双重确认），不允许把未完成标成已完成（防"有记录但结果为空"的参数被排序）；
  - 每组输出**全部 12 项指标**（不只收益率），排序=年化40%+夏普30%+回撤30%（可配）；
  - **训练/验证切分**：切分点写入任务快照（S06-02）；
  - 过拟合标记（S06-02 三查的 W1 落地）：**笔数不足（<20）/ 参数贴边（距边界≤5%
    量程——地形贴边代理）/ 单年贡献（某一年贡献≥80%总利润）/ 样本外失效
    （训练夏普>0.5 而验证夏普≤0 或空值）**；
  - 并发=核数−4 属部署形态（Redis+PG 分片表），W1 串行+checkpoint 等价落地；
    抽样复核（50只抽样→前20组全池复核）为池级遍历口径，W1 单标的池直接全跑。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
from typing import Callable, Dict, List, Optional, Tuple

from .engine import BacktestEngine
from .metrics import compute_metrics
from . import config as C

WEIGHTS = {"年化": 0.4, "夏普": 0.3, "回撤": 0.3}      # S06-01 §5 默认权重，可配
_last_backend = "serial"                               # 最近一次 sweep 的实际后端（观测面）
MIN_TRADES_FLAG = 20                                   # 笔数不足阈值（S43 冻结口径）
EDGE_TOL = 0.05                                        # 参数贴边：距边界≤5%量程
YEAR_CONCENTRATION = 0.80                              # 单年贡献阈值


# ---------------------------------------------------------------- 参数空间

def parse_param(spec: str) -> Tuple[str, float, float, Optional[float]]:
    """'fast=5:60' → (name, lo, hi, None)；'fast=5:60:5' → 显式步长。"""
    name, rng = spec.split("=", 1)
    parts = [float(x) for x in rng.split(":")]
    if len(parts) == 2:
        return name.strip(), parts[0], parts[1], None
    if len(parts) == 3:
        return name.strip(), parts[0], parts[1], parts[2]
    raise ValueError(f"参数区间格式错误：{spec}（应为 name=lo:hi[:step]）")


def grid_combos(specs: List[Tuple[str, float, float, Optional[float]]]) -> List[dict]:
    """网格遍历（D-005：仅两参数以内）；步长默认=区间/10（B-12）。"""
    if len(specs) > 2:
        raise SystemExit("网格遍历仅支持两参数以内（D-005 冻结口径）——"
                         "三参数以上请用 --mode random（同等预算期望覆盖更好）")
    axes = []
    for name, lo, hi, step in specs:
        st = step if step is not None else max(1.0, (hi - lo) / 10.0)
        vals, v = [], lo
        while v <= hi + 1e-9:
            vals.append(int(v) if float(v).is_integer() else round(v, 4))
            v += st
        axes.append((name, vals))
    combos = [{}]
    for name, vals in axes:
        combos = [dict(c, **{name: v}) for c in combos for v in vals]
    return combos


def random_combos(specs: List[Tuple[str, float, float, Optional[float]]],
                  n: int, seed: int) -> List[dict]:
    """随机搜索（D-005 主策略）：种子化均匀采样，整数参数取整。"""
    rng = random.Random(seed)
    combos = []
    for _ in range(n):
        c = {}
        for name, lo, hi, _step in specs:
            v = rng.uniform(lo, hi)
            c[name] = int(round(v)) if float(lo).is_integer() else round(v, 4)
        combos.append(c)
    return combos


def params_hash(params: dict) -> str:
    return hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]


# ---------------------------------------------------------------- 单组回测

def run_combo(bars: Dict[str, List], calendar: List[str], strategy_cls: Callable,
              params: dict, split: float, snapshot_id: str) -> dict:
    """训练/验证切分回测：切分点写入快照；两侧各自全新账户（S06-02 口径）。"""
    n = len(calendar)
    cut = max(2, min(int(n * split), n - 2))   # 保证训练/验证两侧各≥2根K线
    train_cal, val_cal = calendar[:cut], calendar[cut:]

    def slice_bars(days):
        dayset = set(days)
        return {s: [b for b in seq if b.date in dayset] for s, seq in bars.items()}

    def run_slice(days):
        eb = {s: seq for s, seq in slice_bars(days).items() if seq}
        ec = [d for d in days if any(b.date == d for seq in eb.values() for b in seq)]
        eng = BacktestEngine(eb, strategy_cls(**params), ec, seed_note=snapshot_id)
        eng.run()
        return eng, compute_metrics(eng.equity_curve, eng.trades)

    eng_tr, m_tr = run_slice(train_cal)
    eng_va, m_va = run_slice(val_cal)

    def pack(eng, m):
        return {"累计收益": m.values["累计收益"], "年化收益": m.values["年化收益"],
                "夏普": m.values["夏普"], "最大回撤": m.values["最大回撤"],
                "卡玛": m.values["卡玛"], "胜率": m.values["胜率"],
                "盈亏比": m.values["盈亏比"], "换手率(年化双边)": m.values["换手率(年化双边)"],
                "成交笔数": m.values["成交笔数"]}

    # ---- 过拟合标记（三查的 W1 落地）----
    flags = []
    if (m_tr.values["成交笔数"] or 0) < MIN_TRADES_FLAG:
        flags.append(f"笔数不足(<{MIN_TRADES_FLAG})")
    # 参数贴边（地形贴边代理）：距最近边界≤5%量程
    for name, lo, hi, _s in _last_specs:
        rng_ = hi - lo
        v = params.get(name)
        if rng_ > 0 and v is not None and (
                (v - lo) / rng_ <= EDGE_TOL or (hi - v) / rng_ <= EDGE_TOL):
            flags.append(f"参数贴边({name})")
            break
    # 单年贡献（训练段权益曲线逐年利润占比）
    eq_tr = eng_tr.equity_curve
    yearly: Dict[str, float] = {}
    prev = eq_tr[0][1]
    for d, e, _m in eq_tr:
        y = d[:4]
        yearly[y] = e
    start_eq = eq_tr[0][1]
    prev = start_eq
    diffs = []
    for y, e in yearly.items():
        diffs.append((y, e - prev))
        prev = e
    total_pnl = eq_tr[-1][1] - start_eq
    if abs(total_pnl) > 1e-9:
        for y, dp in diffs:
            if abs(dp) >= YEAR_CONCENTRATION * abs(total_pnl):
                flags.append(f"单年贡献({y}≥80%)")
                break
    # 样本外失效
    tr_sh, va_sh = m_tr.values["夏普"], m_va.values["夏普"]
    if tr_sh is not None and tr_sh > 0.5 and (va_sh is None or va_sh <= 0):
        flags.append("样本外失效")

    score = None
    ann, sh, mdd = m_va.values["年化收益"], m_va.values["夏普"], m_va.values["最大回撤"]
    if None not in (ann, sh, mdd):
        score = (WEIGHTS["年化"] * ann + WEIGHTS["夏普"] * sh
                 + WEIGHTS["回撤"] * abs(mdd))
    return {"params": params, "score": score,
            "train": pack(eng_tr, m_tr), "val": pack(eng_va, m_va),
            "flags": flags,
            "snapshot": {"split_index": cut, "split_ratio": split,
                         "split_date": train_cal[-1] if train_cal else "",
                         "snapshot_id": snapshot_id}}


_last_specs: List[Tuple[str, float, float, Optional[float]]] = []


# ---------------------------------------------------------------- 主流程

def _rank_key(r: dict):
    """排序键（双后端一致）：验证分 → 训练夏普 → 参数规范化串（平分时顺序确定，
    保证双后端结果逐位可比——S52-01 §5 硬口径）。"""
    return (-(r["score"] if r["score"] is not None else -1e18),
            -(r["train"].get("夏普") or -1e18),
            json.dumps(r["params"], sort_keys=True, ensure_ascii=False))


def sweep(bars: Dict[str, List], calendar: List[str], strategy_cls: Callable,
          specs: List[Tuple[str, float, float, Optional[float]]], mode: str = "random",
          groups: int = 100, split: float = 0.7, seed: int = 20261113,
          checkpoint_dir: str = "out", backend: str = "auto",
          per_group_seconds: float = 0.03,
          tasks_db: Optional[str] = None) -> Tuple[List[dict], str]:
    """backend（V8-04，只增参数）：auto=触发器三取一判定 / serial=单机串行（原路径
    零改动）/ parallel=本机并行（bt_tasks 分片+结果落盘+幂等，S52-02 协议）。
    双后端结果**逐位一致**（worker 与串行同版本 run_combo——并行不改算）。"""
    if backend not in ("auto", "serial", "parallel", "remote"):
        raise SystemExit(f"未知 backend：{backend}（可选 auto/serial/parallel/remote）")
    from . import parallel as PL
    from .config import DISTRIBUTED_BACKEND
    backend = PL.decide_backend(groups, len(bars), per_group_seconds,
                                backend if backend != "auto" else DISTRIBUTED_BACKEND)
    global _last_backend
    _last_backend = backend
    global _last_specs
    _last_specs = specs
    snapshot_id = f"SYN-{len(bars)}sym-{len(calendar)}bars"
    task = f"{getattr(strategy_cls, 'name', 'strategy')}-{params_hash({'specs': [tuple(s) for s in specs], 'mode': mode})}-{snapshot_id}"
    ck_path = os.path.join(checkpoint_dir, f"sweep_{task}.json")
    records: Dict[str, dict] = {}
    checksums: Dict[str, str] = {}
    if os.path.exists(ck_path):
        with open(ck_path, "r", encoding="utf-8") as f:
            prev = json.load(f)
        for tid, rec in prev.get("records", {}).items():
            body = json.dumps(rec, sort_keys=True, ensure_ascii=False)
            if hashlib.md5(body.encode()).hexdigest() == prev.get("checksums", {}).get(tid):
                records[tid] = rec                      # 已完成=记录存在+校验和双确认
                checksums[tid] = prev["checksums"][tid]
        print(f"断点续跑：载入 {len(records)} 组已完成记录（校验和核验通过）")

    if mode == "grid":
        combos = grid_combos(specs)
    else:
        combos = random_combos(specs, groups, seed)

    # ---- V8 分发：parallel 走独立执行器（serial 路径保持原样零回归）----
    if backend == "parallel":
        results_dir = os.path.join(checkpoint_dir, "sweep_results")
        db_path = os.path.join(os.path.dirname(checkpoint_dir), "data", "qf.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = PL.open_tasks(db_path)
        try:
            PL.reset_running(conn)
            precords = PL.run_parallel(bars, calendar,
                                       getattr(strategy_cls, "name", "strategy"),
                                       combos, split, snapshot_id, conn,
                                       results_dir)
        finally:
            conn.close()
        ranked = sorted(precords, key=_rank_key)
        return ranked, ck_path

    # ---- W8.1 远程算力（仅显式选择；auto 永不产生 remote——S52-02 冻结）----
    if backend == "remote":
        from . import remote as RM
        results_dir = os.path.join(checkpoint_dir, "sweep_results")
        db_path = tasks_db or os.path.join(os.path.dirname(checkpoint_dir),
                                           "data", "qf.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = PL.open_tasks(db_path)
        try:
            PL.reset_running(conn)
            rrecords = RM.run_remote(bars, calendar,
                                     getattr(strategy_cls, "name", "strategy"),
                                     combos, split, snapshot_id, conn,
                                     results_dir)
        finally:
            conn.close()
        ranked = sorted(rrecords, key=_rank_key)
        return ranked, ck_path

    done = 0
    for i, params in enumerate(combos):
        tid = params_hash(params)
        if tid in records:
            continue                                    # 断点续跑：跳过已完成组
        try:
            rec = run_combo(bars, calendar, strategy_cls, params, split, snapshot_id)
            rec["task_id"] = tid
            body = json.dumps(rec, sort_keys=True, ensure_ascii=False)
            records[tid] = rec
            checksums[tid] = hashlib.md5(body.encode()).hexdigest()
        except (ValueError, AssertionError) as exc:
            # 非法组合退回并留痕（S50 拒绝分类精神：不生成"看起来能跑"的记录）
            rec = {"task_id": tid, "params": params, "score": None,
                   "train": {}, "val": {},
                   "flags": [f"参数非法(构造失败: {exc})"], "snapshot": {}}
            records[tid] = rec
            checksums[tid] = ""
        done += 1
        # **每组完成即落盘**（S06-01 §7 进度持久化；杀进程后从 checkpoint 继续）
        if done % 5 == 0 or i == len(combos) - 1:
            os.makedirs(checkpoint_dir, exist_ok=True)
            with open(ck_path, "w", encoding="utf-8") as f:
                json.dump({"task": task, "records": records,
                           "checksums": checksums}, f, ensure_ascii=False)

    # 聚合排序：验证段加权分（None 排最后）；平分时按参数规范化串（双后端一致）
    ranked = sorted(records.values(), key=_rank_key)
    os.makedirs(checkpoint_dir, exist_ok=True)
    with open(ck_path, "w", encoding="utf-8") as f:
        json.dump({"task": task, "records": records, "checksums": checksums},
                  f, ensure_ascii=False)
    return ranked, ck_path


def format_top(ranked: List[dict], top: int = 10) -> str:
    L = [f"== 寻优结果（按验证段加权分排序，权重 年化{WEIGHTS['年化']}/夏普{WEIGHTS['夏普']}/回撤{WEIGHTS['回撤']}）=="]
    L.append(f"{'#':<3} {'参数':<22} {'验证得分':>8} {'训练夏普':>8} {'验证夏普':>8} "
             f"{'验证年化':>8} {'验证回撤':>8} {'笔数':>5} 标记")
    for i, r in enumerate(ranked[:top], 1):
        p = ",".join(f"{k}={v}" for k, v in r["params"].items())
        va = r["val"]
        L.append(f"{i:<3} {p:<22} "
                 f"{('—' if r['score'] is None else f'{r['score']:.3f}'):>8} "
                 f"{('—' if r['train']['夏普'] is None else f'{r['train']['夏普']:.2f}'):>8} "
                 f"{('—' if va['夏普'] is None else f'{va['夏普']:.2f}'):>8} "
                 f"{('—' if va['年化收益'] is None else f'{va['年化收益']:+.2%}'):>8} "
                 f"{('—' if va['最大回撤'] is None else f'{va['最大回撤']:.2%}'):>8} "
                 f"{va['成交笔数']:>5} {';'.join(r['flags']) or '—'}")
    n_flag = sum(1 for r in ranked if r["flags"])
    L.append(f"过拟合标记：{n_flag}/{len(ranked)} 组带标记——"
             f"带标记组的排名**不可作为结论**（S43 排名不可信三情形联动）")
    return "\n".join(L)
