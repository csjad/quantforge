# -*- coding: utf-8 -*-
"""qf.parallel — V8 本机并行执行器与任务分片表（S52-02 协议的 W8-01 实现）

实现口径（S52 冻结，改动须走 C-XXX）：
  - **任务标识三段绑定**：策略标识+参数哈希+数据快照号 → task_id=md5（天然幂等键）；
  - **bt_tasks 分片表三状态**（SQLite 等价 D-008 模式；Redis+PG 留部署形态）：
    待执行/执行中/已完成；**"执行中"重启一律回退"待执行"**（S06-01 §4.3：
    不许把执行中标已完成省时间——防"有记录但结果为空"的幽灵参数）；
  - **幂等三重防护（A3 前置条件）**：①结果先落盘（JSON+checksum）②task_id
    已完成且校验和过 → 跳过 ③重复提交入队即去重；
  - **本机并行**：ProcessPoolExecutor(processes=物理核数−4)（C-034）；
    worker 经 initializer 装载数据（每进程一次），任务只传轻量载荷；
    **并行不改算**——worker 内跑与串行路径同版本 run_combo，验收=双后端逐位一致；
  - **触发器（V8-04）**：auto 判定三取一（组数≥1000/池≥500/预估耗时≥30分钟）
    → parallel；否则 serial（单机零改动回归）；
  - 单组超时 300 秒：W8.0 以**整批 deadline**（组数×300s）执行，逐组粒度留部署形态
    （docstring 诚实边界）；Windows spawn 安全：worker 为本模块顶层函数。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from . import config as C

# ---------------------------------------------------------------- 触发器（V8-04）

def decide_backend(groups: int, pool_size: int, per_group_seconds: float = 0.03,
                   backend: str = C.DISTRIBUTED_BACKEND) -> str:
    """auto 三取一判定（S52-02 §1）；显式 backend 优先（只增不改）。
    remote 仅显式选择（auto 永不产生 remote——S52-02 冻结）。"""
    if backend in ("serial", "parallel", "remote"):
        return backend
    est = groups * max(per_group_seconds, 1e-6)
    if (groups >= C.DISTRIBUTED_TRIGGER_GROUPS
            or pool_size >= C.DISTRIBUTED_TRIGGER_POOL
            or est >= C.DISTRIBUTED_TRIGGER_SECONDS):
        return "parallel"
    return "serial"


# ---------------------------------------------------------------- bt_tasks 分片表（V8-01）

def open_tasks(db_path: str) -> sqlite3.Connection:
    """打开 bt_tasks 连接。
    check_same_thread=False：主控的 /result 回调在 HTTP 线程写库（远程后端），
    写路径由 TaskMaster.lock 串行化——连接创建方与写方分离是有意为之。"""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS bt_tasks(
      task_id TEXT PRIMARY KEY, status TEXT, params_json TEXT,
      snapshot_id TEXT, result_file TEXT, checksum TEXT, updated_at TEXT);
    """)
    conn.commit()
    return conn


