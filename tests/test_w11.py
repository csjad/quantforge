# -*- coding: utf-8 -*-
"""W11 大改造用例：A1 runs列表/run详情 / A3 对比 / A2 sweep / B1 池 / B2 沙箱接入。"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf.webconsole import (_dispatch, _pool_get, _pool_set,  # noqa: E402
                           _run_full, _runs_list, _yearly_monthly, make_server)
from qf.strategies import MaCross  # noqa: E402


class TestHelpers(unittest.TestCase):
    def test_yearly_monthly_real_dates(self):
        eq = [("2024-06-20", 100.0, 0.0), ("2024-12-31", 110.0, 0.0),
              ("2025-01-02", 110.0, 0.0), ("2025-06-30", 121.0, 0.0)]
        yrets, mrets = _yearly_monthly(eq)
        self.assertEqual(yrets[0]["year"], "2024")
        self.assertAlmostEqual(yrets[0]["ret"], 0.10, places=6)
        self.assertAlmostEqual(yrets[1]["ret"], 0.10, places=6)
        self.assertTrue(any(m["month"] == "2024-12" for m in mrets))

    def test_pool_store_whitelist(self):
        old = _pool_get()
        try:
            _pool_set(["600519", "000001", "abc", "60051", "600519"])
            got = _pool_get()
            self.assertEqual(got, ["600519", "000001"])   # 非法/重复剔除
        finally:
            _pool_set(old)

    def test_pool_escape_rejected(self):
        """池为空时 fetch-pool 拒绝（与测试顺序/真实池状态无关的确定性用例）。"""
        old = _pool_get()
        try:
            _pool_set([])
            with self.assertRaises(ValueError):
                _dispatch("fetch-pool", {})
        finally:
            _pool_set(old)


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = make_server()
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}",
                                    timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_state_has_runs_and_pool(self):
        s = self._get("/api/state")
        self.assertIn("runs", s)
        self.assertIn("pool", s)
        self.assertIn("sweep", s)
        self.assertIn("strategies", s)
        self.assertIn("latest", s)

    def test_run_detail_structure(self):
        runs = self._get("/api/state")["runs"]
        self.assertTrue(runs, msg="库中无回测记录——先跑一次 demo")
        rid = runs[0]["run_id"]
        d = self._get(f"/api/run/{rid}")
        self.assertIn("yearly", d)
        self.assertIn("monthly", d)
        self.assertIn("charts", d)
        self.assertEqual(d["charts"].count("<svg"), 3)
        self.assertNotIn("t0 →", d["charts"])          # 伪日期已根除

    def test_compare_pair(self):
        runs = self._get("/api/state")["runs"]
        if len(runs) < 2:
            self.skipTest("不足两条回测")
        a, b = runs[0]["run_id"], runs[1]["run_id"]
        d = self._get(f"/api/compare?a={a}&b={b}")
        self.assertIn("a", d)
        self.assertIn("b", d)

    def test_dispatch_pool_action(self):
        old = _pool_get()
        try:
            _pool_set(["600519"])
            args = _dispatch("backtest", {"pool": True})
            self.assertIn("--pool", args)
            args2 = _dispatch("fetch-pool", {})
            self.assertIn("fetch-real", args2)
            self.assertIn("600519", " ".join(args2))
        finally:
            _pool_set(old)

    def test_verify_clean_strategy_via_api(self):
        code = '''
import math

class Strategy:
    name = "api_test"
    warmup_windows = [5]

    def attach(self, engine):
        self.engine = engine
        self.local = {b.date: i for i, b in enumerate(engine.bars["S"])}

    def _li(self, t):
        return self.local.get(self.engine.calendar[t])

    def on_bar(self, t):
        li = self._li(t)
        if li is None or li < 5:
            return []
        seq = self.engine.bars["S"]
        closes = [b.close for b in seq]
        m5 = sum(closes[li - 5:li]) / 5
        if closes[li] > m5:
            return [("BUY", "S", self.engine.equity_now() * 0.30)]
        if self.engine._pos("S").held:
            return [("SELL", "S", -1)]
        return []
'''
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/verify", method="POST",
            data=json.dumps({"name": "api_test", "content": code}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode("utf-8"))
        self.assertTrue(d["ok"], msg=d.get("report", ""))
        self.assertIn("三步", d["report"])

    def test_verify_rejects_bad_name(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/verify", method="POST",
            data=json.dumps({"name": "../evil", "content": "x"}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
                self.assertFalse(d["ok"])
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)


if __name__ == "__main__":
    unittest.main()
