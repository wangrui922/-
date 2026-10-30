# -*- coding: utf-8 -*-
"""接地气素材检索服务 · 单测（A-1 期）。

覆盖目标
--------
本文件守的是 **PM 写死的验收判据**，每条都对应一个「上线会出事」的场景：

1. **年级过滤零初高中泄漏** —— 题库里躺着 881 条初高中语料
   （g7:158 / g8:172 / g9:187 / g10:113 / g11:121 / g12:130），
   漏一条进小学孩子的提示词就是事故。
   **对抗样本**：本文件**故意往库里塞一条 g7 的题**，断言它在小学素材包里
   命中数**恰好为 0**（不是「大概率查不到」）。
2. **两池分离** —— 讲解素材池必须含 ``kp_id`` 为空的题（实测 864 道
   scenario 题，生活场景词最丰富）；归因类素材池必须 ``kp_id IS NOT NULL``。
3. **空结果优雅** —— 查不到就返回空列表 / 空素材包，**绝不抛错**。
4. **开关关闭零变化** —— ``RAG_GROUNDING_ENABLED`` 为假时，
   ``render_grounding_segment`` 必须返回空串（调用方据此完全不追加后缀）。
5. **检索不崩** —— 检索失败（脏数据 / 库表缺失）必须吞掉异常返回空，
   绝不阻断辅导主流程。

测试隔离（为什么每条题面都带一个中文标记）
------------------------------------------
测试库是**会话级共享**的内存 SQLite（conftest 的 ``db`` fixture 正常跑完
就 commit），别的测试模块也会往 ``questions`` 塞数据 ——
``test_quality_gate`` 造过「小明买了一些铅笔」，``test_t05_docx_import``
造过「小明去超市买了苹果」。本文件的断言必须是「**只有我造的题**符合」，
所以每条题面都带一个**全局唯一的中文标记**，检索时也用它做目标串。
没有标记的话，断言会随「谁先跑」漂移 —— 那是最难查的一类假绿。

环境铁律
--------
**绝不设 ``DATABASE_URL``**（conftest 默认 ``:memory:``）—— 设成文件库的话，
测试会读写自己正要断言的 dev.db（测试自噬），且跨用例互相打架。
"""

from __future__ import annotations

import uuid
from typing import List, Optional

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.grounding_materials import GroundingMaterials
from app.services import grounding_service as gs
from app.services.grounding_service import (
    GROUNDING_CONFIG_KEY,
    POOL_ATTRIBUTION,
    POOL_TEACHING,
    GroundingCandidate,
    GroundingQuery,
    build_grounding_materials,
    clear_grounding_cache,
    grounding_enabled,
    render_grounding_segment,
    search_candidates,
    table_exists,
)
from app.models.content import KnowledgePoint, Question
from app.models.system_config import SystemConfig
from rag_tagging import new_tag, rag_cleanup, track_config_key, track_kp, track_question


# ============================================================
# 造数工具
# ============================================================

#: 全局唯一中文标记（**跨测试文件共享**，见 ``tests/rag_tagging.py``）。
#:
#: 早先本文件自带一套标记（从 1 开始数），与 ``test_grounding_injection.py``
#: 的标记**完全撞车** → 两个文件造的题互相命中 → 全量跑时 2 条用例变红。
#: 现在统一走共享计数器。
_tag = new_tag


def _uid() -> str:
    """取一个带随机后缀的短串，规避 UNIQUE 约束（同会话库跨用例共享）。"""
    return uuid.uuid4().hex[:8]


def _make_kp(
    db: Session,
    *,
    name: str = "测试知识点",
    description: str = "加减法",
    grade: Optional[int] = None,
) -> KnowledgePoint:
    """造一个知识点（带释义，供 kp_anchor 段取材）。"""
    kp = KnowledgePoint(name=f"{name}{_uid()}", description=description, grade=grade)
    db.add(kp)
    db.flush()
    return track_kp(kp)


def _make_question(
    db: Session,
    *,
    content: str,
    grade: Optional[int] = 3,
    kp_id: Optional[object] = None,
    status: str = "active",
    difficulty: int = 3,
    question_type: str = "应用",
) -> Question:
    """造一道题。"""
    question = Question(
        content=content,
        grade=grade,
        kp_id=kp_id,
        status=status,
        difficulty=difficulty,
        type_=question_type,
    )
    db.add(question)
    db.flush()
    return track_question(question)


