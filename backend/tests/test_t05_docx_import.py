"""docx 题库导入模块测试（T05 · T5）。

覆盖关键路径：
    * 命名空间与 seed_db 一致（同源 uuid5，docx: 与 qbank: 不撞）
    * V7 线上事故坑：存量题 kp_verified 回填 / 严格模式拦截未复核存量
    * P1 质量闸门：clean 新题→active 进池、review 新题→archived 待复核 + 原因回写
    * P1 幂等：重导入重新定性，把上一轮误上架的 review 题打回 archived
    * R-22：upsert 是「原地更新」而非「删行再插」，不级联抹掉学生数据
    * R-23：重导入不回退 kp_verified（人工复核成果保留）
    * R-02：苏教 A 组配对闸门①（文件缺失）抛 PairingError（由 CLI 整卷跳过）
    * R-02：闸门③ 分档处置——答案不全时**保留全部题目**、只挂精确匹配的答案
    * 青岛版答案区标记定位（必须在清洗前做，且要跳过目录行/页眉重复）

测试库用独立内存 SQLite 引擎，与 conftest 的会话库隔离。
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

# ⚠️ 必须在 import app 之前固定测试库，避免污染 conftest 的会话级内存库
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import pytest  # noqa: E402

from app.models import Base  # noqa: E402
from app.models import Answer, Question  # noqa: E402
from app.models.content import (  # noqa: E402
    QUESTION_STATUS_ACTIVE,
    QUESTION_STATUS_ARCHIVED,
    SUBJECT_DEFAULT,
)
from app.services.importers.loader import (  # noqa: E402
    NS_QUESTION_BANK,
    load_outcome,
)
from app.services.importers.schema import (  # noqa: E402
    LoadStats,
    PairingError,
    ParsedQuestion,
    ParseOutcome,
    SchemaGuardError,
)
from app.services.importers.schema_guard import ensure_bank_columns  # noqa: E402
from app.services.importers import qingdao, sujiao  # noqa: E402
from app.services.importers.blocks import (  # noqa: E402
    NO_MINOR,
    RawBlock,
    is_page_residual,
    is_standalone_minor,
    segment_blocks,
)
from app.services.importers.manifest import (  # noqa: E402
    ALL_SOURCES,
    DEFAULT_SOURCE_DIR,
    DocxSource,
    SUJIAO_B_SOURCES,
    missing_files,
    sources_by_key,
)
from app.services.importers.schema import (  # noqa: E402
    build_stable_key,
)
from app.services.importers.text_clean import clean_line  # noqa: E402

# T15：登记 5 份苏教 3 下专项复习卷后，源总数应为 20。
EXPECTED_SOURCE_COUNT = 20


@pytest.fixture
def session():
    """独立内存 SQLite + T05 列就绪 + V7 守卫放行。"""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    ensure_bank_columns(eng, strict=False)
    SM = sessionmaker(bind=eng, expire_on_commit=False)
    s = SM()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


def _make_question(content: str, answer: str | None = "42") -> ParsedQuestion:
    return ParsedQuestion(
        stable_key=f"docx:unit_test.docx|巩固提升1|一|1",
        content=content,
        q_type="计算",
        paper_no="巩固提升1",
        major_no="一",
        minor_no="1",
        section="温故",
        correct_answer=answer,
        analysis=None,
        options=None,
    )


def _make_outcome(question: ParsedQuestion, *, source_doc: str = "unit_test.docx") -> ParseOutcome:
    o = ParseOutcome(
        source_key="ut",
        label="单元测试卷",
        edition="青岛版",
        source_type="衔接",
        grade=2,
        source_doc=source_doc,
    )
    o.questions.append(question)
    return o


# ==================================================================== 命名空间
def test_namespace_matches_seed_db():
    """loader 的 uuid5 命名空间必须与 seed_db 完全一致（同源派生）。

    直接读 ``scripts/seed_db.py`` 源码提取 NS_QUESTION_BANK 字面量做比对，
    避免依赖 seed_db 模块的 import 链路。
    """
    import re

    seed_path = BACKEND_DIR / "scripts" / "seed_db.py"
    src = seed_path.read_text(encoding="utf-8")
    m = re.search(r'NS_QUESTION_BANK\s*[:=].*?UUID\("([0-9a-f-]{36})"\)', src, re.S)
    assert m, "未能在 seed_db.py 中找到 NS_QUESTION_BANK 定义"
    seed_ns = uuid.UUID(m.group(1))
    assert NS_QUESTION_BANK == seed_ns
    assert str(NS_QUESTION_BANK) == "6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21"


def test_stable_key_prefix_no_collision():
    """docx: 前缀与 seed 的 qbank: 天然不撞，避免两套主键混淆。"""
    from app.services.importers.schema import build_stable_key

    docx_key = build_stable_key("a.docx", "p", "一", "1")
    assert docx_key.startswith("docx:")
    # 同业务键若用 seed 前缀生成应是不同的字符串
    seed_key = f"qbank:{docx_key}"
    assert seed_key != docx_key


# ==================================================================== V7 线上事故坑
def test_v7_backfill_legacy_unverified():
    """存量题（source_doc=None）且 kp_verified=False → 守卫应回填 True。"""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    SM = sessionmaker(bind=eng)
    s = SM()
    q = Question(
        id=uuid.uuid4(),
        content="存量题",
        subject=SUBJECT_DEFAULT,
        grade=1,
        type_="计算",
        status=QUESTION_STATUS_ACTIVE,
        source_doc=None,
        kp_verified=False,
    )
    s.add(q)
    s.commit()

    info = ensure_bank_columns(eng, strict=False)
    s.refresh(q)
    assert q.kp_verified is True
    assert info["backfilled"] >= 1
    s.close()
    eng.dispose()


def test_v7_guard_blocks_legacy_unverified_strict():
    """严格模式下仍有未复核存量 → 抛 SchemaGuardError（CLI 据此 exit 2）。"""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    SM = sessionmaker(bind=eng)
    s = SM()
    q = Question(
        id=uuid.uuid4(),
        content="未复核存量",
        subject=SUBJECT_DEFAULT,
        grade=1,
        type_="计算",
        status=QUESTION_STATUS_ACTIVE,
        source_doc=None,
        kp_verified=False,
    )
    s.add(q)
    s.commit()
    s.close()

    with pytest.raises(SchemaGuardError):
        ensure_bank_columns(eng, strict=True)
    eng.dispose()


# ==================================================================== P1 质量闸门 · 新题状态
def test_new_question_gate_clean_becomes_active(session):
    """落库的**干净**新题必须 active + kp_verified=False（进推荐池）。"""
    outcome = _make_outcome(_make_question("一、计算 1+1"))
    stats = load_outcome(session, outcome)
    session.commit()

    q = session.execute(
        select(Question).where(Question.source_doc == "unit_test.docx")
    ).scalars().one()
    assert q.status == QUESTION_STATUS_ACTIVE
    assert q.kp_verified is False
    assert q.grade == 2  # U1：升入年级
    assert stats.inserted == 1
    assert stats.gated_clean == 1
    assert stats.gated_review == 0


def test_new_question_gate_polluted_becomes_archived(session):
    """落库的**污染**新题必须 archived + 原因回写 Answer.analysis（不上架）。"""
    # 题干过长（>250 字）触发「题干过长疑似多题粘连」
    long_content = "一、计算。" + "小明去超市买了苹果。" * 40
    outcome = _make_outcome(_make_question(long_content, answer="42"))
    load_outcome(session, outcome)
    session.commit()

    q = session.execute(
        select(Question).where(Question.source_doc == "unit_test.docx")
    ).scalars().one()
    assert q.status == QUESTION_STATUS_ARCHIVED

    ans = session.execute(
        select(Answer).where(Answer.question_id == q.id)
    ).scalar_one()
    assert ans.analysis is not None
    assert "【质量待复核】" in ans.analysis
    assert "题干过长" in ans.analysis


def test_quality_gate_reasons_are_idempotent_on_reimport(session):
    """重导入 review 题：原因只写一次，不越叠越长（旧块先剥后加）。"""
    long_content = "一、计算。" + "小红有 3 个苹果。" * 40
    outcome = _make_outcome(_make_question(long_content, answer="x"))

    load_outcome(session, outcome)
    session.commit()
    qid = session.execute(
        select(Question.id).where(Question.source_doc == "unit_test.docx")
    ).scalar_one()

    # 再跑一轮（题干不变）
    load_outcome(session, outcome)
    session.commit()

    ans = session.execute(select(Answer).where(Answer.question_id == qid)).scalar_one()
    # 「【质量待复核】」只应出现一次
    assert ans.analysis.count("【质量待复核】") == 1
    assert ans.correct_answer == "x"


def test_quality_gate_reimport_downgrades_promoted_review_question(session):
    """P1 关键不变更：上一轮被误上架的 review 题，重导入后必须打回 archived。"""
    long_content = "一、计算。" + "小刚跳绳每分钟跳 120 下。" * 40
    outcome = _make_outcome(_make_question(long_content, answer="ok"))
    load_outcome(session, outcome)
    session.commit()
    qid = session.execute(
        select(Question.id).where(Question.source_doc == "unit_test.docx")
    ).scalar_one()

    # 模拟上一轮 --promote --all 误上架
    q = session.get(Question, qid)
    q.status = QUESTION_STATUS_ACTIVE
    session.commit()

    # 这一轮重导入（闸门重新定性）
    load_outcome(session, outcome)
    session.commit()

    reloaded = session.get(Question, qid)
    assert reloaded.status == QUESTION_STATUS_ARCHIVED


def test_quality_gate_seed_question_untouched(session):
    """source_doc 为空的题（种子/空来源）不走闸门，长题也保持原 status。"""
    long_content = "一、计算。" + "小丽跑步很快。" * 40
    # source_doc="" → is_new_bank=False → 跳过质量闸门，即便题干很长也按原逻辑落库
    outcome = _make_outcome(_make_question(long_content, answer="9"), source_doc="")
    stats = load_outcome(session, outcome)
    session.commit()

    q = session.execute(
        select(Question).where(Question.source_doc == "")
    ).scalars().one()
    # 未走闸门：长题不会被打成 archived
    assert q.status == QUESTION_STATUS_ACTIVE
    assert stats.gated_clean == 0
    assert stats.gated_review == 0


# ==================================================================== R-22 / R-23
def test_r22_r23_upsert_preserves_operational_marks_and_keeps_rows(session):
    """重导入：① 不删行（原地更新）；② kp_verified 保持人工复核值；③ 干净题闸门重判仍为 active。"""
    # 首轮：新题（干净）→ active + 未复核
    outcome1 = _make_outcome(_make_question("一、计算 1+1", answer="2"))
    load_outcome(session, outcome1)
    session.commit()
    qid = session.execute(
        select(Question.id).where(Question.source_doc == "unit_test.docx")
    ).scalar_one()

    # 人工复核（kp_verified=True）
    q = session.get(Question, qid)
    q.status = QUESTION_STATUS_ACTIVE
    q.kp_verified = True
    session.commit()

    # 第二轮：重导入（题干变更）
    outcome2 = _make_outcome(_make_question("一、计算 1+1 = ?（修订版）", answer="2"))
    load_outcome(session, outcome2)
    session.commit()

    # 行数仍 1（没 delete+insert 出重复行）
    rows = session.execute(
        select(Question).where(Question.source_doc == "unit_test.docx")
    ).scalars().all()
    assert len(rows) == 1

    # 干净题闸门重判 = active（与人工上架一致）
    assert rows[0].status == QUESTION_STATUS_ACTIVE
    # kp_verified 未被重跑冲掉（R-23 精神保留）
    assert rows[0].kp_verified is True
    # 内容确实更新了（原地 update 生效）
    assert "修订版" in rows[0].content

    # R-22 精神：答案行未被删重建（1:1 answer 仍在且答案更新）
    ans = session.execute(
        select(Answer).where(Answer.question_id == qid)
    ).scalar_one_or_none()
    assert ans is not None
    assert ans.correct_answer == "2"


# ==================================================================== R-02 苏教配对闸门
def test_sujiao_pairing_missing_file_raises():
    """闸门①（文件缺失）必须抛 PairingError —— CLI 据此整卷跳过+记失败。"""
    body = DocxSource(
        key="sj-a-test-body",
        filename="__no_such_body__.docx",
        edition="苏教版",
        source_type="期末",
        grade=5,
        parser="sujiao_a",
        role="body",
        pair_key=(5, "一"),
        section="摸底",
        label="苏教A·测试·正文",
    )
    answer = DocxSource(
        key="sj-a-test-answer",
        filename="__no_such_answer__.docx",
        edition="苏教版",
        source_type="期末",
        grade=5,
        parser="sujiao_a",
        role="answer",
        pair_key=(5, "一"),
        section="摸底",
        label="苏教A·测试·答案",
    )
    with pytest.raises(PairingError):
        sujiao.parse_pair([body], [answer], DEFAULT_SOURCE_DIR)


def test_compute_overlap_basic():
    """闸门③ 的 overlap 比率计算正确。"""
    q_keys = {("一", "1"), ("一", "2"), ("二", "1")}
    a_keys = {("一", "1"), ("一", "2")}
    assert sujiao.compute_overlap(q_keys, a_keys) == pytest.approx(2 / 3)
    assert sujiao.compute_overlap(set(), set()) == 0.0


# ==================================================================== 报告
def test_report_renders_failures():
    """build_report 能把整单元失败写进报告（苏教配对失败显形）。"""
    outcome = _make_outcome(_make_question("一、计算 3+3"))
    failures = [{"source": "苏教A·5下·(一)", "reason": "配对闸门未通过：overlap=0.50"}]
    rep = __import__("app.services.importers.report", fromlist=["build_report"]).build_report(
        [outcome], LoadStats(), failures=failures
    )
    md = __import__("app.services.importers.report", fromlist=["render_markdown"]).render_markdown(rep)
    assert "苏教A·5下·(一)" in md
    assert rep["failures"][0]["reason"].startswith("配对闸门")


# =============================================== R-02 闸门③ 分档处置（答案不全不丢题）
def _pair_sources(tmp_path: Path) -> tuple[DocxSource, DocxSource]:
    """造一对「文件确实存在」的正文/答案登记项，让闸门① 放行。

    Args:
        tmp_path: pytest 临时目录。

    Returns:
        ``(正文登记项, 答案登记项)``。
    """
    (tmp_path / "body.docx").write_bytes(b"")
    (tmp_path / "answer.docx").write_bytes(b"")
    body = DocxSource(
        key="sj-a-fake-body",
        filename="body.docx",
        edition="苏教版",
        source_type="期末",
        grade=5,
        parser="sujiao_a",
        role="body",
        pair_key=(5, "一"),
        section="摸底",
        label="苏教A·构造卷·正文",
    )
    answer = DocxSource(
        key="sj-a-fake-answer",
        filename="answer.docx",
        edition="苏教版",
        source_type="期末",
        grade=5,
        parser="sujiao_a",
        role="answer",
        pair_key=(5, "一"),
        section="摸底",
        label="苏教A·构造卷·答案",
    )
    return body, answer


def _fake_blocks(keys: list[tuple[str, str]]) -> list[RawBlock]:
    """按 ``(大题号, 小题号)`` 造题块，题干长度保证过 MIN_CONTENT_LENGTH。

    Args:
        keys: 题号列表。

    Returns:
        题块列表。
    """
    return [
        RawBlock(
            major_no=major,
            major_title=f"{major}、填一填。",
            minor_no=minor,
            lines=[f"第 {major} 大题第 {minor} 小题的题干内容。"],
        )
        for major, minor in keys
    ]


def test_sujiao_pairing_partial_answers_keeps_all_questions(tmp_path):
    """配对正确但答案不全（0.3 ≤ overlap < 0.8）：不抛异常、题目一道不丢。

    这是本次修复的核心行为变更——过去 overlap 不达标会 ``raise PairingError``
    导致 CLI **连题目一起丢掉**；现在只挂精确匹配到的答案，其余留空。
    """
    body_src, answer_src = _pair_sources(tmp_path)
    body_keys = [("一", str(i)) for i in range(1, 6)] + [("二", str(i)) for i in range(1, 6)]
    matched_keys = {("一", "1"), ("一", "3"), ("一", "5"), ("二", "2"), ("二", "4")}
    fake_answers = {key: f"答案-{key[0]}{key[1]}" for key in matched_keys}

    with patch.object(sujiao, "_load_lines", return_value=[]), \
         patch.object(sujiao, "_segment_body", return_value=_fake_blocks(body_keys)), \
         patch.object(sujiao, "_segment_answers", return_value=dict(fake_answers)):
        outcome = sujiao.parse_pair([body_src], [answer_src], tmp_path)

    # 题目全保留（10 道），一道都没被闸门连坐丢弃
    assert len(outcome.questions) == len(body_keys)

    # 匹配上的挂答案、没匹配上的留空——绝不顺序硬凑
    for question in outcome.questions:
        key = (question.major_no, question.minor_no)
        if key in matched_keys:
            assert question.correct_answer == fake_answers[key]
        else:
            assert question.correct_answer is None

    assert sum(1 for q in outcome.questions if q.correct_answer) == len(matched_keys)
    assert any("部分答案缺失" in w for w in outcome.warnings)


def test_sujiao_pairing_very_low_overlap_blanks_answers_but_keeps_questions(tmp_path):
    """overlap < 0.3（疑似答案文件错配）：整卷答案置空，但题目照常保留。"""
    body_src, answer_src = _pair_sources(tmp_path)
    body_keys = [("一", str(i)) for i in range(1, 11)]
    fake_answers = {("一", "1"): "答案1", ("一", "2"): "答案2"}  # overlap = 0.2

    with patch.object(sujiao, "_load_lines", return_value=[]), \
         patch.object(sujiao, "_segment_body", return_value=_fake_blocks(body_keys)), \
         patch.object(sujiao, "_segment_answers", return_value=dict(fake_answers)):
        outcome = sujiao.parse_pair([body_src], [answer_src], tmp_path)

    assert len(outcome.questions) == len(body_keys)
    assert all(q.correct_answer is None for q in outcome.questions)
    assert any("疑似答案文件错配" in w for w in outcome.warnings)


def test_sujiao_answer_keys_outside_body_are_filtered(tmp_path):
    """答案侧切出的「正文没有的题号」必须被过滤掉，绝不挂到别的题上（R-02）。"""
    body_src, answer_src = _pair_sources(tmp_path)
    body_keys = [("一", "1"), ("一", "2"), ("一", "3"), ("一", "4")]
    fake_answers = {
        ("一", "1"): "真答案1",
        ("一", "2"): "真答案2",
        ("一", "3"): "真答案3",
        ("三", "7"): "伪键答案",   # 正文里根本没有「三、7」
        ("九", "9"): "伪键答案2",
    }

    with patch.object(sujiao, "_load_lines", return_value=[]), \
         patch.object(sujiao, "_segment_body", return_value=_fake_blocks(body_keys)), \
         patch.object(sujiao, "_segment_answers", return_value=dict(fake_answers)):
        outcome = sujiao.parse_pair([body_src], [answer_src], tmp_path)

    assert len(outcome.questions) == len(body_keys)
    attached = {q.correct_answer for q in outcome.questions if q.correct_answer}
    assert attached == {"真答案1", "真答案2", "真答案3"}
    assert "伪键答案" not in attached and "伪键答案2" not in attached


def test_sujiao_empty_body_still_raises(tmp_path):
    """闸门②：**正文**解析题数为 0 是结构性错误，仍必须抛 PairingError。"""
    body_src, answer_src = _pair_sources(tmp_path)

    with patch.object(sujiao, "_load_lines", return_value=[]), \
         patch.object(sujiao, "_segment_body", return_value=[]), \
         patch.object(sujiao, "_segment_answers", return_value={("一", "1"): "答案1"}):
        with pytest.raises(PairingError):
            sujiao.parse_pair([body_src], [answer_src], tmp_path)


def test_sujiao_empty_answers_does_not_raise(tmp_path):
    """闸门②：**答案**为 0 不是结构性错误 → 不抛错，题目入库、答案留空。"""
    body_src, answer_src = _pair_sources(tmp_path)
    body_keys = [("一", "1"), ("一", "2")]

    with patch.object(sujiao, "_load_lines", return_value=[]), \
         patch.object(sujiao, "_segment_body", return_value=_fake_blocks(body_keys)), \
         patch.object(sujiao, "_segment_answers", return_value={}):
        outcome = sujiao.parse_pair([body_src], [answer_src], tmp_path)

    assert len(outcome.questions) == len(body_keys)
    assert all(q.correct_answer is None for q in outcome.questions)


# =============================================== 青岛版答案区标记定位
def test_qingdao_marker_must_be_located_before_cleaning():
    """clean_line 会把「视频详讲答案」整段抠掉——所以必须先定位、再清洗。

    这条测试把 bug 的成因钉死：一旦有人把 parse() 里的顺序换回去（先清洗
    再找标记），标记就会消失，全册答案归零。
    """
    assert clean_line("视频详讲答案") == ""
    assert qingdao._find_answer_marker(["视频详讲答案"]) == (0, "视频详讲答案")


def test_qingdao_answer_marker_skips_toc_and_page_headers():
    """标记定位：跳过目录行、取第一个独立成段的标记（页眉重复不影响边界）。"""
    raw = [
        "实验班暑假衔接",
        "视频详讲答案/59",          # 目录行（带页码）→ 必须跳过
        "巩固提升1 20以内的退位减法/1",
        "一、填一填。",
        "1. 计算 12-5=( )。",
        "视频详讲答案",              # ← 真正的答案区边界
        "巩固提升1",
        "一、1. 7",
        "视频详讲答案",              # 答案区页眉重复 → 不能当边界
        "二、1. 对",
    ]
    idx, mark = qingdao._find_answer_marker(raw)
    assert idx == 5
    assert mark == "视频详讲答案"

    body_raw, answer_raw = qingdao._split_regions(raw)
    assert len(body_raw) == 5                 # 标记行本身两边都不要
    assert "视频详讲答案" not in body_raw
    assert answer_raw[0] == "巩固提升1"
    assert answer_raw[-1] == "二、1. 对"


def test_qingdao_no_marker_returns_everything_as_body():
    """全文找不到标记：整册当题干区，答案区为空（退化成安全态）。"""
    raw = ["一、填一填。", "1. 计算 12-5=( )。"]
    assert qingdao._find_answer_marker(raw) == (-1, None)
    body_raw, answer_raw = qingdao._split_regions(raw)
    assert body_raw == raw
    assert answer_raw == []


# =============================================== 扁平单元（无大题号）不再被整单元丢弃
def test_flat_unit_without_major_falls_back_to_synthetic_major():
    """青岛版扁平小节：副标题横幅后直接是 ``1. 2. 3.``，必须切出题块而非 0 块。

    历史缺陷：``_major_index == 0`` 时 ``_append`` 把内容全当游离文本丢弃，
    整个小节切出 0 块 → 上层报 ``empty_unit`` 整单元跳过，真实题目全丢。
    新语义：直接以小题开头的扁平单元，自动归到合成大题「一」下。
    """
    lines = [
        "●细心思考填一填。",
        "1. 至少用( )根小棒。",
        "2. 长方形有( )条边。",
        "3. 三角形有( )个角。",
    ]
    blocks = segment_blocks(
        lines,
        require_major_separator=False,
        allow_inline_major=False,
        loose_minor=False,
    )

    assert len(blocks) == 3
    assert [b.major_no for b in blocks] == ["一", "一", "一"]
    assert [b.minor_no for b in blocks] == ["1", "2", "3"]
    assert "小棒" in blocks[0].text
    assert "长方形" in blocks[1].text
    assert "三角形" in blocks[2].text


# =========================================== 孤立题号行不再被误当页码残留丢弃
def test_standalone_minor_line_triggers_minor_split():
    """小题号**独占一行**（``1.`` / ``2.``）必须识别成小题触发点。

    历史缺陷：``_find_minor`` 的「行尾孤号当页码残留」保护一刀切，凡是编号落在
    行尾就跳过。独占一行的 ``1.`` 天然满足「编号在行尾」，于是被当页码丢掉 →
    小题永不触发、合成大题「一」也不开 → 整单元 0 块 → 上层报 ``empty_unit``。
    """
    lines = ["1.", "至少用( )根小棒。", "2.", "长方形有( )条边。"]
    blocks = segment_blocks(
        lines,
        require_major_separator=False,
        allow_inline_major=False,
        loose_minor=False,
    )

    assert len(blocks) == 2
    assert [b.minor_no for b in blocks] == ["1", "2"]
    assert "小棒" in blocks[0].text
    assert "长方形" in blocks[1].text
    # 题号本身不应残留进题干
    assert not blocks[0].text.startswith("1.")


def test_standalone_minor_line_with_full_width_and_dun_separator():
    """独占行小题号的全角点 ``2．`` / 顿号 ``3、`` 变体同样要认。"""
    lines = ["一、填一填。", "1.", "苹果有( )个。", "2．", "梨有( )个。", "3、", "一共( )个。"]
    blocks = segment_blocks(
        lines,
        require_major_separator=False,
        allow_inline_major=False,
        loose_minor=False,
    )

    assert [b.minor_no for b in blocks] == ["1", "2", "3"]
    assert [b.major_no for b in blocks] == ["一", "一", "一"]
    assert "苹果" in blocks[0].text
    assert "梨" in blocks[1].text


def test_page_residual_line_is_not_treated_as_minor_number():
    """真·页码残留（孤行纯数字 ``37`` / ``49``）绝不能当小题号切开。

    这是上一条修复的反向护栏：不能一刀切把所有孤行都当题号。
    """
    assert is_page_residual("37") is True
    assert is_page_residual(" 49 ") is True
    assert is_standalone_minor("37") is False
    assert is_standalone_minor("49") is False

    # 纯数字孤行不带题号后缀，不会触发小题切分（1 号小题正常，页码不额外切块）
    lines = ["一、填一填。", "1.", "长方形有( )条边。", "37"]
    blocks = segment_blocks(
        lines,
        require_major_separator=False,
        allow_inline_major=False,
        loose_minor=False,
    )
    assert len(blocks) == 1
    assert blocks[0].minor_no == "1"


def test_standalone_minor_predicates_are_mutually_exclusive():
    """``is_page_residual`` 与 ``is_standalone_minor`` 必须互斥且各自准确。"""
    for token in ("1.", "2．", "3、", "4)", "5）"):
        assert is_standalone_minor(token) is True, token
        assert is_page_residual(token) is False, token
    for token in ("1", "12", "300"):
        assert is_page_residual(token) is True, token
        assert is_standalone_minor(token) is False, token
    # 带正文的行两者都不是
    for token in ("1. 长方形有几条边", "第 3 页", "长方形有( )条边。"):
        assert is_page_residual(token) is False, token
        assert is_standalone_minor(token) is False, token


def test_duplicate_unit_titles_get_distinct_occurrence_labels():
    """同名单元（``第1课时`` 出现两次）必须编号区分，否则主键相撞 / 答案串单元。

    实测：一册青岛版里 ``第1课时`` / ``第2课时`` / ``第3课时`` 会在不同章节各出现两次。
    历史缺陷：``build_stable_key`` 以 ``(文件, 单元, 大题, 小题)`` 为主键，同名单元
    直接撞主键 → 落库 ``UNIQUE constraint failed``（实测 58 行）；``_build_answer_index``
    也以单元号为键 → 后一个同名单元的答案覆盖前一个，题目挂上别单元的答案。
    """
    lines = [
        "第1课时",
        "一、填一填。",
        "1. 第一次出现的题。",
        "第2课时",
        "一、填一填。",
        "1. 中间单元的题。",
        "第1课时",
        "一、填一填。",
        "1. 第二次出现的题。",
    ]
    groups = qingdao._group_by_unit(lines, track_section=False)
    labels = [unit for unit, _section, _buffer in groups]

    assert labels == ["第1课时", "第2课时", "第1课时#2"]
    assert len(labels) == len(set(labels)), "单元标签必须两两不同，否则 stable_key 相撞"
    # 内容没有被合并：两次出现各自保留自己的题
    assert any("第一次出现" in line for line in groups[0][2])
    assert any("第二次出现" in line for line in groups[2][2])


def test_duplicate_unit_answers_do_not_cross_contaminate():
    """答案区同名单元也按出现次序编号，``#2`` 的题只能拿到 ``#2`` 的答案。"""
    answer_lines = [
        "第1课时",
        "一、1. 甲",
        "第1课时",
        "一、1. 乙",
    ]
    index = qingdao._build_answer_index(answer_lines)

    assert index[("第1课时", "一", "1")] == "甲"
    assert index[("第1课时#2", "一", "1")] == "乙"


