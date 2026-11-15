# -*- coding: utf-8 -*-
"""qf.indicators — 九指标精确口径实现（S04-02 冻结口径）

口径要点（每条都来自蓝图冻结结论，改动须走 C-XXX）：
  - MA：窗口含当日；前 N-1 根空值；**缺失日中断窗口并标记，不跳过计数**（TC-IND-002）
  - EMA：**初值取前 N 根简单均值，不得取首根收盘价**（TC-IND-005）；α=2/(N+1)
  - 标准差：**总体口径**（÷N 而非 N−1）（TC-IND-010）
  - ATR：首根无前收 TR=H−L；Wilder 平滑系数 1/N（TC-IND-013/014）
  - RSI：Wilder 平滑（不得用简单均值）；全同值 U=D=0 → 空值（TC-IND-020）
  - BOLL：总体标准差；σ=0 → 带宽 0，上下轨=中轨，相对位置输出空值（TC-IND-023）
  - 量比：比值化（无量纲）；过去均值为 0 → 空值（TC-IND-028/029）
  - KDJ：K、D 初值 50；H9=L9 → RSV 未定义 → 空值（TC-IND-034/035）
  - CCI：MD=0 → 空值（TC-IND-041）
  - OBV：初值 0，涨加跌减平不变（TC-IND-043/044）

所有函数：输入序列可含 None（停牌/缺口），None 传播 → 输出 None（缺口中断）。
输出序列长度=输入长度，未达窗口处为 None。
"""
from __future__ import annotations

import math
from typing import List, Optional

Series = List[Optional[float]]


def _win(vals: Series, t: int, n: int) -> Optional[List[float]]:
    """取窗口 [t-n+1, t]；窗口未满或含 None（缺口中断）→ None。"""
    if t + 1 < n:
        return None
    w = vals[t - n + 1: t + 1]
    if any(v is None for v in w):
        return None
    return w


# ---------------------------------------------------------------- 均线族

def sma(closes: Series, n: int) -> Series:
    """MA(N)：简单均线，窗口含当日。"""
    out: Series = []
    for t in range(len(closes)):
        w = _win(closes, t, n)
        out.append(sum(w) / n if w is not None else None)
    return out


def ema(closes: Series, n: int) -> Series:
    """EMA(N)：初值=前 N 根简单均值（TC-IND-005 错误初值对照），α=2/(N+1)。"""
    alpha = 2.0 / (n + 1)
    out: Series = [None] * len(closes)
    prev: Optional[float] = None
    for t in range(len(closes)):
        c = closes[t]
        if prev is None:
            w = _win(closes, t, n)
            if w is None:
                continue
            prev = sum(w) / n
            out[t] = prev
        else:
            if c is None:  # 缺口：EMA 冻结至数据恢复（不跳根）
                out[t] = None
                continue
            prev = prev + alpha * (c - prev)
            out[t] = prev
    return out


def wma(closes: Series, n: int) -> Series:
    """WMA(N)：权重线性，最旧权重=n 递减至最新=1（冻结教科书口径：
    [10,11,12] N=3 → (10×3+11×2+12×1)/6=64/6，TC-IND-007）。
    ⚠️ 口径披露：主流行情软件的 WMA 方向相反（最新=最高权重）——与第三方
    对账时此项须列入口径表（S09 对账四拆法的口径差异项）。"""
    out: Series = []
    for t in range(len(closes)):
        w = _win(closes, t, n)
        if w is None:
            out.append(None)
        else:
            out.append(sum((n - i) * v for i, v in enumerate(w)) / (n * (n + 1) / 2.0))
    return out


# ---------------------------------------------------------------- 波动族

def stddev(vals: Series, n: int) -> Series:
    """总体标准差（÷N，TC-IND-010）；全同值 → 0（合法值，BOLL 层再判）。"""
    out: Series = []
    for t in range(len(vals)):
        w = _win(vals, t, n)
        if w is None:
            out.append(None)
        else:
            m = sum(w) / n
            out.append(math.sqrt(sum((v - m) ** 2 for v in w) / n))
    return out


def true_range(highs: Series, lows: Series, closes: Series) -> Series:
    """TR：首根无前收 → TR=H−L 并标记（TC-IND-013）。"""
    out: Series = []
    for t in range(len(closes)):
        h, l, c = highs[t], lows[t], closes[t]
        if h is None or l is None or c is None:
            out.append(None)
            continue
        if t == 0 or closes[t - 1] is None:
            out.append(h - l)
        else:
            pc = closes[t - 1]
            out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def wilder(vals: Series, n: int) -> Series:
    """Wilder 平滑（系数 1/N）：第 n 根起，初值=前 n 根简单均值。"""
    out: Series = [None] * len(vals)
    prev: Optional[float] = None
    for t in range(len(vals)):
        v = vals[t]
        if prev is None:
            w = _win(vals, t, n)
            if w is None:
                continue
            prev = sum(w) / n
            out[t] = prev
        else:
            if v is None:
                out[t] = None
                continue
            prev = prev + (v - prev) / n
            out[t] = prev
    return out