@pytest.fixture(autouse=True)
def _auto_cleanup(rag_cleanup):
    """每个用例结束后清掉本模块造的题 / 知识点 / 配置行。

    会话级共享内存库不清理就会污染后续模块（本分支实测踩过：
    ``RAG_GROUNDING_ENABLED=true`` 被 commit 出去，后续模块读到「开关已开」）。
    """
    yield


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前后清空检索缓存（进程内缓存会跨用例串味）。"""
    clear_grounding_cache()
    yield
    clear_grounding_cache()


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """每个用例前后清空 ``system_config`` 的进程内缓存。

    ``get_config`` 会把值缓存在模块级 ``_cache`` 里；不清理的话，
    「开关默认关闭」这条断言会受**用例执行顺序**影响（假绿/假红）。
    """
    from app.core.system_config import reload_config

    reload_config()
    yield
    reload_config()


# ============================================================
# 1. 年级过滤：零初高中泄漏
# ============================================================


def test_primary_pool_never_returns_junior_high_question(db: Session):
    """小学素材包里初高中题的命中数**恰好为 0**。

    **对抗样本构造**：往库里塞一条 ``grade=7`` 的题，内容里带一个
    小学题**绝不会出现**的关键词（「导数」「抛物线」），这样即使按内容
    排序把它捞进来，靠内容也能认出来。

    断言口径：
    * ``search_candidates`` 走讲解池 + 3 年级窗口 → 结果里 ``grade=7`` 的
      命中数 **== 0**；
    * 逐条断言每条候选的 ``grade <= 6``（不靠「恰好一条都没捞到」的运气）；
    * 小学题自己必须在（否则说明过滤过头了，把素材池掐死了）。
    """
    tag = _tag()
    junior = _make_question(
        db,
        content=f"求函数导数并判断抛物线开口方向 {tag}",
        grade=7,
    )
    primary = _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔，一共花了 5 元。{tag}",
        grade=3,
    )
    db.flush()

    candidates = search_candidates(
        db,
        GroundingQuery(text=tag, student_grade=3),
        pool=POOL_TEACHING,
    )

    junior_hits = [c for c in candidates if c.question_id == str(junior.id)]
    assert junior_hits == [], "g7 的题漏进了小学素材包"
    assert all(c.grade is None or c.grade <= 6 for c in candidates), candidates
    # 小学题自己必须在（否则说明过滤过头了，把素材池掐死了）
    assert [c.question_id for c in candidates] == [str(primary.id)]


def test_junior_high_adversarial_sample_is_never_retrieved(db: Session):
    """对抗样本：g7 题与小学题内容高度相似，也不能被捞进来。

    这是最危险的情形 —— 如果只按内容相似度过滤，g7 的题会混进小学包。
    """
    tag = _tag()
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔，一共花了 5 元。{tag}",
        grade=7,  # ← 只改年级，内容与小学题几乎一样
    )
    _make_question(
        db,
        content=f"小明在文具店买了 2 支铅笔，一共花了 4 元。{tag}",
        grade=3,
    )
    db.flush()

    candidates = search_candidates(
        db,
        GroundingQuery(text=tag, student_grade=3),
        pool=POOL_TEACHING,
    )
    assert candidates, "小学题自己都没被捞到（过滤过头）"
    assert all(c.grade == 3 for c in candidates), candidates


def test_grade_window_is_hard_capped_at_six(db: Session):
    """年级窗口宽度调大也**绝不**越过 6（初高中一条都不许进）。"""
    tag = _tag()
    for grade in (7, 8, 9, 10, 11, 12):
        _make_question(
            db,
            content=f"初高中题目样本 {grade} 抛物线焦点弦长 {tag}",
            grade=grade,
        )
    _make_question(
        db,
        content=f"小学题目样本 文具店 铅笔 一共 {tag}",
        grade=3,
    )
    db.flush()

    for width in (1, 3, 10):
        candidates = search_candidates(
            db,
            GroundingQuery(
                text=f"文具店 铅笔 {tag}", student_grade=3, grade_width=width
            ),
            pool=POOL_TEACHING,
        )
        assert all(c.grade is None or c.grade <= 6 for c in candidates), (
            width,
            candidates,
        )


def test_student_above_primary_gets_empty_materials(db: Session):
    """学生年级 > 6（初中/高中）→ **空素材包**（本期只服务小学）。"""
    tag = _tag()
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔。{tag}",
        grade=3,
    )
    db.flush()

    materials = build_grounding_materials(
        db,
        GroundingQuery(text=f"小明在文具店买了铅笔 {tag}", student_grade=8),
    )
    assert materials.is_empty()
    assert materials.analogy == ""
    assert materials.kp_anchor == ""


def test_materials_for_primary_student_contain_no_junior_high(db: Session):
    """小学生拿到的素材包里不得含任何初中高中内容。

    **真实泄漏场景**：题库里有「小学题挂到初中知识点上」的脏数据 ——
    这里造一条 ``grade=3`` 的题指向 ``grade=9`` 的知识点（释义
    「二次方程的解法与应用」）。小学孩子的素材包里**绝不能**出现
    「二次方程」。

    服务层的防线：``_fetch_kp_description`` 会看知识点自己的年级，
    超过小学上限一律当「取不到」。
    """
    tag = _tag()
    junior_kp = KnowledgePoint(
        name=f"二次方程{_uid()}",
        description="二次方程的解法与应用",
        grade=9,
    )
    db.add(junior_kp)
    db.flush()
    track_kp(junior_kp)
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔。{tag}",
        grade=3,
        kp_id=junior_kp.id,
    )
    db.flush()

    materials = build_grounding_materials(
        db,
        GroundingQuery(text=tag, student_grade=3),
    )
    assert "二次方程" not in materials.kp_anchor
    assert materials.kp_anchor == "", "超纲知识点的释义被注入了小学素材包"


def test_junior_high_kp_never_reaches_primary_student(db: Session):
    """同一知识点，**直接**指定 kp_id 时也必须被年级闸门拦下。"""
    tag = _tag()
    junior_kp = KnowledgePoint(
        name=f"物理密度{_uid()}",
        description="密度的计算与应用",
        grade=8,
    )
    db.add(junior_kp)
    db.flush()
    track_kp(junior_kp)

    materials = build_grounding_materials(
        db,
        GroundingQuery(
            text=f"小明在文具店买了铅笔 {tag}",
            kp_id=str(junior_kp.id),
            student_grade=3,
        ),
    )
    assert materials.kp_anchor == ""
    assert "密度" not in materials.kp_anchor


# ============================================================
# 2. 两池分离
# ============================================================


def test_teaching_pool_includes_questions_without_kp(db: Session):
    """讲解素材池必须**含 kp_id 为空的题**（864 道 scenario 题的来源）。

    口径：scenario 题（``kp_id IS NULL``）的生活场景词最丰富，
    正是「接地气」的来源 —— 池子把它们排在外面 = 把最好的素材扔了。
    """
    tag = _tag()
    kp = _make_kp(db, description="加减法")
    with_kp = _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔。{tag}",
        grade=3,
        kp_id=kp.id,
    )
    without_kp = _make_question(
        db,
        content=f"小红在超市买了 4 盒牛奶。{tag}",
        grade=3,
        kp_id=None,
    )
    db.flush()

    candidates = search_candidates(
        db,
        GroundingQuery(text=tag, student_grade=3),
        pool=POOL_TEACHING,
    )
    ids = [c.question_id for c in candidates]
    assert str(without_kp.id) in ids, "讲解池把 kp_id 为空的题排除了"
    assert str(with_kp.id) in ids


def test_attribution_pool_requires_kp_id(db: Session):
    """归因类素材池必须 ``kp_id IS NOT NULL``。"""
    tag = _tag()
    kp = _make_kp(db, description="加减法")
    with_kp = _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔。{tag}",
        grade=3,
        kp_id=kp.id,
    )
    without_kp = _make_question(
        db,
        content=f"小红在超市买了 4 盒牛奶。{tag}",
        grade=3,
        kp_id=None,
    )
    db.flush()

    candidates = search_candidates(
        db,
        GroundingQuery(text=tag, student_grade=3),
        pool=POOL_ATTRIBUTION,
    )
    ids = [c.question_id for c in candidates]
    assert str(without_kp.id) not in ids, "归因池漏进了没有知识点的题"
    assert str(with_kp.id) in ids
    assert all(c.has_kp() for c in candidates), candidates


def test_pools_do_not_cross_contaminate(db: Session):
    """两池**绝不混用**：归因池的每一条在讲解池口径下也必须挂得上 KP。"""
    tag = _tag()
    kp = _make_kp(db, description="加减法")
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔。{tag}",
        grade=3,
        kp_id=kp.id,
    )
    _make_question(
        db,
        content=f"小红在超市买了 4 盒牛奶。{tag}",
        grade=3,
        kp_id=None,
    )
    db.flush()

    teaching = search_candidates(
        db,
        GroundingQuery(text=tag, student_grade=3, top_k=50),
        pool=POOL_TEACHING,
    )
    attribution = search_candidates(
        db,
        GroundingQuery(text=tag, student_grade=3, top_k=50),
        pool=POOL_ATTRIBUTION,
    )
    attribution_ids = {c.question_id for c in attribution}
    assert attribution_ids
    assert attribution_ids <= {c.question_id for c in teaching}
    assert len(attribution) < len(teaching)


def test_unknown_pool_raises(db: Session):
    """未知池名必须报错（拼写错要立刻暴露，不能静默当成讲解池）。"""
    with pytest.raises(ValueError):
        search_candidates(db, GroundingQuery(text="x"), pool="不存在的池")


# ============================================================
# 3. 空结果 / 异常优雅
# ============================================================


def test_empty_result_returns_empty_list(db: Session):
    """查不到 → 空列表（不抛错）。

    相关性下限：目标串与库里任何题面都没有共同片段 → 一条都不该返回。
    """
    tag = _tag()
    candidates = search_candidates(
        db,
        GroundingQuery(text=tag, student_grade=3),
        pool=POOL_TEACHING,
    )
    assert candidates == []


def test_empty_materials_when_nothing_found(db: Session):
    """查不到 → 空素材包（四段皆空），不抛错。"""
    tag = _tag()
    materials = build_grounding_materials(
        db,
        GroundingQuery(text=tag, student_grade=3),
    )
    assert materials.is_empty()
    assert isinstance(materials, GroundingMaterials)


def test_materials_use_sibling_question_scene(db: Session):
    """当前题面没场景时，**借兄弟题的场景骨架**（这是轻量 RAG 的核心价值）。

    当前题面是「在括号里填上 > < 或 =」—— 没有任何生活场景，
    也和兄弟题**没有任何共同片段**；同 KP 的兄弟题是「在文具店买了 3 支铅笔」
    —— 有购物场景。素材包应当借用兄弟题的场景，产出「生活类比」段。

    ⚠️ 这条链路靠的是「同 KP」这个**结构化信号**，不是文本相似度 ——
    所以检索层不能把 LIKE 当硬过滤（会把唯一有场景的兄弟题筛掉）。
    """
    tag = _tag()
    kp = _make_kp(db, description="比较大小")
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔，一共花了 5 元。{tag}",
        grade=3,
        kp_id=kp.id,
    )
    db.flush()

    materials = build_grounding_materials(
        db,
        GroundingQuery(
            text=tag,
            kp_id=str(kp.id),
            student_grade=3,
        ),
    )
    assert materials.analogy, "没有借到兄弟题的场景骨架"
    assert "文具店" in materials.analogy
    assert materials.kp_anchor == "比较大小"


def test_table_probe_uses_connection_not_bind(db: Session):
    """表探测走 ``inspect(db.connection())``，**不用** ``inspect(db.bind)``。

    为什么这么重要（写在这里是为了防止将来被「优化」回去）：
    ``db.bind`` 会从连接池**再借一条连接**；对 StaticPool 单连接库
    （测试用的内存 SQLite 就是它），那条连接归还时会 rollback ——
    **静默回滚调用方同事务里尚未提交的写入**，不报错，极难查。
    """
    assert table_exists(db, "questions") is True
    assert table_exists(db, "一张根本不存在的表") is False


def test_table_probe_does_not_rollback_pending_writes(db: Session):
    """表探测**不得**回滚调用方未提交的写入（StaticPool 静默回滚陷阱）。

    这条测试是对 :func:`table_exists` 的**行为级**守卫：
    先写入一条题（不 commit），再探测表存在性，最后断言写入还在。
    如果实现改回 ``inspect(db.bind)``，这条测试会红。
    """
    question = _make_question(
        db,
        content="小明在文具店买了 3 支铅笔。",
        grade=3,
    )
    db.flush()  # flush 但不 commit

    assert table_exists(db, "questions") is True
    assert table_exists(db, "一张根本不存在的表") is False

    # 写入必须还在（没被静默回滚）
    rows = db.execute(
        select(Question).where(Question.id == question.id)
    ).scalars().all()
    assert len(rows) == 1, "表探测把调用方未提交的写入静默回滚了"


# ============================================================
# 4. 开关关闭零变化
# ============================================================


def _set_switch(db: Session, value: str) -> None:
    """写 ``RAG_GROUNDING_ENABLED`` 配置行（同事务内可见）。"""
    row = db.execute(
        select(SystemConfig).where(SystemConfig.key == GROUNDING_CONFIG_KEY)
    ).scalar_one_or_none()
    if row is None:
        row = SystemConfig(key=GROUNDING_CONFIG_KEY, value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    track_config_key(GROUNDING_CONFIG_KEY)


def test_switch_defaults_to_off(db: Session):
    """开关默认关闭（灰度口径：默认不改变线上行为）。"""
    assert grounding_enabled(db) is False
    assert grounding_enabled() is False


def test_switch_off_returns_empty_segment(db: Session):
    """开关关闭 → ``render_grounding_segment`` 返回空串。

    空串 = 调用方**完全不追加后缀** → 输出与改动前逐字一致。
    """
    tag = _tag()
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔，一共花了 5 元。{tag}",
        grade=3,
    )
    db.flush()
    _set_switch(db, "false")

    segment = render_grounding_segment(
        db,
        GroundingQuery(text=tag, student_grade=3),
    )
    assert segment == ""


def test_switch_on_returns_material_segment(db: Session):
    """开关打开 → 返回素材文本（带内部提示标注）。"""
    tag = _tag()
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔，一共花了 5 元。{tag}",
        grade=3,
    )
    db.flush()
    _set_switch(db, "true")

    segment = render_grounding_segment(
        db,
        GroundingQuery(text=tag, student_grade=3),
    )
    assert segment, "开关打开却没吐出素材"
    assert "内部提示" in segment


@pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "TRUE", " On "])
def test_switch_truthy_values(db: Session, raw: str):
    """开关的真值口径与 ``system_config._to_bool`` 一致。"""
    _set_switch(db, raw)
    assert grounding_enabled(db) is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "FALSE", " Off "])
def test_switch_falsy_values(db: Session, raw: str):
    """``bool("false")`` 是 ``True`` —— 字符串配置必须走 ``_to_bool``。"""
    _set_switch(db, raw)
    assert grounding_enabled(db) is False


# ============================================================
# 5. 检索稳健性
# ============================================================


def test_retrieval_survives_bad_kp_id(db: Session):
    """kp_id 传了非 UUID 的脏值 → 检索不崩（返回空或忽略该过滤）。"""
    tag = _tag()
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔。{tag}",
        grade=3,
    )
    db.flush()

    candidates = search_candidates(
        db,
        GroundingQuery(text=tag, kp_id="不是一个uuid", student_grade=3),
        pool=POOL_ATTRIBUTION,
    )
    assert isinstance(candidates, list)


def test_retrieval_survives_empty_text(db: Session):
    """目标串为空 → 不崩，返回空列表（没有共同片段 → 无相关候选）。"""
    _make_question(
        db,
        content="小明在文具店买了 3 支铅笔。",
        grade=3,
    )
    db.flush()

    candidates = search_candidates(
        db,
        GroundingQuery(text="", student_grade=3),
        pool=POOL_TEACHING,
    )
    assert isinstance(candidates, list)
    assert candidates == []


def test_archived_questions_are_excluded(db: Session):
    """``status != active`` 的题不进素材池。"""
    tag = _tag()
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔。{tag}",
        grade=3,
        status="archived",
    )
    db.flush()

    candidates = search_candidates(
        db,
        GroundingQuery(text=tag, student_grade=3),
        pool=POOL_TEACHING,
    )
    assert candidates == []


def test_cache_returns_same_result(db: Session):
    """进程内缓存：同一查询两次返回同一结果（且不重复查库）。"""
    tag = _tag()
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔。{tag}",
        grade=3,
    )
    db.flush()

    query = GroundingQuery(text=tag, student_grade=3)
    first = search_candidates(db, query, pool=POOL_TEACHING)
    second = search_candidates(db, query, pool=POOL_TEACHING)
    assert [c.question_id for c in first] == [c.question_id for c in second]
    assert [c.score for c in first] == [c.score for c in second]


def test_cache_key_includes_grade_window(db: Session):
    """缓存键**必须**含年级窗口 —— 不同年级的结果绝不能互相复用。

    这是安全边界：3 年级窗口 (2,3,4) 与 5 年级窗口 (4,5,6) 重叠，
    若缓存键不含窗口，5 年级的查询可能命中 3 年级缓存的旧结果。
    """
    tag = _tag()
    kp = _make_kp(db, description="加减法")
    _make_question(
        db,
        content=f"小明在文具店买了 3 支铅笔。{tag}",
        grade=2,
        kp_id=kp.id,
    )
    _make_question(
        db,
        content=f"小红在超市买了 4 盒牛奶。{tag}",
        grade=6,
        kp_id=kp.id,
    )
    db.flush()

    grade3 = GroundingQuery(text=tag, student_grade=3)
    grade5 = GroundingQuery(text=tag, student_grade=5)
    assert grade3.fingerprint(POOL_TEACHING) != grade5.fingerprint(POOL_TEACHING)

    lower = search_candidates(db, grade3, pool=POOL_TEACHING)
    upper = search_candidates(db, grade5, pool=POOL_TEACHING)
    assert {c.grade for c in lower} <= {2, 3, 4}
    assert {c.grade for c in upper} <= {4, 5, 6}