def task_id_of(strategy_name: str, params: dict, snapshot_id: str) -> str:
    """任务标识三段绑定（策略+参数哈希+数据快照号，S06-01 §4.1）。"""
    body = json.dumps({"s": strategy_name, "p": params, "snap": snapshot_id},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.md5(body.encode()).hexdigest()


def claim_pending(conn, task_id: str, params: dict, snapshot_id: str,
                  result_file: str = "", checksum: str = "") -> bool:
    """幂等核验：已完成且校验和过 → False（跳过，不重算）；否则置执行中 → True。"""
    row = conn.execute("SELECT status, checksum FROM bt_tasks WHERE task_id=?",
                       (task_id,)).fetchone()
    if row and row[0] == "已完成" and result_file and os.path.exists(result_file) \
            and row[1] == checksum:
        return False
    conn.execute("INSERT OR REPLACE INTO bt_tasks VALUES(?,?,?,?,?,?,?)",
                 (task_id, "执行中", json.dumps(params, sort_keys=True,
                                               ensure_ascii=False),
                  snapshot_id, result_file, checksum,
                  time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return True


def mark_done(conn, task_id: str, result_file: str, checksum: str):
    """结果先落盘后调用：置已完成（双确认由 claim 端核验）。"""
    conn.execute("UPDATE bt_tasks SET status='已完成', result_file=?, checksum=?, "
                 "updated_at=? WHERE task_id=?",
                 (result_file, checksum, time.strftime("%Y-%m-%d %H:%M:%S"), task_id))
    conn.commit()


def reset_running(conn) -> int:
    """重启恢复：'执行中'一律回退'待执行'（S06-01 §4.3 冻结）。返回回退数。"""
    cur = conn.execute("UPDATE bt_tasks SET status='待执行', updated_at=? "
                       "WHERE status='执行中'",
                       (time.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------- 结果文件（幂等②）

def write_result_file(results_dir: str, task_id: str, record: dict) -> Tuple[str, str]:
    """结果 JSON 独立落盘（先落盘后置状态）→ (path, checksum)。"""
    os.makedirs(results_dir, exist_ok=True)
    path = os.path.join(results_dir, f"{task_id}.json")
    body = json.dumps(record, sort_keys=True, ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path, hashlib.md5(body.encode()).hexdigest()


# ---------------------------------------------------------------- 并行 worker（Windows spawn 安全：模块顶层）

_STATE: Dict[str, object] = {}


def _init_worker(bars, calendar, snapshot_id):
    """每 worker 进程装载一次数据（进程隔离=沙箱第一道边界复用）。"""
    _STATE["bars"] = bars
    _STATE["calendar"] = calendar
    _STATE["snapshot"] = snapshot_id


def _worker_one(payload: Tuple[str, dict, str, float]) -> dict:
    """单组任务：与串行路径**同版本 run_combo**（并行不改算——逐位一致的前提）。
    payload=(strategy_name, params, split, initial_cash)；策略经注册表名查类
    （避免 pickle 类）。异常即返回失败记录（不生成"看起来能跑"的结果）。"""
    from . import sweep as SW
    from .strategies import STRATEGY_REGISTRY
    strategy_name, params, split, initial_cash = payload
    try:
        bars = _STATE["bars"]
        calendar = _STATE["calendar"]
        snapshot_id = _STATE["snapshot"]
        rec = SW.run_combo(bars, calendar, STRATEGY_REGISTRY[strategy_name],
                           params, split, snapshot_id)
        rec["initial_cash"] = initial_cash
        rec["ok"] = True
        return rec
    except Exception as exc:  # noqa - 失败原因可读（S50 拒绝分类精神）
        return {"params": params, "ok": False, "flags": [f"执行失败: {exc}"],
                "train": {}, "val": {}, "score": None, "snapshot": {}}


def run_parallel(bars, calendar, strategy_name: str, combos: List[dict],
                 split: float, snapshot_id: str, conn,
                 results_dir: str, deadline_seconds: int = 300 * 10000,
                 initial_cash: float = C.INITIAL_CASH) -> List[dict]:
    """并行后端：提交→收集→结果先落盘→bt_tasks 置已完成。
    deadline=整批预算（组数×300s 的执行面，W8.0 诚实边界见模块 docstring）。"""
    if not combos:
        return []
    workers = max(1, (os.cpu_count() or 4) - 4)   # C-034 冻结：物理核数−4
    workers = min(workers, len(combos))
    records: Dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=workers,
                             initializer=_init_worker,
                             initargs=(bars, calendar, snapshot_id)) as ex:
        futures = {ex.submit(_worker_one, (strategy_name, p, split, initial_cash)):
                   p for p in combos}
        t0 = time.perf_counter()
        for fut in as_completed(futures, timeout=deadline_seconds):
            params = futures[fut]
            rec = fut.result()
            tid = task_id_of(strategy_name, params, snapshot_id)
            rec["task_id"] = tid
            rec["params"] = params
            path, ck = write_result_file(results_dir, tid, rec)
            # 幂等：结果先落盘，再核验-置已完成（重复提交/中断重启不重算）
            if claim_pending(conn, tid, params, snapshot_id, path, ck):
                mark_done(conn, tid, path, ck)
            records[tid] = rec
        elapsed = time.perf_counter() - t0
        del elapsed
    return list(records.values())
