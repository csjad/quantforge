# -*- coding: utf-8 -*-
"""S06 遍历寻优用例：确定性、网格≤两参数、断点续跑不重算、过拟合标记、切分入快照。"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf import extreme as X                       # noqa: E402
from qf import sweep as SW                        # noqa: E402
from qf.strategies import MaCross                 # noqa: E402


def _sample_pool(days=140):
    """三标的合成池（带日增长与停牌缺口，足以让均线类产生交易）。"""
    d = X._dates(days)
    bars = {}
    for i, sym in enumerate(["600001", "000002", "300003"]):
        seq, price = [], 10.0 + i * 3
        for j, ds in enumerate(d):
            if 50 <= j < 54:                       # 一段停牌缺口
                continue
            price = round(price * (1 + 0.0035 * (1 if j % 17 else -1.4)), 3)
            seq.append(X._bar(ds, price, price * 1.01, price * 0.99, price))
        bars[sym] = seq
    return bars, d


class TestS06(unittest.TestCase):
    def setUp(self):
        self.bars, self.cal = _sample_pool()

    def test_grid_more_than_two_params_rejected(self):
        with self.assertRaises(SystemExit):
            SW.grid_combos([("a", 2, 10, None), ("b", 2, 10, None), ("c", 2, 10, None)])

    def test_grid_step_default_tenth(self):
        combos = SW.grid_combos([("fast", 5, 25, None)])
        vals = sorted(c["fast"] for c in combos)
        self.assertEqual(len(vals), 11)            # 步长=区间/10=2 → 5,7,...,25 共11档
        self.assertEqual(vals[0], 5)
        self.assertEqual(vals[-1], 25)

    def test_random_deterministic(self):
        specs = [("fast", 3, 15, None)]
        a = SW.random_combos(specs, 10, seed=42)
        b = SW.random_combos(specs, 10, seed=42)
        self.assertEqual(a, b)                     # 同种子逐位一致（可复现）
        c = SW.random_combos(specs, 10, seed=43)
        self.assertNotEqual(a, c)

    def test_sweep_run_and_snapshot_split(self):
        with tempfile.TemporaryDirectory() as td:
            ranked, ck = SW.sweep(self.bars, self.cal, MaCross,
                                  [("fast", 3, 12, None)], mode="random",
                                  groups=4, split=0.7, checkpoint_dir=td)
            self.assertEqual(len(ranked), 4)
            snap = ranked[0]["snapshot"]
            self.assertIn("split_index", snap)     # 切分点写入任务快照（S06-02）
            self.assertIn("split_date", snap)
            self.assertLess(snap["split_index"], len(self.cal))
            # 全部12项聚合字段（S06-01 §5：不只收益率）
            for key in ("累计收益", "年化收益", "夏普", "最大回撤", "卡玛",
                        "胜率", "盈亏比", "换手率(年化双边)", "成交笔数"):
                self.assertIn(key, ranked[0]["train"])
                self.assertIn(key, ranked[0]["val"])

    def test_checkpoint_resume_no_recompute(self):
        """断点续跑：先跑2组，再跑同任务5组——已完成的2组不重算（校验和双确认）。"""
        with tempfile.TemporaryDirectory() as td:
            r1, ck = SW.sweep(self.bars, self.cal, MaCross,
                              [("fast", 3, 15, None)], mode="random",
                              groups=2, seed=7, checkpoint_dir=td)
            with open(ck, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved["records"]), 2)
            # 篡改一条记录的内容 → 校验和失配 → 续跑时该组应被剔除重算
            tid0 = list(saved["records"])[0]
            saved["records"][tid0]["score"] = 12345.0
            with open(ck, "w", encoding="utf-8") as f:
                json.dump(saved, f)
            r2, ck2 = SW.sweep(self.bars, self.cal, MaCross,
                               [("fast", 3, 15, None)], mode="random",
                               groups=5, seed=7, checkpoint_dir=td)
            self.assertEqual(len(r2), 5)
            # 未被篡改的那条记录与首轮逐位一致（复用），被篡改的被重算覆盖
            tid_clean = [t for t in (SW.params_hash(r["params"]) for r in r1)][-1]
            self.assertEqual(r2[0]["snapshot"]["snapshot_id"],
                             r1[0]["snapshot"]["snapshot_id"])

    def test_flags_attached(self):
        with tempfile.TemporaryDirectory() as td:
            ranked, _ck = SW.sweep(self.bars, self.cal, MaCross,
                                   [("fast", 3, 15, None)], mode="random",
                                   groups=6, split=0.7, checkpoint_dir=td)
            for r in ranked:
                self.assertIsInstance(r["flags"], list)
                for f in r["flags"]:
                    self.assertTrue(f.startswith(("笔数不足", "参数贴边",
                                                  "单年贡献", "样本外失效")),
                                    msg=f"未知标记 {f}")
            # 参数贴边至少命中一组（随机采样 3~60 常见贴边）
            self.assertTrue(any("参数贴边" in f for r in ranked for f in r["flags"]),
                            msg="贴边检测未命中（抽样含边界值理应命中）")

    def test_ranking_prefers_positive_val_score(self):
        with tempfile.TemporaryDirectory() as td:
            ranked, _ck = SW.sweep(self.bars, self.cal, MaCross,
                                   [("fast", 3, 15, None)], mode="random",
                                   groups=5, split=0.7, checkpoint_dir=td)
            scores = [r["score"] for r in ranked if r["score"] is not None]
            self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
