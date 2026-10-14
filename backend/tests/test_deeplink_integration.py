"""V4 · 错题深链 → /guide 引导 端到端集成测试（T05）。

被测主链路
----------
前端错题卡片上的「让老师带我想一想」深链按钮，点开即带 ``question_id`` +
``work`` 打到 ``POST /api/practice/guide``，路由层用 ``run_in_threadpool``
包一层同步的 ``practice_service.guide_question``。本文件**直接测服务层入口**
``guide_question``，它是这条深链的全部业务逻辑所在，覆盖：

    加载会话 → 校验题在 session → 加载 question+answer → 拼 prompt
    （替换 ``{{QUESTION}}`` / ``{{WORK}}``）→ ``ark_client.ark_respond``
    → 解析 JSON（reply / askFinalConfirm）→ ``sanitize_guide_reply``
    （内含 ``judge_engine.leaks_answer()`` 泄题闸门）→ 出参四件套。

⚠️ 零网络铁律（本文件最重要的约束）
-----------------------------------
沙箱出网被代理拦截，任何真实 HTTPS 到方舟都会 502 并把测试拖死。因此：

    * ``ark_client.ark_respond`` 是这条链路上**唯一的出网点**，一律用
      ``FakeArkRespond`` 打桩（返回固定字符串，绝不碰 httpx）；
    * 另有 ``no_network_guard`` 夹具做**零网络守卫**：把 httpx 的收发口与
      ``socket.connect`` 全换成「记账 + 抛 ``NetworkEscape``」的地雷，
      用例结束时断言账本为空。一旦有真实出网，测试立刻 fail 并明确报
      「检测到真实网络调用，违反零网络守卫」。

写法参考 ``test_backfill_kp.py``（``FakeArkRespond`` + ``iso_db`` 独立内存库）
与 ``test_guide_regression.py``（``leaks_answer`` 硬断言）。

跨方言：不写任何裸 SQL，全部走 ORM / 服务层函数。
"""

from __future__ import annotations

import socket
import uuid
from typing import Any, Dict, Iterator, List, Optional

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.prompts import practice_guide
from app.models.base import Base
from app.models.content import Answer, Question
from app.models.learning import LearningTask
from app.models.user import Student, User
from app.services import judge_engine, practice_service
from app.services.practice_service import GUIDE_FALLBACK_REPLY


# ==================================================================== 零网络守卫
class NetworkEscape(BaseException):
    """真实网络调用逃逸信号。

    刻意继承 ``BaseException`` 而非 ``Exception``：业务代码里遍布
    ``except Exception`` 的兜底（如 ``guide_question`` 解析 JSON 那段），
    普通异常会被静默吞掉，守卫就形同虚设。用 ``BaseException`` 保证
    「一旦出网必定炸穿到用例层」。
    """


#: 需要下地雷的出网入口（对象, 属性名, 展示名）
_NETWORK_ENTRYPOINTS = (
    (httpx.Client, "request", "httpx.Client.request"),
    (httpx.Client, "send", "httpx.Client.send"),
    (httpx.AsyncClient, "request", "httpx.AsyncClient.request"),
    (httpx.AsyncClient, "send", "httpx.AsyncClient.send"),
    (httpx.HTTPTransport, "handle_request", "httpx.HTTPTransport.handle_request"),
    (socket.socket, "connect", "socket.socket.connect"),
)


@pytest.fixture(autouse=True)
def no_network_guard(monkeypatch) -> Iterator[List[str]]:
    """零网络守卫：整条用例期间禁止任何真实外部请求。

    ``autouse=True``，本文件每条用例自动生效，无需显式声明。

    Yields:
        逃逸账本（``list[str]``）。正常情况下始终为空；自测守卫本身的用例
        可以在断言过它之后手动 ``clear()`` 平账。
    """
    breaches: List[str] = []

    def _make_mine(label: str):
        def _mine(*_args: Any, **_kwargs: Any):
            breaches.append(label)
            raise NetworkEscape(f"检测到真实网络调用，违反零网络守卫：{label}")

        return _mine

    for target, attr, label in _NETWORK_ENTRYPOINTS:
        monkeypatch.setattr(target, attr, _make_mine(label), raising=False)

    yield breaches

    assert not breaches, (
        "检测到真实网络调用，违反零网络守卫；逃逸点：" + "、".join(sorted(set(breaches)))
    )


