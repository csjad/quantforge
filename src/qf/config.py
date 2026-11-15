# -*- coding: utf-8 -*-
"""qf.config — 冻结基准常量（全部来自工程蓝图冻结结论，改动须走 C-XXX 冲突单）

口径出处：
  - 费率/滑点：S03-01 §6 成本模型 + D-004（固定每边1‰）
  - 仓位/熔断：S07 冻结基准（单标的30%/总仓100%/单日亏损熔断5%）
  - 涨跌幅：S03-01 §1 规则基准（主板±10%/创业科创±20%/ST±5%）
  - 初始资金：S03-01 §1（¥100,000）
  - 年化基准：S09-01（250交易日）
"""
from __future__ import annotations

# ---- 资金与费用（S03-01）----
INITIAL_CASH = 100_000.0          # 初始资金
COMMISSION_RATE = 0.00025         # 佣金 0.25‰，买卖双边
COMMISSION_MIN = 5.0              # 最低佣金 5元/笔
STAMP_RATE = 0.0005               # 印花税 0.5‰，仅卖出
TRANSFER_RATE = 0.00001           # 过户费 0.01‰，仅沪市（6xx 开头），深市 0
SLIPPAGE = 0.001                  # 滑点固定每边 1‰（D-004 方案甲）
SLIPPAGE_MODEL = "fixed"          # fixed | volatility | depth（D-004 可切换，W1 实现 fixed）

# ---- 仓位与风控（S07 冻结基准）----
POSITION_CAP_SINGLE = 0.30        # 单标的仓位上限 30%
POSITION_CAP_TOTAL = 1.00         # 总仓位上限 100%
DAILY_LOSS_HALT = 0.05            # 单日亏损熔断 5%（触发：当日禁新买入；不强制平仓、禁加仓）
CONSEC_LOSS_RULE = False          # 连续亏损降仓（S07：连续3笔亏损→单笔仓位降至5成，
                                  # 出现盈利笔解除——冻结口径"可配，默认关"）
CONSEC_LOSS_N = 3
CONSEC_LOSS_FACTOR = 0.5
MIN_VOLUME_LIMIT = 0              # 流动性过滤：日成交量下限（股），0=关闭（S16 量能骤降断言的落地开关）
LOT_SIZE = 100                    # 整手约束：买入须 100 股整数倍；卖出允许零股仅限清仓
T_PLUS_1 = True                   # 当日买入次日方可卖
ORDER_MAX_PENDING_DAYS = 5        # 停牌委托不顺延超过 5 个交易日，超期作废（S03-01 §5.3）

# ---- 涨跌幅（S03-01 §1；按代码前缀判定板别）----
LIMIT_PCT_MAIN = 0.10             # 主板 600/000/001…
LIMIT_PCT_GEM = 0.20              # 创业板 300/301、科创板 688
LIMIT_PCT_ST = 0.05               # ST ±5%（名称含 ST）

# ---- 绩效（S09-01 冻结）----
TRADING_DAYS_PER_YEAR = 250       # 年化基准 250 交易日（非 365 自然日）
ANNUALIZE_MIN_DAYS = 100          # 区间 <100 交易日：年化类指标标"参考"，不参与排序
METRIC_TOL = 1e-6                 # 浮点容差（S00-A4 / S04-02）

# ---- V8 分布式触发器（S52-02：参数化三取一，默认关——不达条件保持单机）----
DISTRIBUTED_TRIGGER_GROUPS = 1000       # 遍历组数阈值（表67 原口径）
DISTRIBUTED_TRIGGER_POOL = 500          # 标的池规模阈值
DISTRIBUTED_TRIGGER_SECONDS = 30 * 60   # 预估总耗时阈值（30分钟上限）
DISTRIBUTED_BACKEND = "auto"            # auto | serial | parallel（remote 留 V8.1）

# ---- 涨跌停判定容差 ----
PRICE_EPS = 1e-9


def limit_pct_for(symbol: str, name: str = "") -> float:
    """按代码前缀与 ST 标记返回涨跌幅限制。"""
    if "ST" in (name or "").upper():
        return LIMIT_PCT_ST
    if symbol.startswith(("300", "301", "688")):
        return LIMIT_PCT_GEM
    return LIMIT_PCT_MAIN


def is_shanghai(symbol: str) -> bool:
    """沪市（过户费计费方向）：6xx 开头；深市 0/3 开头过户费为 0。"""
    return symbol.startswith("6")
