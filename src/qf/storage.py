# -*- coding: utf-8 -*-
"""qf.storage — W1 嵌入式存储（D-008）：CSV K线按标的+年分区 + SQLite 运行留痕

对齐 D-002 分区键（标的+年）；换 PostgreSQL/Parquet 只改本模块（接口隔离）。
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sqlite3
from typing import Dict, List

from .engine import Bar


def load_bars_csv(kline_dir: str, symbols=None) -> Dict[str, List[Bar]]:
    """读取 data/kline/{symbol}_{year}.csv → {symbol: [Bar...]}（按日期升序）。"""
    bars: Dict[str, List[Bar]] = {}
    for path in sorted(glob.glob(os.path.join(kline_dir, "*.csv"))):
        name = os.path.splitext(os.path.basename(path))[0]
        symbol = name.rsplit("_", 1)[0]
        if symbols and symbol not in symbols:
            continue
        seq = bars.setdefault(symbol, [])
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                seq.append(Bar(row["date"], float(row["open"]), float(row["high"]),
                               float(row["low"]), float(row["close"]),
                               float(row["volume"])))
    for s in bars:
        bars[s].sort(key=lambda b: b.date)
    return bars


class RunStore:
    """SQLite 运行留痕：runs / trades / equity 三表（表结构同构 D-002 部署形态）。"""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self):
        cur = self.conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS runs(
          run_id TEXT PRIMARY KEY, created_at TEXT, strategy TEXT, params TEXT,
          config TEXT, metrics TEXT, selfcheck TEXT, events TEXT);
        CREATE TABLE IF NOT EXISTS trades(
          run_id TEXT, date TEXT, symbol TEXT, side TEXT, exec_price REAL,
          open_price REAL, qty INTEGER, amount REAL, fees REAL, slip_cost REAL,
          realized_pnl REAL, created_bar INTEGER);
        CREATE TABLE IF NOT EXISTS equity(
          run_id TEXT, date TEXT, equity REAL, holding_mv REAL);
        """)
        self.conn.commit()

    def save_full(self, run_id: str, strategy_name: str, params: dict,
                  config: dict, engine, metric_values: dict, metric_notes: dict,
                  short_period: bool):
        cur = self.conn.cursor()
        created = __import__("datetime").datetime.now().isoformat(
            sep=" ", timespec="seconds")
        cur.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, created,
                     strategy_name, json.dumps(params, ensure_ascii=False),
                     json.dumps(config, ensure_ascii=False),
                     json.dumps({k: (None if v is None else round(v, 6) if isinstance(v, float) else v)
                                 for k, v in metric_values.items()}, ensure_ascii=False),
                     json.dumps(metric_notes, ensure_ascii=False),
                     json.dumps(short_period)))
        cur.execute("DELETE FROM trades WHERE run_id=?", (run_id,))
        for t in engine.trades:
            cur.execute("INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, t.date, t.symbol, t.side, t.exec_price, t.open_price,
                         t.qty, t.amount, t.fees, t.slip_cost, t.realized_pnl,
                         t.created_bar))
        cur.execute("DELETE FROM equity WHERE run_id=?", (run_id,))
        for d, e, m in engine.equity_curve:
            cur.execute("INSERT INTO equity VALUES(?,?,?,?)", (run_id, d, e, m))
        self.conn.commit()

    def list_runs(self) -> List[tuple]:
        cur = self.conn.cursor()
        return cur.execute(
            "SELECT run_id, created_at, strategy FROM runs ORDER BY created_at"
        ).fetchall()

    def load_run(self, run_id: str) -> dict:
        cur = self.conn.cursor()
        row = cur.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return {}
        cols = [c[0] for c in cur.description]
        out = dict(zip(cols, row))
        out["trades"] = cur.execute(
            "SELECT * FROM trades WHERE run_id=? ORDER BY date", (run_id,)).fetchall()
        out["equity"] = cur.execute(
            "SELECT * FROM equity WHERE run_id=? ORDER BY date", (run_id,)).fetchall()
        return out
