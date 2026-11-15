# -*- coding: utf-8 -*-
"""qf.strategies — 五个模板策略（S05-02 冻结口径，回归测试基线）

五模板的意义不是收益，而是把引擎的五种典型调用路径各走一遍（S05-02）：
  模板一 双均线趋势  —— 单标的时序指标路径（EMA 金叉/死叉）
  模板二 布林带增强  —— 通道指标 + 趋势过滤 + 止损（复合路径）
  模板三 RSI 反转    —— 动量指标 + 区间判断（Wilder 平滑，S04 口径）
  模板四 ATR 突破    —— 通道突破 + 波动指标（前 N 日极值不含当根，因果性约束）
  模板五 多因子打分  —— 横截面因子 + 组合持仓（V2 组合调仓预演路径）

共性约束（S05-02，全部模板必须遵守）：
  1. 仓位上限声明：买入目标金额 = 权益 × cap（默认 30%，实际成交由引擎风控裁剪）；
  2. 信号时序：on_bar(t) 只允许读索引 ≤ t 的数据（未来函数三重检测对应）；
  3. 去重防抖：持仓期同标的同方向不重复触发（引擎层拦截并计数）；
  4. 可溯源：订单带 created_bar（引擎落盘）。
信号格式：("BUY", symbol, target_amount) / ("SELL", symbol, qty_or_-1)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import indicators as ind
from .engine import Bar

CAP_DEFAULT = 0.30  # 单标的仓位上限默认 30%（冻结基准；实际成交由引擎第八步裁剪）


class StrategyBase:
    """模板基类：attach 时预计算因果指标数组（只读索引 ≤ 局部 t）。

    索引纪律（W2 实现期修正，doctor 全池运行暴露）：
    全局日历索引 t ≠ 标的局部序列索引（停牌日缺行会错位）——直接用 t 访问
    标的序列=隐性前视/漏看。所有指标访问必须经 `_li(s, t)` 映射到局部索引；
    映射为 None（当日停牌）→ 不产生信号。"""
    name = "base"
    warmup_windows: List[int] = []
    cap = CAP_DEFAULT

    def attach(self, engine):
        self.engine = engine
        self.state: Dict[str, dict] = {}
        self.local = {s: {b.date: i for i, b in enumerate(seq)}
                      for s, seq in engine.bars.items()}
        self._precompute()

    def _li(self, s: str, t: int) -> Optional[int]:
        """日历索引 t → 标的 s 的局部索引；当日停牌 → None。"""
        if t >= len(self.engine.calendar):
            return None
        return self.local[s].get(self.engine.calendar[t])

    def _precompute(self):
        """子类覆写：对每个标的预计算指标序列（因果：索引 li 只依赖 ≤li 数据）。"""
        raise NotImplementedError

    def on_bar(self, t: int) -> List[tuple]:
        raise NotImplementedError

    # ---- 辅助 ----
    def _bar(self, symbol: str, t: int) -> Optional[Bar]:
        li = self._li(symbol, t)
        seq = self.engine.bars.get(symbol)
        return seq[li] if (seq and li is not None and li < len(seq)) else None

    def _held(self, symbol: str) -> bool:
        return self.engine._pos(symbol).held > 0


def _rolling_max_prior(vals, n: int, t: int):
    """前 N 日极值（不含当根）：窗口 [t−n, t−1]；不足或含 None → None。"""
    if t < n:
        return None
    w = vals[t - n: t]
    if any(v is None for v in w):
        return None
    return max(w)


def _rolling_min_prior(vals, n: int, t: int):
    if t < n:
        return None
    w = vals[t - n: t]
    if any(v is None for v in w):
        return None
    return min(w)


# ---------------------------------------------------------- 模板一：双均线趋势
class MaCross(StrategyBase):
    name = "ma_cross"
    warmup_windows = [20]

    def __init__(self, fast: int = 5, slow: int = 20, cap: float = CAP_DEFAULT):
        assert fast < slow, "fast_window 必须 < slow_window"
        self.fast, self.slow, self.cap = fast, slow, cap
        self.warmup_windows = [slow + 1]

    def _precompute(self):
        self.ema_f, self.ema_s = {}, {}
        for s, seq in self.engine.bars.items():
            closes = [b.close for b in seq]
            self.ema_f[s] = ind.ema(closes, self.fast)
            self.ema_s[s] = ind.ema(closes, self.slow)

    def on_bar(self, t: int) -> List[tuple]:
        sigs = []
        for s in self.engine.bars:
            li = self._li(s, t)
            if li is None or li < 1:
                continue
            f, sl = self.ema_f[s], self.ema_s[s]
            if f[li] is None or sl[li] is None or f[li - 1] is None or sl[li - 1] is None:
                continue
            gold = f[li - 1] <= sl[li - 1] and f[li] > sl[li]   # 金叉
            dead = f[li - 1] >= sl[li - 1] and f[li] < sl[li]   # 死叉
            if gold:
                sigs.append(("BUY", s, self.engine.equity_now() * self.cap))
            elif dead and self._held(s):
                sigs.append(("SELL", s, -1))                     # 死叉清仓
        return sigs


# ---------------------------------------------------------- 模板二：布林带增强
class BollTrend(StrategyBase):
    name = "boll_trend"
    warmup_windows = [60 + 1]

    def __init__(self, window: int = 20, k_multiplier: float = 2.0,
                 trend_window: int = 60, stop_loss: float = 0.05,
                 cap: float = CAP_DEFAULT):
        self.window, self.k = window, k_multiplier
        self.trend_window, self.stop_loss, self.cap = trend_window, stop_loss, cap
        self.warmup_windows = [max(window, trend_window) + 1]

    def _precompute(self):
        self.mid, self.upper, self.lower, self.bw = {}, {}, {}, {}
        self.trend = {}
        for s, seq in self.engine.bars.items():
            closes = [b.close for b in seq]
            self.mid[s], self.upper[s], self.lower[s], self.bw[s] = \
                ind.boll(closes, self.window, self.k)
            self.trend[s] = ind.sma(closes, self.trend_window)

    def on_bar(self, t: int) -> List[tuple]:
        sigs = []
        for s in self.engine.bars:
            li = self._li(s, t)
            if li is None or li < 1:
                continue
            mid, tr = self.mid[s], self.trend[s]
            if mid[li] is None or tr[li] is None or tr[li - 1] is None:
                continue
            seq = self.engine.bars[s]
            bar = seq[li]
            close = bar.close
            trend_up = tr[li] > tr[li - 1]                  # 趋势过滤：长均线方向向上
            prev_close = seq[li - 1].close if li >= 1 else None
            breakout = close > mid[li] and (li == 0 or prev_close is None
                                            or prev_close <= mid[li - 1])
            if breakout and trend_up:
                sigs.append(("BUY", s, self.engine.equity_now() * self.cap))
            elif self._held(s):
                p = self.engine._pos(s)
                stop_hit = close <= p.avg_cost * (1 - self.stop_loss)
                if close < mid[li] or stop_hit:
                    sigs.append(("SELL", s, -1))
        return sigs


# ---------------------------------------------------------- 模板三：RSI 反转
class RsiReversal(StrategyBase):
    name = "rsi_reversal"
    warmup_windows = [14 + 2]

    def __init__(self, rsi_window: int = 14, oversold: float = 30.0,
                 overbought: float = 70.0, cap: float = CAP_DEFAULT):
        self.n, self.os, self.ob, self.cap = rsi_window, oversold, overbought, cap
        self.warmup_windows = [rsi_window + 2]

    def _precompute(self):
        self.rsi = {}
        for s, seq in self.engine.bars.items():
            self.rsi[s] = ind.rsi([b.close for b in seq], self.n)

    def on_bar(self, t: int) -> List[tuple]:
        sigs = []
        for s in self.engine.bars:
            li = self._li(s, t)
            if li is None or li < 1:
                continue
            r = self.rsi[s]
            if r[li] is None or r[li - 1] is None:
                continue
            rev_up = r[li - 1] < self.os and r[li] >= self.os   # 从超卖区回升
            rev_dn = r[li - 1] > self.ob and r[li] <= self.ob   # 从超买区回落
            if rev_up:
                sigs.append(("BUY", s, self.engine.equity_now() * self.cap))
            elif rev_dn and self._held(s):
                sigs.append(("SELL", s, -1))
        return sigs


# ---------------------------------------------------------- 模板四：ATR 突破
class AtrBreakout(StrategyBase):
    name = "atr_breakout"
    warmup_windows = [20 + 14 + 1]

    def __init__(self, breakout_window: int = 20, k_atr: float = 2.0,
                 atr_window: int = 14, cap: float = CAP_DEFAULT):
        self.n, self.k, self.an, self.cap = breakout_window, k_atr, atr_window, cap
        self.warmup_windows = [max(breakout_window, atr_window) + 2]

    def _precompute(self):
        self.atr = {}
        for s, seq in self.engine.bars.items():
            self.atr[s] = ind.atr([b.high for b in seq], [b.low for b in seq],
                                  [b.close for b in seq], self.an)

    def on_bar(self, t: int) -> List[tuple]:
        sigs = []
        for s in self.engine.bars:
            li = self._li(s, t)
            if li is None or li < 1:
                continue
            seq = self.engine.bars[s]
            a = self.atr[s]
            if a[li] is None:
                continue
            bar = seq[li]
            hh = _rolling_max_prior([b.high for b in seq], self.n, li)
            ll = _rolling_min_prior([b.low for b in seq], self.n, li)
            if hh is None or ll is None:
                continue
            if bar.close > hh + self.k * a[li]:
                sigs.append(("BUY", s, self.engine.equity_now() * self.cap))
            elif bar.close < ll - self.k * a[li] and self._held(s):
                sigs.append(("SELL", s, -1))
        return sigs


# ---------------------------------------------------------- 模板五：多因子打分选股
class MultiFactorTopN(StrategyBase):
    name = "multi_factor_topn"
    warmup_windows = [20 + 10 + 1]

    def __init__(self, momentum_window: int = 20, vol_window: int = 5,
                 top_n: int = 10, rebalance_freq: str = "weekly",
                 hold_weight: float = 0.10):
        self.mw, self.vw = momentum_window, vol_window
        self.top_n, self.freq, self.weight = top_n, rebalance_freq, hold_weight
        self.warmup_windows = [max(momentum_window, 2 * vol_window) + 2]

    def _precompute(self):
        self.mom, self.vr = {}, {}
        for s, seq in self.engine.bars.items():
            closes = [b.close for b in seq]
            vols = [b.volume for b in seq]
            self.mom[s] = ind.roc(closes, self.mw)          # 动量因子
            self.vr[s] = ind.volume_ratio(vols, self.vw)    # 量价因子（比值化）

    def _score(self, s: str, t: int) -> Optional[float]:
        m, v = self.mom[s][t], self.vr[s][t]
        if m is None or v is None:
            return None
        # 打分 = 动量 + 放量加成（量比>1.5 加 1 分，缩量<0.7 减 0.5 分）
        bonus = 1.0 if v > 1.5 else (-0.5 if v < 0.7 else 0.0)
        return m + bonus

    def _rebalance_day(self, t: int) -> bool:
        if self.freq == "daily":
            return True
        if self.freq == "weekly":
            return t % 5 == 0
        return t % 20 == 0                                   # monthly

    def on_bar(self, t: int) -> List[tuple]:
        if not self._rebalance_day(t):
            return []
        scored = []
        for s in self.engine.bars:
            li = self._li(s, t)
            if li is None:
                continue                       # 停牌日不参与打分
            sc = self._score(s, li)
            if sc is not None:
                scored.append((sc, s))
        if not scored:
            return []
        scored.sort(reverse=True)
        top = [s for _, s in scored[:max(1, min(self.top_n, len(scored)))]]
        sigs = []
        for s in self.engine.bars:
            if self._held(s) and s not in top:
                sigs.append(("SELL", s, -1))                 # 跌出前 N 名清仓
        n_new = sum(1 for s in top if not self._held(s))
        if n_new == 0:
            return sigs
        eq = self.engine.equity_now()
        weight = min(self.weight, CAP_DEFAULT)               # 受 30% 仓位上限约束
        for s in top:
            if not self._held(s):
                sigs.append(("BUY", s, eq * weight))
        return sigs


STRATEGY_REGISTRY = {
    MaCross.name: MaCross,
    BollTrend.name: BollTrend,
    RsiReversal.name: RsiReversal,
    AtrBreakout.name: AtrBreakout,
    MultiFactorTopN.name: MultiFactorTopN,
}
