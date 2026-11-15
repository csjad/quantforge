# -*- coding: utf-8 -*-
"""qf.remote_worker — 远程算力 worker（受信任子进程，S55 协议客户端）

循环：GET /claim 认领任务 → 本地跑 run_combo（与串行/并行同版本，并行不改算）
→ POST /result 回传。队列空即退出。鉴权 token 必配，403 即退出（不重试蛮打）。
诚实边界：数据文件经命令行路径共享（同机）；跨机数据同步=部署形态。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from qf.engine import Bar              # noqa: E402（受信任侧）


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, obj: dict):
    body = json.dumps(obj, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--bars", required=True)
    a = ap.parse_args()

    with open(a.bars, "r", encoding="utf-8", newline="") as f:
        f.readline()
        bars = []
        for line in f:
            parts = line.strip().split(",")
            if len(parts) == 6:
                d, o, h, l, c, v = parts
                bars.append(Bar(d, float(o), float(h), float(l), float(c), float(v)))
    bars_dict = {"S": bars}
    calendar = sorted({b.date for b in bars})

    while True:
        try:
            resp = _get(f"{a.master}/claim?token={a.token}&worker={os.getpid()}")
        except Exception as exc:  # noqa - 网络/鉴权失败即退出（不蛮打）
            print(f"worker exit: {exc}", file=sys.stderr)
            return
        task = resp.get("task")
        if not task:
            return                                  # 队列空
        from qf import sweep as SW
        from qf.strategies import STRATEGY_REGISTRY
        params = task["params"]
        try:
            rec = SW.run_combo(bars_dict, calendar,
                               STRATEGY_REGISTRY[resp["strategy"]],
                               params, resp["split"], resp["snapshot_id"])
            rec["ok"] = True
        except Exception as exc:  # noqa
            rec = {"params": params, "ok": False, "flags": [f"执行失败: {exc}"],
                   "train": {}, "val": {}, "score": None, "snapshot": {}}
        rec["task_id"] = task["task_id"]
        rec["params"] = params
        _post(f"{a.master}/result?token={a.token}", rec)
        time.sleep(0.02)


if __name__ == "__main__":
    main()
