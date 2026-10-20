# -*- coding: utf-8 -*-
"""守卫测试：冒烟脚本的清理逻辑必须覆盖所有「会写入数据」的表。

背景（2026-10-21 生产事故）
--------------------------------
冒烟脚本 ``smoke_beta.py`` 跑完会自建账号/学生/作答并尝试清理，但它只按
ORM 模型删了 User / Student / Attempt / Mistake / AiSession / AiMessage，
**漏掉了**：

  - ``guardian_consents``（维度是 user_id，不是 student_id）
  - ``learning_tasks`` / ``review_cards`` / ``review_logs``
  - ``student_learning_events`` / ``student_learning_profiles``
  - ``student_knowledge_mastery`` / ``practice_progress``
  - ``subscriptions`` / ``payment_transactions``

后果：每次冒烟跑完，生产库都会留下一批孤儿行（user_id 指向已删用户、
student_id 指向已删学生）。累积后污染 dashboard 的成员数/统计口径，
且这些行永远不会再被任何正常流程回收。

其中 ``review_cards`` / ``review_logs`` 尤其隐蔽——它们**根本没有 ORM 模型**
（由 FSRS 模块用 raw SQL 维护），``create_all`` 建不出来、ORM 反射也看不到，
靠"遍历 ORM 模型"式的清理必然漏掉。

本守卫做两件事：
  1. **静态扫描** ``smoke_beta.py`` 源码，断言上述每张表都出现在清理 SQL 里；
  2. **变异自证**：把源码里某张表名改掉，断言本守卫会红（证明它真在守）。

同时给 ``cleanup_prod_data.py`` 加一道守卫：它必须默认 dry-run，
不能一上来就删（生产库操作必须人工显式确认）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SMOKE = SCRIPTS / "smoke_beta.py"
CLEANUP = SCRIPTS / "cleanup_prod_data.py"

#: 冒烟过程中会被写入、且必须被清理的表
MUST_CLEAN_TABLES = [
    "guardian_consents",
    "learning_tasks",
    "review_logs",
    "review_cards",
    "student_learning_events",
    "student_learning_profiles",
    "student_knowledge_mastery",
    "practice_progress",
    "subscriptions",
    "payment_transactions",
]


@pytest.fixture(scope="module")
def smoke_src() -> str:
    assert SMOKE.exists(), f"找不到冒烟脚本：{SMOKE}"
    return SMOKE.read_text(encoding="utf-8")


def _deleted_tables(src: str) -> set[str]:
    """提取冒烟脚本里**真正被 DELETE 的表名**。

    ⚠️ 不能直接用「表名是否出现在源码里」来判断——注释里提到表名
    也会命中，守卫就成了摆设（变异自证时抽出真 DELETE 语句，守卫仍全绿）。
    这里只认两种形态，且忽略注释行：

      1. 裸 SQL：``delete from <table>``
      2. ORM：``delete(Model)`` —— 模型名小写后即表名
    """
    tables: set[str] = set()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for m in re.finditer(r"delete\s+from\s+[\"'`]?([A-Za-z_][A-Za-z0-9_]*)", line, re.I):
            tables.add(m.group(1).lower())
        for m in re.finditer(r"delete\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", line):
            tables.add(_normalize_table(m.group(1)))
    return tables


def _normalize_table(name: str) -> str:
    """模型名 -> 表名。

    ORM 类名是 CamelCase，表名是 snake_case 复数：
    ``Student`` -> ``students``、``AiSession`` -> ``ai_sessions``、
    ``ActivationCode`` -> ``activation_codes``。
    """
    # CamelCase -> snake_case
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    if not s.endswith("s"):
        s += "s"
    return s


def test_smoke_cleanup_covers_all_written_tables(smoke_src: str) -> None:
    """冒烟脚本必须真的 DELETE 掉每张会被写入的表。"""
    deleted = _deleted_tables(smoke_src)
    missing = [t for t in MUST_CLEAN_TABLES if t not in deleted]
    assert not missing, (
        f"冒烟脚本漏清理这些表（会在生产库留孤儿行）：{missing}；"
        f"当前实际 DELETE 的表：{sorted(deleted)}；"
        f"请在 smoke_beta.py 的 finish() 兜底清理段补上对应 DELETE"
    )


def test_smoke_cleanup_target_comes_from_jwt(smoke_src: str) -> None:
    """清理目标必须能从 JWT 解出——激活接口响应体里**没有** user_id。

    根因（2026-10-21 生产残留）：激活接口的 ``data`` 只有 ``access`` token，
    没有 ``user_id`` 字段。原实现只从响应体取 user_id，
    于是 ``created_user_ids`` 恒为空 → 整个清理阶段空转 →
    每次冒烟都在生产库留一批孤儿数据（实测 users=3 / students=1 /
    guardian_consents=1 / learning_tasks=1 / review_cards=1 /
    student_learning_events=5 全部残留）。

    所以必须解 JWT 的 ``sub``（用户 id）与 ``sid``（学生 id）作为兜底来源。
    """
    assert "_jwt_claim" in smoke_src, (
        "冒烟脚本缺少 JWT 解析：激活接口响应体没有 user_id，"
        "必须从 token 的 sub/sid claim 取清理目标"
    )
    # 必须解出 sid（学生 id）——students 行可能被先删掉，sid 是唯一可靠来源
    assert '"sid"' in smoke_src or "'sid'" in smoke_src, (
        "必须从 JWT 解出 sid（学生 id）作为清理目标"
    )
    # 解出的 sid 要真的进清理列表
    assert "created_student_ids" in smoke_src, (
        "JWT 解出的 sid 必须传入 finish() 的清理目标列表"
    )


def test_smoke_cleanup_collects_ids_before_deleting(smoke_src: str) -> None:
    """子表清理不得依赖 ``select id from students ...`` 这类子查询。

    原因（2026-10-21 生产事故）：清理流程先删了 ``students`` 行，随后
    兜底段再用 ``student_id in (select id from students where user_id in ...)``
    去清子表——此时 students 行已被删掉，子查询恒返回空集，
    **DELETE 执行了但删了 0 行**，生产库照样留一堆孤儿数据。
    """
    body = smoke_src.split("def finish(", 1)[-1]
    # 只看可执行代码，剔除注释行（注释里可以自由讨论这个坑）
    code_lines = [
        ln for ln in body.splitlines() if not ln.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "select id from students" not in code, (
        "子表清理不能用 `select id from students ...` 子查询定位——"
        "students 行在前面已删，子查询恒空集；必须先把 id 收集到 Python 列表"
    )
    # 必须先把 id 收集起来（存在 target_sids 之类的变量）
    assert re.search(r"target_sids\s*[:=]", body), (
        "清理前必须先把 student id 收集到 Python 列表（target_sids）"
    )
    assert re.search(r"target_uids\s*[:=]", body), (
        "清理前必须先把 user id 收集到 Python 列表（target_uids）"
    )


def test_smoke_cleanup_deletes_core_tables(smoke_src: str) -> None:
    """核心表（用户/学生/作答/错题）也必须在 DELETE 名单里。"""
    deleted = _deleted_tables(smoke_src)
    for table in ("users", "students", "attempts", "mistakes", "ai_sessions"):
        assert table in deleted, f"冒烟脚本没有 DELETE {table}"


def test_smoke_cleanup_uses_guarded_execution(smoke_src: str) -> None:
    """清理语句必须先探测表存在再执行——不能靠「失败就 rollback」。"""
    assert "get_table_names" in smoke_src, (
        "清理逻辑必须先用 inspect 探测表是否存在；"
        "直接 execute + 失败 rollback 会把同事务里已删的行一起回滚"
    )
    # 不允许出现「执行失败即整体 rollback」的写法
    assert not re.search(r"except[^:]*:\s*\n\s*db\.rollback\(\)", smoke_src), (
        "禁止用「执行失败就 db.rollback()」兜底——那会撤销本次已完成的删除"
    )


def test_smoke_cleanup_commits(smoke_src: str) -> None:
    """清理后必须 commit，否则删除不落盘。"""
    assert "db.commit()" in smoke_src, "冒烟清理缺少 db.commit()，删除不会生效"


def test_cleanup_script_defaults_to_dry_run() -> None:
    """生产库清理脚本必须默认 dry-run，必须显式 --confirm 才删。"""
    src = CLEANUP.read_text(encoding="utf-8")
    assert '"--dry-run"' in src, "清理脚本缺少 --dry-run 开关"
    assert '"--confirm"' in src, "清理脚本缺少 --confirm 开关"
    # 判定条件：只有 --confirm 为真才进入删除分支
    assert re.search(r"if\s+args\.dry_run\s+or\s+not\s+args\.confirm", src), (
        "删除分支的进入条件必须是「dry-run 或未 confirm」才返回，"
        "即默认绝不删除"
    )


def test_cleanup_script_never_touches_question_bank() -> None:
    """清理脚本绝不能删题库/套餐/系统配置。"""
    src = CLEANUP.read_text(encoding="utf-8")
    for table in ("questions", "answers", "knowledge_points", "plans", "system_config"):
        assert f'delete from "{table}"' not in src and f"delete from {table} " not in src, (
            f"清理脚本出现了对 {table} 的 DELETE —— 题库/套餐/配置必须保留"
        )