def test_duplicate_unit_stable_keys_are_unique():
    """同名单元编号后，派生的 stable_key 必须唯一（落库不再撞 UNIQUE）。"""
    from app.services.importers.schema import build_stable_key

    key_first = build_stable_key("book.docx", "第1课时", "一", "1")
    key_second = build_stable_key("book.docx", "第1课时#2", "一", "1")
    assert key_first != key_second


def test_trailing_number_inside_long_line_still_protected():
    """长行**末尾**挂着的孤号仍按页码/答案残留处理，不切（保留原保护）。"""
    lines = ["一、填一填。", "小明一共有多少个? 2."]
    blocks = segment_blocks(
        lines,
        require_major_separator=False,
        allow_inline_major=False,
        loose_minor=False,
    )

    assert len(blocks) == 1
    assert blocks[0].minor_no == NO_MINOR
    assert "小明" in blocks[0].text


# ==================================================================== T15 增量回归
def test_manifest_total_source_count_is_twenty():
    """T15：登记 5 份苏教 3 下专项复习卷后，源总数恰好 20 且无磁盘缺失。

    这是 DELTA 任务的核心不变量——任何一处登记漏写 / 文件名拼错都会让
    ``len(ALL_SOURCES) != 20`` 或 ``missing_files()`` 非空，从而被本测试拦下。
    """
    assert len(ALL_SOURCES) == EXPECTED_SOURCE_COUNT, (
        f"登记源总数应为 {EXPECTED_SOURCE_COUNT}，实际 {len(ALL_SOURCES)}"
    )
    # 各分组计数与清单注释一致：青岛 5 + 苏教 A 4 配对(=8 文件) + 苏教 B 7
    from app.services.importers.manifest import QINGDAO_SOURCES, SUJIAO_A_SOURCES

    assert len(QINGDAO_SOURCES) == 5
    assert len(SUJIAO_A_SOURCES) == 8
    assert len(SUJIAO_B_SOURCES) == 7
    # 磁盘上全部存在（沙箱无 docx 时跳过，避免 CI 误红）
    missing = missing_files(DEFAULT_SOURCE_DIR)
    assert missing == [], f"以下登记文件在源目录缺失：{missing}"


