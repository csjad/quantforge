# -*- coding: utf-8 -*-
"""示例策略：RSI 超卖回升买入 / 超买回落卖出（Wilder 平滑，S04 口径）。"""


class Strategy:
    name = "rsi_example"
    warmup_windows = [16]

    def __init__(self, window=14, oversold=30.0, overbought=70.0):
        self.n, self.os, self.ob = window, oversold, overbought

    def attach(self, engine):
        self.engine = engine
        self.local = {s: {b.date: i for i, b in enumerate(seq)}
                      for s, seq in engine.bars.items()}
        self.rsi = {}
        for s, seq in engine.bars.items():
            self.rsi[s] = self._rsi([b.close for b in seq], self.n)

    @staticmethod
    def _rsi(closes, n):
        out = [None] * len(closes)
        up = dn = None
        for t in range(1, len(closes)):
            chg = closes[t] - closes[t - 1]
            g, l = (chg, 0.0) if chg >= 0 else (0.0, -chg)
            if up is None:
                if t < n:
                    continue
                gains = [max(closes[k] - closes[k - 1], 0.0) for k in range(t - n + 1, t + 1)]
                losses = [max(closes[k - 1] - closes[k], 0.0) for k in range(t - n + 1, t + 1)]
                up, dn = sum(gains) / n, sum(losses) / n
            else:
                up = (up * (n - 1) + g) / n
                dn = (dn * (n - 1) + l) / n
            out[t] = None if (up == 0 and dn == 0) else                      (100.0 if dn == 0 else 100.0 - 100.0 / (1.0 + up / dn))
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
            r = self.rsi[s]
            if None in (r[li], r[li - 1]):
                continue
            if r[li - 1] < self.os <= r[li]:
                sigs.append(("BUY", s, self.engine.equity_now() * 0.30))
            elif r[li - 1] > self.ob >= r[li] and self.engine._pos(s).held:
                sigs.append(("SELL", s, -1))
        return sigs
