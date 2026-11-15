# -*- coding: utf-8 -*-
"""qf.gui — QuantForge 图形界面（tkinter，Python 自带，零第三方依赖——D-008 纪律）

设计口径：
  - 五件事一键化（S44）：生成样例→自检→回测→报告→查看，全部按钮化；
  - 进阶区：doctor 三重检测 / sweep 参数寻优（默认参数即可跑）；
  - 回测/检测在**后台线程**跑子进程（复用已测的 CLI 路径 `python qf.py ...`），
    输出实时流入文本区，UI 不卡死；
  - 打开最新报告=webbrowser 打开 HTML 单文件（D-009，离线可看）；
  - 诚实边界（C-039 同源）：界面写明"离线包不含在线拉取"。
"""
from __future__ import annotations

import glob
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QF_PY = os.path.join(ROOT, "qf.py")
OUT_DIR = os.path.join(ROOT, "out")

# (代码, 中文名)——与 strategies.STRATEGY_REGISTRY 一一对应
STRATEGY_CHOICES: List[tuple] = [
    ("ma_cross", "双均线趋势"),
    ("boll_trend", "布林带增强"),
    ("rsi_reversal", "RSI 反转"),
    ("atr_breakout", "ATR 突破"),
    ("multi_factor_topn", "多因子打分"),
]
SYMBOL_CHOICES = ["全部（样例池）", "600001 沪主板", "000002 深主板",
                  "300003 创业板", "688005 科创板", "600006 沪主板"]


def build_cmd(strategy: str, symbol: Optional[str],
              fast: Optional[str] = None, slow: Optional[str] = None,
              data_dir: Optional[str] = None,
              cash: Optional[str] = None) -> List[str]:
    """构造回测命令行（纯函数，供测试；不含解释器前缀）。"""
    cmd = ["backtest", "--strategy", strategy]
    if symbol:
        cmd += ["--symbol", symbol]
    if fast:
        cmd += ["--fast", str(fast)]
    if slow:
        cmd += ["--slow", str(slow)]
    if data_dir:
        cmd += ["--data-dir", data_dir]
    if cash:
        cmd += ["--cash", str(cash)]
    return cmd


def latest_report(pattern: str = "report_*.html") -> Optional[str]:
    """最新的报告文件（按修改时间；复盘=review_*.md）。"""
    files = glob.glob(os.path.join(OUT_DIR, pattern))
    return max(files, key=os.path.getmtime) if files else None