# ==================================================================== 打桩 / 夹具
class FakeArkRespond:
    """``ark_respond`` 的假实现：只记录调用、返回预设文本，绝不发网络请求。

    与 ``test_backfill_kp.FakeArkRespond`` 同款（签名对齐真实
    ``ark_client.ark_respond``），此处独立一份避免跨文件耦合。

    Attributes:
        reply: 固定返回的文本；为 ``None`` 时返回空串。
        exc: 非 ``None`` 时每次调用直接抛该异常（模拟方舟故障）。
        calls: 每次调用的关键字参数记录。
    """

    def __init__(self, reply: Optional[str] = "", exc: Optional[BaseException] = None) -> None:
        self.reply: Optional[str] = reply
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
        return self.reply or ""

    @property
    def count(self) -> int:
        """被调用了几次。"""
        return len(self.calls)

    @property
    def last_prompt(self) -> str:
        """最后一次调用实际送进模型的 user prompt 全文。"""
        assert self.calls, "ark_respond 还没被调用过"
        return str(self.calls[-1]["messages"][0]["content"])


def _patch_ark(monkeypatch, fake: Optional[FakeArkRespond] = None) -> FakeArkRespond:
    """把 ``practice_service`` 用到的 ``ark_respond`` 换成假实现。

    ⚠️ patch 目标是 ``app.services.practice_service.ark_client.ark_respond``：
    ``practice_service`` 是 ``from app.ai import ark_client`` 后按属性调用的，
    所以要打在 ``ark_client`` 模块对象的属性上（monkeypatch 会自动还原）。

    Args:
        monkeypatch: pytest 的 monkeypatch 夹具。
        fake: 自定义假实现，缺省用 ``FakeArkRespond()``。

    Returns:
        实际生效的 ``FakeArkRespond``，可用于断言调用次数与入参。
    """
    ark = fake if fake is not None else FakeArkRespond()
    monkeypatch.setattr(practice_service.ark_client, "ark_respond", ark)
    return ark


