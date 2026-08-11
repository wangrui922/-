"""T03 单元测试：知识点自动归因批处理（``app/scripts/backfill_kp.py``）。

覆盖 L2 二级分类方案：
    * Level 1 规则词典 ``classify_by_rules``：93 条规则（13 L1 priority 0–30 +
      80 L2 priority 100–199），L2 天然压过 L1；消歧边界、PRD §5.4 回归
    * Level 2 方舟兜底 ``classify_by_ark``：两段式（13 选 1 → 子类选 1），
      **全程 mock，零网络零 HTTP**
    * Level 3 批处理 ``backfill``：落库 / dry-run / kp_verified=0 /
      ``--relabel-l1`` / ``--no-ark`` / 错题同步

零网络硬性约束：
    ``backfill_kp.py`` 用 ``from app.ai.ark_client import ark_respond`` 导入，
    函数对象在 import 时已复制进该模块命名空间，因此必须 patch
    ``app.scripts.backfill_kp.ark_respond``（"patch where it's used"）。
    本文件所有会走到 Level 2 的用例都通过 ``_patch_ark`` 打桩；
    另有 ``test_no_real_http_client_is_created`` 把 ``httpx.Client`` 换成炸弹，
    确保测试过程中不会有任何真实 HTTP 连接被建立。

数据隔离：
    ``backfill`` 的统计口径是"全库 kp_id 为空的题"，跑在 conftest 的会话级共享
    内存库上会被别的用例污染。因此批处理用例一律使用 ``iso_db`` 夹具——一个
    独立的内存 SQLite 引擎，计数完全确定。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.ark_client import ArkError
from app.models.base import Base
from app.models.content import KnowledgePoint, Question
from app.models.learning import Mistake
from app.models.user import Student, User
from app.scripts import backfill_kp as bk

#: uuid5 命名空间（与 seed_db.py 一致，design §K1）
_NS = uuid.NAMESPACE_DNS

#: 种子文件路径
SEED_PATH: Path = Path(__file__).resolve().parent.parent / "seed" / "knowledge_points.json"

#: 13 个 L1 编码（与 seed/knowledge_points.json 完全一致）
ALL_L1_CODES: List[str] = [
    "KP_CALC",
    "KP_EQUATION",
    "KP_WORD",
    "KP_FRACTION",
    "KP_DECIMAL",
    "KP_PERCENT",
    "KP_RATIO",
    "KP_GEOMETRY",
    "KP_SHAPE",
    "KP_STATISTICS",
    "KP_UNIT",
    "KP_NUMBER_THEORY",
    "KP_MIXED",
]

#: 期望规则总数 / L1 规则数 / L2 规则数
EXPECT_TOTAL_RULES: int = 93
EXPECT_L1_RULES: int = 13
EXPECT_L2_RULES: int = 80

#: PRD §5.4 三个回归示例：(content, expected_code)
PRD_EXAMPLES: List[Any] = [
    ("解方程：3x+5=20，求x的值", "KP_EQ_LINEAR1"),
    ("用简便方法计算 25×32×125", "KP_CALC_SIMPLE"),
    ("比例尺 1:5000，求实际距离", "KP_RATIO_SCALE"),
]

#: L2 分类样本：(content, expected_code) —— L2 priority 100–199 天然压过 L1
L2_CASES: List[Any] = [
    ("用简便方法计算 25×32×125", "KP_CALC_SIMPLE"),
    ("解方程：3x+5=20，求x的值", "KP_EQ_LINEAR1"),
    ("比例尺 1:5000，求实际距离", "KP_RATIO_SCALE"),
    ("3千米等于多少米", "KP_UNIT_LENGTH"),
]

#: 仅命中 L1（无 L2 命中）的样本：(content, expected_code)
L1_ONLY_CASES: List[Any] = [
    ("买了3本书，一共花了24元，还剩多少元", "KP_WORD"),
    ("合计", "KP_UNIT"),
]


# ================================================================== 夹具 / 工具
class FakeArkRespond:
    """``ark_respond`` 的假实现：只记录调用、返回预设文本，绝不发网络请求。

    支持两种模式：
        * ``reply="KP_CALC"``：每次调用都返回同一文本（向后兼容单段式用例）。
        * ``replies=["KP_CALC", "KP_CALC_SIMPLE"]``：按序弹出，供两段式方舟使用。

    Attributes:
        exc: 非 ``None`` 时每次调用直接抛出该异常（模拟方舟故障）。
        calls: 每次调用的关键字参数记录。
    """

    def __init__(
        self,
        reply: Optional[str] = None,
        *,
        replies: Optional[List[str]] = None,
        exc: Optional[BaseException] = None,
    ) -> None:
        self._static_reply: Optional[str] = reply
        self._replies: Optional[List[str]] = list(replies) if replies is not None else None
        self.exc: Optional[BaseException] = exc
        self.calls: List[Dict[str, Any]] = []

    def __call__(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        image_b64: Optional[str] = None,
        timeout: float = 60.0,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "image_b64": image_b64,
                "timeout": timeout,
            }
        )
        if self.exc is not None:
            raise self.exc
        if self._replies is not None and self._replies:
            return self._replies.pop(0)
        if self._static_reply is not None:
            return self._static_reply
        return ""

    @property
    def count(self) -> int:
        """被调用了几次。"""
        return len(self.calls)


def _patch_ark(monkeypatch, fake: Optional[FakeArkRespond] = None) -> FakeArkRespond:
    """把 ``backfill_kp`` 模块内的 ``ark_respond`` 换成假实现。

    Args:
        monkeypatch: pytest 的 monkeypatch 夹具。
        fake: 自定义假实现，缺省用 ``FakeArkRespond()``。

    Returns:
        实际生效的 ``FakeArkRespond``，可用于断言调用次数与入参。
    """
    ark = fake if fake is not None else FakeArkRespond()
    monkeypatch.setattr(bk, "ark_respond", ark)
    return ark


@pytest.fixture(scope="module")
def rules() -> List[Dict[str, Any]]:
    """加载一次规则词典，模块内共享（纯读，不会被用例改写）。"""
    return bk._load_rules()


@pytest.fixture
def iso_db() -> Iterator[Session]:
    """独立的内存 SQLite 会话，保证批处理统计口径干净可预期。

    不复用 conftest 的会话级共享内存库：``backfill`` 会扫全库 ``kp_id IS NULL``
    的题，共享库里别的用例插的题会让计数漂移。
    """
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _kp_uuid(code: str) -> uuid.UUID:
    """确定性主键（与 seed_db.kp_uuid 同公式，design §K1）。"""
    return uuid.uuid5(_NS, "aicoach:kp:" + code)


def _seed_kps(db: Session) -> Dict[str, uuid.UUID]:
    """把 93 个知识点写进库（两趟：先 L1 flush 再 L2），返回 ``{kp_code: kp_id}``。"""
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        seed = json.load(f)
    kp_raw = seed["knowledgePoints"]

    mapping: Dict[str, uuid.UUID] = {}
    # 两趟写入（与 seed_db.py 一致）：先 L1 flush 再 L2，保证父行先落库
    for is_l1_pass in (True, False):
        for kp in kp_raw:
            if (kp["parentCode"] is None) != is_l1_pass:
                continue
            kp_id = _kp_uuid(kp["code"])
            parent_id = _kp_uuid(kp["parentCode"]) if kp["parentCode"] else None
            db.add(
                KnowledgePoint(
                    id=kp_id,
                    name=kp["name"],
                    code=kp["code"],
                    sort=kp.get("sort", 0),
                    description=kp.get("description"),
                    parent_id=parent_id,
                )
            )
        db.flush()
    db.commit()

    for kp in kp_raw:
        mapping[kp["code"]] = _kp_uuid(kp["code"])
    return mapping


def _add_question(db: Session, content: str, *, kp_id: Optional[uuid.UUID] = None,
                  kp_verified: bool = True) -> uuid.UUID:
    """插一道题并返回其 id。``kp_id`` 缺省为空（即待归因）。"""
    question = Question(
        content=content,
        subject="数学",
        grade=4,
        difficulty=3,
        type_="计算",
        kp_id=kp_id,
        kp_verified=kp_verified,
    )
    db.add(question)
    db.flush()
    db.commit()
    return question.id


def _add_student(db: Session, phone: str = "13900000001") -> uuid.UUID:
    """建一个学生（含所属账号），返回 ``students.id``。"""
    user = User(phone=phone, nickname="测试学生", role="student", status="active")
    db.add(user)
    db.flush()
    student = Student(user_id=user.id, real_name="测试学生", grade=4, stage="primary")
    db.add(student)
    db.flush()
    db.commit()
    return student.id


def _add_mistake(db: Session, student_id: uuid.UUID, question_id: uuid.UUID,
                 *, kp_id: Optional[uuid.UUID] = None) -> uuid.UUID:
    """建一条错题，返回其 id。``kp_id`` 缺省为空。"""
    mistake = Mistake(
        student_id=student_id,
        question_id=question_id,
        error_type="计算错",
        error_reason="进位出错",
        kp_id=kp_id,
    )
    db.add(mistake)
    db.flush()
    db.commit()
    return mistake.id


# ================================================================== 词典自身
def test_rules_file_loads_and_has_93_rules(rules):
    """规则词典可加载，且正好是 93 条（13 L1 + 80 L2）。"""
    assert isinstance(rules, list), rules
    assert len(rules) == EXPECT_TOTAL_RULES, f"应为 93 条规则，实际 {len(rules)}"

    l1_rules = [r for r in rules if r.get("priority", 0) <= 30]
    l2_rules = [r for r in rules if r.get("priority", 0) >= 100]
    assert len(l1_rules) == EXPECT_L1_RULES, f"L1 规则应为 13 条，实际 {len(l1_rules)}"
    assert len(l2_rules) == EXPECT_L2_RULES, f"L2 规则应为 80 条，实际 {len(l2_rules)}"


def test_rules_codes_match_seed_knowledge_points(rules):
    """词典的 93 个 ``kp_code`` 与 ``seed/knowledge_points.json`` 完全对齐。"""
    seed_path = bk.BACKEND_DIR / "seed" / "knowledge_points.json"
    with open(seed_path, "r", encoding="utf-8") as handle:
        seed = json.load(handle)

    seed_codes = {item["code"] for item in seed["knowledgePoints"]}
    rule_codes = {rule["kp_code"] for rule in rules}
    assert rule_codes == seed_codes, f"词典与 seed 知识点不一致: {rule_codes ^ seed_codes}"


def test_every_rule_has_keywords_and_priority(rules):
    """每条规则都必须有非空 keywords 和整型 priority。"""
    for rule in rules:
        code = rule["kp_code"]
        assert rule.get("keywords"), f"{code} 的 keywords 为空"
        assert all(isinstance(kw, str) and kw for kw in rule["keywords"]), f"{code} 有空关键词"
        assert isinstance(rule.get("priority"), int), f"{code} 的 priority 不是整数"


def test_l1_priority_in_0_to_30(rules):
    """L1 规则的 priority 必须在 [0, 30] 区间（design §K3）。"""
    l1_rules = [r for r in rules if r["kp_code"] in ALL_L1_CODES]
    for rule in l1_rules:
        p = rule["priority"]
        assert 0 <= p <= 30, f"L1 规则 {rule['kp_code']} priority={p} 不在 [0,30]"


def test_l2_priority_in_100_to_199(rules):
    """L2 规则的 priority 必须在 [100, 199] 区间（design §K3）。"""
    l2_rules = [r for r in rules if r["kp_code"] not in ALL_L1_CODES]
    for rule in l2_rules:
        p = rule["priority"]
        assert 100 <= p <= 199, f"L2 规则 {rule['kp_code']} priority={p} 不在 [100,199]"


def test_l2_priority_always_higher_than_l1(rules):
    """不变式：max(L1.priority) < min(L2.priority) —— 任一 L2 命中必胜任一 L1。"""
    l1_max = max(r["priority"] for r in rules if r["kp_code"] in ALL_L1_CODES)
    l2_min = min(r["priority"] for r in rules if r["kp_code"] not in ALL_L1_CODES)
    assert l1_max < l2_min, f"max(L1)={l1_max} 不小于 min(L2)={l2_min}"


def test_no_single_char_keywords(rules):
    """红线（design §K4）：全部关键词长度 >= 2，禁单字词。"""
    for rule in rules:
        for kw in rule["keywords"]:
            assert len(kw) >= 2, f"{rule['kp_code']} 有单字关键词: {kw!r}"


def test_load_rules_raises_when_file_missing(monkeypatch, tmp_path):
    """词典文件不存在 → ``FileNotFoundError``。"""
    monkeypatch.setattr(bk, "RULES_PATH", tmp_path / "nope.json")
    with pytest.raises(FileNotFoundError):
        bk._load_rules()


def test_load_rules_raises_when_rules_empty(monkeypatch, tmp_path):
    """词典 ``rules`` 字段为空 → ``ValueError``。"""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": "2.0.0", "rules": []}), encoding="utf-8")
    monkeypatch.setattr(bk, "RULES_PATH", bad)
    with pytest.raises(ValueError):
        bk._load_rules()


# ================================================================== Level 1 规则分类
@pytest.mark.parametrize(("content", "expected_code"), L2_CASES)
def test_classify_by_rules_l2_beats_l1(rules, content, expected_code):
    """L2 priority (100–199) 天然压过 L1 (0–30)：简便运算题归到 L2 而非 L1。"""
    got = bk.classify_by_rules(content, rules)
    assert got == expected_code, f"文本 {content!r} 期望 {expected_code}，实际 {got}"


@pytest.mark.parametrize(("content", "expected_code"), PRD_EXAMPLES)
def test_classify_by_rules_prd_examples(rules, content, expected_code):
    """PRD §5.4 三个校验示例回归。"""
    got = bk.classify_by_rules(content, rules)
    assert got == expected_code, f"PRD 回归失败: {content!r} 期望 {expected_code}，实际 {got}"


@pytest.mark.parametrize(("content", "expected_code"), L1_ONLY_CASES)
def test_classify_by_rules_l1_only_texts(rules, content, expected_code):
    """仅命中 L1 关键词（无 L2 命中）时正确返回 L1 编码。"""
    got = bk.classify_by_rules(content, rules)
    assert got == expected_code, f"文本 {content!r} 期望 {expected_code}，实际 {got}"


@pytest.mark.parametrize("content", ["", None])
def test_classify_by_rules_empty_content_returns_none(rules, content):
    """边界：空内容 / None → 直接返回 None，不抛异常。"""
    assert bk.classify_by_rules(content, rules) is None


def test_classify_by_rules_empty_rules_returns_none():
    """边界：规则为空列表 → 返回 None。"""
    assert bk.classify_by_rules("解方程 3x = 9", []) is None


@pytest.mark.parametrize(
    "content",
    ["ABCDEFG", "小猫在窗台上睡觉", "今天星期三", "   "],
)
def test_classify_by_rules_no_match_returns_none(rules, content):
    """边界：任何桶都不命中 → 返回 None（交方舟兜底）。"""
    assert bk.classify_by_rules(content, rules) is None


def test_classify_by_rules_higher_priority_wins_over_more_hits_l1(rules):
    """多桶命中：L1 priority 高的胜出，即使命中数更少。

    "买了花了还剩每支多少元合计"
        KP_WORD priority=5  命中 5 个（买了/花了/还剩/每支/多少元）
        KP_UNIT priority=30 命中 1 个（合计）
    → 按 priority 消歧，返回 KP_UNIT。
    """
    got = bk.classify_by_rules("买了花了还剩每支多少元合计", rules)
    assert got == "KP_UNIT", got


def test_classify_by_rules_higher_priority_wins_over_more_hits_l2(rules):
    """多桶命中：L2 priority 高的胜出，即使命中数更少。

    "简便方法简便计算简算凑整巧算交换律"
        KP_CALC_SIMPLE priority=150 命中 5 个
        KP_CALC_LAW    priority=180 命中 1 个（交换律）
    → 返回 KP_CALC_LAW。
    """
    got = bk.classify_by_rules("简便方法简便计算简算凑整巧算交换律", rules)
    assert got == "KP_CALC_LAW", got


def test_kp_unit_has_no_bare_he_keyword(rules):
    """回归：KP_UNIT 不得再出现单字「合」。

    KP_UNIT 的 priority=30 是 L1 中最高，而消歧是 priority 压倒命中数。
    单字「合」会让任何含「合数/组合/集合/符合/适合」的题干只凭 1 个命中
    就压过命中数远多的正确桶，是真实的误判地雷。改用多字词「合计」。
    """
    unit_rule = next(rule for rule in rules if rule["kp_code"] == "KP_UNIT")
    keywords = unit_rule["keywords"]

    assert "合" not in keywords, "KP_UNIT 不能含单字「合」（priority=30 会压垮正确桶）"
    assert "合计" in keywords, "应保留多字词「合计」守住合计类语义"


@pytest.mark.parametrize(
    "content",
    [
        "求 12 和 18 的最大公因数，判断 17 是质数还是合数",
        "从 5 个球里任取 2 个，一共有多少种不同的组合",
        "这个集合里一共有多少个元素",
    ],
)
def test_he_shu_family_not_misrouted_to_unit(rules, content):
    """回归：「合数 / 组合 / 集合」类题干不再被误判成单位换算。"""
    got = bk.classify_by_rules(content, rules)
    assert got != "KP_UNIT", f"{content!r} 不该归到 KP_UNIT，实际 {got}"


def test_he_ji_still_matches_unit(rules):
    """「合计」语义仍被 KP_UNIT 兜住，改词典没有丢掉原有覆盖。"""
    assert bk.classify_by_rules("合计", rules) == "KP_UNIT"


def test_classify_by_rules_same_priority_more_hits_wins(rules):
    """多桶命中：priority 相同时，命中数多的胜出（L2 间消歧）。

    "同分母分数约分通分"
        KP_FRAC_ADD_SUB  priority=150 命中 1 个（同分母分数）
        KP_FRAC_PROPERTY  priority=150 命中 2 个（约分/通分）
    → 返回 KP_FRAC_PROPERTY。
    """
    got = bk.classify_by_rules("同分母分数约分通分", rules)
    assert got == "KP_FRAC_PROPERTY", got


def test_classify_by_rules_unresolvable_tie_returns_none(rules):
    """多桶命中：priority 与命中数都相同 → 不可消歧，返回 None 交方舟兜底。

    "分数和小数"：KP_FRACTION(25, 1 命中) 与 KP_DECIMAL(25, 1 命中) 打平。
    """
    assert bk.classify_by_rules("分数和小数", rules) is None


def test_classify_by_rules_single_bucket_shortcut(rules):
    """只命中一个桶时走 ``len(scored) == 1`` 短路分支，直接返回该桶。

    "合计" 只命中 KP_UNIT（无 L2 命中 KP_UNIT 的"合计"关键词）。
    """
    assert bk.classify_by_rules("合计", rules) == "KP_UNIT"


def test_classify_by_rules_ignores_rules_without_keywords(rules):
    """规则缺 keywords 时被跳过，不影响其余桶的判定。"""
    patched = [{"kp_code": "KP_EMPTY", "keywords": [], "priority": 999}] + list(rules)
    assert bk.classify_by_rules("解方程：3x - 5 = 7", patched) == "KP_EQ_LINEAR1"


def test_classify_by_rules_is_pure_and_does_not_mutate_rules(rules):
    """分类是纯函数：不得改写传入的规则词典。"""
    before = json.dumps(rules, ensure_ascii=False, sort_keys=True)
    bk.classify_by_rules("求 12 与 18 的最大公因数、最小公倍数", rules)
    after = json.dumps(rules, ensure_ascii=False, sort_keys=True)
    assert before == after, "classify_by_rules 不应修改 rules"


# ================================================================== _pick_code
def test_pick_code_longest_first():
    """``_pick_code`` 最长前缀匹配：KP_CALC_ADD 不被 KP_CALC 抢先命中。"""
    valid_codes = {"KP_CALC", "KP_CALC_ADD"}
    assert bk._pick_code("KP_CALC_ADD", valid_codes) == "KP_CALC_ADD"


def test_pick_code_fuzzy_longest_first():
    """模糊匹配也遵循最长优先。"""
    valid_codes = {"KP_CALC", "KP_CALC_ADD", "KP_WORD"}
    assert bk._pick_code("这道题归到 KP_CALC_ADD 没问题", valid_codes) == "KP_CALC_ADD"


def test_pick_code_exact_match():
    """精确匹配优先于模糊匹配。"""
    valid_codes = {"KP_CALC", "KP_CALC_ADD"}
    assert bk._pick_code("KP_CALC", valid_codes) == "KP_CALC"


def test_pick_code_empty_reply():
    """空回复 → None。"""
    assert bk._pick_code("", {"KP_CALC"}) is None


def test_pick_code_no_valid_codes():
    """空编码集合 → None。"""
    assert bk._pick_code("KP_CALC", set()) is None


# ================================================================== Level 2 方舟兜底（全 mock）
#: 两段式方舟用例的假知识点树（3 L1 + 2 L2）
_FAKE_L1_CALC_ID = uuid.uuid4()
_FAKE_L1_EQ_ID = uuid.uuid4()
_FAKE_L1_GEO_ID = uuid.uuid4()
_FAKE_L2_SIMPLE_ID = uuid.uuid4()
_FAKE_L2_LINEAR1_ID = uuid.uuid4()

FAKE_KP_TREE: List[Dict[str, Any]] = [
    {
        "id": _FAKE_L1_CALC_ID,
        "code": "KP_CALC",
        "name": "计算",
        "description": "四则运算",
        "parent_id": None,
        "parent_code": None,
    },
    {
        "id": _FAKE_L1_EQ_ID,
        "code": "KP_EQUATION",
        "name": "方程",
        "description": "解方程",
        "parent_id": None,
        "parent_code": None,
    },
    {
        "id": _FAKE_L1_GEO_ID,
        "code": "KP_GEOMETRY",
        "name": "几何",
        "description": "图形测量",
        "parent_id": None,
        "parent_code": None,
    },
    {
        "id": _FAKE_L2_SIMPLE_ID,
        "code": "KP_CALC_SIMPLE",
        "name": "简便运算",
        "description": "简算技巧",
        "parent_id": _FAKE_L1_CALC_ID,
        "parent_code": "KP_CALC",
    },
    {
        "id": _FAKE_L2_LINEAR1_ID,
        "code": "KP_EQ_LINEAR1",
        "name": "一元一次方程",
        "description": "解一元一次方程",
        "parent_id": _FAKE_L1_EQ_ID,
        "parent_code": "KP_EQUATION",
    },
]

#: L1 无子节点的假树（只含 KP_GEOMETRY）
FAKE_NO_CHILD_TREE: List[Dict[str, Any]] = [
    {
        "id": _FAKE_L1_GEO_ID,
        "code": "KP_GEOMETRY",
        "name": "几何",
        "description": "图形测量",
        "parent_id": None,
        "parent_code": None,
    },
]


def test_classify_by_ark_two_stage_returns_l2(monkeypatch):
    """两段式方舟：段①选 L1，段②选 L2，返回 L2 code。"""
    ark = _patch_ark(monkeypatch, FakeArkRespond(replies=["KP_CALC", "KP_CALC_SIMPLE"]))
    got = bk.classify_by_ark("用简便方法计算 25×32×125", FAKE_KP_TREE)
    assert got == "KP_CALC_SIMPLE", got
    assert ark.count == 2, "两段式应调两次方舟"


def test_classify_by_ark_l2_miss_degrades_to_l1(monkeypatch):
    """段②未命中 → 降级返回段①的 L1 code（保 recall，不返回 None）。"""
    ark = _patch_ark(monkeypatch, FakeArkRespond(replies=["KP_CALC", "KP_GHOST"]))
    got = bk.classify_by_ark("一道计算题", FAKE_KP_TREE)
    assert got == "KP_CALC", f"段②失败应降级到 L1，实际 {got}"
    assert ark.count == 2


def test_classify_by_ark_no_children_returns_l1(monkeypatch):
    """L1 无子节点 → 不调段②，直接返回 L1 code。"""
    ark = _patch_ark(monkeypatch, FakeArkRespond(reply="KP_GEOMETRY"))
    got = bk.classify_by_ark("求长方形的面积", FAKE_NO_CHILD_TREE)
    assert got == "KP_GEOMETRY", got
    assert ark.count == 1, "无子节点时只调一次方舟"


def test_classify_by_ark_l1_miss_returns_none(monkeypatch):
    """段①未命中 → 直接返回 None，不调段②。"""
    ark = _patch_ark(monkeypatch, FakeArkRespond(reply="KP_GHOST"))
    got = bk.classify_by_ark("一道题", FAKE_KP_TREE)
    assert got is None, "段①失败应返回 None"
    assert ark.count == 1, "段①失败只调一次方舟"


def test_classify_by_ark_strips_whitespace(monkeypatch):
    """方舟返回带首尾空白 → strip 后仍能精确匹配。"""
    _patch_ark(monkeypatch, FakeArkRespond(replies=["  KP_CALC \n", "  KP_CALC_SIMPLE  "]))
    assert bk.classify_by_ark("一道计算题", FAKE_KP_TREE) == "KP_CALC_SIMPLE"


def test_classify_by_ark_fuzzy_match_in_sentence(monkeypatch):
    """方舟啰嗦地回一句话 → 模糊匹配能从中捞出合法编码。"""
    _patch_ark(
        monkeypatch,
        FakeArkRespond(replies=["我认为是 KP_CALC", "归到 KP_CALC_SIMPLE 没错"]),
    )
    assert bk.classify_by_ark("一道计算题", FAKE_KP_TREE) == "KP_CALC_SIMPLE"


def test_classify_by_ark_invalid_code_returns_none(monkeypatch):
    """方舟返回封闭集合之外的编码 → 返回 None。"""
    _patch_ark(monkeypatch, FakeArkRespond(reply="KP_NOT_EXIST"))
    assert bk.classify_by_ark("随便一道题", FAKE_KP_TREE) is None


def test_classify_by_ark_empty_reply_returns_none(monkeypatch):
    """方舟返回空串 → 返回 None。"""
    _patch_ark(monkeypatch, FakeArkRespond(reply=""))
    assert bk.classify_by_ark("随便一道题", FAKE_KP_TREE) is None


def test_classify_by_ark_swallows_ark_error(monkeypatch):
    """方舟抛 ``ArkError``（Key 未配置 / 上游 5xx）→ 吞掉并返回 None。"""
    _patch_ark(monkeypatch, FakeArkRespond(exc=ArkError("ARK_API_KEY 未配置")))
    assert bk.classify_by_ark("随便一道题", FAKE_KP_TREE) is None


def test_classify_by_ark_swallows_generic_exception(monkeypatch):
    """方舟抛任意异常（如网络超时）→ 同样吞掉并返回 None。"""
    _patch_ark(monkeypatch, FakeArkRespond(exc=RuntimeError("connection reset")))
    assert bk.classify_by_ark("随便一道题", FAKE_KP_TREE) is None


@pytest.mark.parametrize(("content", "kp_tree"), [("", FAKE_KP_TREE), ("有内容", [])])
def test_classify_by_ark_short_circuits_without_calling_ark(monkeypatch, content, kp_tree):
    """边界：内容为空或知识点表为空 → 直接返回 None，**不发起方舟调用**。"""
    ark = _patch_ark(monkeypatch, FakeArkRespond(reply="KP_CALC"))
    assert bk.classify_by_ark(content, kp_tree) is None
    assert ark.count == 0, "短路分支不应调用方舟"


def test_classify_by_ark_two_stage_prompt_is_closed_choice(monkeypatch):
    """方舟入参校验：段① system 说明单选、user 带全部 L1 封闭选项；
    段② user 只带选中 L1 的子类选项，且 system 含范围提示。"""
    ark = _patch_ark(monkeypatch, FakeArkRespond(replies=["KP_CALC", "KP_CALC_SIMPLE"]))
    content = "用简便方法计算 25×32×125"
    bk.classify_by_ark(content, FAKE_KP_TREE)

    assert ark.count == 2

    # 段①
    call1 = ark.calls[0]
    assert call1["system"] and "只回答知识点的编码" in call1["system"]
    assert call1["timeout"] == bk.ARK_TIMEOUT
    assert call1["image_b64"] is None, "分类不该传图"
    user_msg1 = call1["messages"][0]["content"]
    assert call1["messages"][0]["role"] == "user"
    assert content in user_msg1, "题干必须进 prompt"
    for code in ("KP_CALC", "KP_EQUATION", "KP_GEOMETRY"):
        assert code in user_msg1, f"段①封闭选项缺少 {code}"

    # 段② —— 只含选中 L1 的子类
    call2 = ark.calls[1]
    user_msg2 = call2["messages"][0]["content"]
    assert "KP_CALC_SIMPLE" in user_msg2, "段②应含子类选项"
    assert "KP_EQ_LINEAR1" not in user_msg2, "段②不应含其他 L1 的子类"
    assert "这道题已经确定属于" in call2["system"], "段② system 应含范围提示"


# ================================================================== _load_kp_list
def test_load_kp_list_reads_all_93(iso_db):
    """``_load_kp_list`` 读出全部 93 个带 code 的知识点。"""
    _seed_kps(iso_db)
    kp_list = bk._load_kp_list(iso_db)

    assert len(kp_list) == EXPECT_TOTAL_RULES, f"应读出 93 个知识点，实际 {len(kp_list)}"
    codes = [kp["code"] for kp in kp_list]
    assert len(set(codes)) == EXPECT_TOTAL_RULES, "code 不唯一"


def test_load_kp_list_includes_parent_info(iso_db):
    """L1 的 parent_id/parent_code 为 None，L2 的非 None。"""
    _seed_kps(iso_db)
    kp_list = bk._load_kp_list(iso_db)

    l1_nodes = [kp for kp in kp_list if kp["parent_id"] is None]
    l2_nodes = [kp for kp in kp_list if kp["parent_id"] is not None]
    assert len(l1_nodes) == EXPECT_L1_RULES, f"L1 应 13 个，实际 {len(l1_nodes)}"
    assert len(l2_nodes) == EXPECT_L2_RULES, f"L2 应 80 个，实际 {len(l2_nodes)}"
    assert all(kp["parent_code"] is None for kp in l1_nodes), "L1 parent_code 应为 None"
    assert all(kp["parent_code"] is not None for kp in l2_nodes), "L2 parent_code 应非 None"


def test_load_kp_list_skips_rows_without_code(iso_db):
    """没有 ``code`` 的知识点不进封闭选项集合。"""
    _seed_kps(iso_db)
    iso_db.add(KnowledgePoint(name="无编码知识点", code=None, sort=999))
    iso_db.commit()

    codes = [kp["code"] for kp in bk._load_kp_list(iso_db)]
    assert None not in codes
    assert len(codes) == EXPECT_TOTAL_RULES, codes


# ================================================================== Level 3 批处理
def test_backfill_dry_run_does_not_write(monkeypatch, iso_db):
    """dry-run：统计正常产出，但一行都不落库。"""
    ark = _patch_ark(monkeypatch)
    _seed_kps(iso_db)
    qid = _add_question(iso_db, "用简便方法计算 25×32×125")

    stats = bk.backfill(iso_db, dry_run=True)

    assert stats["total_empty"] == 1, stats
    assert stats["rule_matched"] == 1, stats
    assert stats["ark_matched"] == 0, stats
    assert stats["unmatched"] == 0, stats
    assert stats["distribution"] == {"KP_CALC_SIMPLE": 1}, stats
    assert ark.count == 0, "规则命中就不该调方舟"

    iso_db.rollback()
    kp_id = iso_db.execute(select(Question.kp_id).where(Question.id == qid)).scalar_one()
    assert kp_id is None, "dry-run 不应落库"


def test_backfill_writes_kp_id_with_kp_verified_false(monkeypatch, iso_db):
    """落库路径：写入 ``kp_id`` 且 ``kp_verified`` 一律为 0（归因安全）。"""
    _patch_ark(monkeypatch)
    kp_map = _seed_kps(iso_db)
    qid = _add_question(iso_db, "3千米等于多少米")

    stats = bk.backfill(iso_db, dry_run=False)
    assert stats["rule_matched"] == 1, stats

    iso_db.rollback()
    row = iso_db.execute(
        select(Question.kp_id, Question.kp_verified).where(Question.id == qid)
    ).one()
    assert row.kp_id == kp_map["KP_UNIT_LENGTH"], row
    assert bool(row.kp_verified) is False, "自动归因必须 kp_verified=0，不能污染可归因池"


def test_backfill_l2_classification(monkeypatch, iso_db):
    """端到端：4 道 L2 + 1 道 L1，全部通过规则词典归到正确的 kp_id。"""
    ark = _patch_ark(monkeypatch)
    kp_map = _seed_kps(iso_db)

    questions: Dict[str, uuid.UUID] = {
        code: _add_question(iso_db, content)
        for content, code in [
            ("用简便方法计算 25×32×125", "KP_CALC_SIMPLE"),
            ("解方程：3x+5=20，求x的值", "KP_EQ_LINEAR1"),
            ("比例尺 1:5000，求实际距离", "KP_RATIO_SCALE"),
            ("3千米等于多少米", "KP_UNIT_LENGTH"),
            ("买了3本书，一共花了24元，还剩多少元", "KP_WORD"),
        ]
    }

    stats = bk.backfill(iso_db, dry_run=False)

    assert stats["total_empty"] == 5, stats
    assert stats["rule_matched"] == 5, stats
    assert stats["ark_matched"] == 0, stats
    assert stats["unmatched"] == 0, stats
    assert stats["l2_matched"] == 4, stats
    assert stats["l1_matched"] == 1, stats
    assert ark.count == 0, "全部走规则命中，不该有方舟调用"

    iso_db.rollback()
    for code, qid in questions.items():
        actual = iso_db.execute(select(Question.kp_id).where(Question.id == qid)).scalar_one()
        assert actual == kp_map[code], f"{code} 归类落库不正确: {actual} != {kp_map[code]}"


def test_backfill_falls_back_to_ark_when_rules_miss(monkeypatch, iso_db):
    """规则未命中 → 走方舟兜底，命中计入 ``ark_matched``。"""
    ark = _patch_ark(monkeypatch, FakeArkRespond(reply="KP_MIXED"))
    kp_map = _seed_kps(iso_db)
    qid = _add_question(iso_db, "小猫在窗台上睡觉")  # 任何桶都不命中

    stats = bk.backfill(iso_db, dry_run=False)

    assert stats["rule_matched"] == 0, stats
    assert stats["ark_matched"] == 1, stats
    assert stats["l1_matched"] == 1, stats  # KP_MIXED 是 L1
    assert stats["distribution"] == {"KP_MIXED": 1}, stats
    assert ark.count == 2, "两段式方舟应调两次（段①+段②）"

    iso_db.rollback()
    kp_id = iso_db.execute(select(Question.kp_id).where(Question.id == qid)).scalar_one()
    assert kp_id == kp_map["KP_MIXED"]


def test_backfill_leaves_kp_empty_when_both_levels_miss(monkeypatch, iso_db):
    """两级都未命中 → 计入 ``unmatched``，``kp_id`` 留空，不阻塞流程。"""
    _patch_ark(monkeypatch, FakeArkRespond(exc=ArkError("方舟不可用")))
    _seed_kps(iso_db)
    qid = _add_question(iso_db, "ABCDEFG")

    stats = bk.backfill(iso_db, dry_run=False)

    assert stats["unmatched"] == 1, stats
    assert stats["rule_matched"] == 0, stats
    assert stats["ark_matched"] == 0, stats
    assert stats["distribution"] == {}, stats

    iso_db.rollback()
    kp_id = iso_db.execute(select(Question.kp_id).where(Question.id == qid)).scalar_one()
    assert kp_id is None, "未命中的题必须留空"


def test_backfill_ignores_ark_code_outside_kp_map(monkeypatch, iso_db):
    """方舟返回库里不存在的编码 → 当作未命中，不写脏 kp_id。"""
    _patch_ark(monkeypatch, FakeArkRespond(reply="KP_GHOST"))
    _seed_kps(iso_db)
    qid = _add_question(iso_db, "小猫在窗台上睡觉")

    stats = bk.backfill(iso_db, dry_run=False)
    assert stats["unmatched"] == 1, stats

    iso_db.rollback()
    kp_id = iso_db.execute(select(Question.kp_id).where(Question.id == qid)).scalar_one()
    assert kp_id is None


def test_backfill_skips_questions_that_already_have_kp(monkeypatch, iso_db):
    """只处理 ``kp_id`` 为空的题；已归因的题不进统计也不被改写。"""
    _patch_ark(monkeypatch)
    kp_map = _seed_kps(iso_db)
    done = _add_question(iso_db, "解方程：3x+5=20", kp_id=kp_map["KP_CALC"])
    _add_question(iso_db, "3千米等于多少米")

    stats = bk.backfill(iso_db, dry_run=False)

    assert stats["total_empty"] == 1, "已归因的题不该被扫进来"

    iso_db.rollback()
    kept = iso_db.execute(select(Question.kp_id).where(Question.id == done)).scalar_one()
    assert kept == kp_map["KP_CALC"], "已归因的题不得被改写"


def test_backfill_respects_limit(monkeypatch, iso_db):
    """``limit`` 生效：只处理前 N 道空 kp 题。"""
    _patch_ark(monkeypatch)
    _seed_kps(iso_db)
    for content in ["解方程 3x = 9", "3千米等于多少米", "求 24 的因数"]:
        _add_question(iso_db, content)

    stats = bk.backfill(iso_db, dry_run=True, limit=2)
    assert stats["total_empty"] == 2, stats


def test_backfill_handles_empty_question_table(monkeypatch, iso_db):
    """空题库：统计全 0，不抛异常。"""
    _patch_ark(monkeypatch)
    _seed_kps(iso_db)

    stats = bk.backfill(iso_db, dry_run=False)
    assert stats["total_empty"] == 0
    assert stats["total_targets"] == 0
    assert stats["rule_matched"] == 0
    assert stats["ark_matched"] == 0
    assert stats["unmatched"] == 0
    assert stats["distribution"] == {}


def test_backfill_handles_null_content(monkeypatch, iso_db):
    """题干为空串的题：``content or ""`` 兜底，计入未命中而不是崩溃。"""
    _patch_ark(monkeypatch, FakeArkRespond(reply=""))
    _seed_kps(iso_db)
    _add_question(iso_db, "")

    stats = bk.backfill(iso_db, dry_run=False)
    assert stats["unmatched"] == 1, stats


# ---------------------------------------------------------------- --no-ark
def test_backfill_no_ark_skips_ark(monkeypatch, iso_db):
    """``--no-ark``：规则未命中时跳过方舟，直接计入 unmatched。"""
    ark = _patch_ark(monkeypatch, FakeArkRespond(reply="KP_MIXED"))
    _seed_kps(iso_db)
    _add_question(iso_db, "小猫在窗台上睡觉")  # 规则必然未命中

    stats = bk.backfill(iso_db, dry_run=False, no_ark=True)

    assert stats["unmatched"] == 1, stats
    assert stats["ark_matched"] == 0, stats
    assert ark.count == 0, "--no-ark 不该调方舟"


# ---------------------------------------------------------------- --relabel-l1
def test_backfill_relabel_l1_targets_l1_unverified(monkeypatch, iso_db):
    """``--relabel-l1``：停在 L1 且 kp_verified=False 的题也被纳入重打标。"""
    _patch_ark(monkeypatch)
    kp_map = _seed_kps(iso_db)

    # 停在 L1 且未复核 → 应被重打标
    q_relabel = _add_question(
        iso_db, "用简便方法计算 25×32×125",
        kp_id=kp_map["KP_CALC"], kp_verified=False,
    )
    # kp_id 为空 → 任何模式都处理
    q_empty = _add_question(iso_db, "3千米等于多少米")

    stats = bk.backfill(iso_db, dry_run=True, relabel_l1=True, no_ark=True)

    assert stats["total_targets"] == 2, "应处理 2 道（1 空 + 1 L1 未复核）"
    assert stats["rule_matched"] == 2, stats


def test_backfill_relabel_l1_skips_verified(monkeypatch, iso_db):
    """``--relabel-l1`` 红线：kp_verified=1（人工复核过）的题绝不被重打标。"""
    _patch_ark(monkeypatch)
    kp_map = _seed_kps(iso_db)

    # 已人工复核 → 不得被重打标
    q_verified = _add_question(
        iso_db, "用简便方法计算 25×32×125",
        kp_id=kp_map["KP_CALC"], kp_verified=True,
    )

    stats = bk.backfill(iso_db, dry_run=False, relabel_l1=True, no_ark=True)

    assert stats["total_targets"] == 0, "kp_verified=1 的题不该被扫进来"

    iso_db.rollback()
    kept = iso_db.execute(
        select(Question.kp_id, Question.kp_verified).where(Question.id == q_verified)
    ).one()
    assert kept.kp_id == kp_map["KP_CALC"], "复核过的题不得被改写"
    assert bool(kept.kp_verified) is True


def test_backfill_relabel_l1_idempotent(monkeypatch, iso_db):
    """``--relabel-l1`` 幂等：第一次把 L1 题升到 L2，第二次不再选中它。"""
    _patch_ark(monkeypatch)
    kp_map = _seed_kps(iso_db)
    qid = _add_question(
        iso_db, "用简便方法计算 25×32×125",
        kp_id=kp_map["KP_CALC"], kp_verified=False,
    )

    # 第一次：L1 → L2
    stats1 = bk.backfill(iso_db, dry_run=False, relabel_l1=True, no_ark=True)
    assert stats1["changed"] == 1, "第一次应改动 1 道"

    iso_db.rollback()
    actual = iso_db.execute(select(Question.kp_id).where(Question.id == qid)).scalar_one()
    assert actual == kp_map["KP_CALC_SIMPLE"], "应升级到 L2"

    # 第二次：题已在 L2（不在 13 个 L1 里），relabel 不再选中
    stats2 = bk.backfill(iso_db, dry_run=False, relabel_l1=True, no_ark=True)
    assert stats2["total_targets"] == 0, "已升到 L2 的题不该被重复处理"
    assert stats2["changed"] == 0


# ---------------------------------------------------------------- 二次回填错题
def test_backfill_propagates_kp_to_mistakes(monkeypatch, iso_db):
    """二次回填：题目归因后，``mistakes.kp_id`` 从题目继承。"""
    _patch_ark(monkeypatch)
    kp_map = _seed_kps(iso_db)
    student_id = _add_student(iso_db)
    qid = _add_question(iso_db, "3千米等于多少米")
    mid = _add_mistake(iso_db, student_id, qid)

    bk.backfill(iso_db, dry_run=False)

    iso_db.rollback()
    kp_id = iso_db.execute(select(Mistake.kp_id).where(Mistake.id == mid)).scalar_one()
    assert kp_id == kp_map["KP_UNIT_LENGTH"], "错题应从题目继承 kp_id"


def test_backfill_syncs_mistake_when_question_kp_changes(monkeypatch, iso_db):
    """重打标后题目 kp 从 L1 升到 L2，错题 kp_id 随之同步（design §4.3）。"""
    _patch_ark(monkeypatch)
    kp_map = _seed_kps(iso_db)
    student_id = _add_student(iso_db)
    qid = _add_question(iso_db, "3千米等于多少米")
    # 错题建档时从题目快照了 L1 的 KP_CALC（与题目将归到的 KP_UNIT_LENGTH 不同）
    mid = _add_mistake(iso_db, student_id, qid, kp_id=kp_map["KP_CALC"])

    bk.backfill(iso_db, dry_run=False)

    iso_db.rollback()
    kp_id = iso_db.execute(select(Mistake.kp_id).where(Mistake.id == mid)).scalar_one()
    assert kp_id == kp_map["KP_UNIT_LENGTH"], "题目 kp 变更（未复核）后错题应同步"


def test_backfill_preserves_mistake_when_question_verified(monkeypatch, iso_db):
    """题目 kp_verified=1 时，错题的已有 kp_id 不得被覆盖。"""
    _patch_ark(monkeypatch)
    kp_map = _seed_kps(iso_db)
    student_id = _add_student(iso_db)
    # 题目已复核，kp_id=KP_CALC
    qid = _add_question(
        iso_db, "3千米等于多少米",
        kp_id=kp_map["KP_CALC"], kp_verified=True,
    )
    # 错题有不同快照值
    mid = _add_mistake(iso_db, student_id, qid, kp_id=kp_map["KP_UNIT"])

    # 默认模式不选已归因题，relabel 模式不选 verified 题
    bk.backfill(iso_db, dry_run=False, relabel_l1=True, no_ark=True)

    iso_db.rollback()
    kp_id = iso_db.execute(select(Mistake.kp_id).where(Mistake.id == mid)).scalar_one()
    assert kp_id == kp_map["KP_UNIT"], "题目已复核时错题 kp_id 不得被覆盖"


def test_backfill_dry_run_skips_mistake_propagation(monkeypatch, iso_db):
    """dry-run 不做二次回填，``mistakes.kp_id`` 保持为空。"""
    _patch_ark(monkeypatch)
    _seed_kps(iso_db)
    student_id = _add_student(iso_db)
    qid = _add_question(iso_db, "3千米等于多少米")
    mid = _add_mistake(iso_db, student_id, qid)

    bk.backfill(iso_db, dry_run=True)

    iso_db.rollback()
    kp_id = iso_db.execute(select(Mistake.kp_id).where(Mistake.id == mid)).scalar_one()
    assert kp_id is None, "dry-run 不该动错题"


# ================================================================== 报告 & 零网络
def test_print_report_runs_for_both_modes(capsys):
    """``_print_report`` 两种模式都能正常输出，不抛异常。"""
    stats = {
        "total_empty": 3,
        "total_targets": 3,
        "rule_matched": 2,
        "ark_matched": 1,
        "unmatched": 0,
        "kept_original": 0,
        "changed": 3,
        "l1_matched": 1,
        "l2_matched": 2,
        "l2_fallback_matched": 0,
        "distribution": {"KP_CALC": 1, "KP_CALC_SIMPLE": 1, "KP_UNIT": 1},
        "relabel_l1": False,
        "no_ark": False,
    }
    bk._print_report(stats, dry_run=True)
    bk._print_report(stats, dry_run=False)

    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "KP_CALC" in out and "KP_UNIT" in out
    assert "kp_verified=0" in out


def test_print_report_without_matches(capsys):
    """全未命中时不做除零运算，报告仍可打印。"""
    bk._print_report(
        {
            "total_empty": 1,
            "total_targets": 1,
            "rule_matched": 0,
            "ark_matched": 0,
            "unmatched": 1,
            "kept_original": 0,
            "changed": 0,
            "l1_matched": 0,
            "l2_matched": 0,
            "l2_fallback_matched": 0,
            "distribution": {},
            "relabel_l1": False,
            "no_ark": False,
        },
        dry_run=False,
    )
    assert "未命中" in capsys.readouterr().out


def test_no_real_http_client_is_created(monkeypatch, iso_db):
    """硬性约束：整条批处理链路零 HTTP。

    把 ``httpx.Client`` 换成"地雷"：一旦被调用就**记账**（而不是只抛异常）。

    为什么必须记账：``classify_by_ark`` 的兜底是 ``except Exception``，会把
    地雷抛出的 ``AssertionError`` 一起吞掉。若只靠抛异常，网络泄漏会被静默
    咽下去、用例照样绿。改成记账后，无论异常被谁吞掉，``tripped`` 都留痕。
    """
    import httpx

    tripped: List[str] = []

    def _boom(*args: Any, **kwargs: Any) -> None:
        tripped.append("httpx.Client")
        raise AssertionError("测试过程中不允许创建真实 httpx.Client（禁止发起网络请求）")

    monkeypatch.setattr(httpx, "Client", _boom)

    ark = _patch_ark(monkeypatch, FakeArkRespond(reply="KP_MIXED"))
    _seed_kps(iso_db)
    _add_question(iso_db, "解方程：3x+5=20，求x的值")  # 规则命中 → KP_EQ_LINEAR1
    _add_question(iso_db, "小猫在窗台上睡觉")  # 规则未命中 → 方舟兜底

    stats = bk.backfill(iso_db, dry_run=False)

    assert tripped == [], f"链路里发生了真实 HTTP 连接尝试: {tripped}"
    assert stats["rule_matched"] == 1, stats
    assert stats["ark_matched"] == 1, stats
    # 两段式方舟：段①返回 KP_MIXED，段② KP_MIXED 不在 children → 降级 L1
    assert ark.count == 2, "方舟只应被假实现接管（两段式 = 2 次调用）"


def test_http_bomb_guard_actually_detects_a_leak(monkeypatch, iso_db):
    """元测试：证明上面的地雷守卫真的能抓到泄漏，而不是摆设。

    这里**故意不 patch** ``ark_respond``，让规则未命中的题走真实
    ``ark_respond`` → 它会去 ``httpx.Client(...)`` → 踩中地雷。
    断言地雷确实被踩响，同时 ``classify_by_ark`` 把异常吞掉、批处理不崩。
    """
    import httpx

    tripped: List[str] = []

    def _boom(*args: Any, **kwargs: Any) -> None:
        tripped.append("httpx.Client")
        raise AssertionError("bomb")

    monkeypatch.setattr(httpx, "Client", _boom)

    # 让真实 ark_respond 越过"Key/模型未配置"的前置校验，真的走到建连那一步。
    from app.ai import ark_client as ark_client_mod

    monkeypatch.setattr(ark_client_mod.settings, "ark_api_key", "test-key-not-real")
    monkeypatch.setattr(ark_client_mod.settings, "ark_model", "test-model")

    _seed_kps(iso_db)
    _add_question(iso_db, "小猫在窗台上睡觉")  # 规则必然未命中 → 走真实方舟

    stats = bk.backfill(iso_db, dry_run=False)

    assert tripped, "地雷没被踩响：说明守卫失效，网络泄漏抓不住"
    assert stats["unmatched"] == 1, "方舟建连失败应降级为未命中，而不是让批处理崩掉"