class QuantForgeApp:
    """主窗口：五件事 + 进阶区 + 输出流。

    惰性 tkinter：本模块可在无 tkinter 的解释器下被导入（核心测试/CLI 不受影响）；
    只有真正构建窗口（main / __init__）才需要 tkinter（本机=Anaconda 基座 3.13.9
    自带 Tk 8.6；WorkBuddy 3.13.14 未打包 tkinter）。"""

    def __init__(self, root):
        self.root = root
        root.title("QuantForge 量化回测平台（W1 图形版·离线可用）")
        root.geometry("960x680")
        self.q: queue.Queue = queue.Queue()
        self.running = False
        self._build()
        root.after(120, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- 界面构建 ----
    def _build(self):
        import tkinter as tk
        from tkinter import ttk, scrolledtext
        self._tk_mod, self._ttk_mod = tk, ttk
        pad = dict(padx=6, pady=4)
        top = ttk.Frame(self.root); top.pack(fill="x", **pad)
        ttk.Label(top, text="QuantForge｜本地A股量化回测（合成样例·离线五件事）",
                  font=("Microsoft YaHei", 12, "bold")).pack(side="left")
        self.status = tk.StringVar(value="空闲")
        ttk.Label(top, textvariable=self.status, foreground="#1d4ed8").pack(side="right")

        box1 = ttk.LabelFrame(self.root, text="① 离线五件事（一键）")
        box1.pack(fill="x", **pad)
        self.buttons: List[ttk.Button] = []
        def add_btn(parent, text, fn):
            b = ttk.Button(parent, text=text, command=fn)
            b.pack(side="left", **pad)
            self.buttons.append(b)
            return b
        add_btn(box1, "1️⃣ 生成样例数据", lambda: self.run(["gen-sample"]))
        add_btn(box1, "2️⃣ 首次自检五项", lambda: self.run(["init"]))
        add_btn(box1, "▶ 一键五件事（推荐首次）", lambda: self.run(["demo"]))

        box2 = ttk.LabelFrame(self.root, text="② 跑一次回测（选策略与标的）")
        box2.pack(fill="x", **pad)
        self.strategy_var = tk.StringVar()
        self.strategy_box = ttk.Combobox(
            box2, state="readonly", width=22,
            values=[f"{cn}（{code}）" for code, cn in STRATEGY_CHOICES])
        self.strategy_box.current(0)
        self.strategy_box.pack(side="left", **pad)
        self.symbol_var = tk.StringVar()
        self.symbol_box = ttk.Combobox(box2, textvariable=self.symbol_var,
                                       state="readonly", width=18,
                                       values=SYMBOL_CHOICES)
        self.symbol_box.current(0)
        self.symbol_box.pack(side="left", **pad)
        ttk.Label(box2, text="初始资金=").pack(side="left", **pad)
        self.cash_var = tk.StringVar(value="100000")
        ttk.Entry(box2, textvariable=self.cash_var, width=9).pack(side="left", **pad)
        add_btn(box2, "▶ 开始回测", self.do_backtest)
        add_btn(box2, "📄 打开最新报告", self.open_report)
        add_btn(box2, "📂 打开报告文件夹", self.open_out_dir)

        box3 = ttk.LabelFrame(self.root, text="③ 进阶：质量检测与参数寻优")
        box3.pack(fill="x", **pad)
        add_btn(box3, "🩺 三重检测（防未来函数）", self.do_doctor)
        add_btn(box3, "📋 打开最新复盘", self.open_review)
        ttk.Label(box3, text="寻优参数 fast= slow= 组数=").pack(side="left", **pad)
        self.fast_var = tk.StringVar(value="3:15")
        self.slow_var = tk.StringVar(value="30:90")
        self.groups_var = tk.StringVar(value="30")
        ttk.Entry(box3, textvariable=self.fast_var, width=8).pack(side="left", **pad)
        ttk.Entry(box3, textvariable=self.slow_var, width=8).pack(side="left", **pad)
        ttk.Entry(box3, textvariable=self.groups_var, width=5).pack(side="left", **pad)
        add_btn(box3, "🔍 开始寻优", self.do_sweep)

        boxr = ttk.LabelFrame(self.root, text="④ 真实行情（可选层，akshare；个人研究专用，禁止二次分发）")
        boxr.pack(fill="x", **pad)
        self.source_var = tk.StringVar()
        self.source_box = ttk.Combobox(boxr, textvariable=self.source_var,
                                       state="readonly", width=16,
                                       values=["样例池（合成）", "真实行情池"])
        self.source_box.current(0)
        self.source_box.pack(side="left", **pad)
        ttk.Label(boxr, text="代码=").pack(side="left", **pad)
        self.real_sym_var = tk.StringVar(value="600519,000001")
        ttk.Entry(boxr, textvariable=self.real_sym_var, width=16).pack(side="left", **pad)
        ttk.Label(boxr, text="年数=").pack(side="left", **pad)
        self.real_years_var = tk.StringVar(value="3")
        ttk.Entry(boxr, textvariable=self.real_years_var, width=4).pack(side="left", **pad)
        add_btn(boxr, "🌐 拉取真实行情", self.do_fetch_real)
        ttk.Label(boxr, text="(需联网；东财限流自动切新浪)",
                  foreground="#6b7280").pack(side="left", **pad)

        box4 = ttk.LabelFrame(self.root, text="输出")
        box4.pack(fill="both", expand=True, **pad)
        self.text = scrolledtext.ScrolledText(box4, height=22, font=("Consolas", 9))
        self.text.pack(fill="both", expand=True, **pad)
        self.text.insert("end",
            "使用顺序：先点【一键五件事】→ 再选策略点【开始回测】→ 点【打开最新报告】。\n"
            "所有计算在本地完成；离线包不含在线拉取（数据通道=本地文件导入）。\n"
            "回测结果基于合成样例数据，仅供功能验证与学习，不构成投资建议。\n\n")

    # ---- 命令执行（后台线程 + 子进程流式输出）----
    def _code_from_box(self) -> str:
        sel = self.strategy_box.get()
        for code, cn in STRATEGY_CHOICES:
            if code in sel:
                return code
        return "ma_cross"

    def _symbol_from_box(self) -> Optional[str]:
        sel = self.symbol_var.get()
        return sel.split()[0] if sel and not sel.startswith("全部") else None

    def run(self, args: List[str]):
        if self.running:
            self.log("⚠ 已有任务在运行，请稍候…\n")
            return
        self.running = True
        self.status.set("运行中…")
        for b in self.buttons:
            b.state(["disabled"])
        self.log(f"$ python qf.py {' '.join(args)}\n")

        def worker():
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            proc = subprocess.Popen(
                [sys.executable, QF_PY] + args, cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for raw in iter(proc.stdout.readline, b""):
                self.q.put(raw.decode("utf-8", errors="replace"))
            proc.wait()
            self.q.put(f"\n[退出码 {proc.returncode}] {'✅ 完成' if proc.returncode == 0 else '❌ 失败（看上方输出定位）'}\n\n")
            self.q.put("__DONE__")

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item == "__DONE__":
                    self.running = False
                    self.status.set("空闲")
                    for b in self.buttons:
                        self._ttk_mod.Button.state(b, ["!disabled"])
                else:
                    self.log(item)
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def log(self, s: str):
        self.text.insert("end", s)
        self.text.see("end")

    # ---- 按钮动作 ----
    def do_backtest(self):
        code = self._code_from_box()
        sym = self._symbol_from_box()
        data_dir = None
        if self.source_var.get() == "真实行情池":
            data_dir = os.path.join(ROOT, "data", "kline_real")
        cash = self.cash_var.get().strip() or None
        self.run(build_cmd(code, sym, data_dir=data_dir, cash=cash))

    def do_fetch_real(self):
        syms = self.real_sym_var.get().strip()
        if not syms:
            self.log("⚠ 请填写真实标的代码（如 600519,000001）——合成代码无效\n")
            return
        try:
            years = int(self.real_years_var.get())
        except ValueError:
            self.log("⚠ 年数须为整数\n")
            return
        self.run(["fetch-real", "--symbol", syms, "--years", str(years)])

    def open_review(self):
        path = latest_report("review_*.md")
        if path:
            os.startfile(path)
        else:
            self.log("⚠ 还没有复盘报告——先跑一次回测（复盘自动生成）\n")

    def do_doctor(self):
        self.run(["doctor", "--strategy", self._code_from_box()])

    def do_sweep(self):
        try:
            groups = int(self.groups_var.get())
        except ValueError:
            self.log("⚠ 组数须为整数\n")
            return
        self.run(["sweep", "--strategy", self._code_from_box(),
                  "--param", f"fast={self.fast_var.get()}",
                  "--param", f"slow={self.slow_var.get()}",
                  "--mode", "random", "--groups", str(groups)])

    def open_report(self):
        path = latest_report()
        if path:
            webbrowser.open("file:///" + path.replace("\\", "/"))
        else:
            self.log("⚠ 还没有报告——先点【一键五件事】或【开始回测】\n")

    def open_out_dir(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        os.startfile(OUT_DIR)   # Windows 资源管理器

    def _on_close(self):
        if self.running:
            if not __import__("tkinter.messagebox", fromlist=["askokcancel"]) \
                    .askokcancel("退出", "任务仍在运行，确定退出？"):
                return
        self.root.destroy()


def main():
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    QuantForgeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
