# -*- coding: utf-8 -*-
"""qf.cli — 离线五件事命令行（S44 冻结口径的 W1 落地）

五件事（S44）：启动服务/导入样例数据/跑一次回测/生成报告/查看结果——
W1 形态（D-008）下"启动服务"映射为 init 自检（CLI 无端口，显式说明，
呼应 C-039：诚实声明不含在线拉取）。
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid

from . import config as C
from . import sample_data
from .engine import BacktestEngine
from .metrics import compute_metrics
from .report import gen_report
from .storage import RunStore, load_bars_csv
from .strategies import STRATEGY_REGISTRY

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, "data")
KLINE_DIR = os.path.join(DATA_DIR, "kline")
DB_PATH = os.path.join(DATA_DIR, "qf.db")
OUT_DIR = os.path.join(ROOT, "out")


def cmd_init(_args):
    """首次启动自检五项（S48 冻结：逐条自检并**打印结果**——使用者不看日志），
    失败即停止并给出修复提示。CLI 形态无端口项，显式说明（C-039 口径）。"""
    checks = []
    # ① 目录可写
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        probe = os.path.join(DATA_DIR, ".probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        checks.append(("目录可写", True, DATA_DIR))
    except OSError as e:
        checks.append(("目录可写", False, str(e)))
    # ② 端口空闲 —— W1 为 CLI 形态，不占用端口（诚实声明，不做假检查）
    checks.append(("端口空闲", True, "CLI 形态不占用端口（部署形态按 S18 检查六端口）"))
    # ③ 样例数据存在
    csvs = os.listdir(KLINE_DIR) if os.path.isdir(KLINE_DIR) else []
    checks.append(("样例数据存在", bool(csvs),
                   f"{KLINE_DIR}（{len(csvs)} 个分区文件）" if csvs
                   else "未找到——运行 python qf.py gen-sample 生成"))
    # ④ 配置完整（冻结基准常量可导入）
    try:
        _ = (C.INITIAL_CASH, C.SLIPPAGE, C.COMMISSION_RATE)
        checks.append(("配置完整", True, "冻结基准常量加载正常"))
    except Exception as e:  # noqa
        checks.append(("配置完整", False, str(e)))
    # ⑤ 日志可写
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        checks.append(("日志可写", True, OUT_DIR))
    except OSError as e:
        checks.append(("日志可写", False, str(e)))

    print("== 首次启动自检（五项，失败即停止）==")
    all_ok = True
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        all_ok &= ok
    if not all_ok:
        print("自检未通过，按 FAIL 项修复后重试。")
        sys.exit(1)
    print("自检全部通过。")
    print("说明：离线包不含在线拉取（C-039）；数据通道=本地文件导入。")


def cmd_gen_sample(_args):
    calendar = sample_data.gen_calendar()
    bars = sample_data.gen_sample_bars()
    counts = sample_data.write_sample_csv(bars, KLINE_DIR)
    sample_data.write_license_note(KLINE_DIR)
    total = sum(counts.values())
    print(f"样例数据生成完毕：{len(bars)} 标的 × {len(calendar)} 交易日历，"
          f"共 {total} 根K线 → {KLINE_DIR}")
    print("授权说明已写入 data/kline/README_数据来源与授权.md（A2 条件项）")


def _run_backtest(args) -> str:
    if args.strategy not in STRATEGY_REGISTRY:
        raise SystemExit(f"未知策略 {args.strategy}；可选：{list(STRATEGY_REGISTRY)}")
    data_dir = getattr(args, "data_dir", None) or KLINE_DIR
    scope = None
    if getattr(args, "user", None):
        from .users import user_scope
        scope = user_scope(ROOT, args.user)
        os.makedirs(scope["out_dir"], exist_ok=True)
    bars = load_bars_csv(data_dir)
    if not bars:
        raise SystemExit("未找到样例数据——先运行 python qf.py gen-sample")
    if getattr(args, "pool", False):
        pool_file = os.path.join(ROOT, "data", "pool.json")
        try:
            pool = json.load(open(pool_file, encoding="utf-8")).get("symbols", [])
        except (OSError, ValueError):
            pool = []
        if not pool:
            raise SystemExit("自选池为空——在控制台保存池代码后再用 --pool")
        bars = {s: seq for s, seq in bars.items() if s in pool}
        if not bars:
            raise SystemExit(f"自选池标的 {pool} 在数据目录中不存在——先 fetch-real")
    if args.symbol:
        bars = {s: seq for s, seq in bars.items() if s == args.symbol}
        if not bars:
            raise SystemExit(f"标的 {args.symbol} 不在样例池")
    # 全局交易日历 = 各标的日期并集（停牌=单标的缺日）
    calendar = sorted({b.date for seq in bars.values() for b in seq})
    strat_cls = STRATEGY_REGISTRY[args.strategy]
    kwargs = {}
    if args.fast:
        kwargs["fast"] = args.fast
    if args.slow:
        kwargs["slow"] = args.slow
    strategy = strat_cls(**kwargs)
    seed_note = "gen-sample 种子=20261113（合成样例）"
    cash = args.cash if getattr(args, "cash", None) else C.INITIAL_CASH
    engine = BacktestEngine(bars, strategy, calendar, seed_note=seed_note,
                            initial_cash=cash)
    engine.run()

    run_id = f"{args.strategy}_{uuid.uuid4().hex[:8]}"
    pool_note = (f"单标的 {args.symbol}" if args.symbol
                 else f"样例池 {len(bars)} 只（合成数据，授权见 data/kline/README）")
    mr = compute_metrics(engine.equity_curve, engine.trades, initial_cash=cash)
    config = {
        "初始资金": cash, "佣金率": C.COMMISSION_RATE,
        "印花税率": C.STAMP_RATE, "过户费率": C.TRANSFER_RATE,
        "滑点": C.SLIPPAGE, "仓位上限单标的": C.POSITION_CAP_SINGLE,
        "熔断": C.DAILY_LOSS_HALT, "预热窗口": engine.warmup,
    }
    store = RunStore(scope["db_path"] if scope else DB_PATH)
    store.save_full(run_id, args.strategy, kwargs, config, engine,
                    mr.values, mr.notes, mr.short_period)
    is_real = "kline_real" in os.path.abspath(data_dir)
    snapshot_id = f"{'REAL' if is_real else 'SYN'}-{C.INITIAL_CASH:.0f}-{len(bars)}sym"
    if is_real:
        pool_note = f"真实行情池 {len(bars)} 只（{os.path.basename(data_dir)}，授权见目录README）"
    path = gen_report(engine, run_id, args.strategy, kwargs, mr,
                      snapshot_id=snapshot_id,
                      out_dir=(scope["out_dir"] if scope else OUT_DIR),
                      symbol_pool_note=pool_note)
    from .report_html import gen_html_report
    path_html = gen_html_report(engine, run_id, args.strategy, kwargs, mr,
                                snapshot_id=f"SYN-{C.INITIAL_CASH}-{len(bars)}sym",
                                out_dir=OUT_DIR, symbol_pool_note=pool_note)
    print(f"回测完成：run_id={run_id}")
    print(f"  成交 {engine.filled_count} 笔 / 拒单 {engine.rejected_count} / "
          f"拦截 {engine.blocked_signals} / 守恒失败 {engine.conservation_failures} / "
          f"费用恒等失败 {engine.fee_check_failures}")
    print(f"  报告：{path}")
    print(f"  HTML ：{path_html}（内嵌SVG，双击离线可看）")
    # S11 复盘 Agent + S13 质量告警（回测管线固定尾巴，缺一即未完成）
    from .review import run_review, save_review
    from .alerts import evaluate_backtest, notify_lines
    rr = run_review(engine, mr)
    rpath = save_review(rr, run_id, scope["out_dir"] if scope else OUT_DIR)
    print(f"  复盘：{rpath}（规则式五判据，综合风险等级 {rr.level}）")
    ae = evaluate_backtest(engine, mr, data_dir=data_dir, run_id=run_id)
    for line in notify_lines(ae):
        print(f"  [告警] {line}")
    return run_id


def cmd_backtest(args):
    _run_backtest(args)


def cmd_show(args):
    db = DB_PATH
    if getattr(args, "user", None):
        from .users import user_scope
        db = user_scope(ROOT, args.user)["db_path"]
    store = RunStore(db)
    runs = store.list_runs()
    if not runs:
        print("尚无回测记录——先运行 python qf.py backtest")
        return
    run_id = args.run_id or runs[-1][0]
    data = store.load_run(run_id)
    if not data:
        raise SystemExit(f"run_id {run_id} 不存在")
    import json
    metrics = json.loads(data["metrics"])
    print(f"== 回测结果 {run_id} ==")
    print(f"策略：{data['strategy']}｜配置：{data['config']}")
    print("十二项指标：")
    for k, v in metrics.items():
        print(f"  {k}: {'空值(无意义)' if v is None else v}")
    eq = data["equity"]
    if eq:
        print(f"权益：初始 {C.INITIAL_CASH:,.2f} → 期末 {eq[-1][2]:,.2f} "
              f"（{eq[-1][1]}）")


def cmd_doctor(args):
    """未来函数三重检测（S03-01 §10）：静态扫描/末日陷阱/双跑比对。"""
    from .doctor import run_doctor, format_report
    from .strategies import STRATEGY_REGISTRY as REG
    if args.strategy not in REG:
        raise SystemExit(f"未知策略 {args.strategy}；可选：{list(REG)}")
    bars = load_bars_csv(KLINE_DIR)
    if not bars:
        raise SystemExit("未找到样例数据——先运行 python qf.py gen-sample")
    if args.symbol:
        bars = {s: seq for s, seq in bars.items() if s == args.symbol}
    calendar = sorted({b.date for seq in bars.values() for b in seq})
    v = run_doctor(bars, lambda: REG[args.strategy](), calendar)
    print(format_report(v))
    sys.exit(0 if v["合格"] else 1)


def cmd_sweep(args):
    """参数遍历寻优（S06/D-005）：随机为主/网格对照 + 断点续跑 + 过拟合标记。"""
    from . import sweep as SW
    from .storage import load_bars_csv
    from .strategies import STRATEGY_REGISTRY as REG
    if args.strategy not in REG:
        raise SystemExit(f"未知策略 {args.strategy}；可选：{list(REG)}")
    specs = [SW.parse_param(p) for p in args.param]
    if not specs:
        raise SystemExit("至少一个 --param name=lo:hi[:step]")
    bars = load_bars_csv(KLINE_DIR)
    if not bars:
        raise SystemExit("未找到样例数据——先运行 python qf.py gen-sample")
    calendar = sorted({b.date for seq in bars.values() for b in seq})
    ranked, ck = SW.sweep(bars, calendar, REG[args.strategy], specs,
                          mode=args.mode, groups=args.groups, split=args.split,
                          seed=args.seed,
                          checkpoint_dir=(user_scope(ROOT, args.user)["out_dir"]
                                          if getattr(args, "user", None) else OUT_DIR),
                          backend=args.backend,
                          tasks_db=(user_scope(ROOT, args.user)["db_path"]
                                    if getattr(args, "user", None) else None))
    print(f"执行后端：{getattr(SW, '_last_backend', 'serial')}（触发器 auto 三取一）")
    print(SW.format_top(ranked, top=args.top))
    print(f"checkpoint：{ck}（中断后重跑同命令即断点续跑）")


def cmd_fetch_real(args):
    """真实行情拉取（D-010 可选采集层）：akshare→CSV→授权说明，与样例池物理隔离。"""
    from .realdata import fetch_real, REAL_DIR_DEFAULT
    symbols = [x.strip() for x in args.symbol.split(",") if x.strip()]
    counts, out_dir = fetch_real(symbols, args.years,
                                 out_dir=args.out_dir or REAL_DIR_DEFAULT)
    total = sum(counts.values())
    print(f"真实行情拉取完成：{len(symbols)} 标的，共 {total} 根K线 → {out_dir}")
    print("授权说明已写入目录 README_数据来源与授权.md（个人研究专用，禁止二次分发/售卖）")


def cmd_verify_strategy(args):
    """策略沙箱三步校验（S50）：静态→沙箱试跑→小样本回测。"""
    from .sandbox import verify_strategy, format_report
    from .sample_data import gen_sample_bars
    if not os.path.exists(args.path):
        raise SystemExit(f"策略文件不存在：{args.path}")
    bars = gen_sample_bars()["600001"][:120]
    v = verify_strategy(args.path, bars, timeout=args.timeout)
    print(format_report(v))
    sys.exit(0 if v["合格"] else 1)


def cmd_demo(_args):
    """一键五件事：生成样例→自检→导入→回测→报告→查看（断网可用）。
    次序说明：样例数据先生成，自检五项才能全绿（首跑机器无数据会卡第一步，
    正是 S44 冻结的"最常缺样例数据"场景——demo 内置自愈）。"""
    cmd_gen_sample(_args)
    print()
    cmd_init(_args)
    print()
    run_id = _run_backtest(argparse.Namespace(strategy="ma_cross", symbol="600001",
                                              fast=None, slow=None, cash=None,
                                              data_dir=None))
    print()
    cmd_show(argparse.Namespace(run_id=run_id))
    print()
    print("五件事全部离线完成（目标：断网新机器 30 分钟内跑出首份报告——"
          "本机实测秒级）。")


def main(argv=None):
    p = argparse.ArgumentParser(prog="qf", description="QuantForge W1 离线回测引擎")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="首次启动自检五项").set_defaults(func=cmd_init)
    sub.add_parser("gen-sample", help="生成合成样例数据（自制，合规）").set_defaults(
        func=cmd_gen_sample)
    bt = sub.add_parser("backtest", help="跑一次回测")
    bt.add_argument("--strategy", default="ma_cross",
                    choices=sorted(STRATEGY_REGISTRY))
    bt.add_argument("--symbol", default=None, help="单标的（默认整个样例池）")
    bt.add_argument("--fast", type=int, default=None)
    bt.add_argument("--slow", type=int, default=None)
    bt.add_argument("--data-dir", default=None,
                    help="K线目录（默认样例池 data/kline；真实池 data/kline_real）")
    bt.add_argument("--cash", type=float, default=None,
                    help="初始资金（冻结默认100000；高股价标的可调高）")
    bt.add_argument("--user", default=None,
                    help="用户作用域（V9：data/users/<u>/ 与 out/users/<u>/ 隔离）")
    bt.add_argument("--pool", action="store_true",
                    help="回测自选池全部标的（data/pool.json，B1）")
    bt.set_defaults(func=cmd_backtest)
    sh = sub.add_parser("show", help="查看回测结果")
    sh.add_argument("--run-id", default=None)
    sh.add_argument("--user", default=None)
    sh.set_defaults(func=cmd_show)
    dc = sub.add_parser("doctor", help="未来函数三重检测（静态/末日陷阱/双跑）")
    dc.add_argument("--strategy", default="ma_cross",
                    choices=sorted(STRATEGY_REGISTRY))
    dc.add_argument("--symbol", default=None)
    dc.set_defaults(func=cmd_doctor)
    sw = sub.add_parser("sweep", help="参数遍历寻优（随机/网格+断点续跑+过拟合标记）")
    sw.add_argument("--strategy", default="ma_cross", choices=sorted(STRATEGY_REGISTRY))
    sw.add_argument("--param", action="append", required=True,
                    help="参数区间 name=lo:hi[:step]，可多次")
    sw.add_argument("--mode", default="random", choices=["random", "grid"])
    sw.add_argument("--groups", type=int, default=40)
    sw.add_argument("--split", type=float, default=0.7, help="训练/验证切分比例")
    sw.add_argument("--seed", type=int, default=20261113)
    sw.add_argument("--top", type=int, default=10)
    sw.add_argument("--backend", default="auto",
                    choices=["auto", "serial", "parallel", "remote"],
                    help="执行后端（auto=触发器三取一；remote=W8.1 显式启用）")
    sw.add_argument("--user", default=None, help="用户作用域（V9 隔离）")
    sw.set_defaults(func=cmd_sweep)
    fr = sub.add_parser("fetch-real", help="拉取真实行情（akshare，可选层，禁入售卖包）")
    fr.add_argument("--symbol", required=True, help="逗号分隔，如 600519,000001")
    fr.add_argument("--years", type=int, default=3)
    fr.add_argument("--out-dir", default=None)
    fr.set_defaults(func=cmd_fetch_real)
    vs = sub.add_parser("verify-strategy", help="策略沙箱三步校验（S50/S05四道边界）")
    vs.add_argument("--path", required=True, help="用户策略 .py（须定义 Strategy 类）")
    vs.add_argument("--timeout", type=int, default=30)
    vs.set_defaults(func=cmd_verify_strategy)
    sub.add_parser("gate", help="一键质量门禁（测试+三重检测+守恒+性能+产物）").set_defaults(
        func=lambda a: sys.exit(0 if __import__("qf.gate", fromlist=["run_gate"]).run_gate() else 1))
    sub.add_parser("demo", help="一键五件事（断网可用）").set_defaults(func=cmd_demo)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
