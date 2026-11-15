# -*- coding: utf-8 -*-
"""qf.webconsole — 本地 Web 控制台（ADR D-012 + W11 大改造）

新增 API（只增）：
  GET  /api/runs            全部回测列表（A1）
  GET  /api/run/<id>        任一回测详情（KPI/图表/年度表/月度热力，A1/B4）
  GET  /api/compare?a&b     两回测十二项指标对比（A3）
  GET  /api/sweep           最近一次寻优结果（A2）
  GET  /api/pool            自选池（B1）
  POST /api/pool            保存自选池（B1，代码白名单校验）
  POST /api/verify          用户策略沙箱三步校验（B2，S50）
  GET  /api/strategies      已验证策略列表（B2）
  POST /api/run action=fetch-pool / backtest pool=True（B1/B3）
"""
from __future__ import annotations

import glob
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QF_PY = os.path.join(ROOT, "qf.py")
DB_PATH = os.path.join(ROOT, "data", "qf.db")
OUT_DIR = os.path.join(ROOT, "out")
STRAT_DIR = os.path.join(ROOT, "data", "strategies")
POOL_FILE = os.path.join(ROOT, "data", "pool.json")

STRATEGIES = [("ma_cross", "双均线趋势"), ("boll_trend", "布林带增强"),
              ("rsi_reversal", "RSI 反转"), ("atr_breakout", "ATR 突破"),
              ("multi_factor_topn", "多因子打分")]
SYMBOLS = ["600001", "000002", "300003", "688005", "600006"]

STATE: dict = {"running": False, "log": []}
LOG_Q: "queue.Queue" = queue.Queue()
PROC: Optional[subprocess.Popen] = None


# ================================================================ 数据面

def _last_close(symbol: str) -> dict:
    """最新收盘价：真实池优先，其次样例池（买得起吗滑杆用真实价）。"""
    import csv
    for kind in ("kline_real", "kline"):
        d = os.path.join(ROOT, "data", kind)
        if not os.path.isdir(d):
            continue
        best = None
        for f in glob.glob(os.path.join(d, f"{symbol}_*.csv")):
            try:
                with open(f, "r", encoding="utf-8", newline="") as fh:
                    rows = list(csv.DictReader(fh))
                if rows:
                    last = rows[-1]
                    if best is None or last["date"] > best[0]:
                        best = (last["date"], float(last["close"]))
            except (OSError, ValueError, KeyError):
                continue
        if best:
            return {"symbol": symbol, "date": best[0], "close": best[1]}
    return {"symbol": symbol, "date": "", "close": 0.0}


def _pool_get() -> List[str]:
    """自选池（B1）：data/pool.json，代码白名单校验。"""
    try:
        with open(POOL_FILE, "r", encoding="utf-8") as f:
            syms = json.load(f).get("symbols", [])
    except (OSError, ValueError):
        return []
    ok = []
    for s in syms:
        if re.fullmatch(r"\d{6}", str(s)):
            ok.append(str(s))
    return ok


def _pool_set(symbols: List[str]) -> int:
    ok: List[str] = []
    for s in symbols:
        s = str(s).strip()
        if re.fullmatch(r"\d{6}", s) and s not in ok:
            ok.append(s)
    os.makedirs(os.path.dirname(POOL_FILE), exist_ok=True)
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        json.dump({"symbols": ok}, f, ensure_ascii=False)
    return len(ok)


def _runs_list() -> List[dict]:
    """A1：全部回测（新→旧），含指标速览。"""
    try:
        from .storage import RunStore
        store = RunStore(DB_PATH)
        rows = store.conn.execute(
            "SELECT run_id, created_at, strategy, metrics FROM runs "
            "ORDER BY created_at DESC").fetchall()
        out = []
        for rid, ts, strat, mjson in rows:
            try:
                m = json.loads(mjson)
                lite = {"累计收益": m.get("累计收益"), "夏普": m.get("夏普"),
                        "最大回撤": m.get("最大回撤"), "成交笔数": m.get("成交笔数")}
            except ValueError:
                lite = {}
            out.append({"run_id": rid, "created_at": ts, "strategy": strat,
                        "metrics": lite})
        return out
    except Exception as exc:  # noqa
        return [{"error": str(exc)}]


def _yearly_monthly(eq: List[tuple]) -> Tuple[List[dict], List[dict]]:
    """B4：年度收益表 + 月度收益序列（真实日期口径）。"""
    if not eq:
        return [], []
    yearly: Dict[str, float] = {}
    for d, v, _m in eq:
        yearly[d[:4]] = v
    yrets, prev = [], eq[0][1]
    for y in sorted(yearly):
        v = yearly[y]
        yrets.append({"year": y, "ret": (v / prev - 1.0) if prev else 0.0})
        prev = v
    monthly: Dict[str, float] = {}
    for d, v, _m in eq:
        monthly[d[:7]] = v
    mrets, prev = [], eq[0][1]
    for m in sorted(monthly):
        v = monthly[m]
        mrets.append({"month": m, "ret": (v / prev - 1.0) if prev else 0.0})
        prev = v
    return yrets, mrets


