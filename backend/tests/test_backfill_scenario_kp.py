# -*- coding: utf-8 -*-
"""B-0「题库自动补题闭环 · 存量回填」的守卫测试。

覆盖 team 要求的四组断言：

    1. **dry-run 零写入**（含**变异自证**：把 dry-run 守卫摘掉，测试必须变红）；
    2. **幂等**：重跑不产生副作用，已回填的题被跳过并单独计数；
    3. **外键校验**：映射的 ``kp_code`` 缺失 / 非 active 时**整批中止**，
       且退出码**非零**；
    4. **只改 ``kp_id``**：回填前后**逐列**比对 ``questions`` 行，其余列字节级一致；
    5. **映射表本身**：恰好 24 条、无重复、无遗漏，逐行对齐 team 定稿表。

⚠️ 本文件**绝不设置** ``DATABASE_URL``（回归测试一律走内存 SQLite；
指向文件库会让整个测试会话读写真实题库）。

⚠️ 需要连**文件库**跑的用例一律用 ``backend/.build/`` 下的**副本**，
   绝不碰真实 ``dev.db``。
"""

from __future__ import annotations

import ast
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BACKEND_DIR / "scripts"
BUILD_DIR = BACKEND_DIR / ".build"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import backfill_scenario_kp as bsk  # noqa: E402
from app.engines import scenario_kp_mapping as mapping  # noqa: E402


# ============================================================ team 定稿的 24 行映射表
#: 期望值独立写死在这里（**不引用被测实现**），逐行比对。
#: 格式：``scenario_template -> (kp_code, 期望题数)``
EXPECTED_MAPPING: Dict[str, Tuple[str, int]] = {
    "cake/0": ("KP_CALC_DIV_1D_VERIFY", 41),
    "cake/1": ("KP_CALC_DIV_1D", 34),
    "cake/2": ("KP_CALC_DIV_1D", 38),
    "cake/3": ("KP_CALC_MUL", 31),
    "cake/4": ("KP_CALC_ADD", 46),
    "cake/5": ("KP_CALC_MUL", 26),
    "distance/0": ("KP_WORD_TRAVEL", 32),
    "distance/1": ("KP_WORD_TRAVEL", 37),
    "distance/2": ("KP_WORD_TRAVEL", 36),
    "distance/3": ("KP_WORD_TRAVEL", 33),
    "distance/4": ("KP_WORD_TRAVEL", 40),
    "distance/5": ("KP_WORD_TRAVEL", 38),
    "shopping/0": ("KP_CALC_MUL", 34),
    "shopping/1": ("KP_CALC_MUL", 42),
    "shopping/2": ("KP_CALC_MUL", 38),
    "shopping/3": ("KP_CALC_DIV_1D", 30),
    "shopping/4": ("KP_CALC_MUL", 36),
    "shopping/5": ("KP_CALC_ADD", 36),
    "time/0": ("KP_UNIT_TIME_ELAPSE", 50),
    "time/1": ("KP_UNIT_TIME_ELAPSE", 51),
    "time/2": ("KP_UNIT_TIME_ELAPSE", 50),
    "time/3": ("KP_CALC_MUL", 8),
    "time/4": ("KP_UNIT_TIME", 8),
    "time/5": ("KP_CALC_ADD", 49),
}

#: 回填覆盖的题目总数
EXPECTED_TOTAL = 864


