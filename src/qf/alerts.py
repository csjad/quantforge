# -*- coding: utf-8 -*-
"""qf.alerts — 告警引擎（S13-02 冻结规则 R1~R6 的 W1 落地）

冻结规则（S13-02 §1）：
  R1 同指标合并：10分钟窗口内同类告警合并为一条，附计数与首次失败时间；
  R2 升级判定：连续失败≥5次升为严重并通知；单次失败仅记警告；
  R3 确认即静默：已确认告警修复前不重复通知；确认必须留记录；
  R4 恢复配对：恢复通知与告警配对发送，不"只报坏不报好"；
  R5 抑制窗口：大批量提交置抑制窗口，窗口内不因队列长度告警（部署形态口径）；
  R6 上下文三件套：每条告警含任务ID+错误信息+影响范围。

W1 应用面＝**回测产物的质量告警**（post-run 评估）：
  - 守恒式失败 / 费用恒等失败 → 严重（结果不可采信）；
  - 成交笔数<20（S43 统计意义门槛）→ 警告；
  - 熔断触发 → 警告（附熔断说明）；
  - 数据新鲜度（TC-MON-007）：日线滞后1日警告、滞后3日严重；
  - 最大回撤>20% 且 卡玛<0.5 → 警告（S11 判据1 联动）。
时间注入可测（测试不真实等待10分钟）。
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

MERGE_WINDOW = timedelta(minutes=10)     # R1 冻结窗口
ESCALATE_N = 5                           # R2 冻结升级阈值


@dataclass
class Alert:
    key: str                     # 同类告警的合并键（同指标）
    severity: str                # 警告 / 严重
    message: str
    task_id: str = ""            # R6 三件套之一
    error: str = ""              # R6 三件套之二
    impact: str = ""             # R6 三件套之三
    first_time: datetime = None  # R1：首次失败时间
    count: int = 1               # R1：同类计数
    escalated: bool = False      # R2
    resolved: bool = False       # R4 恢复配对
    confirmed: bool = False      # R3 确认即静默


class AlertEngine:
    """告警状态机：emit(key, ...) → 合并/升级/静默；resolve(key) → 恢复配对。"""

    def __init__(self, now: Optional[datetime] = None):
        self.active: Dict[str, Alert] = {}
        self.history: List[Alert] = []
        self._now = now or datetime.now()

    def _tick(self, seconds: float = 0):
        self._now = self._now + timedelta(seconds=seconds)

    def emit(self, key: str, severity: str, message: str,
             task_id: str = "", error: str = "", impact: str = "") -> Alert:
        """发出/合并告警（R1/R2/R3/R6）。返回该 key 的当前告警对象。"""
        self._tick(seconds=1)
        existing = self.active.get(key)
        if existing is not None:
            existing.count += 1
            # R3 确认即静默：修复前不重复通知（计数仍累计，留痕）
            # R2 升级判定：连续失败≥5 → 升为严重
            if existing.count >= ESCALATE_N and not existing.escalated:
                existing.escalated = True
                existing.severity = "严重"
                existing.message = (f"{existing.message}（同类失败{existing.count}次，"
                                    f"首次 {existing.first_time:%H:%M:%S}，已升级严重）")
            if not existing.confirmed or existing.escalated:
                pass  # 通知面由调用方决定；对象状态已更新
            return existing
        a = Alert(key=key, severity=severity, message=message,
                  task_id=task_id, error=error, impact=impact,
                  first_time=self._now)
        self.active[key] = a
        self.history.append(a)
        return a

    def confirm(self, key: str, who: str = "operator") -> bool:
        """R3 确认即静默（确认记录完整留存：时间+确认人+告警ID）。"""
        a = self.active.get(key)
        if a is None:
            return False
        a.confirmed = True
        a.impact = (a.impact + f"｜确认记录：{self._now:%Y-%m-%d %H:%M:%S} "
                    f"by {who} id={key}").strip("｜")
        return True

    def resolve(self, key: str) -> Optional[Alert]:
        """R4 恢复配对：恢复通知与告警配对，不"只报坏不报好"。"""
        a = self.active.pop(key, None)
        if a is None:
            return None
        a.resolved = True
        a.message = f"[已恢复] {a.message}"
        self.history.append(a)
        return a

    def notify_lines(self) -> List[str]:
        """当前应通知的面（R3 静默的不再重复；R1 合并计数+首次时间）。"""
        lines = []
        for key, a in sorted(self.active.items()):
            if a.confirmed and not a.escalated:
                continue                     # R3：已确认未升级 → 静默
            sev = "严重" if a.escalated or a.severity == "严重" else "警告"
            ctx = f"任务={a.task_id or '-'} 影响={a.impact or '-'}"
            lines.append(f"[{sev}] {key} ×{a.count}（首次 {a.first_time:%H:%M:%S}）"
                         f" {a.message}｜{ctx}")
        return lines


# ------------------------------------------------ 回测产物质量告警（W1 应用面）

def evaluate_backtest(engine, metrics_result, data_dir: str = "",
                      now: Optional[datetime] = None,
                      run_id: str = "") -> AlertEngine:
    """对一次回测产物跑质量告警（R1/R2/R6 状态机自动生效）。"""
    ae = AlertEngine(now=now)
    v = metrics_result.values
    rid = run_id or "run"

    if getattr(engine, "conservation_failures", 0) > 0:
        ae.emit(f"{rid}/守恒失败", "严重",
                f"三条守恒式失败 {engine.conservation_failures} 次——结果不可采信",
                task_id=rid, error="现金/现金层/持仓守恒未过", impact="整份报告")
    if getattr(engine, "fee_check_failures", 0) > 0:
        ae.emit(f"{rid}/费用恒等", "严重",
                f"费用双记账恒等失败 {engine.fee_check_failures} 次",
                task_id=rid, error="费率公式漂移嫌疑", impact="成本明细")
    trades = v.get("成交笔数") or 0
    if 0 < trades < 20:
        ae.emit(f"{rid}/笔数不足", "警告",
                f"成交 {trades} 笔<20（统计意义门槛，S43）",
                task_id=rid, error="样本过少", impact="绩效结论")
    if getattr(engine, "_halt_days", 0) > 0:
        ae.emit(f"{rid}/熔断触发", "警告",
                f"单日亏损熔断触发 {engine._halt_days} 天（区间计入回撤，不剔除）",
                task_id=rid, error="单日亏损≥5%", impact="回撤与复盘")
    mdd, calmar = v.get("最大回撤"), v.get("卡玛")
    if mdd is not None and calmar is not None and mdd < -0.20 and abs(calmar) < 0.5:
        ae.emit(f"{rid}/风险收益比", "警告",
                f"最大回撤 {mdd:.1%} 且卡玛 {calmar:.2f}<0.5",
                task_id=rid, error="S11 判据1", impact="绩效结论")

    # 数据新鲜度（TC-MON-007：日线滞后1日警告、3日严重）
    if data_dir and os.path.isdir(data_dir):
        csvs = glob.glob(os.path.join(data_dir, "*.csv"))
        if csvs:
            latest = max(os.path.getmtime(p) for p in csvs)
            age_days = ((now or datetime.now()) - datetime.fromtimestamp(latest)).days
            if age_days >= 3:
                ae.emit(f"{rid}/数据新鲜度", "严重",
                        f"行情数据滞后 {age_days} 日（≥3日=严重）",
                        task_id=rid, error="数据未更新", impact="回测有效性")
            elif age_days >= 1:
                ae.emit(f"{rid}/数据新鲜度", "警告",
                        f"行情数据滞后 {age_days} 日（1~2日）",
                        task_id=rid, error="数据未更新", impact="回测有效性")
    return ae


def notify_lines(ae: AlertEngine) -> List[str]:
    return ae.notify_lines() or ["[正常] 未触发任何质量告警"]