def _run_full(run_id: str) -> Optional[dict]:
    """A1/B4：任一回测详情（指标+权益+年度表+月度序列+三图含悬停）。"""
    try:
        from .storage import RunStore
        store = RunStore(DB_PATH)
        data = store.load_run(run_id)
        if not data:
            return None
        metrics = json.loads(data["metrics"])
        eq = [(r[1], r[2], r[3]) for r in data["equity"]]  # (date, equity, mv)
        yrets, mrets = _yearly_monthly(eq)
        from .charts import equity_svg, drawdown_svg, monthly_svg
        curve = [(d, v) for d, v, _m in eq]
        dates = [d for d, _v, _m in eq]
        charts = (equity_svg(curve, hover_dates=dates)
                  + drawdown_svg(curve) + monthly_svg(curve))
        return {"run_id": run_id, "strategy": data["strategy"],
                "metrics": metrics, "equity": eq[-400:],
                "yearly": yrets, "monthly": mrets[-36:],
                "charts": charts}
    except Exception as exc:  # noqa
        return {"error": str(exc)}


def _sweep_latest() -> Optional[dict]:
    """A2：最近一次寻优结果（checkpoint JSON → 排名记录）。"""
    files = glob.glob(os.path.join(OUT_DIR, "sweep_*.json"))
    if not files:
        return None
    path = max(files, key=os.path.getmtime)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records", {})
        ranked = sorted(records.values(),
                        key=lambda r: (-(r.get("score") if r.get("score") is not None
                                         else -1e18),
                                       json.dumps(r.get("params", {}),
                                                  sort_keys=True)))
        return {"task": data.get("task"), "total": len(ranked),
                "file": os.path.basename(path), "rows": ranked[:30]}
    except Exception as exc:  # noqa
        return {"error": str(exc)}


def _strategies_list() -> List[dict]:
    """B2：已验证策略列表（data/strategies/*.py + 验证状态 JSON）。"""
    out = []
    if os.path.isdir(STRAT_DIR):
        for p in sorted(glob.glob(os.path.join(STRAT_DIR, "*.py"))):
            name = os.path.splitext(os.path.basename(p))[0]
            vfile = p + ".verify.json"
            verified, ok = False, None
            if os.path.exists(vfile):
                try:
                    with open(vfile, "r", encoding="utf-8") as f:
                        v = json.load(f)
                    verified, ok = True, v.get("合格")
                except ValueError:
                    pass
            out.append({"name": name, "verified": verified, "ok": ok})
    return out


# ================================================================ 执行面

LOG_Q: "queue.Queue" = queue.Queue()


