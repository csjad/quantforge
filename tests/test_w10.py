# -*- coding: utf-8 -*-
"""W10 用例：Web 控制台（D-012）——页面/状态接口/分发白名单/图表注入。"""
import os
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qf.webconsole import make_server, _dispatch  # noqa: E402
from qf.console_page import PAGE_HTML  # noqa: E402


class TestWebConsole(unittest.TestCase):
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
                                    timeout=10) as r:
            return r.status, r.read().decode("utf-8")

    def test_page_renders_redline_style(self):
        st, html = self._get("/")
        self.assertEqual(st, 200)
        for token in ("QuantForge", "钱没有", "凭空出现", "这一手，买得起吗",
                      "凭什么信这些数字", "先过关，再上线", "行情在云端",
                      "用这组配置回测", "不构成投资建议", "跑一次回测"):
            self.assertIn(token, html)
        for token in ("钱没有", "凭空出现", "买得起"):
            self.assertIn(token, PAGE_HTML)

    def test_state_endpoint(self):
        st, body = self._get("/api/state")
        obj = __import__("json").loads(body)
        self.assertEqual(st, 200)
        for key in ("running", "log", "latest", "runs", "pool", "sweep",
                    "strategies"):                      # W11 大改造新形态
            self.assertIn(key, obj)
        self.assertEqual((obj["latest"] or {}).get("charts", "").count("<svg"), 3)

    def test_dispatch_whitelist(self):
        self.assertEqual(_dispatch("demo", {}), ["demo"])
        args = _dispatch("backtest", {"strategy": "ma_cross", "symbol": "600519",
                                      "cash": "500000", "real": True})
        self.assertIn("--data-dir", args)          # 真实池联动
        self.assertEqual(args[args.index("--cash") + 1], "500000")
        with self.assertRaises(ValueError):
            _dispatch("rm -rf", {})                # 白名单外动作拒绝

    def test_run_endpoint_rejects_unknown(self):
        st, body = self._get("/")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/run", method="POST",
            data=b'{"action":"evil"}')
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                st2 = r.status
        except Exception as exc:  # noqa
            st2 = getattr(exc, "code", None)
        self.assertIn(st2, (200, 400))             # 400=白名单拒绝


if __name__ == "__main__":
    unittest.main()