def atr(highs: Series, lows: Series, closes: Series, n: int = 14) -> Series:
    """ATR(N)：Wilder 平滑 TR（S04 冻结 N=14 用默认）。"""
    return wilder(true_range(highs, lows, closes), n)


# ---------------------------------------------------------------- 动量族

def roc(closes: Series, n: int = 20) -> Series:
    """ROC(N)=(C(t)/C(t−N)−1)×100；C(t−N)=0 → 空值（TC-IND-017）。"""
    out: Series = []
    for t in range(len(closes)):
        if t < n or closes[t] is None or closes[t - n] in (None, 0):
            out.append(None)
        else:
            out.append((closes[t] / closes[t - n] - 1.0) * 100.0)
    return out


def rsi(closes: Series, n: int = 14) -> Series:
    """RSI(N)：Wilder 平滑；全同值 → 空值（TC-IND-020）；连续上涨 → 100（TC-IND-019）。"""
    out: Series = [None] * len(closes)
    up: Optional[float] = None
    dn: Optional[float] = None
    for t in range(len(closes)):
        if t == 0 or closes[t] is None or closes[t - 1] is None:
            continue
        chg = closes[t] - closes[t - 1]
        gain, loss = (chg, 0.0) if chg >= 0 else (0.0, -chg)
        if up is None:
            if t < n:
                continue
            # 初值=前 n 个涨/跌幅的简单均值（Wilder 入场口径）；种子窗口含缺口 → 不播种
            gains, losses = [], []
            ok = True
            for k in range(t - n + 1, t + 1):
                if closes[k] is None or closes[k - 1] is None:
                    ok = False
                    break
                d = closes[k] - closes[k - 1]
                gains.append(max(d, 0.0))
                losses.append(max(-d, 0.0))
            if not ok:
                continue
            up = sum(gains) / n
            dn = sum(losses) / n
        else:
            up = (up * (n - 1) + gain) / n
            dn = (dn * (n - 1) + loss) / n
        if up == 0.0 and dn == 0.0:
            out[t] = None  # 全同值：RS 未定义（防"分母写成 U−D"类半对公式）
        elif dn == 0.0:
            out[t] = 100.0
        else:
            rs = up / dn
            out[t] = 100.0 - 100.0 / (1.0 + rs)
    return out


# ---------------------------------------------------------------- 通道族

def boll(closes: Series, n: int = 20, k: float = 2.0):
    """BOLL(N,K)：中轨=MA(N)（含当日），总体 σ；返回 (mid, upper, lower, bandwidth)。
    σ=0 → 带宽 0、上下轨=中轨（TC-IND-023），信号层的"相对带宽位置"须输出空值。"""
    mid = sma(closes, n)
    sd = stddev(closes, n)
    upper: Series = []
    lower: Series = []
    bw: Series = []
    for t in range(len(closes)):
        m, s = mid[t], sd[t]
        if m is None or s is None:
            upper.append(None); lower.append(None); bw.append(None)
        else:
            upper.append(m + k * s)
            lower.append(m - k * s)
            bw.append(0.0 if s == 0.0 else (2.0 * k * s) / m)
    return mid, upper, lower, bw


# ---------------------------------------------------------------- 量能族

def vol_ma(vols: Series, n: int) -> Series:
    """成交量均线；量为 0 是合法值，不视为空（TC-IND-026）。"""
    out: Series = []
    for t in range(len(vols)):
        w = _win(vols, t, n)
        out.append(sum(w) / n if w is not None else None)
    return out


def volume_ratio(vols: Series, n: int = 5) -> Series:
    """量比=当前量能均值 ÷ 过去一段均值（不含当根）；>1.5 放量、<0.7 缩量。
    过去均值为 0 → 空值（TC-IND-029）；量整体乘常数，量比不变（TC-IND-030）。"""
    ma_cur = vol_ma(vols, n)
    out: Series = []
    for t in range(len(vols)):
        if ma_cur[t] is None or t < 2 * n - 1:
            out.append(None)
            continue
        past = vols[t - n: t]
        if any(v is None for v in past):
            out.append(None)
            continue
        pm = sum(past) / n
        out.append(None if pm == 0 else ma_cur[t] / pm)
    return out


# ---------------------------------------------------------------- MACD / KDJ / ADX / CCI / OBV

def macd(closes: Series, fast: int = 12, slow: int = 26, sig: int = 9):
    """MACD：DIF=EMA12−EMA26；DEA=EMA9(DIF)；柱=2×(DIF−DEA)。EMA 初值=前 N 根简单均值。"""
    ef = ema(closes, fast)
    es = ema(closes, slow)
    dif: Series = [None if (a is None or b is None) else a - b for a, b in zip(ef, es)]
    dea = ema(dif, sig)
    hist: Series = [None if (d is None or e is None) else 2.0 * (d - e) for d, e in zip(dif, dea)]
    return dif, dea, hist


