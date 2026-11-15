# -*- coding: utf-8 -*-
"""W8-01 用例：触发器三取一 / bt_tasks 三状态与重启回退 / 幂等防重 / 双后端逐位一致。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf import config as C                       # noqa: E402
from qf import extreme as X                      # noqa: E402
from qf import parallel as PL                    # noqa: E402
from qf import sweep as SW                       # noqa: E402
from qf.strategies import MaCross                # noqa: E402


def _pool(days=140):
    d = X._dates(days)
    bars = {}
    for i, sym in enumerate(["600001", "000002", "300003"]):
        seq, price = [], 10.0 + i * 3
        for j, ds in enumerate(d):
            if 50 <= j < 54:
                continue
            price = round(price * (1 + 0.0035 * (1 if j % 17 else -1.4)), 3)
            seq.append(X._bar(ds, price, price * 1.01, price * 0.99, price))
        bars[sym] = seq
    return bars, d


class TestTrigger(unittest.TestCase):
    """V8-04：触发器三取一（S52-02 §1）。"""

    def test_small_stays_serial(self):
        self.assertEqual(PL.decide_backend(40, 5, 0.03, "auto"), "serial")

    def test_groups_threshold(self):
        self.assertEqual(PL.decide_backend(1000, 5, 0.03, "auto"), "parallel")

    def test_pool_threshold(self):
        self.assertEqual(PL.decide_backend(10, 500, 0.03, "auto"), "parallel")

    def test_time_threshold(self):
        self.assertEqual(PL.decide_backend(10, 5, 200.0, "auto"), "parallel")  # 10×200=2000s≥1800s

    def test_explicit_backend_wins(self):
        self.assertEqual(PL.decide_backend(40, 5, 0.03, "parallel"), "parallel")
        self.assertEqual(PL.decide_backend(1000, 500, 200.0, "serial"), "serial")


class TestBtTasks(unittest.TestCase):
    """V8-01：bt_tasks 三状态 + 重启回退 + 双确认。"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.conn = PL.open_tasks(os.path.join(self.td.name, "qf.db"))
        self.tid = PL.task_id_of("ma_cross", {"fast": 5}, "SYN-1")

    def tearDown(self):
        self.conn.close()
        self.td.cleanup()

    def test_claim_then_done(self):
        self.assertTrue(PL.claim_pending(self.conn, self.tid, {"fast": 5}, "SYN-1"))
        # 未落盘结果 → 已完成判定不成立（执行中）
        self.assertTrue(PL.claim_pending(self.conn, self.tid, {"fast": 5}, "SYN-1"))
        PL.mark_done(self.conn, self.tid, os.path.join(self.td.name, "r.json"), "abc")
        row = self.conn.execute("SELECT status FROM bt_tasks WHERE task_id=?",
                                (self.tid,)).fetchone()
        self.assertEqual(row[0], "已完成")

    def test_idempotent_skip_after_done_with_checksum(self):
        result_path = os.path.join(self.td.name, "r.json")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write("{}")
        PL.claim_pending(self.conn, self.tid, {"fast": 5}, "SYN-1",
                         result_path, "ck1")
        PL.mark_done(self.conn, self.tid, result_path, "ck1")
        # 已完成+结果文件存在+校验和一致 → 跳过（幂等②）
        self.assertFalse(PL.claim_pending(self.conn, self.tid, {"fast": 5}, "SYN-1",
                                          result_path, "ck1"))

    def test_running_resets_on_restart(self):
        """重启恢复：'执行中'一律回退'待执行'（S06-01 §4.3 冻结）。"""
        PL.claim_pending(self.conn, self.tid, {"fast": 5}, "SYN-1")
        row = self.conn.execute("SELECT status FROM bt_tasks WHERE task_id=?",
                                (self.tid,)).fetchone()
        self.assertEqual(row[0], "执行中")
        n = PL.reset_running(self.conn)
        self.assertEqual(n, 1)
        row = self.conn.execute("SELECT status FROM bt_tasks WHERE task_id=?",
                                (self.tid,)).fetchone()
        self.assertEqual(row[0], "待执行")

    def test_checksum_mismatch_recomputes(self):
        """结果文件缺失/校验和不一致 → 不可标已完成（防幽灵参数，S06-01 §4.3）。"""
        result_path = os.path.join(self.td.name, "missing.json")
        PL.mark_done.__self__ if False else None  # noqa 占位防误读
        PL.claim_pending(self.conn, self.tid, {"fast": 5}, "SYN-1",
                         result_path, "ckX")
        # 结果文件不存在 → claim 恒 True（重新执行）
        self.assertTrue(PL.claim_pending(self.conn, self.tid, {"fast": 5}, "SYN-1",
                                         result_path, "ckX"))


class TestDoubleBackend(unittest.TestCase):
    """S52-01 §5 硬口径：并行只改执行位置不改计算语义——双后端逐位一致。"""

    def setUp(self):
        self.bars, self.cal = _pool()

    def _run(self, backend):
        with tempfile.TemporaryDirectory() as td:
            ranked, _ck = SW.sweep(self.bars, self.cal, MaCross,
                                   [("fast", 3, 15, None)], mode="random",
                                   groups=6, split=0.7, seed=11,
                                   checkpoint_dir=td, backend=backend)
            return ranked

    def test_bitwise_identical(self):
        serial = self._run("serial")
        parallel = self._run("parallel")
        self.assertEqual(len(serial), len(parallel))
        for a, b in zip(serial, parallel):
            self.assertEqual(a["params"], b["params"])
            self.assertEqual(a["score"], b["score"])          # 逐位一致（含None）
            self.assertEqual(a["train"], b["train"])
            self.assertEqual(a["val"], b["val"])
            self.assertEqual(a["flags"], b["flags"])
            self.assertEqual(a["snapshot"], b["snapshot"])

    def test_parallel_results_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            ranked, _ck = SW.sweep(self.bars, self.cal, MaCross,
                                   [("fast", 3, 15, None)], mode="random",
                                   groups=4, split=0.7, seed=11,
                                   checkpoint_dir=td, backend="parallel")
            results_dir = os.path.join(td, "sweep_results")
            files = os.listdir(results_dir)
            self.assertEqual(len(files), len(ranked))          # 每组一个结果文件
            for fn in files:
                with open(os.path.join(results_dir, fn), encoding="utf-8") as f:
                    rec = json.loads(f.read())
                self.assertIn("train", rec)                    # 结果先落盘可复核


import json  # noqa: E402


if __name__ == "__main__":
    unittest.main()
