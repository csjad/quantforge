# -*- coding: utf-8 -*-
"""qf.extreme — 六类极端行情数据集生成器（S16-02 表29，A3 条件项：附生成脚本、
禁止手工构造——本模块即生成脚本，固定种子，纳入版本管理）。

场景与断言（S16-02 表29 / §3 走查）：
  1 连续涨停  十日一字板        → 买入成交为零（若非零，报出前三笔成交的日期与价格）
  2 连续跌停  十日一字板        → 卖出**不成交**（C-042 定案：按 §3.2 走查，不允许
                                  按跌停价强行成交；表29 该格与走查矛盾，已登记消解）
  3 单日腰斩  开盘暴跌          → 止损触发且滑点记录（合法口径：创业板 −19.9% 崩盘日，
                                  ±10%/20% 涨跌停制度下 −50% 开盘只能来自除权，归数据层）
  4 长期停牌  九十日无量        → 持仓不变、估值沿用最后有效价（停牌期间权益恒定）
  5 除权跳空  因子突变          → 复权后收益连续（引擎消费复权序列，无人工回撤）
  6 量能骤降  成交额降九成      → 流动性过滤生效（MIN_VOLUME_LIMIT 开关拦截建仓）

断言纪律（S16-02 §2）：**必须带数值边界与失败信息**；六场景判定为"零或非零"离散
判定，容忍度为零。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Tuple

from .engine import Bar


def _dates(n: int, start: date = date(2022, 1, 4)) -> List[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:            # 工作日历（合成口径）
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bar(d, o, h, l, c, v=100000.0) -> Bar:
    return Bar(d, o, max(h, o, c), min(l, o, c), c, v)


# ------------------------------------------------ ① 连续涨停：十日一字板
def scenario_limit_up_run(days: int = 12) -> Tuple[Dict[str, List[Bar]], List[str]]:
    """前2日正常（供信号产生），后10日一字涨停（主板±10%，前收连乘）。"""
    d = _dates(days)
    seq, price = [], 10.0
    for i, ds in enumerate(d):
        if i < 2:
            seq.append(_bar(ds, price, price + 0.1, price - 0.1, price))
        else:
            lu = round(price * 1.10, 2)
            seq.append(_bar(ds, lu, lu, lu, lu, v=30000.0))   # 一字板：O=H=L=C=涨停价
            price = lu
    return {"600001": seq}, d


# ------------------------------------------------ ② 连续跌停：十日一字板
def scenario_limit_down_run(days: int = 13) -> Tuple[Dict[str, List[Bar]], List[str]]:
    """第1日正常建仓，后10日一字跌停（前收连乘），末日正常（检验持仓未虚假离场）。"""
    d = _dates(days)
    seq, price = [], 10.0
    seq.append(_bar(d[0], 10.0, 10.1, 9.9, 10.0))
    price = 10.0
    for i in range(1, days - 1):
        ld = round(price * 0.90, 2)
        seq.append(_bar(d[i], ld, ld, ld, ld, v=30000.0))     # 一字跌停
        price = ld
    seq.append(_bar(d[-1], price * 1.01, price * 1.03, price * 0.99, price * 1.02))
    return {"600001": seq}, d


# ------------------------------------------------ ③ 单日腰斩：合法口径=创业板崩盘日
def scenario_crash_gem(days: int = 8, crash_pct: float = -0.199) \
        -> Tuple[Dict[str, List[Bar]], List[str]]:
    """创业板（±20%）：日增 0.4% 的上行序列中插入单日 −19.9% 崩盘（合法涨跌停内
    极限），触发止损。保留日增长——平盘序列会让均线/前视类策略无信号可产生。"""
    d = _dates(days)
    seq = []
    price = 20.0
    for i, ds in enumerate(d):
        if i == 4:
            o = round(price * (1 + crash_pct * 0.6), 2)       # 低开 −12%
            c = round(price * (1 + crash_pct), 2)             # 收 −19.9%（贴跌停）
            seq.append(_bar(ds, o, o * 1.02, c, c, v=180000.0))
            price = c
        else:
            price = round(price * (1 + 0.004), 2)
            seq.append(_bar(ds, price, price * 1.01, price * 0.99, price))
    return {"300003": seq}, d


# ------------------------------------------------ ④ 长期停牌：九十日无量
def scenario_suspension_90() -> Tuple[Dict[str, List[Bar]], List[str]]:
    """建仓后停牌 90 个交易日（整行缺失），复牌后正常。"""
    d = _dates(96)
    seq = [_bar(d[0], 10.0, 10.1, 9.9, 10.0)]
    seq.append(_bar(d[1], 10.0, 10.1, 9.9, 10.0))
    # d[2..91] 停牌：缺行
    seq.append(_bar(d[92], 10.0, 10.1, 9.9, 10.0))
    seq.append(_bar(d[93], 10.05, 10.15, 9.95, 10.10))
    for i in range(94, 96):
        seq.append(_bar(d[i], 10.1, 10.2, 10.0, 10.15))
    return {"600001": seq}, d


# ------------------------------------------------ ⑤ 除权跳空：因子突变
def scenario_exright_gap(days: int = 10, factor: float = 2.0) \
        -> Tuple[Dict[str, List[Bar]], Dict[str, List[Bar]], List[str]]:
    """返回 (原始含缺口序列, 前复权连续序列, 日历)。除权日名义价格÷factor（如 1拆2）。
    前复权口径：除权前的复权价=名义价÷因子 → 复权序列全程连续（无人工缺口）；
    原始序列在除权日出现 −50% 级跳空——该缺口属 S02 数据校验层职责。"""
    d = _dates(days)
    raw, adj = [], []
    nominal = 20.0
    split_day = days // 2
    for i, ds in enumerate(d):
        if i == split_day:
            nominal = round(nominal / factor, 2)        # 除权：名义价格减半
        elif i > 0:
            nominal = round(nominal * 1.004, 2)
        raw_p = nominal
        adj_p = round(nominal / factor, 2) if i < split_day else nominal
        raw.append(_bar(ds, raw_p, raw_p * 1.01, raw_p * 0.99, raw_p))
        adj.append(_bar(ds, adj_p, adj_p * 1.01, adj_p * 0.99, adj_p))
    return {"600001": raw}, {"600001": adj}, d


# ------------------------------------------------ ⑥ 量能骤降：成交额降九成
def scenario_volume_dry(days: int = 10, normal_vol: float = 200000.0) \
        -> Tuple[Dict[str, List[Bar]], List[str]]:
    """前5日正常量能，后5日骤降九成（供 MIN_VOLUME_LIMIT 过滤断言）。"""
    d = _dates(days)
    seq, price = [], 10.0
    for i, ds in enumerate(d):
        v = normal_vol if i < 5 else normal_vol * 0.1        # 降九成
        seq.append(_bar(ds, price, price * 1.01, price * 0.99, price, v=v))
    return {"600001": seq}, d


def failure_info(trades) -> str:
    """S16-02 §2 断言失败信息：输出前三笔成交的日期与价格。"""
    head = [(t.date, t.symbol, t.side, round(t.exec_price, 4), t.qty) for t in trades[:3]]
    return f"前三笔成交（日期/标的/方向/价格/数量）={head}，共{len(trades)}笔"
