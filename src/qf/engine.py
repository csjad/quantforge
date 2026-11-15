# -*- coding: utf-8 -*-
"""qf.engine — 回测引擎核心（S03-01 冻结语义的 W1 实现）

实现口径（改动须走 C-XXX）：
  - 九步时序（S03-01 §4）：撮合在信号之前——信号只能用截至本根收盘的数据，
    只能参与次日开盘撮合；**代码中不存在"当根收盘成交"路径**（S00-A3 禁令）。
  - T+1 解锁时点：按 S03-01 §12 数字走查第 9~10 步口径——**"次日开盘前解锁、
    次日卖出"**：D 日开盘买入 → D 日收盘可产生卖出委托（冻结）→ D+1 开盘撮合前
    解锁 → D+1 开盘成交。即"当日买入次日方可卖出"在次日开盘撮合口径下的唯一
    自洽实现。九步表将解锁列为第 6 步（撮合之后）与走查存在次序歧义，按铁律一
    （数值优先）以走查为准，此处显式登记。
  - 成交价：**统一按开盘价**，滑点方向买加卖减（C-026 消解定案）。
  - 涨跌停三情形（§5.2）：一字涨停封住→买单不成交作废、卖单按开盘（=涨停价）成交；
    一字跌停封住→反之；盘中打开正常撮合。开盘价越出涨跌停价=数据异常，
    交数据校验层（S02），不在引擎内消化。
  - 停牌（§5.3）：不撮合、保留持仓估值（最后收盘价）；委托不顺延超过 5 个
    交易日，超期作废并记录。
  - 成本模型（§6）：佣金 0.25‰ 双边最低 5 元；印花税 0.5‰ 仅卖出；过户费
    0.01‰ 仅沪市；滑点固定 1‰ 每边（D-004）。费用四项逐笔留痕、可对账。
  - 三条守恒式（§2.3）**逐笔校验**（含费成本口径的现金守恒等价形式：
    权益 == 初始资金 + 已实现盈亏(净) + 未实现盈亏(净)——买入费用内嵌于含费
    均价、卖出费用从收入中扣除，与蓝图"初始资金 = 权益 − 已实现 − 未实现 −
    全部成本"完全等价，且避免双重扣费）。
  - 四自检量（§11）全部落盘：成交笔数/拒单笔数/被拦截信号数/守恒式失败次数。
  - 资金约束（§5.2-2）：可用不足时按可用资金**反算最大可买整手数**，不直接
    拒单；不足一手 → 拒单并记录原因"资金不足一手"（§13，不允许融通/零股买入）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config as C
from .indicators import warmup_of

# ---------------------------------------------------------------- 数据结构

@dataclass
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Order:
    order_id: int
    symbol: str
    side: str                    # "BUY" / "SELL"
    target_amount: float = 0.0   # BUY：目标金额（执行日按开盘价反算整手数）
    quantity: int = 0            # SELL：冻结卖出股数
    status: str = "待撮合"        # 待撮合/已成交/已拒/已作废
    created_bar: int = 0         # 信号生成K线索引（可溯源 R-E-006）
    age: int = 0                 # 停牌顺延交易日计数
    reason: str = ""
    filled_price: float = 0.0
    filled_qty: int = 0
    fees: float = 0.0


@dataclass
class Position:
    """持仓层（S03-01 §2.1）。持仓守恒：可卖 + 冻结卖 + T+1待解锁 = 持仓总量。"""
    symbol: str
    qty: int = 0
    sellable: int = 0            # 可卖数量
    frozen_sell: int = 0         # 卖出委托冻结股数
    pending_t1: int = 0          # 当日买入、次日开盘前解锁的股数
    avg_cost: float = 0.0        # 买入含费均价

    @property
    def held(self) -> int:
        """持仓总量 = 可卖 + 冻结卖 + T+1待解锁（持仓守恒左端合计）。"""
        return self.sellable + self.frozen_sell + self.pending_t1


@dataclass
class Trade:
    date: str
    symbol: str
    side: str
    exec_price: float            # 含滑点执行价
    open_price: float            # 当日开盘价（滑点成本基准）
    qty: int
    amount: float                # 成交金额 = 执行价 × 股数
    fees: float                  # 佣金+印花税+过户费
    slip_cost: float             # 滑点成本额 = 开盘价 × 股数 × 滑点率
    realized_pnl: float          # 卖出净实现盈亏
    created_bar: int


@dataclass
class Account:
    cash_available: float = C.INITIAL_CASH
    cash_frozen: float = 0.0     # 现金层：冻结（未成交买单占用）
    cash_ledger: float = C.INITIAL_CASH  # 独立现金流账本（守恒式2对账基准）
    realized_pnl: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)


# ---------------------------------------------------------------- 成本模型（S03-01 §6）

def commission_of(amount: float) -> float:
    return round(max(amount * C.COMMISSION_RATE, C.COMMISSION_MIN), 2)


def transfer_of(amount: float, symbol: str) -> float:
    return round(amount * C.TRANSFER_RATE, 2) if C.is_shanghai(symbol) else 0.0


def stamp_of(amount: float) -> float:
    return round(amount * C.STAMP_RATE, 2)


def buy_total_cost(price: float, qty: int, symbol: str) -> float:
    """买入总支出 = 成交金额(含滑点执行价) + 佣金 + 过户费。"""
    amt = price * qty
    return amt + commission_of(amt) + transfer_of(amt, symbol)


def sell_net_proceeds(price: float, qty: int, symbol: str):
    """卖出净收入 = 成交金额 − 佣金 − 印花税 − 过户费。返回 (net, fees)。"""
    amt = price * qty
    fees = commission_of(amt) + stamp_of(amt) + transfer_of(amt, symbol)
    return amt - fees, fees


def one_lot_cost(price: float, symbol: str) -> float:
    """一手含费成本（§13 口径；过户费按 0.01‰ 复算——蓝图 §13 原文'0.1元'
    为笔误，1000×0.00001=0.01，按铁律一数值优先以复算为准）。"""
    amt = price * C.LOT_SIZE
    return buy_total_cost(price, C.LOT_SIZE, symbol)


def max_buyable_lots(available: float, price: float, symbol: str) -> int:
    """按可用资金反算最大可买整手数（§13：资金反算而非拒单）。"""
    per_lot = one_lot_cost(price, symbol)
    if available < per_lot:
        return 0
    return int(available // per_lot)


# ---------------------------------------------------------------- 引擎

class BacktestEngine:
    """单账户、事件驱动（K线为步长）回测引擎——W1 形态（D-008）。"""

    def __init__(self, bars: Dict[str, List[Bar]], strategy, calendar: List[str],
                 params: Optional[dict] = None, seed_note: str = "",
                 initial_cash: float = C.INITIAL_CASH):
        self.bars = bars
        self.strategy = strategy
        self.calendar = calendar
        self.params = params or {}
        self.initial_cash = initial_cash      # 冻结默认=10万（可参数化，如高股价标的）
        self.account = Account(cash_available=initial_cash,
                               cash_ledger=initial_cash)
        self.orders: List[Order] = []
        self.pending: List[Order] = []
        self.trades: List[Trade] = []
        self.equity_curve: List[tuple] = []      # (date, equity) 逐日快照
        self.last_close: Dict[str, float] = {}
        self.bar_index: Dict[tuple, Bar] = {
            (s, b.date): b for s, seq in bars.items() for b in seq}
        self._dates_idx: Dict[str, Dict[str, int]] = {
            s: {b.date: i for i, b in enumerate(seq)} for s, seq in bars.items()}
        self._oid = 0
        # 四自检量（S03-01 §11）+ W2 实现期新增两项（费用恒等式/拒单原因分类）
        self.filled_count = 0
        self.rejected_count = 0
        self.blocked_signals = 0
        self.conservation_failures = 0
        self.fee_check_failures = 0        # 第5自检量：费用双记账恒等式（报告端独立复算）
        self.reject_reasons: Dict[str, int] = {}   # 拒单/作废原因分类计数（ taxonomy ）
        self.consec_losses = 0             # 连续亏损笔数（S07 降仓规则状态）
        self.signals_by_bar: Dict[int, int] = {}   # 每根K线收到的信号数（末日陷阱在信号层比对）
        self.events: List[str] = []
        self.warmup = max(warmup_of(getattr(strategy, "warmup_windows", [0])),
                          getattr(strategy, "min_bars", 0))
        self.seed_note = seed_note
        self._halt_today = False
        self._halt_days = 0

    # ---- 账户辅助 ----
    def _pos(self, symbol: str) -> Position:
        if symbol not in self.account.positions:
            self.account.positions[symbol] = Position(symbol)
        return self.account.positions[symbol]

    def equity_now(self) -> float:
        mv = sum(self.last_close.get(s, 0.0) * p.held
                 for s, p in self.account.positions.items() if p.held)
        return self.account.cash_available + self.account.cash_frozen + mv

    def unrealized_pnl(self) -> float:
        return sum((self.last_close.get(s, 0.0) - p.avg_cost) * p.held
                   for s, p in self.account.positions.items() if p.held)

    def _check_conservation(self, where: str):
        """三条守恒式（S03-01 §2.3，逐笔校验）：
        1) 现金层守恒：现金流账本 == 可用 + 冻结
        2) 持仓守恒：可卖 + 冻结卖 + T+1待解锁 == 持仓
        3) 现金守恒（含费等价形式）：权益 == 初始 + 已实现 + 未实现"""
        acc = self.account
        ok1 = math.isclose(acc.cash_ledger, acc.cash_available + acc.cash_frozen,
                           rel_tol=C.METRIC_TOL, abs_tol=1e-6)
        ok2 = all(p.sellable + p.frozen_sell + p.pending_t1 == p.qty
                  for p in acc.positions.values())
        eq = self.equity_now()
        ok3 = math.isclose(eq, self.initial_cash + acc.realized_pnl + self.unrealized_pnl(),
                           rel_tol=C.METRIC_TOL, abs_tol=1e-6)
        if not (ok1 and ok2 and ok3):
            self.conservation_failures += 1
            self.events.append(
                f"[守恒失败@{where}] ledger={acc.cash_ledger:.6f} "
                f"avail+frozen={acc.cash_available + acc.cash_frozen:.6f} "
                f"pos_ok={ok2} eq={eq:.6f} init+r+u="
                f"{C.INITIAL_CASH + acc.realized_pnl + self.unrealized_pnl():.6f}")

    def _reject(self, o: Order, reason: str):
        """拒单/作废统一入口：计数 + 原因分类（五类 taxonomy，四自检量可归因）。"""
        o.status = "已拒" if reason.startswith(("资金不足", "无可卖")) else "已作废"
        o.reason = reason
        self.rejected_count += 1
        self.reject_reasons[reason] = self.reject_reasons.get(reason, 0) + 1

    # ---- 主循环：九步时序 ----
    def run(self) -> "BacktestEngine":
        self.strategy.attach(self)
        for t, date in enumerate(self.calendar):
            self._halt_today = False
            # 步2 停牌判断：缺K线=停牌——不撮合、保留估值、委托顺延计数（§5.3）
            for s, seq in self.bars.items():
                i = self._dates_idx[s].get(date)
                if i is not None:
                    self.last_close[s] = seq[i].close
            expired = []
            for o in self.pending:
                if self.bar_index.get((o.symbol, date)) is None:
                    o.age += 1
                    if o.age > C.ORDER_MAX_PENDING_DAYS:
                        self._reject(o, "停牌顺延超5个交易日")
                        expired.append(o)
                        self.events.append(f"[作废@{date}] {o.symbol} {o.side} {o.reason}")
            for o in expired:
                self._release_order(o)

            # 步3~5 处理昨夜委托：开盘撮合（先于任何信号计算——顺序即防线）
            self._execute_pending(date, t)

            # 步7 计算当日信号：只用截至本根收盘的数据（物理截断）
            signals = []
            if t >= self.warmup:
                signals = self.strategy.on_bar(t) or []

            # 步8 风控裁剪：熔断/仓位上限/去重/整手/资金反算
            self._risk_clip(t, date, signals)

            # 步9 权益快照与日志 + 守恒校验（成交时已逐笔校验，日终再校验一次）
            held_mv = sum(self.last_close.get(s, 0.0) * p.held
                          for s, p in self.account.positions.items() if p.held)
            eq = self.equity_now()
            self.equity_curve.append((date, eq, held_mv))   # (日期, 权益, 持仓市值)
            self._check_conservation(f"bar{t}#close")

        for o in self.pending:
            if o.status == "待撮合":
                o.status = "已作废"
                o.reason = "回测结束仍未成交"
                self._release_order(o)
        # 第5自检量：费用双记账恒等式——按留痕的执行价/股数独立复算全部费用，
        # 与逐笔留痕比对（任何一侧公式被改动而另一侧未同步，此处必暴露）
        fee_sum = fee_recalc = 0.0
        for tr in self.trades:
            amt = tr.exec_price * tr.qty
            if tr.side == "BUY":
                f = commission_of(amt) + transfer_of(amt, tr.symbol)
            else:
                f = commission_of(amt) + stamp_of(amt) + transfer_of(amt, tr.symbol)
            fee_sum += tr.fees
            fee_recalc += f
            if abs(tr.slip_cost - tr.open_price * C.SLIPPAGE * tr.qty) > 1e-6:
                self.fee_check_failures += 1
        if abs(fee_sum - fee_recalc) > 1e-6:
            self.fee_check_failures += 1
            self.events.append(f"[费用恒等失败] 留痕={fee_sum:.6f} 复算={fee_recalc:.6f}")
        return self

    # ---- 委托冻结/释放（现金层与持仓层守恒的执行面）----
    def _freeze_buy(self, amount: float):
        f = min(amount, self.account.cash_available)
        self.account.cash_available -= f
        self.account.cash_frozen += f

    def _release_order(self, o: Order):
        if o.side == "BUY" and o.target_amount > 0:
            rel = min(o.target_amount, self.account.cash_frozen)
            self.account.cash_frozen -= rel
            self.account.cash_available += rel
        elif o.side == "SELL" and o.quantity > 0:
            p = self._pos(o.symbol)
            rel = min(o.quantity, p.frozen_sell)
            p.frozen_sell -= rel
            p.sellable += rel

    def _unlock_t1(self):
        """T+1 解锁：待解锁股数 → 可卖（在开盘撮合前执行——走查口径）。"""
        for p in self.account.positions.values():
            if p.pending_t1:
                p.sellable += p.pending_t1
                p.pending_t1 = 0

    # ---- 撮合（步3~5）----
    def _execute_pending(self, date: str, t: int):
        self._unlock_t1()  # 次日开盘前解锁（走查第9步口径）
        remaining: List[Order] = []
        for o in self.pending:
            if o.status != "待撮合":
                continue
            bar = self.bar_index.get((o.symbol, date))
            if bar is None:                     # 停牌：保留委托（顺延计数已在步2处理）
                remaining.append(o)
                continue
            prev_close = self._prev_close(o.symbol, date)
            pct = C.limit_pct_for(o.symbol)
            limit_up = round(prev_close * (1 + pct), 2) if prev_close else None
            limit_down = round(prev_close * (1 - pct), 2) if prev_close else None

            if o.side == "BUY":
                self._fill_buy(o, bar, date, t, limit_up)
            else:
                self._fill_sell(o, bar, date, t, limit_down)
            if o.status == "待撮合":
                remaining.append(o)
        self.pending = remaining

    def _fill_buy(self, o: Order, bar: Bar, date: str, t: int, limit_up):
        # 涨跌停三情形①：一字涨停封住（开盘=涨停且最低≥涨停）→ 买单作废
        if limit_up is not None and bar.open >= limit_up - C.PRICE_EPS \
                and bar.low >= limit_up - C.PRICE_EPS:
            self._reject(o, "一字涨停无法买入")
            self._release_order(o)
            self.events.append(f"[作废@{date}] {o.symbol} 买单 {o.reason}")
            return
        price = bar.open * (1 + C.SLIPPAGE)
        spendable = self.account.cash_available + self.account.cash_frozen
        lots = int(o.target_amount // price // C.LOT_SIZE)
        lots = min(lots, max_buyable_lots(spendable, price, o.symbol))
        qty = lots * C.LOT_SIZE
        while qty > 0 and buy_total_cost(price, qty, o.symbol) > spendable + 1e-6:
            qty -= C.LOT_SIZE
        if qty < C.LOT_SIZE:
            self._reject(o, "资金不足一手")
            self._release_order(o)
            self.events.append(f"[拒单@{date}] {o.symbol} 买入 {o.reason}")
            return
        total = buy_total_cost(price, qty, o.symbol)
        amt = price * qty
        fees = commission_of(amt) + transfer_of(amt, o.symbol)
        slip_cost = bar.open * C.SLIPPAGE * qty
        self._release_order(o)                    # 解冻目标金额，按实际支出支付
        self.account.cash_available -= total
        self.account.cash_ledger -= total
        p = self._pos(o.symbol)
        old_cost = p.avg_cost * p.qty
        p.qty += qty
        p.avg_cost = (old_cost + total) / p.qty   # 含费均价
        p.pending_t1 += qty                       # T+1：次日开盘前解锁
        o.status = "已成交"
        o.filled_price = price
        o.filled_qty = qty
        o.fees = fees
        self.filled_count += 1
        self.trades.append(Trade(date, o.symbol, "BUY", price, bar.open, qty,
                                 amt, fees, slip_cost, 0.0, o.created_bar))
        self.events.append(f"[成交@{date}] 买 {o.symbol} {qty}股 @{price:.4f} "
                           f"费={fees:.2f} 滑点={slip_cost:.2f}")
        self._check_conservation(f"fill BUY {o.symbol}@{date}")

    def _fill_sell(self, o: Order, bar: Bar, date: str, t: int, limit_down):
        # 涨跌停三情形②：一字跌停封住 → 卖单作废（C-042 定案：不允许按跌停价强行成交）
        if limit_down is not None and bar.open <= limit_down + C.PRICE_EPS \
                and bar.high <= limit_down + C.PRICE_EPS:
            self._reject(o, "一字跌停无法卖出")
            self._release_order(o)
            self.events.append(f"[作废@{date}] {o.symbol} 卖单 {o.reason}")
            return
        price = bar.open * (1 - C.SLIPPAGE)
        p = self._pos(o.symbol)
        qty = min(o.quantity, p.frozen_sell)
        if qty <= 0:
            self._reject(o, "无可卖数量")
            return
        net, fees = sell_net_proceeds(price, qty, o.symbol)
        slip_cost = bar.open * C.SLIPPAGE * qty
        cost = p.avg_cost * qty
        realized = net - cost
        p.qty -= qty
        p.frozen_sell -= qty
        if p.qty == 0:
            p.avg_cost = 0.0
        self.account.cash_available += net
        self.account.cash_ledger += net
        self.account.realized_pnl += realized
        # 连续亏损状态（S07 降仓规则的状态源）：亏损笔累加、盈利笔清零
        if realized < 0:
            self.consec_losses += 1
        elif realized > 0:
            self.consec_losses = 0
        o.status = "已成交"
        o.filled_price = price
        o.filled_qty = qty
        o.fees = fees
        self.filled_count += 1
        self.trades.append(Trade(date, o.symbol, "SELL", price, bar.open, qty,
                                 price * qty, fees, slip_cost, realized, o.created_bar))
        self.events.append(f"[成交@{date}] 卖 {o.symbol} {qty}股 @{price:.4f} "
                           f"费={fees:.2f} 实现盈亏={realized:.2f}")
        self._check_conservation(f"fill SELL {o.symbol}@{date}")

    def _prev_close(self, symbol: str, date: str) -> Optional[float]:
        seq = self.bars.get(symbol, [])
        i = self._dates_idx[symbol].get(date)
        if i is None or i == 0:
            return None
        return seq[i - 1].close

    # ---- 风控裁剪（步8，S07 冻结基准）----
    def _risk_clip(self, t: int, date: str, signals):
        acc = self.account
        self.signals_by_bar[t] = self.signals_by_bar.get(t, 0) + len(signals)
        eq = self.equity_now()
        # 单日亏损熔断 5%：触发当日禁新买入；不强制平仓、不剔除区间（S07 语义）
        if self.equity_curve:
            prev_eq = self.equity_curve[-1][1]
            if prev_eq > 0 and (eq / prev_eq - 1.0) <= -C.DAILY_LOSS_HALT:
                self._halt_today = True
                self._halt_days += 1
                self.events.append(f"[熔断@{date}] 单日亏损≥5%，当日禁新买入（不强制平仓）")
        held_mv = sum(self.last_close.get(s, 0.0) * p.held
                      for s, p in acc.positions.items())
        for sig in signals:
            kind = sig[0]
            if kind == "BUY":
                _, symbol, amount = sig
                if self._halt_today:
                    self.blocked_signals += 1
                    self.events.append(f"[拦截@{date}] {symbol} 买入被熔断拦截")
                    continue
                if self._pos(symbol).held > 0:
                    # 去重防抖（S05-02 共性约束3）：持仓期同标的同方向不重复触发
                    self.blocked_signals += 1
                    continue
                # 流动性过滤（S16 量能骤降断言落地开关，默认关闭）
                bar_now = self.bar_index.get((symbol, date))
                if C.MIN_VOLUME_LIMIT > 0 and bar_now is not None \
                        and bar_now.volume < C.MIN_VOLUME_LIMIT:
                    self.blocked_signals += 1
                    self.reject_reasons["流动性不足(量能过滤)"] = \
                        self.reject_reasons.get("流动性不足(量能过滤)", 0) + 1
                    self.events.append(f"[拦截@{date}] {symbol} 成交量{bar_now.volume:.0f}"
                                       f"<下限{C.MIN_VOLUME_LIMIT}，禁止建仓")
                    continue
                if C.CONSEC_LOSS_RULE and self.consec_losses >= C.CONSEC_LOSS_N:
                    amount *= C.CONSEC_LOSS_FACTOR   # S07：连续亏损→单笔仓位降至5成
                    self.events.append(f"[降仓@{date}] {symbol} 连续亏损"
                                       f"{self.consec_losses}笔，目标仓位×{C.CONSEC_LOSS_FACTOR}")
                amount = min(amount, eq * C.POSITION_CAP_SINGLE)      # 单标的≤30%
                amount = min(amount, max(0.0, eq * C.POSITION_CAP_TOTAL - held_mv))
                if amount < 1.0:                       # 目标金额过小，不足产生有效整手委托
                    self.blocked_signals += 1
                    continue
                frozen = min(amount, acc.cash_available)   # 冻结额如实入账（防多单互吃）
                if frozen < 1.0:
                    self.blocked_signals += 1
                    continue
                self._oid += 1
                o = Order(self._oid, symbol, "BUY", target_amount=frozen, created_bar=t)
                self._freeze_buy(frozen)
                self.pending.append(o)
                self.orders.append(o)
            elif kind == "SELL":
                _, symbol, qty = sig            # qty=-1 → 清仓（允许零股，仅限清仓）
                p = self._pos(symbol)
                # 卖出来源池 = 可卖 + 当日买入（次日开盘前解锁，走查口径）
                pool = p.sellable + p.pending_t1
                q = pool if qty in (-1, None) else min(int(qty), pool)
                if q <= 0:
                    self.blocked_signals += 1
                    self.events.append(f"[拦截@{date}] {symbol} 卖出无可卖数量")
                    continue
                # 冻结：优先从可卖扣，不足部分占用待解锁股（执行前必已解锁）
                from_sell = min(q, p.sellable)
                from_t1 = q - from_sell
                p.sellable -= from_sell
                p.pending_t1 -= from_t1
                p.frozen_sell += q              # 持仓守恒：减可卖/待解锁，增冻结
                self._oid += 1
                o = Order(self._oid, symbol, "SELL", quantity=q, created_bar=t)
                self.pending.append(o)
                self.orders.append(o)