@pytest.fixture
def iso_db() -> Iterator[Session]:
    """独立的内存 SQLite 会话，与 conftest 的会话级共享库隔离。

    深链用例要精确控制「这道题的标准答案是什么」，共享库里别的用例插的题
    会让泄题断言口径漂移，所以每条用例开一个干净的库。
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


#: 深链场景的选择题选项（正文 ≥2 字，便于 leaks_answer 的正文比对生效）
DEEPLINK_OPTIONS: Dict[str, str] = {"A": "8元", "B": "15元", "C": "10元", "D": "2元"}

#: 该题标准答案（选择题键位）
DEEPLINK_CORRECT: str = "B"


class DeepLinkScene:
    """一条错题深链所需的最小上下文（学生 / 题目 / 练习会话）。

    Attributes:
        student_id: 学生 ID（UUID）。
        question_id: 题目 ID（UUID）。
        session_id: 练习会话（``LearningTask``）ID（UUID）。
        q_type: 题型，做泄题断言时要传给 ``leaks_answer``。
        options: 题目选项原始值。
        correct_answer: 标准答案。
    """

    def __init__(
        self,
        *,
        student_id: uuid.UUID,
        question_id: uuid.UUID,
        session_id: uuid.UUID,
        q_type: Optional[str],
        options: Any,
        correct_answer: Optional[str],
    ) -> None:
        self.student_id = student_id
        self.question_id = question_id
        self.session_id = session_id
        self.q_type = q_type
        self.options = options
        self.correct_answer = correct_answer

    def leaks(self, reply: str) -> bool:
        """这条回复按本题口径算不算泄题。"""
        return judge_engine.leaks_answer(reply, self.q_type, self.options, self.correct_answer)


def _seed_deeplink(
    db: Session,
    *,
    q_type: str = "选择",
    content: str = "小明买了3支笔，每支5元，一共花了多少钱？",
    options: Any = None,
    correct_answer: Optional[str] = DEEPLINK_CORRECT,
) -> DeepLinkScene:
    """造一条完整的「错题深链」上下文：学生 + 题目 + 答案 + 练习会话。

    会话走真实的 ``practice_service.create_session``，保证 ``items`` 快照
    结构与线上一致（深链校验「题在不在本次练习中」就是读它）。

    Args:
        db: 数据库会话。
        q_type: 题型（选择 / 判断）。
        content: 题干。
        options: 选项；``None`` 且题型为选择时用 ``DEEPLINK_OPTIONS``。
        correct_answer: 标准答案。

    Returns:
        ``DeepLinkScene``。
    """
    if options is None and q_type == "选择":
        options = dict(DEEPLINK_OPTIONS)

    user = User(phone=f"139{uuid.uuid4().int % 10**8:08d}", role="student", status="active")
    db.add(user)
    db.flush()

    student = Student(user_id=user.id, real_name="深链测试娃", grade=4)
    db.add(student)
    db.flush()

    question = Question(
        content=content,
        subject="数学",
        grade=4,
        difficulty=3,
        type_=q_type,
        options=options,
        status="active",
    )
    db.add(question)
    db.flush()

    db.add(
        Answer(
            question_id=question.id,
            correct_answer=correct_answer,
            analysis="先算单价乘数量。",
            distractor_analysis={},
        )
    )
    db.commit()

    task: LearningTask = practice_service.create_session(
        db, student_id=student.id, question_ids=[question.id]
    )

    return DeepLinkScene(
        student_id=student.id,
        question_id=question.id,
        session_id=task.id,
        q_type=q_type,
        options=options,
        correct_answer=correct_answer,
    )


@pytest.fixture
def scene(iso_db: Session) -> DeepLinkScene:
    """默认深链场景：一道选择题（标准答案 B）+ 一个只含这道题的练习会话。"""
    return _seed_deeplink(iso_db)


#: ``guide_question`` 出参必须齐的四个键
EXPECTED_KEYS = {"question_id", "reply", "ask_final_confirm", "prompt_version"}


def _assert_contract(result: Dict[str, Any], scene: DeepLinkScene) -> None:
    """出参契约通用断言：四件套齐全 + 类型正确 + 版本号 + 题号回显。"""
    assert set(result) == EXPECTED_KEYS, f"出参键不符合契约：{sorted(result)}"
    assert result["question_id"] == str(scene.question_id)
    assert isinstance(result["reply"], str) and result["reply"].strip()
    assert isinstance(result["ask_final_confirm"], bool)
    # ⚠️ 不要在这里硬编码版本号：提示词是核心教学资产，升版是常态（T4 已 1.1.0→1.2.0）。
    #    契约要保证的是「出参回带的版本 == 当前生效的提示词版本」，而不是某个具体数值。
    assert isinstance(result["prompt_version"], str) and result["prompt_version"].strip()
    assert result["prompt_version"] == practice_guide.VERSION


# ==================================================================== 用例
class TestDeepLinkGuideHappyPath:
    """用例 1：错题深链正常引导（happy path）。"""

    def test_guide_returns_full_contract_and_does_not_leak(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """深链点开 → 模型给合法引导 JSON → 出参四件套齐全且不泄题。"""
        # Arrange：方舟返回一条标准的引导 JSON
        ark = _patch_ark(
            monkeypatch,
            FakeArkRespond(reply='{"reply":"你觉得先算什么？","askFinalConfirm":false}'),
        )

        # Act
        result = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work="孩子写了一步",
        )

        # Assert：契约 + 内容 + no-leak
        _assert_contract(result, scene)
        assert result["reply"] == "你觉得先算什么？"
        assert result["ask_final_confirm"] is False
        assert scene.leaks(result["reply"]) is False, "引导语泄题了，V1 铁律破功"
        assert result["reply"] != GUIDE_FALLBACK_REPLY, "合法引导不该被兜底话术替换"
        assert ark.count == 1, "深链引导应当且只当调一次方舟"

    def test_ask_final_confirm_true_is_passed_through(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """模型判定孩子已写完时，``askFinalConfirm=true`` 要如实透传。"""
        _patch_ark(
            monkeypatch,
            FakeArkRespond(reply='{"reply":"这一步的思路说得挺清楚，要不要一起再检查一遍？","askFinalConfirm":true}'),
        )

        result = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work="3×5=15，所以一共15元",
        )

        _assert_contract(result, scene)
        assert result["ask_final_confirm"] is True

    def test_student_says_unknown_gets_key_hint_instead_of_repeating_question(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """学生明确卡住后，老师必须换成题目相关的关键提示，不能重复上一问。"""
        class SequentialArk:
            def __init__(self) -> None:
                self.calls = 0
                self.prompts: List[str] = []

            def __call__(self, *, messages, system=None, image_b64=None, timeout=60.0):
                self.calls += 1
                self.prompts.append(messages[0]["content"])
                return '{"reply":"你觉得先算什么？","askFinalConfirm":false}'

        ark = SequentialArk()
        monkeypatch.setattr(practice_service.ark_client, "ark_respond", ark)

        first = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work="我想先算总价",
        )
        second = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work="不知道",
        )
        third = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work="还是不知道",
        )
        fourth = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work="我真的不知道",
        )

        assert first["reply"] == "你觉得先算什么？"
        assert second["reply"] != first["reply"]
        assert "关键条件" in second["reply"] or "3支笔" in second["reply"]
        assert third["reply"] != second["reply"]
        assert "总量" in third["reply"] or "一部分" in third["reply"]
        assert fourth["reply"] != third["reply"]
        assert "小判断" in fourth["reply"]
        assert "不知道" in ark.prompts[1]
        assert "你觉得先算什么？" in ark.prompts[1]
        assert ark.calls == 4


class TestDeepLinkLeakGuard:
    """用例 2：泄题闸门（代码层兜底，C11/C12）。"""

    def test_leaking_reply_is_replaced_by_fallback(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """模型抖出「答案是B」→ 整条丢弃换兜底话术，绝不把答案递给孩子。"""
        leaking = '{"reply":"答案是B，因为3×5=15","askFinalConfirm":false}'
        ark = _patch_ark(monkeypatch, FakeArkRespond(reply=leaking))

        # 前置确认：这条回复在本题口径下确实算泄题（否则用例是假绿）
        assert scene.leaks("答案是B，因为3×5=15") is True

        result = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work="不会做",
        )

        _assert_contract(result, scene)
        assert result["reply"] == GUIDE_FALLBACK_REPLY, "泄题回复没有被 sanitize 拦下"
        assert "答案是B" not in result["reply"]
        assert scene.leaks(result["reply"]) is False, "兜底话术本身不得泄题"
        assert ark.count == 1

    @pytest.mark.parametrize(
        "leaking_reply",
        [
            "答案是B，因为3×5=15",  # 键位直给
            "所以应该选 B 才对哦",  # 结论式键位
            "一共是15元，你算对了",  # 选项正文原样抄出
        ],
    )
    def test_multiple_leak_shapes_all_fall_back(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch, leaking_reply: str
    ) -> None:
        """三种典型泄题形态（键位 / 结论 / 选项正文）都必须被闸门拦下。"""
        _patch_ark(monkeypatch, FakeArkRespond(reply=leaking_reply))

        result = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
        )

        assert result["reply"] == GUIDE_FALLBACK_REPLY, f"泄题形态未被拦截：{leaking_reply}"

    def test_empty_reply_falls_back(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """模型返回空 → 也要给孩子一句能往下走的话，而不是空白。"""
        _patch_ark(monkeypatch, FakeArkRespond(reply=""))

        result = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
        )

        _assert_contract(result, scene)
        assert result["reply"] == GUIDE_FALLBACK_REPLY


class TestZeroNetworkGuard:
    """用例 3：零意外网络守卫——全程没有一个真实 HTTPS 打到方舟。"""

    def test_guard_is_actually_armed(self, no_network_guard: List[str]) -> None:
        """守卫自测：真去发一个 httpx 请求，必须当场炸。

        这条用例证明守卫不是「摆设式 assert 空列表」——地雷是真的埋下去了。
        断言完成后手动清账，避免 teardown 把这次**故意**的逃逸判成违规。
        """
        with pytest.raises(NetworkEscape) as excinfo:
            httpx.Client().get("https://ark.cn-beijing.volces.com/api/v3/chat/completions")

        assert "违反零网络守卫" in str(excinfo.value)
        assert no_network_guard, "守卫应当记账，但账本是空的"
        no_network_guard.clear()  # 已验证，平账

    def test_full_deeplink_chain_makes_no_real_request(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch, no_network_guard: List[str]
    ) -> None:
        """完整深链链路跑一遍，账本必须为空，且出网点确实走的是假实现。"""
        sentinel = '{"reply":"这里的“每支5元”是在说什么？","askFinalConfirm":false}'
        ark = _patch_ark(monkeypatch, FakeArkRespond(reply=sentinel))

        result = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work="孩子写了一步",
        )

        # 唯一出网点被假实现接管：调用发生了，且吐的是预设字符串
        assert ark.count == 1, "ark_respond 没被调用，链路可能根本没跑到出网点"
        assert ark.reply == sentinel
        assert result["reply"] == "这里的“每支5元”是在说什么？"

        # 真实网络栈零触达
        assert no_network_guard == [], (
            "检测到真实网络调用，违反零网络守卫；逃逸点：" + "、".join(no_network_guard)
        )

    def test_fake_ark_never_touches_httpx(self, monkeypatch, no_network_guard: List[str]) -> None:
        """``FakeArkRespond`` 本身是纯内存的：连调 20 次也不碰网络栈。"""
        ark = FakeArkRespond(reply="固定回复")

        for _ in range(20):
            assert ark(messages=[{"role": "user", "content": "x"}]) == "固定回复"

        assert ark.count == 20
        assert no_network_guard == [], "FakeArkRespond 竟然触达了真实网络栈"


class TestDeepLinkWithWork:
    """用例 4：深链带 ``work``（孩子的作答过程）。"""

    def test_work_is_filled_into_prompt(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """深链把孩子写的过程带进来 → prompt 占位符被正确替换，回复如实透传。"""
        work = "我先算 3+5=8，然后写了 8 元"
        model_reply = "你把3和5加起来了，再读一遍题：3支笔和每支5元之间是什么关系？"
        ark = _patch_ark(
            monkeypatch,
            FakeArkRespond(reply=f'{{"reply":"{model_reply}","askFinalConfirm":false}}'),
        )

        result = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work=work,
        )

        # 出参合规 + 内容透传
        _assert_contract(result, scene)
        assert result["reply"] == model_reply
        assert scene.leaks(result["reply"]) is False

        # {{WORK}} / {{QUESTION}} 链路通畅：占位符没残留，题干与作答都进了 prompt
        prompt = ark.last_prompt
        assert "{{WORK}}" not in prompt and "{{QUESTION}}" not in prompt, "提示词占位符没被替换"
        assert work in prompt, "孩子的作答过程没有填进 prompt"
        assert "小明买了3支笔" in prompt, "题干没有填进 prompt"
        for key, text in DEEPLINK_OPTIONS.items():
            assert f"{key}. {text}" in prompt, f"选项 {key} 没随题干带进 prompt"

    def test_missing_work_uses_placeholder_text(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """深链没带 work（孩子只点开求助）→ prompt 里给一句占位说明，不留空。"""
        ark = _patch_ark(
            monkeypatch,
            FakeArkRespond(reply='{"reply":"先说说题目告诉了我们哪些条件？","askFinalConfirm":false}'),
        )

        result = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work=None,
        )

        _assert_contract(result, scene)
        assert "{{WORK}}" not in ark.last_prompt
        assert "还没写下过程" in ark.last_prompt, "空 work 未走占位说明分支"

    def test_blank_work_is_treated_as_missing(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """work 是纯空白串时按「没写」处理，不能把空白直接塞进 prompt。"""
        ark = _patch_ark(
            monkeypatch,
            FakeArkRespond(reply='{"reply":"这道题在问什么？","askFinalConfirm":false}'),
        )

        practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work="   \n  ",
        )

        assert "还没写下过程" in ark.last_prompt


class TestDeepLinkRobustness:
    """加分项：模型返回非 JSON / 会话越权等容错分支。"""

    def test_plain_text_reply_is_used_as_is(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """方舟返回纯文本（不是 JSON）→ 当作 reply 直接用，不能崩。"""
        _patch_ark(monkeypatch, FakeArkRespond(reply="我们一起看看，题目里的“每支5元”是什么意思呢？"))

        result = practice_service.guide_question(
            iso_db,
            student_id=scene.student_id,
            session_id=scene.session_id,
            question_id=scene.question_id,
            work="不知道怎么下手",
        )

        _assert_contract(result, scene)
        assert result["reply"] == "我们一起看看，题目里的“每支5元”是什么意思呢？"
        assert result["ask_final_confirm"] is False, "纯文本回复时确认位应保持 False"

    def test_question_not_in_session_is_rejected(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """深链带了一道不在本次练习里的题 → 400，且根本不该调方舟。"""
        from app.core.response import ApiError

        ark = _patch_ark(monkeypatch, FakeArkRespond(reply="不该被调到"))
        outsider = _seed_deeplink(iso_db)  # 另造一道题（属于另一个会话）

        with pytest.raises(ApiError) as excinfo:
            practice_service.guide_question(
                iso_db,
                student_id=scene.student_id,
                session_id=scene.session_id,
                question_id=outsider.question_id,
            )

        assert excinfo.value.code == 400
        assert ark.count == 0, "校验没过就不该产生任何方舟调用"

    def test_other_students_session_is_not_found(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """拿别人的 session_id 走深链 → 一律 404（不暴露会话是否存在）。"""
        from app.core.response import ApiError

        ark = _patch_ark(monkeypatch, FakeArkRespond(reply="不该被调到"))
        other = _seed_deeplink(iso_db)

        with pytest.raises(ApiError) as excinfo:
            practice_service.guide_question(
                iso_db,
                student_id=other.student_id,
                session_id=scene.session_id,
                question_id=scene.question_id,
            )

        assert excinfo.value.code == 404
        assert ark.count == 0

    def test_ark_failure_maps_to_friendly_500(
        self, iso_db: Session, scene: DeepLinkScene, monkeypatch
    ) -> None:
        """方舟挂了（ArkError）→ 转成 500 + 孩子看得懂的话，不抛原始异常。"""
        from app.ai.ark_client import ArkError
        from app.core.response import ApiError

        _patch_ark(monkeypatch, FakeArkRespond(exc=ArkError("ARK_API_KEY 未配置")))

        with pytest.raises(ApiError) as excinfo:
            practice_service.guide_question(
                iso_db,
                student_id=scene.student_id,
                session_id=scene.session_id,
                question_id=scene.question_id,
            )

        assert excinfo.value.code == 500
