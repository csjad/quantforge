# -*- coding: utf-8 -*-
"""qf.users — V9 多用户隔离（S51 乙路线：数据层租户隔离的 W1.5 实现）

冻结口径（ADR D-011）：
  - **W1.5 隔离形态=按用户分文件**（user-scoped 路径 + 每用户独立 RunStore），
    数据层"租户字段"（S51 技术前提）为部署形态 PG 口径——分文件隔离更彻底
    且零 schema 变更（兼容三条）；
  - 未指定 --user → 全部走历史默认路径（**接口只增不改**：老用法零影响）；
  - **越权防线（A5 条件落地，S51：V9 越权测试提前到 V8 准备）**：
    ①用户名白名单正则（^[\w\-]{1,32}$，拒绝路径分隔符/..）；
    ②路径包含性断言（resolve 后必须落在该用户作用域之下）；
    ③跨用户读写 → PermissionError（TC-USER-003）。
"""
from __future__ import annotations

import os
import re
from typing import Optional

USER_NAME_RE = re.compile(r"^[\w\-]{1,32}$")


def user_scope(root: str, user: Optional[str]) -> dict:
    """解析用户作用域路径（data/out/db 全部收拢）。
    user=None → 历史默认路径（兼容只增不改）。"""
    root = os.path.abspath(root)
    if user is None:
        return {
            "user": None,
            "data_dir": os.path.join(root, "data"),
            "out_dir": os.path.join(root, "out"),
            "db_path": os.path.join(root, "data", "qf.db"),
        }
    if not USER_NAME_RE.match(user):
        raise PermissionError(f"非法用户名：{user!r}（只允许字母数字下划线连字符，"
                              f"≤32位——路径逃逸防线）")
    base = os.path.join(root, "data", "users", user)
    out_base = os.path.join(root, "out", "users", user)
    scope = {
        "user": user,
        "data_dir": os.path.join(base),               # kline/kline_real 放其下
        "out_dir": out_base,
        "db_path": os.path.join(base, "qf.db"),
    }
    for k, p in scope.items():
        if k != "user":
            assert_containment(p, base if k != "out_dir" else out_base)
    return scope


def assert_containment(path: str, base: str):
    """路径包含性断言（越权防线②）：resolve 后必须落在 base 之下。"""
    p = os.path.abspath(path)
    b = os.path.abspath(base)
    if not (p == b or p.startswith(b + os.sep)):
        raise PermissionError(f"路径越权：{p} 不在作用域 {b} 之下")


def kline_dir(scope: dict, kind: str = "kline") -> str:
    """用户作用域下的K线目录（kline/kline_real）。"""
    d = os.path.join(scope["data_dir"], kind)
    assert_containment(d, scope["data_dir"])
    return d


def results_dir(scope: dict) -> str:
    d = os.path.join(scope["out_dir"], "sweep_results")
    assert_containment(d, scope["out_dir"])
    return d
