# -*- coding: utf-8 -*-
"""qf.gate — 一键质量门禁（W2）：一条命令跑完全部质量证据，任何一项不过即红。

门禁五项（对应"史上第一"质量条）：
  1. 单元测试全套（unittest discover，含极端行情/三重检测/sweep/预算）
  2. 未来函数三重检测（S03 §10：静态扫描/末日陷阱/双跑比对）
  3. 三守恒+费用恒等（合成样例全流程，四+1 自检量零失败）
  4. 性能预算（全池 5标的×5年 ≤15s，对应 S06 表17 单组≤3s 的池级子集）
  5. 交付产物冒烟（HTML 报告单文件、零外部依赖、内嵌 SVG）
退出码：全过=0，任一失败=1（CI/门禁可直接挂接）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, "data")
KLINE_DIR = os.path.join(DATA_DIR, "kline")
OUT_DIR = os.path.join(ROOT, "out")


def run_gate(log=print) -> bool:
    checks = []

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        log(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"—— {detail}" if detail else ""))

    # 1 单元测试全套
    log("== 门禁1：单元测试全套 ==")
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                       capture_output=True, text=True, cwd=ROOT)
    tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln][-1:] or ["无输出"]
    add("unittest 全绿", r.returncode == 0, tail[0])

    # 2 三重检测
    log("== 门禁2：未来函数三重检测 ==")
    try:
        from .doctor import run_doctor
        from .storage import load_bars_csv
        from .strategies import MaCross
        bars = load_bars_csv(KLINE_DIR, symbols={"600001"})
        if not bars:
            raise FileNotFoundError("样例数据缺失")
        cal = sorted({b.date for seq in bars.values() for b in seq})
        v = run_doctor(bars, lambda: MaCross(5, 20), cal)
        add("静态扫描+末日陷阱零命中", v["合格"],
            f"双跑差异率 {v['双跑比对']['shift']['diff_ratio']:.0%}（信息性）")
    except Exception as exc:  # noqa
        add("三重检测", False, f"异常：{exc}")

    # 3 三守恒 + 费用恒等 + 性能预算（一次全池回测同时取证）
    log("== 门禁3：三守恒+费用恒等+性能预算 ==")
    try:
        from .sample_data import gen_sample_bars, gen_calendar
        bars = gen_sample_bars()
        cal = gen_calendar()
        t0 = time.perf_counter()
        from .engine import BacktestEngine
        from .strategies import MaCross
        eng = BacktestEngine(bars, MaCross(5, 20), cal).run()
        dt = time.perf_counter() - t0
        add("三守恒+费用恒等零失败",
            eng.conservation_failures == 0 and eng.fee_check_failures == 0,
            f"成交{eng.filled_count}笔/拒单{eng.rejected_count}/拦截{eng.blocked_signals}")
        add("性能预算（全池≤15s）", dt < 15.0, f"实测 {dt:.2f}s")
    except Exception as exc:  # noqa
        add("守恒+性能", False, f"异常：{exc}")

    # 5 交付产物冒烟
    log("== 门禁5：交付产物冒烟 ==")
    htmls = [f for f in os.listdir(OUT_DIR) if f.endswith(".html")] \
        if os.path.isdir(OUT_DIR) else []
    ok = False
    detail = "无 HTML 报告——运行 python qf.py demo 生成"
    if htmls:
        path = os.path.join(OUT_DIR, sorted(htmls)[-1])
        content = open(path, encoding="utf-8").read()
        ok = (content.count("<svg") == 3 and "<script" not in content
              and "九项配置" in content)
        detail = (f"{os.path.basename(path)}（{os.path.getsize(path)//1024}KB，"
                  f"SVG×{content.count('<svg')}，"
                  f"无JS={('<script' not in content)}）")
    add("HTML 单文件报告（零依赖）", ok, detail)

    log("== 门禁汇总 ==")
    all_ok = all(ok for _n, ok, _d in checks)
    for name, ok, detail in checks:
        log(f"  {'✅' if ok else '❌'} {name}")
    log(f"门禁判定：{'全绿 PASS' if all_ok else '存在失败 FAIL'}"
        f"（{sum(1 for _n, o, _d in checks if o)}/{len(checks)}）")
    return all_ok


if __name__ == "__main__":
    ok = run_gate()
    sys.exit(0 if ok else 1)