# ============================================================ 测试夹具
@pytest.fixture()
def sample_db(tmp_path: Path) -> Path:
    """造一个**最小可用**的场景题库副本（自建，不依赖真实 dev.db）。

    结构与真实库一致（``questions`` + ``knowledge_points`` 两张表的必要列），
    规模缩小到 24 条（每模板 1 条），便于逐列比对。
    """
    db_path = tmp_path / "sample.db"
    connection = sqlite3.connect(str(db_path))
    connection.executescript(
        """
        CREATE TABLE knowledge_points (
            id CHAR(36) NOT NULL PRIMARY KEY,
            name VARCHAR(64) NOT NULL,
            parent_id CHAR(36),
            description TEXT,
            code VARCHAR(32),
            sort INTEGER NOT NULL,
            subject VARCHAR(16) NOT NULL,
            grade SMALLINT,
            semester VARCHAR(8),
            chapter VARCHAR(64),
            difficulty SMALLINT NOT NULL,
            importance SMALLINT NOT NULL,
            status VARCHAR(16) NOT NULL,
            created_at DATETIME,
            updated_at DATETIME
        );
        CREATE TABLE questions (
            id CHAR(36) NOT NULL PRIMARY KEY,
            content TEXT NOT NULL,
            image_url VARCHAR(512),
            subject VARCHAR(16) NOT NULL,
            grade SMALLINT,
            difficulty SMALLINT,
            kp_id CHAR(36),
            type VARCHAR(16),
            options TEXT,
            status VARCHAR(16) NOT NULL,
            edition VARCHAR(16),
            source_type VARCHAR(16),
            section VARCHAR(32),
            source_doc VARCHAR(256),
            kp_verified BOOLEAN NOT NULL,
            source_tag VARCHAR(8),
            ship_ready BOOLEAN NOT NULL,
            created_at DATETIME NOT NULL,
            scenario_type VARCHAR(32),
            scenario_template TEXT,
            scenario_params TEXT
        );
        """
    )
    # 7 个目标知识点（id 用可预测的 uuid5 风格字符串）
    kp_specs = [
        ("KP_CALC_DIV_1D_VERIFY", "除法验算与有余数", 3, 4, 3),
        ("KP_CALC_DIV_1D", "除数是一位数的除法", 3, 5, 3),
        ("KP_CALC_MUL", "乘除法", 3, 5, 1),
        ("KP_CALC_ADD", "加减法", 3, 5, 1),
        ("KP_WORD_TRAVEL", "行程问题", 5, 5, 5),
        ("KP_UNIT_TIME_ELAPSE", "经过时间计算", 3, 4, 3),
        ("KP_UNIT_TIME", "时间单位", 3, 4, 3),
    ]
    kp_ids: Dict[str, str] = {}
    for index, (code, name, grade, importance, difficulty) in enumerate(kp_specs):
        kp_id = f"kp-{index:08d}-0000-0000-0000-000000000000"
        kp_ids[code] = kp_id
        connection.execute(
            """
            INSERT INTO knowledge_points
                (id, name, code, sort, subject, grade, difficulty, importance, status)
            VALUES (?, ?, ?, ?, '数学', ?, ?, ?, 'active')
            """,
            (kp_id, name, code, index, grade, difficulty, importance),
        )
    # 一个干扰用的非 active 知识点（用于外键校验失败路径）
    connection.execute(
        """
        INSERT INTO knowledge_points
            (id, name, code, sort, subject, grade, difficulty, importance, status)
        VALUES ('kp-inactive-0000-0000-0000-000000000000', '停用知识点',
                'KP_INACTIVE_PROBE', 99, '数学', 3, 3, 3, 'archived')
        """
    )
    # 24 条场景题（每模板 1 条），全部 kp_id 为空
    for index, template in enumerate(sorted(EXPECTED_MAPPING)):
        connection.execute(
            """
            INSERT INTO questions
                (id, content, subject, grade, difficulty, kp_id, type, options, status,
                 source_type, source_doc, kp_verified, source_tag, ship_ready, created_at,
                 scenario_type, scenario_template, scenario_params)
            VALUES (?, ?, '数学', 4, 3, NULL, '选择', '{"A":"1","B":"2"}', 'active',
                    'scenario_bank', 'scenario_bank', 1, 'green', 1, '2026-08-11 00:00:00',
                    ?, ?, '{"variant": 0}')
            """,
            (
                f"q-{index:08d}-0000-0000-0000-000000000000",
                f"样例题干 {index} {template}",
                template.split("/")[0],
                template,
            ),
        )
    # 一条不属于场景题的普通题（脚本不该碰它）
    connection.execute(
        """
        INSERT INTO questions
            (id, content, subject, grade, difficulty, kp_id, type, options, status,
             source_type, source_doc, kp_verified, source_tag, ship_ready, created_at)
        VALUES ('q-other-0000-0000-0000-000000000000', '非场景题', '数学', 3, 3, NULL,
                '选择', '{"A":"1","B":"2"}', 'active', 'seed', 'seed', 1, 'green', 1,
                '2026-08-11 00:00:00')
        """
    )
    connection.commit()
    connection.close()
    return db_path


