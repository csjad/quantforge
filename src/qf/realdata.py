# -*- coding: utf-8 -*-
"""qf.realdata — 真实行情数据通道（D-003 免费源口径的可选扩展层，ADR D-010）

架构定位（ADR D-010 冻结）：
  - **引擎永远只读 CSV**（本地文件主数据源，D-003/S02 物理截断不变）——
    本模块只是"在线拉取 → 落地为 CSV"的**可选采集器**；
  - **不进离线售卖包**（C-039：离线包不含在线拉取）；akshare 为可选依赖，
    未安装时给出明确指引，不静默降级（SEC-07 同源纪律）；
  - 频控：免费源 60 次/分（D-003）；本模块逐标的串行拉取并 sleep 限频；
  - **数据落 data/kline_real/，与合成样例池物理隔离**，随目录强制写授权说明：
    真实行情版权归原数据商（东方财富，经 akshare 接口），仅限个人研究，
    **不得二次分发、不得进入任何售卖包**（附录D / S19 合规口径）。

akshare 接口：ak.stock_zh_a_hist(symbol, period="daily", start_date, end_date,
adjust="qfq")——列名兼容中英文两版返回。
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .engine import Bar

REAL_DIR_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "kline_real")

FETCH_INTERVAL_SECONDS = 1.1   # 60次/分 ≈ 1秒/次，留余量（D-003 频控）


def _ak():
    try:
        import akshare as ak  # noqa - 可选依赖
        return ak
    except ImportError as exc:
        raise SystemExit(
            "真实行情通道需要 akshare（可选依赖，不进离线包——C-039）。\n"
            "安装：python -m pip install akshare -i "
            "https://mirrors.aliyun.com/pypi/simple") from exc


def _pick(colmap: Dict[str, str], row: Dict[str, object], *keys: str):
    for k in keys:
        if k in colmap and colmap[k] in row:
            return row[colmap[k]]
    return None


def _df_to_bars(df, symbol: str) -> List[Bar]:
    cols = {c.lower(): c for c in df.columns}
    bars: List[Bar] = []
    for _, row in df.iterrows():
        d = _pick(cols, row, "日期", "date")
        o = _pick(cols, row, "开盘", "open")
        h = _pick(cols, row, "最高", "high")
        l = _pick(cols, row, "最低", "low")
        c = _pick(cols, row, "收盘", "close")
        v = _pick(cols, row, "成交量", "volume")
        if None in (d, o, h, l, c):
            continue
        bars.append(Bar(str(d), float(o), float(h), float(l), float(c), float(v)))
    bars.sort(key=lambda b: b.date)
    return bars


def fetch_one(symbol: str, start: str, end: str, source: str = "auto") -> List[Bar]:
    """拉取单标的日线（前复权 qfq）。来源自动降级链：东方财富 → 新浪财经
    （D-003 多免费源适配；东财 push2 限流时切新浪，两源口径差异记入对账）。"""
    ak = _ak()
    df = None
    last_err: Optional[Exception] = None
    if source in ("auto", "east"):
        try:
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                    start_date=start.replace("-", ""),
                                    end_date=end.replace("-", ""), adjust="qfq")
        except Exception as exc:  # noqa - 限流/接口变更 → 降级
            last_err = exc
            df = None
    if df is None or getattr(df, "empty", True):
        if source in ("auto", "sina"):
            prefix = "sh" if symbol.startswith(("6", "9")) else \
                ("bj" if symbol.startswith(("4", "8")) else "sz")
            df = ak.stock_zh_a_daily(symbol=f"{prefix}{symbol}",
                                     start_date=start.replace("-", ""),
                                     end_date=end.replace("-", ""), adjust="qfq")
        elif last_err is not None:
            raise last_err
    if df is None or df.empty:
        raise SystemExit(f"{symbol} 未返回数据（检查代码与区间；注意已退市代码无效）")
    bars = _df_to_bars(df, symbol)
    if not bars:
        raise SystemExit(f"{symbol} 返回数据但无法解析出任何有效K线")
    return bars


def write_real_csv(bars: List[Bar], symbol: str, out_dir: str) -> Dict[str, int]:
    """按标的+年分区写 CSV（D-002 分区键，D-008 载体口径），返回分区计数。"""
    os.makedirs(out_dir, exist_ok=True)
    by_year: Dict[str, List[Bar]] = {}
    for b in bars:
        by_year.setdefault(b.date[:4], []).append(b)
    counts = {}
    for y, rows in sorted(by_year.items()):
        path = os.path.join(out_dir, f"{symbol}_{y}.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("date,open,high,low,close,volume\n")
            for b in rows:
                f.write(f"{b.date},{b.open},{b.high},{b.low},{b.close},{b.volume}\n")
        counts[f"{symbol}_{y}"] = len(rows)
    return counts


def write_license_note(out_dir: str, symbols: List[str]):
    """真实行情授权说明（强制随目录：不得二次分发/不得入售卖包）。"""
    os.makedirs(out_dir, exist_ok=True)
    text = (
        "# 真实行情数据来源与授权说明\n\n"
        f"- 覆盖标的：{', '.join(symbols)}\n"
        "- 来源：**东方财富或新浪财经历史行情（经 akshare 免费接口，前复权 qfq，"
        "限流时自动切源）**；版权归原数据商所有；\n"
        "- 授权边界：**仅限个人研究使用，不得二次分发原始数据，不得进入任何"
        "售卖包/离线交付包**（工程附录D 与 S19 商品合规口径——本目录已被"
        "排除在 monetize 打包之外）；\n"
        "- 频控：拉取间隔 ≥1.1 秒（D-003 免费源 60 次/分）；\n"
        "- 声明：历史行情不代表未来表现；本平台不构成投资建议。\n"
    )
    with open(os.path.join(out_dir, "README_数据来源与授权.md"), "w",
              encoding="utf-8") as f:
        f.write(text)


def fetch_real(symbols: List[str], years: int,
               out_dir: str = REAL_DIR_DEFAULT) -> Tuple[Dict[str, int], str]:
    """多标的串行拉取（限频）→ 落 CSV → 写授权说明。返回 (分区计数, 数据目录)。"""
    end = date.today()
    start = end - timedelta(days=int(years * 365.25) + 10)
    counts: Dict[str, int] = {}
    for i, sym in enumerate(symbols):
        bars = fetch_one(sym, start.isoformat(), end.isoformat())
        counts.update(write_real_csv(bars, sym, out_dir))
        if i < len(symbols) - 1:
            time.sleep(FETCH_INTERVAL_SECONDS)      # D-003 频控
    write_license_note(out_dir, symbols)
    return counts, out_dir
