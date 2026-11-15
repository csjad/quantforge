# -*- coding: utf-8 -*-
"""qf.remote — W8.1 远程算力最小真实闭环（S52-02 §6 协议启用，默认关）

启用条件（S52-02 冻结）：**仅显式选择**（backend="remote"，auto 永不产生 remote）。
协议三环节（真实 HTTP，127.0.0.1 动态端口）：
  分片认领  GET /claim?token=..&worker=..  → 弹出下一任务（幂等 task_id）
  结果回传  POST /result?token=..         → 结果 JSON 落盘 + bt_tasks 双确认置已完成
  鉴权      token 不匹配 → 403 并记录（**V8-06 越权用例落地**：S51 A5 条件
            "V9 越权测试提前到 V8 阶段准备"——本模块即落点）
诚实边界：worker 与主控同机共享数据文件（跨机数据同步=部署形态）；协议语义
（认领/回传/鉴权/幂等）与部署形态一致，换传输层不动逻辑。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional

from . import config as C
from . import parallel as PL

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER_PY = os.path.join(HERE, "remote_worker.py")


class TaskMaster:
    """主控：任务队列 + token 鉴权 + 结果回传落盘（ThreadingHTTPServer）。"""

    def __init__(self, token: str, tasks: List[dict], strategy_name: str,
                 snapshot_id: str, split: float, conn, results_dir: str,
                 initial_cash: float = C.INITIAL_CASH):
        self.token = token
        self.pending = list(tasks)          # [{task_id, params}...]
        self.lock = threading.Lock()
        self.strategy_name = strategy_name
        self.snapshot_id = snapshot_id
        self.split = split
        self.conn = conn
        self.results_dir = results_dir
        self.initial_cash = initial_cash
        self.records: Dict[str, dict] = {}
        self.denied: List[str] = []         # 鉴权拒绝记录（V8-06）
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.port = 0

    # ---- HTTP ----
    def _handler(self):
        master = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):      # 静默默认日志
                pass

            def _send(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                from urllib.parse import urlparse, parse_qs
                u = urlparse(self.path)
                q = parse_qs(u.query)
                if (q.get("token") or [""])[0] != master.token:
                    master.denied.append(f"GET {u.path} token不匹配")
                    self._send(403, {"error": "token不匹配（V8-06 越权拦截）"})
                    return
                if u.path == "/health":
                    self._send(200, {"ok": True, "pending": len(master.pending)})
                elif u.path == "/claim":
                    with master.lock:
                        if master.pending:
                            t = master.pending.pop(0)
                            self._send(200, {"task": t,
                                             "strategy": master.strategy_name,
                                             "snapshot_id": master.snapshot_id,
                                             "split": master.split,
                                             "initial_cash": master.initial_cash})
                        else:
                            self._send(200, {"task": None})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                from urllib.parse import urlparse, parse_qs
                u = urlparse(self.path)
                q = parse_qs(u.query)
                if (q.get("token") or [""])[0] != master.token:
                    master.denied.append(f"POST {u.path} token不匹配")
                    self._send(403, {"error": "token不匹配（V8-06 越权拦截）"})
                    return
                if u.path != "/result":
                    self._send(404, {"error": "not found"})
                    return
                n = int(self.headers.get("Content-Length", 0))
                rec = json.loads(self.rfile.read(n).decode("utf-8"))
                tid = rec.get("task_id")
                rec["params"] = rec.get("params", {})
                with master.lock:                      # DB 写路径串行化（跨线程连接）
                    path, ck = PL.write_result_file(master.results_dir, tid, rec)
                    PL.mark_done(master.conn, tid, path, ck)
                    master.records[tid] = rec
                self._send(200, {"ok": True})

        return H

    def start(self) -> int:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.port

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


def run_remote(bars, calendar, strategy_name: str, combos: List[dict],
               split: float, snapshot_id: str, conn, results_dir: str,
               token: str = "qf-remote-token", initial_cash: float = C.INITIAL_CASH,
               timeout_seconds: int = 300 * 10000) -> List[dict]:
    """远程后端：主控线程 + worker 子进程（真实 HTTP 认领/回传）。"""
    import tempfile
    tasks = [{"task_id": PL.task_id_of(strategy_name, p, snapshot_id), "params": p}
             for p in combos]
    # 任务行预插入 bt_tasks（待执行）——否则 /result 的 mark_done UPDATE 落空
    from datetime import datetime as _dt
    now = _dt.now().isoformat(sep=" ", timespec="seconds")
    for t in tasks:
        conn.execute("INSERT OR REPLACE INTO bt_tasks VALUES(?,?,?,?,?,?,?)",
                     (t["task_id"], "待执行",
                      json.dumps(t["params"], sort_keys=True, ensure_ascii=False),
                      snapshot_id, "", "", now))
    conn.commit()
    master = TaskMaster(token, tasks, strategy_name, snapshot_id, split,
                        conn, results_dir, initial_cash)
    port = master.start()
    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "bars.csv")
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            f.write("date,open,high,low,close,volume\n")
            for seq in bars.values():
                for b in seq:
                    f.write(f"{b.date},{b.open},{b.high},{b.low},{b.close},{b.volume}\n")
        proc = subprocess.Popen(
            [sys.executable, WORKER_PY, "--master", f"http://127.0.0.1:{port}",
             "--token", token, "--bars", csv_path],
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        try:
            t0 = time.perf_counter()
            last_snapshot, last_change = -1, time.perf_counter()
            # 等待：结果数<任务数 且 未超 deadline；
            # **无进展护栏**：快照（结果数×100+队列数）30 秒无变化 → worker 已死，
            # 放弃等待（防无限挂——worker 早死时队列被 claim 清空但不产生结果）
            while (len(master.records) < len(tasks)
                   and (time.perf_counter() - t0) < timeout_seconds):
                time.sleep(0.1)
                snap = len(master.records) * 100 + len(master.pending)
                if snap != last_snapshot:
                    last_snapshot, last_change = snap, time.perf_counter()
                elif time.perf_counter() - last_change > 30:
                    break
        finally:
            proc.terminate()
            master.stop()
    return list(master.records.values())
