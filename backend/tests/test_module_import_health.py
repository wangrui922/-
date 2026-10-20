"""全模块 import 体检（防"孤儿模块"回归）。

事故背景（2026-10-20 发现）：
    仓库里躺着 3 个模块 —— ``app/services/subscription_service.py``、
    ``app/api/subscription.py``、``app/core/entitlement.py`` —— 它们
    **import 即崩**（``ImportError: cannot import name 'Order' from
    'app.models.subscription'``）。

    根因不是写错，而是**没被删干净**：commit 620f0ba 用 billing 域
    （``Plan``/``Subscription``/``PaymentTransaction`` 三表 +
    ``services/billing_service.py`` + ``api/billing.py``）取代了老的
    subscription 域，把老 ``Order`` 模型与老 ``Subscription`` 字段
    （``plan_code``/``started_at``/``expires_at``）删掉了，但老
    service/router/schema 没同步删。620f0ba 同时把
    ``api_router.include_router(subscription.router)`` 摘掉，才让服务
    侥幸起得来 —— 一旦谁把那行 include 加回去，**整个后端启动即崩**。

    为什么 1586 个测试全绿也没抓到？因为没人 import 它们，且它们没注册路由。
    这类缺陷是"沉默"的，只能靠**遍历 import** 才能发现。

本测试锁死：``app`` 包下**每一个**模块都必须能成功 import。
任何新增文件若引用了不存在的模型/符号，本测试立刻红。

口径备注：本文件**不设** ``DATABASE_URL``（conftest 基线闸门，走会话级
共享内存 SQLite）。
"""

from __future__ import annotations

import importlib
import pkgutil

import app


def _all_app_modules() -> list[str]:
    """枚举 app 包下所有子模块名。"""
    return sorted(m.name for m in pkgutil.walk_packages(app.__path__, prefix="app."))


def test_app_has_modules_to_scan():
    """守卫自身有效：必须真的扫到了一批模块（防止 walk_packages 路径写错导致空扫假绿）。"""
    mods = _all_app_modules()
    assert len(mods) > 50, f"只扫到 {len(mods)} 个模块，扫描路径可能不对"


def test_every_app_module_is_importable():
    """app 包下每个模块都必须能 import —— 孤儿模块在此暴露。"""
    failures: list[str] = []
    for name in _all_app_modules():
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - 任何异常都要报出来
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failures, (
        "以下模块 import 失败（多半是引用了已删除的模型/符号，属于孤儿模块）：\n  "
        + "\n  ".join(failures)
    )


def test_no_orphan_subscription_module():
    """老 subscription 域已被 billing 域取代，孤儿文件不得复活。

    这条是"显式墓碑"：万一有人把 ``app/services/subscription_service.py``
    从 git 历史里捞回来（或新建同名文件），本测试会明确报出来，
    而不是等某天有人 include 它时把服务搞崩。
    """
    banned = (
        "app.services.subscription_service",
        "app.api.subscription",
        "app.core.entitlement",
        "app.schemas.subscription",
    )
    present = [m for m in banned if m in _all_app_modules()]
    assert not present, (
        f"老 subscription 域孤儿模块复活了：{present}。"
        "该域已由 billing 域（app/services/billing_service.py + app/api/billing.py）取代。"
    )
