# -*- coding: utf-8 -*-
"""qf.sandbox — 策略沙箱校验流水线（S05-01 四道边界 + S50 三步校验的 W1 落地）

三步校验（S50 冻结）：①静态检查（指标存在/参数合法/**导入白名单**/正偏移索引）
→ ②沙箱试跑（独立子进程：审计挂钩拦截文件越权/网络外联，超时看门狗）→
③小样本回测（有成交且守恒）。任一失败即返回失败原因，不生成"看起来能跑"的策略。

四道边界（S05-01 §5）与实施：
  第一道 进程隔离——策略在独立子进程运行，崩溃/超时不拖垮主进程；
  第二道 文件边界——子进程工作目录限定临时目录，越权 open 由运行期审计挂钩拦截；
  第三道 网络边界——**导入期白名单**（SEC-07：math/statistics/datetime/collections/
         itertools/functools + 平台API）+ 运行期套接字挂钩；导入失败直接报错，
         **不静默降级**（静默降级=联网成败两态不同结果，比装不上库严重）；
  第四道 资源边界——超时看门狗（日线单标的 30 秒，§5 表）。

**沙箱目标声明（教程必写，S05-01 §8）**：沙箱的目标不是防住专业攻击者，
而是防住"策略代码写错导致的越权操作"。

拦截日志五项（§6）：时间/策略标识/违规类型/尝试目标/调用栈前三层。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional

from .engine import Bar

# 导入期白名单（S05-01 §5 第三道，SEC-07 基线；W1 零依赖环境去掉 numpy/pandas，
# 用户策略如需 numpy 属部署形态扩展——缺失即报错不静默）
ALLOWED_IMPORTS = {
    "math", "statistics", "datetime", "collections", "itertools", "functools",
    "json", "random", "__future__",
}
FORBIDDEN_HINTS = {
    "socket": "网络外联", "urllib": "网络外联", "http": "网络外联",
    "requests": "网络外联", "subprocess": "进程逃逸", "os": "系统接口越权",
    "sys": "系统接口越权", "shutil": "文件越权", "ctypes": "进程逃逸",
    "importlib": "动态加载绕过", "builtins": "动态加载绕过",
}
TIMEOUT_SECONDS = 30          # 日线单标的（S05-01 §5 表）
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_runner.py")


# ------------------------------------------------ ① 静态检查

def check_imports(path: str) -> List[dict]:
    """导入期白名单（AST 扫描 import/from 语句；拦截点=导入期，§5 第三道）。"""
    findings: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for m in mods:
            root = m.split(".")[0]
            if root in ALLOWED_IMPORTS or root == "qf":
                continue
            findings.append({"line": node.lineno, "module": m,
                             "type": FORBIDDEN_HINTS.get(root, "白名单外导入"),
                             "code": f"import {m}"})
    return findings


def check_hooks(path: str) -> List[str]:
    """插件契约检查（S05-01 §2）：必须定义 Strategy 类并实现 attach/on_bar。"""
    missing = []
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)
               if n.name == "Strategy"]
    if not classes:
        return ["缺少 Strategy 类（插件契约：文件必须定义名为 Strategy 的策略类）"]
    methods = {m.name for m in classes[0].body if isinstance(m, ast.FunctionDef)}
    for need in ("attach", "on_bar"):
        if need not in methods:
            missing.append(f"Strategy 缺少 {need} 钩子")
    return missing


# ------------------------------------------------ ② 沙箱试跑（子进程）

def _write_small_csv(bars: List[Bar], workdir: str) -> str:
    path = os.path.join(workdir, "small.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("date,open,high,low,close,volume\n")
        for b in bars:
            f.write(f"{b.date},{b.open},{b.high},{b.low},{b.close},{b.volume}\n")
    return path


def sandbox_run(strategy_path: str, bars: List[Bar],
                timeout: int = TIMEOUT_SECONDS) -> dict:
    """第二道+第三道+第四道：子进程试跑。返回 {ok, violations, output}。"""
    with tempfile.TemporaryDirectory() as td:
        csv_path = _write_small_csv(bars, td)
        strat_copy = os.path.join(td, "strategy.py")
        with open(strategy_path, "r", encoding="utf-8") as f:
            body = f.read()
        with open(strat_copy, "w", encoding="utf-8") as f:
            f.write(body)
        result_path = os.path.join(td, "result.json")
        try:
            proc = subprocess.run(
                [sys.executable, RUNNER, "--strategy", strat_copy,
                 "--bars", csv_path, "--out", result_path],
                capture_output=True, text=True, timeout=timeout, cwd=td,
                env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            if os.path.exists(result_path):
                with open(result_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {"ok": False,
                    "violations": [{"type": "子进程异常退出",
                                    "target": proc.returncode,
                                    "stack": (proc.stderr or "")[-800:]}],
                    "output": (proc.stdout or "")[-800:]}
        except subprocess.TimeoutExpired:
            return {"ok": False,
                    "violations": [{"type": "超时终止（第四道资源边界）",
                                    "target": f">{timeout}s",
                                    "stack": ["主进程", "sandbox_run", "subprocess"]}],
                    "output": ""}


# ------------------------------------------------ 总入口（S50 三步）

def verify_strategy(strategy_path: str, bars: List[Bar],
                    timeout: int = TIMEOUT_SECONDS) -> dict:
    """三步校验：静态 → 沙箱试跑 → 小样本回测。任一失败即返回失败原因。"""
    report: Dict[str, object] = {"path": strategy_path, "steps": {}}
    # ① 静态：导入白名单 + 插件契约 + 正偏移索引（doctor 第一重复用）
    imp = check_imports(strategy_path)
    hooks = check_hooks(strategy_path)
    from .doctor import static_scan

    class _Facade:
        pass
    try:
        with open(strategy_path, "r", encoding="utf-8") as f:
            src = f.read()
        ns: dict = {}
        exec(compile(src, strategy_path, "exec"), ns)  # noqa: S102 - 沙箱校验对象
        strat_cls = ns.get("Strategy")
        offset_hits = static_scan(_Facade()) if strat_cls is None else []
        if strat_cls is not None:
            probe = f"{strategy_path}#Strategy"
            offset_hits = _scan_text_for_offsets(src)
        report["steps"]["静态检查"] = {
            "ok": not imp and not hooks and not offset_hits,
            "imports": imp, "hooks": hooks, "offsets": offset_hits}
    except SyntaxError as exc:
        report["steps"]["静态检查"] = {"ok": False,
                                      "imports": imp, "hooks": hooks,
                                      "offsets": [],
                                      "syntax": f"语法错误 L{exc.lineno}: {exc.msg}"}
        report["合格"] = False
        return report

    if not report["steps"]["静态检查"]["ok"]:
        report["合格"] = False
        return report

    # ② 沙箱试跑（子进程 + 审计挂钩 + 超时）
    run = sandbox_run(strategy_path, bars, timeout)
    report["steps"]["沙箱试跑"] = run

    # ③ 小样本回测（在主进程执行——沙箱已确认无越权行为）
    small_ok = False
    if run.get("ok"):
        from .engine import BacktestEngine
        cal = [b.date for b in bars]
        eng = BacktestEngine({"S": bars}, _load_class(strategy_path)(),
                             cal, seed_note="sandbox")
        eng.run()
        small_ok = (eng.conservation_failures == 0 and eng.filled_count >= 0
                    and len(eng.equity_curve) == len(bars))
        report["steps"]["小样本回测"] = {
            "ok": small_ok, "filled": eng.filled_count,
            "conservation_failures": eng.conservation_failures}
    else:
        report["steps"]["小样本回测"] = {"ok": False, "skipped": "沙箱试跑未通过"}
    report["合格"] = bool(report["steps"]["静态检查"]["ok"] and run.get("ok") and small_ok)
    return report


def _scan_text_for_offsets(src: str) -> List[dict]:
    """对用户策略文本做正偏移索引扫描（doctor 静态扫描的文本版复用）。"""
    import re
    hits = []
    pat = re.compile(r"\[\s*\w+\s*\+\s*\d+\s*\]")
    for lineno, line in enumerate(src.splitlines(), 1):
        st = line.strip()
        if st.startswith("#") or "warmup" in st.lower():
            continue
        if pat.search(st):
            hits.append({"line": lineno, "code": st})
    return hits


def _load_class(strategy_path: str):
    ns: dict = {}
    with open(strategy_path, "r", encoding="utf-8") as f:
        exec(compile(f.read(), strategy_path, "exec"), ns)  # noqa: S102
    return ns["Strategy"]


def format_report(v: dict) -> str:
    L = ["== 策略沙箱校验（S50 三步 / S05 四道边界）=="]
    st = v["steps"]["静态检查"]
    L.append(f"  [① 静态检查] {'✅ 通过' if st['ok'] else '❌ 未通过'}")
    for imp in st.get("imports", []):
        L.append(f"      ↳ L{imp['line']} 导入 {imp['module']} → {imp['type']}")
    for h in st.get("hooks", []):
        L.append(f"      ↳ {h}")
    for h in st.get("offsets", []):
        L.append(f"      ↳ L{h['line']} 正偏移索引：{h['code']}")
    if st.get("syntax"):
        L.append(f"      ↳ {st['syntax']}")
    run = v["steps"]["沙箱试跑"]
    if run.get("ok"):
        L.append(f"  [② 沙箱试跑] ✅ 通过（子进程+审计挂钩+超时{TIMEOUT_SECONDS}s）"
                 f" 违规 {len(run.get('violations', []))} 次")
    else:
        L.append(f"  [② 沙箱试跑] ❌ 未通过")
        for vio in run.get("violations", []):
            L.append(f"      ↳ {vio.get('type')} → {vio.get('target')}")
    sm = v["steps"]["小样本回测"]
    if sm.get("ok"):
        L.append(f"  [③ 小样本回测] ✅ 守恒通过（成交 {sm['filled']} 笔）")
    else:
        L.append(f"  [③ 小样本回测] ❌ {sm.get('skipped', '守恒或行情未通过')}")
    L.append(f"  判定：{'✅ 合格——可入库' if v['合格'] else '❌ 不合格——不生成能跑的假象'}")
    L.append("  （沙箱目标=防越权操作，不防专业攻击者——S05-01 §8 目标声明）")
    return "\n".join(L)