def _spawn(args: List[str]):
    global PROC
    if STATE["running"]:
        return False, "已有任务在运行，请等输出区出现「✅ 完成」"
    STATE["running"] = True
    LOG_Q.put(f"\n$ python qf.py {' '.join(args)}\n")

    def worker():
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen([sys.executable, QF_PY] + args, cwd=ROOT,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env)
        for raw in iter(proc.stdout.readline, b""):
            LOG_Q.put(raw.decode("utf-8", errors="replace"))
        proc.wait()
        LOG_Q.put(f"\n[退出码 {proc.returncode}] "
                  f"{'✅ 完成' if proc.returncode == 0 else '❌ 失败'}\n")
        STATE["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return True, "已启动"


def _dispatch(action: str, p: dict) -> List[str]:
    if action == "demo":
        return ["demo"]
    if action == "gen-sample":
        return ["gen-sample"]
    if action == "init":
        return ["init"]
    if action == "doctor":
        return ["doctor", "--strategy", p.get("strategy", "ma_cross")]
    if action == "backtest":
        args = ["backtest", "--strategy", p.get("strategy", "ma_cross")]
        if p.get("pool"):
            args += ["--pool"]
        elif p.get("symbol"):
            args += ["--symbol", p["symbol"]]
        if p.get("cash"):
            args += ["--cash", str(p["cash"])]
        if p.get("real"):
            args += ["--data-dir", os.path.join(ROOT, "data", "kline_real")]
        return args
    if action == "sweep":
        return ["sweep", "--strategy", p.get("strategy", "ma_cross"),
                "--param", f"fast={p.get('fast', '3:15')}",
                "--param", f"slow={p.get('slow', '30:90')}",
                "--groups", str(p.get("groups", 30))]
    if action == "fetch-real":
        return ["fetch-real", "--symbol", p.get("symbol", "600519"),
                "--years", str(p.get("years", 3))]
    if action == "fetch-pool":
        syms = _pool_get()
        if not syms:
            raise ValueError("自选池为空——先保存池代码")
        return ["fetch-real", "--symbol", ",".join(syms),
                "--years", str(p.get("years", 3))]
    if action == "refresh-data":
        syms = _pool_get()
        if not syms:
            raise ValueError("自选池为空——先保存池代码")
        return ["fetch-real", "--symbol", ",".join(syms), "--years", "3"]
    raise ValueError(f"未知动作 {action}")


# ================================================================ HTTP

def make_server():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _nocache(self):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")

        def _html(self, s):
            b = s.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._nocache()
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _json(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._nocache()
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                from .console_page import PAGE_HTML
                opts_s = "".join(f"<option value='{c}'>{n}（{c}）</option>"
                                 for c, n in STRATEGIES)
                opts_y = "".join(f"<option>{s}</option>" for s in SYMBOLS if s)
                self._html(PAGE_HTML.replace("__OPT_S__", opts_s)
                           .replace("__OPT_Y__", opts_y))
            elif u.path == "/api/state":
                try:
                    while True:
                        LOG_Q.get_nowait()
                except queue.Empty:
                    pass
                STATE["log"] = STATE["log"][-300:]
                runs = _runs_list()
                latest = (_run_full(runs[0]["run_id"])
                          if runs and "error" not in runs[0] else None)
                self._json({"running": STATE["running"], "log": STATE["log"],
                            "latest": latest, "runs": runs,
                            "pool": _pool_get(),
                            "strategies": _strategies_list(),
                            "sweep": _sweep_latest()})
            elif u.path.startswith("/api/run/"):
                rid = u.path.split("/")[-1]
                self._json(_run_full(rid) or {"error": "not found"})
            elif u.path == "/api/compare":
                a = (q.get("a") or [""])[0]
                b = (q.get("b") or [""])[0]
                self._json({"a": _run_full(a), "b": _run_full(b)})
            elif u.path == "/api/sweep":
                self._json(_sweep_latest() or {"total": 0, "rows": []})
            elif u.path.startswith("/api/price/"):
                sym = u.path.split("/")[-1]
                self._json(_last_close(sym))
            elif u.path == "/api/pool":
                self._json({"symbols": _pool_get()})
            elif u.path == "/api/strategies":
                self._json({"strategies": _strategies_list()})
            elif u.path == "/api/open-report":
                files = glob.glob(os.path.join(OUT_DIR, "report_*.html"))
                if files:
                    webbrowser.open("file:///" + max(files, key=os.path.getmtime)
                                    .replace("\\", "/"))
                self._json({"ok": bool(files)})
            elif u.path == "/api/open-review":
                files = glob.glob(os.path.join(OUT_DIR, "review_*.md"))
                if files:
                    os.startfile(max(files, key=os.path.getmtime))
                self._json({"ok": bool(files)})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            u = urlparse(self.path)
            if u.path not in ("/api/run", "/api/pool", "/api/verify"):
                self._json({"error": "not found"}, 404)
                return
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            if u.path == "/api/pool":
                cnt = _pool_set(body.get("symbols", []))
                self._json({"ok": True, "count": cnt,
                            "msg": f"自选池已保存（{cnt} 只）"})
                return
            if u.path == "/api/verify":
                self._do_verify(body)
                return
            try:
                args = _dispatch(body.get("action", ""), body)
            except ValueError as exc:
                self._json({"ok": False, "msg": str(exc)}, 400)
                return
            ok, msg = _spawn(args)
            self._json({"ok": ok, "msg": msg})

        def _do_verify(self, body):
            """B2：用户策略沙箱三步校验（S50）——写文件→校验→留档。"""
            from .sandbox import verify_strategy, format_report
            from .sample_data import gen_sample_bars
            name = body.get("name", "")
            content = body.get("content", "")
            if not re.fullmatch(r"[\w\-]{1,32}", name or ""):
                self._json({"ok": False, "msg": "策略名只允许字母数字下划线连字符"}, 400)
                return
            os.makedirs(STRAT_DIR, exist_ok=True)
            path = os.path.join(STRAT_DIR, f"{name}.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            bars = gen_sample_bars()["600001"][:120]
            v = verify_strategy(path, bars, timeout=30)
            with open(path + ".verify.json", "w", encoding="utf-8") as f:
                json.dump({"合格": v["合格"], "steps": v["steps"]}, f,
                          ensure_ascii=False, indent=1)
            self._json({"ok": v["合格"], "name": name,
                        "report": format_report(v)})

    return ThreadingHTTPServer(("127.0.0.1", 0), H)


def main():
    srv = make_server()
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    if sys.stdout:
        print(f"QuantForge 控制台：{url}（Ctrl+C 退出）")
    webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa - pythonw 无控制台：异常落盘否则静默死亡
        import traceback
        err_dir = os.path.join(ROOT, "out")
        os.makedirs(err_dir, exist_ok=True)
        with open(os.path.join(err_dir, "console_err.log"), "a",
                  encoding="utf-8") as f:
            f.write("\n" + datetime.now().isoformat(sep=" ", timespec="seconds") + "\n")
            f.write(traceback.format_exc())