def test_sujiao_b_sources_include_grade_three():
    """T15：苏教 B 组必须包含 5 份 3 下（升入年级=3）专项复习卷。

    文件名 ``专项复习卷N(1).docx`` 不带年级前缀，年级完全由清单硬登记为 3，
    这里断言这 5 份的 ``grade`` 与 ``source_type/section`` 都正确，防止错登成别的年级。
    """
    grade_three = [
        src for src in SUJIAO_B_SOURCES if src.grade == 3
    ]
    assert len(grade_three) == 5, f"苏教 B 组 3 下应有 5 份，实际 {len(grade_three)}"
    for src in grade_three:
        assert src.edition == "苏教版"
        assert src.source_type == "专项复习"
        assert src.section == "专项"
        assert src.parser == "sujiao_b"
        assert src.role == "single"
        assert src.pair_key is None
        assert src.filename.startswith("专项复习卷")
        assert src.key in {
            "sj-b-g3-p1", "sj-b-g3-p2", "sj-b-g3-p3", "sj-b-g3-p4", "sj-b-g3-p5",
        }


def test_all_twenty_source_stable_keys_unique():
    """T15：20 个源的 ``key`` 必须唯一且派生出的 uuid5 稳定标识不撞、可复现。

    ``key`` 是 CLI ``--only`` 选择器与 ``sources_by_key()`` 索引的唯一真相；
    若出现重复，索引会静默覆盖 → 漏导。同时用与 loader 相同的命名空间把每个
    ``key`` 派生成 uuid5，确认派生物既唯一又幂等（同一 key 两次调用结果一致）。
    """
    keys = [src.key for src in ALL_SOURCES]
    assert len(keys) == EXPECTED_SOURCE_COUNT
    assert len(set(keys)) == EXPECTED_SOURCE_COUNT, (
        f"存在重复 key：{[k for k in keys if keys.count(k) > 1]}"
    )

    # sources_by_key 索引也应恰好 20 条（无重复键被覆盖）
    indexed = sources_by_key()
    assert len(indexed) == EXPECTED_SOURCE_COUNT

    # uuid5 派生：唯一 + 幂等
    derived_first = [uuid.uuid5(NS_QUESTION_BANK, k) for k in keys]
    derived_second = [uuid.uuid5(NS_QUESTION_BANK, k) for k in keys]
    assert len(set(derived_first)) == EXPECTED_SOURCE_COUNT, "uuid5 派生出现碰撞"
    assert derived_first == derived_second, "uuid5 派生非幂等（同一 key 两次结果不同）"