def read_questions(db_path: Path) -> Dict[str, Dict[str, Any]]:
    """把 ``questions`` 全表读成 ``{id: {列: 值}}``（用于逐列比对）。"""
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM questions").fetchall()
    finally:
        connection.close()
    return {row["id"]: dict(row) for row in rows}


def kp_id_by_code(db_path: Path) -> Dict[str, str]:
    """读出 ``{code: id}``。"""
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute("SELECT code, id FROM knowledge_points").fetchall()
    finally:
        connection.close()
    return {code: kp_id for code, kp_id in rows if code}


# ============================================================ 1. 映射表守卫
class TestMappingTable:
    """映射表守卫：24 条、无重复、无遗漏、逐行对齐。"""

    def test_mapping_has_exactly_24_entries(self) -> None:
        assert len(mapping.SCENARIO_KP_MAPPING) == 24
        assert len(mapping.SCENARIO_KP_RECORDS) == 24

    def test_mapping_has_no_duplicate_templates(self) -> None:
        templates = [record.scenario_template for record in mapping.SCENARIO_KP_RECORDS]
        assert len(templates) == len(set(templates)), "scenario_template 出现重复"

    def test_mapping_covers_all_24_templates_without_omission(self) -> None:
        expected = {
            f"{scene}/{index}"
            for scene in ("cake", "distance", "shopping", "time")
            for index in range(6)
        }
        assert set(mapping.SCENARIO_KP_MAPPING) == expected

    def test_mapping_matches_team_table_row_by_row(self) -> None:
        """逐行比对 team 定稿表（期望值写死在 EXPECTED_MAPPING，不引用实现）。"""
        assert mapping.SCENARIO_KP_MAPPING == {
            key: code for key, (code, _count) in EXPECTED_MAPPING.items()
        }

    def test_expected_counts_match_team_table_and_sum_to_864(self) -> None:
        counts = {
            record.scenario_template: record.expected_question_count
            for record in mapping.SCENARIO_KP_RECORDS
        }
        assert counts == {key: count for key, (_code, count) in EXPECTED_MAPPING.items()}
        assert sum(counts.values()) == EXPECTED_TOTAL

    def test_every_record_has_a_non_empty_chinese_reason(self) -> None:
        for record in mapping.SCENARIO_KP_RECORDS:
            assert record.reason.strip(), f"{record.scenario_template} 缺 reason"
            # reason 必须是中文句子（至少含一个 CJK 字符）
            assert any("\u4e00" <= char <= "\u9fff" for char in record.reason)

    def test_no_hardcoded_uuid_in_mapping(self) -> None:
        """映射表用 code 作键，绝不能写死 uuid。"""
        uuid_like = {
            str(value)
            for value in mapping.SCENARIO_KP_MAPPING.values()
            if _looks_like_uuid(str(value))
        }
        assert not uuid_like, f"映射表里混进了硬编码 uuid: {uuid_like}"

    def test_export_mapping_json_is_audit_ready(self) -> None:
        payload = mapping.export_mapping_json()
        assert payload["template_count"] == 24
        assert payload["expected_question_count"] == EXPECTED_TOTAL
        assert len(payload["records"]) == 24
        assert payload["annotation_issue"], "必须登记标注问题"
        for record in payload["records"]:
            assert record["reason"].strip()
            assert record["kp_code"].startswith("KP_")
        # 可 JSON 序列化
        json.dumps(payload, ensure_ascii=False)

    def test_validate_mapping_table_reports_ok(self) -> None:
        report = mapping.validate_mapping_table()
        assert report["ok"], report["issues"]
        assert report["stats"]["empty_cells"] == []
        assert report["stats"]["coverage"] == 1.0

    def test_resolve_kp_codes_is_pure_validation_entry(self) -> None:
        resolutions = mapping.resolve_kp_codes(
            ["KP_CALC_MUL", "KP_NOT_EXIST", "KP_WORD_TRAVEL"]
        )
        assert len(resolutions) == 3
        assert resolutions[0].known is True
        assert set(resolutions[0].templates) == {
            "cake/3",
            "cake/5",
            "shopping/0",
            "shopping/1",
            "shopping/2",
            "shopping/4",
            "time/3",
        }
        assert resolutions[0].expected_question_count == sum(
            count for key, (code, count) in EXPECTED_MAPPING.items()
            if code == "KP_CALC_MUL"
        )
        assert resolutions[1].known is False
        assert resolutions[1].templates == ()

    def test_resolve_kp_code_raises_on_unknown_template(self) -> None:
        with pytest.raises(KeyError):
            mapping.resolve_kp_code("cake/99")

    def test_mapping_module_is_pure(self) -> None:
        """engines 层铁律：不得 import sqlalchemy / Session，也不得读 env。

        用 **AST** 判定（只看真实代码），避免文档里出现这些词就误报。
        """
        module_path = BACKEND_DIR / "app" / "engines" / "scenario_kp_mapping.py"
        imported = _imported_modules(module_path)
        assert "sqlalchemy" not in imported
        assert "os" not in imported
        assert imported <= {"__future__", "dataclasses", "typing"}, imported

        referenced = _referenced_attributes(module_path)
        assert "environ" not in referenced
        assert "getenv" not in referenced

        # 真正的"纯层"断言：在**干净解释器**里 import 该模块不会带出 sqlalchemy
        import subprocess

        probe = (
            "import sys; sys.path.insert(0, %r);"
            "import app.engines.scenario_kp_mapping;"
            "print('sqlalchemy' in sys.modules)" % str(BACKEND_DIR)
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            cwd=str(BACKEND_DIR),
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "False", "engines 纯层 import 带出了 sqlalchemy"


def _looks_like_uuid(value: str) -> bool:
    """判断字符串是否像 uuid（36 位含横线，或 32 位十六进制）。"""
    if len(value) == 36 and value.count("-") == 4:
        return True
    if len(value) == 32:
        try:
            int(value, 16)
            return True
        except ValueError:
            return False
    return False


# ============================================================ AST 守卫工具
def _parse_module(path: Path) -> ast.Module:
    """把源码解析成 AST（只看**真实代码**，不看注释与 docstring）。"""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(path: Path) -> set[str]:
    """取出源码里 import 到的模块名（含 ``import x`` 与 ``from x import y``）。"""
    names: set[str] = set()
    for node in ast.walk(_parse_module(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _called_function_names(path: Path) -> set[str]:
    """取出源码里真正调用到的函数名（``foo.bar()`` 记为 ``bar``）。"""
    names: set[str] = set()
    for node in ast.walk(_parse_module(path)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _referenced_attributes(path: Path) -> set[str]:
    """取出源码里访问到的属性名（用于识别 ``os.environ`` 这类写法）。"""
    names: set[str] = set()
    for node in ast.walk(_parse_module(path)):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


# ============================================================ 2. dry-run 零写入
class TestDryRunZeroWrite:
    """默认 dry-run：零写入。"""

    def test_dry_run_writes_nothing(self, sample_db: Path) -> None:
        before = read_questions(sample_db)
        outcome = bsk.run_backfill(sample_db, confirm=False)
        after = read_questions(sample_db)

        assert outcome.mode == "dry-run"
        assert outcome.written == 0
        assert outcome.planned == len(EXPECTED_MAPPING) == 24
        assert outcome.backup_path is None, "dry-run 不应产生备份"
        assert before == after, "dry-run 改动了数据"
        assert all(row["kp_id"] is None for row in after.values())

    def test_dry_run_is_the_default_of_main(self, sample_db: Path, capsys) -> None:
        exit_code = bsk.main([str(sample_db)])
        captured = capsys.readouterr()
        assert exit_code == bsk.EXIT_OK
        assert "dry-run" in captured.out
        assert all(row["kp_id"] is None for row in read_questions(sample_db).values())

    def test_dry_run_guard_mutation_self_proof(self, sample_db: Path) -> None:
        """**变异自证**：把 dry-run 守卫摘掉，下面的断言必须变红。

        做法：复制脚本源码，把 ``if confirm:`` 改成 ``if True:``（即无视 dry-run
        直接写库），在副本上跑一次「dry-run 应当零写入」的等价断言，
        断言它**确实写进去了**——从而证明上面那条断言不是空转。
        """
        source_path = Path(bsk.__file__)
        original = source_path.read_text(encoding="utf-8")
        mutated = original.replace(
            "        if confirm:\n", "        if True:  # MUTATION: 摘掉 dry-run 守卫\n", 1
        )
        assert mutated != original, "变异点未命中——dry-run 守卫写法变了，需同步自证"

        mutated_path = BUILD_DIR / "mutated_backfill_scenario_kp.py"
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        mutated_path.write_text(mutated, encoding="utf-8")
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "mutated_backfill_scenario_kp", mutated_path
            )
            assert spec is not None and spec.loader is not None
            mutated_module = importlib.util.module_from_spec(spec)
            # dataclasses 需要模块已登记进 sys.modules，否则注解解析会失败
            sys.modules[spec.name] = mutated_module
            try:
                spec.loader.exec_module(mutated_module)
            finally:
                sys.modules.pop(spec.name, None)

            mutated_db = sample_db.with_name("mutated_sample.db")
            shutil.copyfile(sample_db, mutated_db)
            mutated_module.run_backfill(mutated_db, confirm=False)

            mutated_rows = read_questions(mutated_db)
            written = sum(1 for row in mutated_rows.values() if row["kp_id"] is not None)
            assert written > 0, "变异体没有写入——自证无效，说明断言可能空转"
            assert written == len(EXPECTED_MAPPING)
        finally:
            if mutated_path.exists():
                mutated_path.unlink()

    def test_main_exit_code_is_zero_on_dry_run(self, sample_db: Path) -> None:
        assert bsk.main([str(sample_db)]) == bsk.EXIT_OK


# ============================================================ 3. 外键校验
class TestForeignKeyValidation:
    """映射的 kp_code 缺失 / 非 active → 整批中止 + 非零退出码。"""

    def test_missing_kp_code_aborts_whole_batch(self, sample_db: Path) -> None:
        """删掉一个映射目标知识点 → 整批中止、零写入。"""
        connection = sqlite3.connect(str(sample_db))
        connection.execute("DELETE FROM knowledge_points WHERE code='KP_CALC_MUL'")
        connection.commit()
        connection.close()

        before = read_questions(sample_db)
        outcome = bsk.run_backfill(sample_db, confirm=True)
        after = read_questions(sample_db)

        assert outcome.exit_code == bsk.EXIT_FK_VALIDATION_FAILED
        assert outcome.exit_code != 0
        assert outcome.validation_errors
        assert any("KP_CALC_MUL" in message for message in outcome.validation_errors)
        assert outcome.written == 0
        assert before == after, "外键校验失败却写了数据"

    def test_missing_kp_code_main_returns_non_zero_exit_code(
        self, sample_db: Path
    ) -> None:
        connection = sqlite3.connect(str(sample_db))
        connection.execute("DELETE FROM knowledge_points WHERE code='KP_WORD_TRAVEL'")
        connection.commit()
        connection.close()

        exit_code = bsk.main([str(sample_db), "--confirm"])
        assert exit_code != 0
        assert exit_code == bsk.EXIT_FK_VALIDATION_FAILED

    def test_inactive_kp_code_aborts_whole_batch(self, sample_db: Path) -> None:
        """把一个目标知识点改成非 active，必须整批中止。"""
        connection = sqlite3.connect(str(sample_db))
        connection.execute(
            "UPDATE knowledge_points SET status='archived' WHERE code='KP_CALC_MUL'"
        )
        connection.commit()
        connection.close()

        before = read_questions(sample_db)
        outcome = bsk.run_backfill(sample_db, confirm=True)
        after = read_questions(sample_db)

        assert outcome.exit_code == bsk.EXIT_FK_VALIDATION_FAILED
        assert any("KP_CALC_MUL" in message for message in outcome.validation_errors)
        assert outcome.written == 0
        assert before == after

    def test_validation_error_lists_every_broken_code(self, sample_db: Path) -> None:
        """校验要一次性报出所有坏 code，而不是只报第一个。"""
        connection = sqlite3.connect(str(sample_db))
        connection.execute("DELETE FROM knowledge_points WHERE code='KP_CALC_ADD'")
        connection.execute(
            "UPDATE knowledge_points SET status='archived' WHERE code='KP_UNIT_TIME'"
        )
        connection.commit()
        connection.close()

        outcome = bsk.run_backfill(sample_db, confirm=True)
        assert outcome.exit_code == bsk.EXIT_FK_VALIDATION_FAILED
        joined = " ".join(outcome.validation_errors)
        assert "KP_CALC_ADD" in joined
        assert "KP_UNIT_TIME" in joined


# ============================================================ 4. 只改 kp_id
class TestOnlyKpIdChanges:
    """回填只允许改 ``questions.kp_id`` 一列。"""

    def test_only_kp_id_column_changes(self, sample_db: Path) -> None:
        before = read_questions(sample_db)
        outcome = bsk.run_backfill(sample_db, confirm=True)
        after = read_questions(sample_db)

        assert outcome.exit_code == bsk.EXIT_OK
        assert outcome.written == len(EXPECTED_MAPPING)

        assert set(before) == set(after), "题目行数变了"
        changed_columns: set[str] = set()
        for question_id, before_row in before.items():
            after_row = after[question_id]
            assert set(before_row) == set(after_row), "列集合变了"
            for column in before_row:
                if before_row[column] != after_row[column]:
                    changed_columns.add(column)
        assert changed_columns == {"kp_id"}, f"除 kp_id 外还有列被改: {changed_columns}"

    def test_kp_id_matches_mapping_resolved_id(self, sample_db: Path) -> None:
        bsk.run_backfill(sample_db, confirm=True)
        codes = kp_id_by_code(sample_db)
        rows = read_questions(sample_db)
        for question_id, row in rows.items():
            if row["source_doc"] != "scenario_bank":
                continue
            expected_id = codes[EXPECTED_MAPPING[row["scenario_template"]][0]]
            assert row["kp_id"] == expected_id

    def test_non_scenario_questions_untouched(self, sample_db: Path) -> None:
        bsk.run_backfill(sample_db, confirm=True)
        rows = read_questions(sample_db)
        other = rows["q-other-0000-0000-0000-000000000000"]
        assert other["kp_id"] is None
        assert other["status"] == "active"

    def test_backup_is_created_before_write(self, sample_db: Path) -> None:
        outcome = bsk.run_backfill(sample_db, confirm=True)
        assert outcome.backup_path is not None
        backup = Path(outcome.backup_path)
        assert backup.exists(), "写入前必须留下备份"
        # 备份里应是「写入前」的状态：kp_id 全空
        backup_rows = read_questions(backup)
        assert all(row["kp_id"] is None for row in backup_rows.values())

    def test_backup_uses_sqlite_backup_api_not_file_copy(self, sample_db: Path) -> None:
        """备份必须走 sqlite3 backup API（直接复制主文件在 WAL 下会得到残缺库）。

        用 **AST** 判定真实调用，避免文档里出现 ``shutil.copy`` 字样就误报。
        """
        script_path = BACKEND_DIR / "scripts" / "backfill_scenario_kp.py"
        called = _called_function_names(script_path)
        for forbidden in ("copy", "copy2", "copyfile", "copytree", "system", "popen"):
            assert forbidden not in called, f"备份禁止用 {forbidden}"
        assert "backup" in called, "必须调用 sqlite3 的 backup()"

        # 实际行为验证：备份文件是**可用的 SQLite 库**，且内容是写入前状态
        outcome = bsk.run_backfill(sample_db, confirm=True)
        backup = Path(outcome.backup_path or "")
        assert backup.exists()
        connection = sqlite3.connect(str(backup))
        try:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute(
                "SELECT COUNT(*) FROM questions WHERE kp_id IS NOT NULL"
            ).fetchone()[0] == 0
        finally:
            connection.close()


# ============================================================ 5. 幂等
class TestIdempotency:
    """幂等：重跑不产生副作用，已回填的题被单独计数。"""

    def test_rerun_is_idempotent(self, sample_db: Path) -> None:
        first = bsk.run_backfill(sample_db, confirm=True)
        assert first.written == len(EXPECTED_MAPPING)

        after_first = read_questions(sample_db)
        second = bsk.run_backfill(sample_db, confirm=True)
        after_second = read_questions(sample_db)

        assert second.written == 0, "第二次不应再写任何行"
        assert second.planned == 0
        assert second.skipped_same == len(EXPECTED_MAPPING)
        assert second.skipped_mismatch == 0
        assert after_first == after_second, "重跑改动了数据"

    def test_idempotent_rerun_reports_zero_kp_delta(self, sample_db: Path) -> None:
        bsk.run_backfill(sample_db, confirm=True)
        second = bsk.run_backfill(sample_db, confirm=True)
        assert second.zero_kp_before == second.zero_kp_after
        assert second.kp_counts_before == second.kp_counts_after

    def test_mismatched_kp_is_not_overwritten(self, sample_db: Path) -> None:
        """已有其它知识点归属的题：不覆盖，只计数并写进报告。"""
        codes = kp_id_by_code(sample_db)
        rows = read_questions(sample_db)
        target = next(
            question_id
            for question_id, row in rows.items()
            if row["source_doc"] == "scenario_bank"
        )
        connection = sqlite3.connect(str(sample_db))
        connection.execute(
            "UPDATE questions SET kp_id = ? WHERE id = ?",
            (codes["KP_CALC_MUL"], target),
        )
        connection.commit()
        connection.close()

        outcome = bsk.run_backfill(sample_db, confirm=True)
        assert outcome.skipped_mismatch == 1
        assert outcome.written == len(EXPECTED_MAPPING) - 1
        assert read_questions(sample_db)[target]["kp_id"] == codes["KP_CALC_MUL"]


# ============================================================ 6. 报告内容
class TestReport:
    """JSON 报告必须包含 team 要求的字段。"""

    def test_report_contains_required_sections(self, sample_db: Path, tmp_path: Path) -> None:
        report_path = tmp_path / "report.json"
        exit_code = bsk.main([str(sample_db), "--confirm", "--report", str(report_path)])
        assert exit_code == bsk.EXIT_OK

        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["batch_id"]
        assert report["generated_at"]
        assert report["mode"] == "confirm"
        assert report["backup_path"]
        for section in (
            "stats",
            "kp_question_counts",
            "zero_question_kp",
            "questions",
            "annotation_issue",
        ):
            assert section in report, f"报告缺 {section} 段"

        assert report["stats"]["written"] == len(EXPECTED_MAPPING)
        assert report["stats"]["skipped_same"] == 0
        assert len(report["questions"]) == len(EXPECTED_MAPPING)
        for item in report["questions"]:
            assert item["kp_id_before"] is None
            assert item["kp_id_after"]
            assert item["action"] == "write"

        assert report["zero_question_kp"]["after"] <= report["zero_question_kp"]["before"]
        assert report["annotation_issue"]

    def test_report_records_per_kp_count_change(self, sample_db: Path, tmp_path: Path) -> None:
        report_path = tmp_path / "report.json"
        bsk.main([str(sample_db), "--confirm", "--report", str(report_path)])
        report = json.loads(report_path.read_text(encoding="utf-8"))

        before = report["kp_question_counts"]["before"]
        after = report["kp_question_counts"]["after"]
        delta = report["kp_question_counts"]["delta"]
        for code in {code for code, _count in EXPECTED_MAPPING.values()}:
            assert after[code] == before[code] + delta[code]
            assert before[code] == 0
        # 样例库每模板只有 1 条题，期望增量 = 映射到该 code 的模板数
        expected_delta = {
            code: sum(
                1 for _key, (kp, _count) in EXPECTED_MAPPING.items() if kp == code
            )
            for code in {code for code, _count in EXPECTED_MAPPING.values()}
        }
        assert delta == expected_delta

    def test_dry_run_report_shows_zero_writes(self, sample_db: Path, tmp_path: Path) -> None:
        report_path = tmp_path / "report.json"
        bsk.main([str(sample_db), "--report", str(report_path)])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["mode"] == "dry-run"
        assert report["stats"]["written"] == 0
        assert report["stats"]["to_backfill"] == len(EXPECTED_MAPPING)
        assert report["backup_path"] is None


# ============================================================ 8. 缺口榜段
class TestGapRankingSection:
    """JSON 报告必须带 ``gap_ranking`` 段（缺口榜 PRD 交付物）。"""

    def test_report_contains_gap_ranking_section(
        self, sample_db: Path, tmp_path: Path
    ) -> None:
        report_path = tmp_path / "report.json"
        bsk.main([str(sample_db), "--report", str(report_path)])
        report = json.loads(report_path.read_text(encoding="utf-8"))

        assert "gap_ranking" in report, "报告缺 gap_ranking 段"
        section = report["gap_ranking"]
        for key in ("note", "total_zero_question_kp", "items"):
            assert key in section, f"gap_ranking 段缺 {key}"

    def test_gap_ranking_note_is_non_empty_and_explains_both_absent_factors(
        self, sample_db: Path, tmp_path: Path
    ) -> None:
        """``note`` 必须写明两个缺席因子的原因，且**不许伪造数据**填它们。"""
        report_path = tmp_path / "report.json"
        bsk.main([str(sample_db), "--report", str(report_path)])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        note = report["gap_ranking"]["note"]

        assert note.strip(), "note 不得为空"
        assert "前置被依赖" in note
        assert "knowledge_relations" in note
        assert "knowledge_prerequisite" in note
        assert "错因命中数" in note

    def test_gap_ranking_items_count_is_correct(
        self, sample_db: Path, tmp_path: Path
    ) -> None:
        """``items`` 条数 = min(top_n, 零题量 KP 数)，且名次连续。

        样例库里有 7 个 active 知识点，回填前**一个题都没有** → 7 个缺口；
        ``top_n`` 默认 50，所以 ``items`` 应当是 7 条。
        """
        report_path = tmp_path / "report.json"
        bsk.main([str(sample_db), "--report", str(report_path)])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        section = report["gap_ranking"]

        assert section["total_zero_question_kp"] == 7
        assert section["ranked_count"] == 7
        assert section["deferred_count"] == 0
        assert section["top_n"] == bsk.GAP_RANKING_TOP_N
        assert len(section["items"]) == min(
            bsk.GAP_RANKING_TOP_N, section["total_zero_question_kp"]
        )
        assert [item["rank"] for item in section["items"]] == [1, 2, 3, 4, 5, 6, 7]

    def test_gap_ranking_items_have_required_fields(
        self, sample_db: Path, tmp_path: Path
    ) -> None:
        """每条 item 的字段集必须齐（人工过目要看得懂）。"""
        report_path = tmp_path / "report.json"
        bsk.main([str(sample_db), "--report", str(report_path)])
        report = json.loads(report_path.read_text(encoding="utf-8"))

        for item in report["gap_ranking"]["items"]:
            for key in (
                "rank",
                "kp_code",
                "kp_name",
                "grade",
                "importance",
                "difficulty",
                "zero_days",
                "reason",
            ):
                assert key in item, f"item 缺 {key}"
            assert item["reason"].strip()

    def test_gap_ranking_reflects_post_backfill_state(
        self, sample_db: Path, tmp_path: Path
    ) -> None:
        """回填后那 7 个 KP 都有了题 → 不再是缺口 → ``items`` 变空。

        缺口榜反映的是**报告生成时刻的库状态**，不是回填前的快照。
        """
        report_path = tmp_path / "report.json"
        bsk.main([str(sample_db), "--confirm", "--report", str(report_path)])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        section = report["gap_ranking"]

        assert section["total_zero_question_kp"] == 0
        assert section["items"] == []

    def test_gap_ranking_does_not_write_anything_in_dry_run(
        self, sample_db: Path
    ) -> None:
        """缺口榜是**只读**的：dry-run 后库内容必须一字未改。"""
        before = read_questions(sample_db)
        bsk.run_backfill(sample_db, confirm=False)
        after = read_questions(sample_db)
        assert before == after


# ============================================================ 7. CLI 其它约束
class TestCliConstraints:
    """CLI 与工程约束。"""

    def test_batch_size_flag_is_accepted(self, sample_db: Path) -> None:
        exit_code = bsk.main([str(sample_db), "--confirm", "--batch-size", "5"])
        assert exit_code == bsk.EXIT_OK
        assert bsk.run_backfill(sample_db, confirm=True).written == 0

    def test_no_new_config_specs_keys_added(self) -> None:
        """批次大小等参数不得塞进 CONFIG_SPECS（31 键 golden 守卫）。"""
        from app.engines.config import CONFIG_SPECS

        assert len(CONFIG_SPECS) == 31
        keys = {spec.key for spec in CONFIG_SPECS}
        assert not any("scenario" in key or "backfill" in key for key in keys)

    def test_script_does_not_set_database_url(self) -> None:
        """脚本不得设置 DATABASE_URL（回归测试环境统一走内存库）。"""
        source = (
            BACKEND_DIR / "scripts" / "backfill_scenario_kp.py"
        ).read_text(encoding="utf-8")
        assert "DATABASE_URL" not in source

    def test_missing_db_file_returns_error_exit_code(self, tmp_path: Path) -> None:
        assert bsk.main([str(tmp_path / "nope.db")]) == bsk.EXIT_DB_ERROR
