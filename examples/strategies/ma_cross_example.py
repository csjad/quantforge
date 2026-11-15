# -*- coding: utf-8 -*-
"""示例策略：双均线（EMA 金叉/死叉）——沙箱兼容写法。

插件契约（S05-01 §2）：文件必须定义 `Strategy` 类，实现 attach / on_bar。
索引纪律：全局日历索引 t 必须经 `_li` 映射到标的局部索引（停牌日缺行会错位）。
"""
import math


class Strategy:
    name = "ma_cross_example"
    warmup_windows = [20]

    def __init__(self, fast=5, slow=20):
        self.fast, self.slow = fast, slow

    def attach(self, engine):
        self.engine = engine
        self.local = {s: {b.date: i for i, b in enumerate(seq)}
                      for s, seq in engine.bars.items()}
        self.ema_f, self.ema_s = {}, {}
        for s, seq in engine.bars.items():
            self.ema_f[s] = self._ema([b.close for b in seq], self.fast)
            self.ema_s[s] = self._ema([b.close for b in seq], self.slow)

    @staticmethod
    def _ema(closes, n):
        a = 2.0 / (n + 1)
        out, prev = [None] * len(closes), None
        for t, c in enumerate(closes):
            if prev is None:
                if t + 1 >= n:
                    prev = sum(closes[:n]) / n
                    out[t] = prev
            else:
                prev = prev + a * (c - prev)
                out[t] = prev
        return out

    def _li(self, s, t):
        if t >= len(self.engine.calendar):
            return None
        return self.local[s].get(self.engine.calendar[t])

    def on_bar(self, t):
        sigs = []
        for s in self.engine.bars:
            li = self._li(s, t)
            if li is None or li < 1:
                continue
            f, sl = self.ema_f[s], self.ema_s[s]
            if None in (f[li], sl[li], f[li - 1], sl[li - 1]):
                continue
            if f[li - 1] <= sl[li - 1] and f[li] > sl[li]:
                sigs.append(("BUY", s, self.engine.equity_now() * 0.30))
            elif f[li - 1] >= sl[li - 1] and f[li] < sl[li]                     and self.engine._pos(s).held:
                sigs.append(("SELL", s, -1))
        return sigs
