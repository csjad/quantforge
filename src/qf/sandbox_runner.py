# -*- coding: utf-8 -*-
"""qf.sandbox_runner — 沙箱子进程运行器（受信任代码，由 sandbox.py 以子进程调用）

运行期审计挂钩（S05-01 §5"拦截不能靠关键词扫描源码，只能靠运行期挂钩"）：
  - 文件边界：写/追加指向白名单根（临时工作目录）之外 → 拒绝并记录；
    读越权（白名单根之外且不在解释器自身目录内，如 ../ 行情库）→ 拒绝并记录；
    解释器加载自身标准库/站点包的读取放行（否则合法 import 全被误杀）；
  - 网络边界：socket.* 事件 → 拒绝并记录（第三道）；
  - 进程逃逸：os.system / subprocess.Popen / os.exec* / os.spawn* → 拒绝并记录；
  - 运行期导入黑名单：socket/urllib/requests/subprocess/os/sys/shutil/ctypes/
    importlib 等在**策略执行期**被导入 → 拒绝（静态层已是白名单，此处兜底动态
    __import__ 路径；解释器内部导入发生在挂钩安装前或不在黑名单内）；
  - 拦截日志五项：时间/策略标识/违规类型/尝试目标/调用栈前三层（§6）。

挂钩在**加载策略模块之前**安装——第三方库"导入时就联网"同样被拦（§5）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))          # .../src/qf
SRC = os.path.dirname(HERE)                                # .../src
sys.path.insert(0, SRC)

from qf.engine import BacktestEngine, Bar            # noqa: E402（受信任侧，挂钩前）

VIOLATIONS: list = []
WORKROOT = ""
ALLOWED_READ_ROOTS: list = []
_strategy_name = "unknown"

FORBIDDEN_RUNTIME_IMPORTS = {
    "socket", "urllib", "http", "requests", "subprocess", "shutil", "ctypes",
    "importlib", "os", "sys", "signal", "pickle", "tempfile", "pathlib",
    "ftplib", "telnetlib", "smtplib", "asyncio",
}


def _stack_top3() -> list:
    tb = traceback.extract_stack()[:-1]
    return [f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in tb[-3:]]


def _record(vtype: str, target: str):
    VIOLATIONS.append({"time": datetime.now().isoformat(sep=" ", timespec="seconds"),
                       "strategy": _strategy_name, "type": vtype,
                       "target": str(target)[:200], "stack": _stack_top3()})


def _inside_any(path: str, roots) -> bool:
    return any(path.startswith(r) for r in roots if r)


def _audit(event: str, args):
    try:
        if event == "open":
            path, mode, _flags = args
            p = os.path.abspath(str(path) if path else "")
            writing = any(m in (mode or "") for m in ("w", "a", "x", "+"))
            if writing and not p.startswith(WORKROOT):
                _record("文件越权写", p)
                raise PermissionError(f"沙箱：禁止写白名单根之外（{p}）")
            if not writing and not _inside_any(p, ALLOWED_READ_ROOTS):
                _record("文件越权读", p)
                raise PermissionError(f"沙箱：禁止读白名单根之外（{p}）")
        elif event.startswith("socket."):
            _record("网络外联", f"{event}: {str(args[:1])[:120]}")
            raise PermissionError("沙箱：禁止创建/使用套接字（第三道网络边界）")
        elif event in ("os.system", "subprocess.Popen", "os.exec", "os.spawn",
                       "os.startfile"):
            _record("进程逃逸", f"{event}: {str(args[:1])[:120]}")
            raise PermissionError("沙箱：禁止启动子进程/系统命令")
        elif event == "import":
            mod = args[0] or ""
            root = mod.split(".")[0]
            if root in FORBIDDEN_RUNTIME_IMPORTS:
                hint = {"socket": "网络外联", "urllib": "网络外联", "requests": "网络外联",
                        "http": "网络外联", "os": "系统接口越权", "sys": "系统接口越权",
                        "subprocess": "进程逃逸", "shutil": "文件越权",
                        "ctypes": "进程逃逸", "importlib": "动态加载绕过"
                        }.get(root, "运行期黑名单")
                _record(f"运行期禁导入（{hint}）", mod)
                raise ImportError(f"沙箱：策略执行期禁止导入 {mod}（{hint}）——"
                                  f"拒绝而非静默降级（SEC-07）")
    except (PermissionError, ImportError):
        raise
    except Exception:  # noqa - 挂钩自身不得中断运行
        pass


def _build_read_roots(workroot: str) -> list:
    import site
    roots = [workroot, sys.prefix, sys.base_prefix, HERE, SRC]
    try:
        roots += list(site.getsitepackages())
        roots.append(site.getusersitepackages())
    except Exception:  # noqa
        pass
    return [os.path.abspath(r) for r in roots if r]


def main():
    global WORKROOT, ALLOWED_READ_ROOTS, _strategy_name
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--bars", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    WORKROOT = os.path.abspath(os.path.dirname(a.out))
    ALLOWED_READ_ROOTS = _build_read_roots(WORKROOT)
    ok = False
    filled = 0
    conservation = 0
    bars_len = 0

    try:
        # 数据装载（受信任侧，挂钩之前）
        bars: list = []
        with open(a.bars, "r", encoding="utf-8", newline="") as f:
            f.readline()
            for line in f:
                parts = line.strip().split(",")
                if len(parts) != 6:
                    continue
                d, o, h, l, c, v = parts
                bars.append(Bar(d, float(o), float(h), float(l), float(c), float(v)))
        bars_len = len(bars)

        # 安装审计挂钩（必须在加载策略之前——导入期即受控）
        sys.addaudithook(_audit)

        # 加载用户策略（其 import 均被挂钩审查）
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location("user_strategy", os.path.abspath(a.strategy))
        mod = _ilu.module_from_spec(spec)
        _strategy_name = os.path.basename(a.strategy)
        spec.loader.exec_module(mod)

        strat_cls = getattr(mod, "Strategy", None)
        if strat_cls is None:
            raise ValueError("策略文件缺少 Strategy 类")
        eng = BacktestEngine({"S": bars}, strat_cls(), [b.date for b in bars],
                             seed_note="sandbox-runner")
        eng.run()
        ok = eng.conservation_failures == 0 and len(eng.equity_curve) == bars_len
        filled = eng.filled_count
        conservation = eng.conservation_failures
    except Exception as exc:  # noqa - 任何异常都写进结果，不静默
        _record("运行异常", f"{type(exc).__name__}: {exc}")
        ok = False

    out = {"ok": ok and not VIOLATIONS, "filled": filled,
           "conservation_failures": conservation, "bars": bars_len,
           "violations": VIOLATIONS}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