def kdj(highs: Series, lows: Series, closes: Series, n: int = 9, smooth: int = 3):
    """KDJ：RSV=(C−L9)/(H9−L9)×100；K=2/3·K前+1/3·RSV；D=2/3·D前+1/3·K；J=3K−2D；K、D 初值 50。"""
    k_out: Series = [None] * len(closes)
    d_out: Series = [None] * len(closes)
    j_out: Series = [None] * len(closes)
    k_prev, d_prev = 50.0, 50.0
    started = False
    for t in range(len(closes)):
        h, l, c = highs[t], lows[t], closes[t]
        if t + 1 < n or h is None or l is None or c is None:
            continue
        w_h = highs[t - n + 1: t + 1]
        w_l = lows[t - n + 1: t + 1]
        if any(v is None for v in w_h + w_l):
            continue
        hh, ll = max(w_h), min(w_l)
        if hh == ll:
            k_out[t] = None  # H9=L9：RSV 未定义 → 空值（TC-IND-035）；K/D 递推状态保持不变
            d_out[t] = None
            j_out[t] = None
            started = True
            continue
        rsv = (c - ll) / (hh - ll) * 100.0
        k_prev = (2.0 / 3.0) * k_prev + (1.0 / 3.0) * rsv
        d_prev = (2.0 / 3.0) * d_prev + (1.0 / 3.0) * k_prev
        started = True
        k_out[t], d_out[t], j_out[t] = k_prev, d_prev, 3.0 * k_prev - 2.0 * d_prev
    return k_out, d_out, j_out


def adx(highs: Series, lows: Series, closes: Series, n: int = 14):
    """ADX(N)：+DM/−DM/TR → Wilder 平滑 → +DI/−DI → DX → ADX=Wilder(DX, N)。"""
    plus_dm: Series = [None] * len(highs)
    minus_dm: Series = [None] * len(highs)
    tr: Series = [None] * len(highs)
    for t in range(1, len(highs)):
        h, l, hp, lp, cp = highs[t], lows[t], highs[t - 1], lows[t - 1], closes[t - 1]
        if None in (h, l, hp, lp, cp):
            continue
        up = h - hp
        down = lp - l
        plus_dm[t] = up if (up > down and up > 0) else 0.0
        minus_dm[t] = down if (down > up and down > 0) else 0.0
        tr[t] = max(h - l, abs(h - cp), abs(l - cp))
    sm_p = wilder(plus_dm, n)
    sm_m = wilder(minus_dm, n)
    sm_tr = wilder(tr, n)
    dx: Series = [None] * len(highs)
    for t in range(len(highs)):
        if None in (sm_p[t], sm_m[t], sm_tr[t]) or sm_tr[t] == 0:
            continue
        pdi = 100.0 * sm_p[t] / sm_tr[t]
        mdi = 100.0 * sm_m[t] / sm_tr[t]
        if pdi + mdi == 0:
            continue
        dx[t] = 100.0 * abs(pdi - mdi) / (pdi + mdi)
    adx_v = wilder(dx, n)
    return adx_v, dx


def cci(highs: Series, lows: Series, closes: Series, n: int = 14) -> Series:
    """CCI(N)：TP=(H+L+C)/3；CCI=(TP−MA(TP,N))/(0.015×MD)；MD=平均绝对偏差；MD=0 → 空值。"""
    tp: Series = [
        None if None in (h, l, c) else (h + l + c) / 3.0
        for h, l, c in zip(highs, lows, closes)
    ]
    out: Series = []
    for t in range(len(tp)):
        w = _win(tp, t, n)
        if w is None:
            out.append(None)
            continue
        m = sum(w) / n
        md = sum(abs(v - m) for v in w) / n
        out.append(None if md == 0 else (tp[t] - m) / (0.015 * md))
    return out


def obv(closes: Series, vols: Series) -> Series:
    """OBV：涨累加成交量、跌累减、平不变；初值 0。"""
    out: Series = []
    acc = 0.0
    for t in range(len(closes)):
        c, v = closes[t], vols[t]
        if c is None or v is None:
            out.append(None if t > 0 else 0.0)
            continue
        if t == 0:
            out.append(0.0)
            continue
        pc = closes[t - 1]
        if pc is None:
            out.append(acc)
            continue
        if c > pc:
            acc += v
        elif c < pc:
            acc -= v
        out.append(acc)
    return out


# ---------------------------------------------------------------- 预热窗口（S03-01 §3）

def warmup_of(specs) -> int:
    """预热窗口=各指标回看期最大值（把预热当常数 50 是本项目冻结的早期最严重错误）。"""
    return max(specs) if specs else 0