def test_dry_run_all_sources_no_crash():
    """T15：对全部 20 个登记源跑一遍 dry-run 解析，不得抛异常、不得整卷失败。

    直接复用 CLI 的 ``_parse_sources``（与真实入库同一条解析链路），断言：
      * 解析过程不抛任何异常；
      * 整单元失败清单为空（配对闸门 / 文件缺失都不应触发）；
      * 产出 16 个解析单元（5 青岛 + 4 苏教A配对 + 7 苏教B）；
      * 总题数 > 0，说明 5 份新增 3 下卷确实被解析到了题目。

    源目录缺失时跳过，避免没有 docx 的 CI 误红（本机有真实 docx 必跑）。
    """
    if missing_files(DEFAULT_SOURCE_DIR):
        pytest.skip("源目录缺失 docx，跳过 dry-run 实测（需本机教辅文件）")

    import importlib

    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    mod = importlib.import_module("import_docx_bank")

    outcomes, failures = mod._parse_sources(DEFAULT_SOURCE_DIR, None)

    assert failures == [], f"存在整单元失败：{failures}"
    assert len(outcomes) == 16, f"解析单元数应为 16，实际 {len(outcomes)}"
    total_questions = sum(len(o.questions) for o in outcomes)
    assert total_questions > 1000, f"总题数异常偏低：{total_questions}"
    # 新增的 5 份 3 下卷应各有产出
    labels = {o.label for o in outcomes}
    for expected in (
        "苏教版·3下·专项复习卷1",
        "苏教版·3下·专项复习卷2",
        "苏教版·3下·专项复习卷3",
        "苏教版·3下·专项复习卷4",
        "苏教版·3下·专项复习卷5",
    ):
        assert expected in labels, f"dry-run 未产出 {expected}"
