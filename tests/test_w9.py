# -*- coding: utf-8 -*-
"""W9 用例：S54 用户隔离越权防线（A5 条件落地）/ S55 远程算力协议（认领/回传/鉴权）。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf import extreme as X                      # noqa: E402
from qf import sweep as SW                       # noqa: E402
from qf import parallel as PL2                   # noqa: E402
from qf import users as U                        # noqa: E402
from qf.strategies import MaCross                # noqa: E402


class TestUsers(unittest.TestCase):
    """S54 越权防线（A5 条件：V9 越权测试提前到 V8/V9 准备——本测试即落点）。"""

    def test_default_scope_backward_compatible(self):
        s = U.user_scope(os.getcwd(), None)
        self.assertIsNone(s["user"])
        self.assertTrue(s["db_path"].endswith("qf.db"))

    def test_user_scope_paths(self):
        with tempfile.TemporaryDirectory() as td:
            s = U.user_scope(td, "alice")
            self.assertIn(os.path.join("users", "alice"), s["db_path"])
            self.assertIn(os.path.join("users", "alice"), s["out_dir"])

    def test_path_escape_rejected(self):
        for bad in ("a/../b", "a/b", "..", "a\\b", "x" * 40):
            with self.assertRaises(PermissionError, msg=f"应拒绝 {bad!r}"):
                U.user_scope(os.getcwd(), bad)

    def test_containment_assert(self):
        with self.assertRaises(PermissionError):
            U.assert_containment("C:/elsewhere/x", "C:/scope")

    def test_kline_dir_contained(self):
        with tempfile.TemporaryDirectory() as td:
            s = U.user_scope(td, "bob")
            d = U.kline_dir(s, "kline")
            self.assertTrue(d.startswith(os.path.abspath(s["data_dir"])))


class TestRemote(unittest.TestCase):
    """S55 协议：认领/回传/鉴权（V8-06 越权拦截）+ 三后端逐位一致。"""

    def setUp(self):
        self.bars, self.cal = _pool40()
        self.td = tempfile.TemporaryDirectory()
        self.conn = PL2.open_tasks(os.path.join(self.td.name, "qf.db"))

    def tearDown(self):
        self.conn.close()
        self.td.cleanup()

    def test_token_mismatch_rejected(self):
        """V8-06：token 不匹配 → 403 + 记录（越权拦截）。"""
        from qf.remote import TaskMaster
        m = TaskMaster("good-token", [], "ma_cross", "SN", 0.7, self.conn,
                       os.path.join(self.td.name, "res"))
        port = m.start()
        try:
            from urllib.request import urlopen
            from urllib.error import HTTPError
            with self.assertRaises(HTTPError) as cm:
                urlopen(f"http://127.0.0.1:{port}/claim?token=BAD&worker=1", timeout=5)
            self.assertEqual(cm.exception.code, 403)
            self.assertTrue(any("token不匹配" in d for d in m.denied))
        finally:
            m.stop()

    def test_remote_worker_end_to_end(self):
        """真实 HTTP 认领/回传闭环：worker 子进程跑完 2 组 → 结果落盘+bt_tasks 已完成。"""
        with tempfile.TemporaryDirectory() as td:
            ranked, _ck = SW.sweep(self.bars, self.cal, MaCross,
                                   [("fast", 3, 12, None)], mode="random",
                                   groups=2, split=0.7, seed=5,
                                   checkpoint_dir=td, backend="remote",
                                   tasks_db=os.path.join(self.td.name, "qf.db"))
            self.assertEqual(len(ranked), 2)
            rows = self.conn.execute(
                "SELECT task_id, status FROM bt_tasks WHERE status='已完成'"
            ).fetchall()
            self.assertEqual(len(rows), 2)                 # 双确认置已完成
            results_dir = os.path.join(td, "sweep_results")
            self.assertEqual(len(os.listdir(results_dir)), 2)

    def test_three_backends_bitwise_identical(self):
        """serial / parallel / remote 三后端：同一 seed → records 逐位一致。"""
        outs = {}
        for backend in ("serial", "parallel", "remote"):
            td = tempfile.mkdtemp()
            ranked, _ck = SW.sweep(self.bars, self.cal, MaCross,
                                   [("fast", 3, 12, None)], mode="random",
                                   groups=3, split=0.7, seed=9,
                                   checkpoint_dir=td, backend=backend,
                                   tasks_db=os.path.join(td, "qf.db"))
            outs[backend] = [(r["params"], r["score"], r["train"], r["val"],
                              r["flags"], r["snapshot"]) for r in ranked]
        self.assertEqual(outs["serial"], outs["parallel"])
        self.assertEqual(outs["serial"], outs["remote"])


import json  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402  # 预热spawn环境


def _pool40():
    d = X._dates(40)
    bars = {}
    for i, sym in enumerate(["600001", "000002"]):
        seq, price = [], 10.0 + i * 3
        for j, ds in enumerate(d):
            price = round(price * (1 + 0.0035 * (1 if j % 9 else -1.2)), 3)
            seq.append(X._bar(ds, price, price * 1.01, price * 0.99, price))
        bars[sym] = seq
    return bars, d


# parallel 远端测试前需预热一次（Windows spawn 下首次 Pool 较慢，预热可稳定计时）
if __name__ == "__main__":
    unittest.main()
