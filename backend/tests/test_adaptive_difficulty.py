"""#4 draw_adaptive 自适应难度测试。

覆盖三层口径（对齐主理人裁决与 ``design/adaptive-difficulty-design.md`` 缺口 A
的验收要求，以裁决为准）：

* **纯算法**（``adaptive_service``）：
    - 档位序列 1/3/5，``step_difficulty`` 越界停在边界；
    - ``adjust_adaptive_state``：窗口 10 题、阈值 0.8/0.4、连续 2 窗口防抖；
      中间档（0.4~0.8）打断 streak；升/降档后窗口清零；
    - 新学生无画像 → 默认难度 3。
* **服务层**（``record_attempt_result`` / ``get_current_difficulty``）：
    - 幂等建行、判分结果落库、连续达标后真实调档并持久化。
* **集成**（``draw_questions(adaptive=True)`` 与 ``POST /api/practice/session``）：
    - 主难度约 70% + 相邻难度约 30%；
    - 主难度池为空时从相邻难度补齐，全空回退普通全难度抽题（不 404 裸奔）；
    - 响应披露 ``adaptive_used`` / ``adaptive_difficulty``；
    - ``adaptive=True`` 时忽略 ``difficulty`` 入参。

跨方言约束：不依赖任何数据库日期/随机函数（Python 侧 ``random.sample``），
每条断言只依赖 SQLAlchemy ORM 读写。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from sqlalchemy import select

from app.db import SessionLocal
from app.models.adaptive import StudentAdaptiveLevel
from app.services import adaptive_service, practice_service
from app.services.adaptive_service import (
    DEFAULT_DIFFICULTY,
    DIFFICULTIES,
    MIN_STREAK,
    UP_THRESHOLD,
    WINDOW_SIZE,
    adjust_adaptive_state,
    get_current_difficulty,
    neighbor_difficulties,
    record_attempt_result,
    step_difficulty,
)
from t04_helpers import assert_ok, login
from test_practice import _draw, make_bank_question


# ================================================================== A. 纯算法
class TestDifficultySteps:
    """档位序列与邻档口径。"""

    def test_steps_within_range(self) -> None:
        assert step_difficulty(1, direction=1) == 3
        assert step_difficulty(3, direction=1) == 5
        assert step_difficulty(5, direction=-1) == 3
        assert step_difficulty(3, direction=-1) == 1

    def test_steps_stay_at_bounds(self) -> None:
        """升/降越过边界时停在边界，不越界。"""
        assert step_difficulty(5, direction=1) == 5
        assert step_difficulty(1, direction=-1) == 1

    def test_steps_normalize_illegal_input(self) -> None:
        """非法输入一律收敛到默认 3。"""
        for illegal in (None, 2, 4, 9, "abc", -1):
            assert step_difficulty(illegal, direction=1) in DIFFICULTIES
            assert step_difficulty(illegal, direction=-1) in DIFFICULTIES

    def test_neighbors_exclude_self(self) -> None:
        assert neighbor_difficulties(1) == [3, 5]
        assert neighbor_difficulties(3) == [1, 5]
        assert neighbor_difficulties(5) == [1, 3]


class TestWindowSettlement:
    """窗口结算 + streak 防抖 + 调档的纯函数口径。"""

    def _almost_full_window(self, correct: int) -> Dict[str, Any]:
        """模拟一个已答 ``WINDOW_SIZE-1`` 题、其中 ``correct`` 题正确的窗口。

        streak 一律从 0/1 显式传入，避免测试隐含假设与实现耦合。
        """
        return dict(
            current_difficulty=3,
            up_streak=0,
            down_streak=0,
            window_correct=correct,
            window_total=WINDOW_SIZE - 1,
        )

    def test_upgrade_after_two_streak_windows(self) -> None:
        """连续 2 个高分窗口（各正确率 0.8）→ 3 → 5。"""
        # 第 1 个窗口：8/10 正确（0.8）→ up_streak=1，防抖不调档
        s1 = adjust_adaptive_state(is_correct=True, **self._almost_full_window(7))
        assert s1["current_difficulty"] == 3, s1  # 防抖：第 1 次达标不调档
        assert s1["up_streak"] == 1, s1
        assert s1["adjusted"] is False, s1

        # 第 2 个窗口：同样 8/10，up_streak=2 → 升到 5
        s2 = adjust_adaptive_state(
            current_difficulty=s1["current_difficulty"],
            up_streak=s1["up_streak"],  # 第 1 窗口积攒的 streak
            down_streak=s1["down_streak"],
            window_correct=7,
            window_total=WINDOW_SIZE - 1,
            is_correct=True,
        )
        assert s2["current_difficulty"] == 5, s2
        assert s2["adjusted"] is True, s2
        # 调档后窗口清零
        assert s2["window_total"] == 0 and s2["up_streak"] == 0, s2

    def test_downgrade_after_two_bad_windows(self) -> None:
        """连续 2 个低分窗口（各正确率 0.4）→ 3 → 1。"""
        # 第 1 个窗口：4/10 正确（0.4）→ down_streak=1，防抖不调档
        s0 = adjust_adaptive_state(
            current_difficulty=3, up_streak=0, down_streak=0,
            window_correct=3, window_total=WINDOW_SIZE - 1, is_correct=False,
        )
        assert s0["down_streak"] == 1 and s0["adjusted"] is False, s0
        # 第 2 个窗口：down_streak=2 → 降到 1
        s1 = adjust_adaptive_state(
            current_difficulty=3, up_streak=0, down_streak=s0["down_streak"],
            window_correct=3, window_total=WINDOW_SIZE - 1, is_correct=False,
        )
        assert s1["current_difficulty"] == 1, s1
        assert s1["adjusted"] is True, s1

    def test_middle_rate_breaks_streak(self) -> None:
        """中间档（0.68<0.8 且 >0.4）把两个 streak 都清零，不调档。"""
        state = self._almost_full_window(6)  # 6/9
        state["up_streak"] = 1
        s = adjust_adaptive_state(is_correct=False, **state)  # 6/10 = 0.6
        assert s["up_streak"] == 0 and s["down_streak"] == 0, s
        assert s["current_difficulty"] == 3 and s["adjusted"] is False, s

    def test_threshold_boundaries(self) -> None:
        """正确率恰为 0.8 记高分窗口；恰为 0.4 记低分窗口。"""
        high = adjust_adaptive_state(
            current_difficulty=3, up_streak=0, down_streak=0,
            window_correct=7, window_total=WINDOW_SIZE - 1, is_correct=True,
        )
        assert high["up_streak"] == 1, high
        low = adjust_adaptive_state(
            current_difficulty=3, up_streak=0, down_streak=0,
            window_correct=3, window_total=WINDOW_SIZE - 1, is_correct=False,
        )
        assert low["down_streak"] == 1, low

    def test_default_floor_and_cap(self) -> None:
        """难度 1 连续满分也最多到 5；难度 5 连续全错也最多到 1。"""
        floor = adjust_adaptive_state(
            current_difficulty=1, up_streak=1, down_streak=0,
            window_correct=9, window_total=WINDOW_SIZE - 1, is_correct=True,
        )
        assert floor["current_difficulty"] == 3, floor
        cap = adjust_adaptive_state(
            current_difficulty=5, up_streak=0, down_streak=1,
            window_correct=0, window_total=WINDOW_SIZE - 1, is_correct=False,
        )
        assert cap["current_difficulty"] == 3, cap


# ================================================================== B. 服务层
class TestAdaptiveStore:
    """DB 读写：建行默认难度、判分落库、调档持久化。"""

    def test_new_student_defaults_to_level3(self, client, new_phone) -> None:
        headers, student_id = login(client, phone=new_phone())
        with SessionLocal() as db:
            assert get_current_difficulty(db, student_id=student_id) == DEFAULT_DIFFICULTY

    def test_record_attempt_persists_window(self, client, new_phone) -> None:
        headers, student_id = login(client, phone=new_phone())
        with SessionLocal() as db:
            out = record_attempt_result(db, student_id=student_id, is_correct=True)
            assert out["current_difficulty"] == DEFAULT_DIFFICULTY
            row = db.execute(
                select(StudentAdaptiveLevel).where(StudentAdaptiveLevel.student_id == student_id)
            ).scalar_one()
            assert row.window_correct == 1 and row.window_total == 1
            assert row.up_streak == 0  # 窗口未满不结算

    def test_upgrade_persists_after_two_windows(self, client, new_phone) -> None:
        """20 题里 16 对（两个 0.8 窗口）→ 3 → 5 且落库。"""
        headers, student_id = login(client, phone=new_phone())
        with SessionLocal() as db:
            # 第 1 窗：8 对 2 错（8/10 = 0.8）；第 2 窗：同样 8 对 2 错
            feed = [True] * 8 + [False] * 2 + [True] * 8 + [False] * 2
            final = None
            for correct in feed:
                final = record_attempt_result(db, student_id=student_id, is_correct=correct)
            assert final["current_difficulty"] == 5, final
            row = db.execute(
                select(StudentAdaptiveLevel).where(StudentAdaptiveLevel.student_id == student_id)
            ).scalar_one()
            assert row.current_difficulty == 5

    def test_adaptive_store_is_stateless_per_difficulty(self, client, new_phone) -> None:
        """升到 5 后，同一学生的下一轮抽题推荐难度就是 5。"""
        headers, student_id = login(client, phone=new_phone())
        with SessionLocal() as db:
            for correct in [True] * 8 + [False] * 2 + [True] * 8 + [False] * 2:
                record_attempt_result(db, student_id=student_id, is_correct=correct)
            assert get_current_difficulty(db, student_id=student_id) == 5


# ================================================================== C. 集成
class TestAdaptiveDrawIntegration:
    """``POST /api/practice/session?adaptive=true`` 全链路。"""

    def _seed_bank(self, *, kp_id: str, counts: Dict[int, int]) -> None:
        """按难度档位插题（每题不同内容避免难度池混淆）。"""
        seq = 0
        for difficulty, count in counts.items():
            for i in range(count):
                seq += 1
                make_bank_question(
                    type_="选择",
                    content=f"自适应题 {difficulty}-{i}: {seq} + 1 = ?",
                    kp_id=kp_id,
                    difficulty=difficulty,
                    correct_answer="B",
                )

    def _fresh_login(self, client, new_phone) -> None:
        """用全新手机号登录，隔离不同用例间的学生画像状态。"""
        return login(client, phone=new_phone())

    def test_default_level3_mix_70_30(self, client, new_phone) -> None:
        """新学生默认难度 3：抽 10 题应为主难度 3 约 7 道 + 邻档约 3 道。"""
        headers, _ = self._fresh_login(client, new_phone)
        kp_id = make_bank_question_kp()
        # 每档 10 道，保证主/邻档池都够
        self._seed_bank(kp_id=kp_id, counts={1: 10, 3: 10, 5: 10})

        data = _draw(client, headers, kp_id=kp_id, adaptive=True, count=10)
        assert data["adaptive_used"] is True, data
        assert data["adaptive_difficulty"] == 3, data

        difficulties = [q["difficulty"] for q in data["questions"]]
        main_count = sum(1 for d in difficulties if d == 3)
        side_count = sum(1 for d in difficulties if d in (1, 5))
        assert 6 <= main_count <= 9, (difficulties, main_count)  # 约 70%
        assert 1 <= side_count <= 4, (difficulties, side_count)  # 约 30%
        assert len(data["questions"]) == 10

    def test_adaptive_ignores_difficulty_param(self, client, new_phone) -> None:
        """``adaptive=true`` 时显式 difficulty 被忽略（按画像推荐难度抽）。"""
        headers, _ = self._fresh_login(client, new_phone)
        kp_id = make_bank_question_kp()
        self._seed_bank(kp_id=kp_id, counts={1: 10, 3: 10, 5: 10})

        data = _draw(client, headers, kp_id=kp_id, adaptive=True, difficulty=5, count=6)
        assert data["adaptive_used"] is True
        # 默认难度 3 为主难度，而不是按入参 5 全抽
        main = [q for q in data["questions"] if q["difficulty"] == 3]
        assert len(main) >= 3, [q["difficulty"] for q in data["questions"]]

    def test_main_pool_empty_uses_neighbors(self, client, new_phone) -> None:
        """主难度 3 的题不存在时，全部从相邻难度补齐（不 404）。"""
        headers, _ = self._fresh_login(client, new_phone)
        kp_id = make_bank_question_kp()
        self._seed_bank(kp_id=kp_id, counts={1: 10, 5: 10})

        data = _draw(client, headers, kp_id=kp_id, adaptive=True, count=8)
        difficulties = {q["difficulty"] for q in data["questions"]}
        assert difficulties <= {1, 5}, difficulties  # 不含难度 3
        assert len(data["questions"]) == 8

    def test_all_pools_empty_falls_back_and_404s(self, client, new_phone) -> None:
        """全难度池都无题时：先回退全难度抽题仍无题 → 404 且披露 adaptive_used。"""
        from app.core.response import CODE_NOT_FOUND

        headers, _ = self._fresh_login(client, new_phone)
        kp_id = make_bank_question_kp()  # 空知识点，不插题

        resp = client.post(
            "/api/practice/session",
            json={"kp_id": kp_id, "adaptive": True, "count": 5},
            headers=headers,
        )
        assert resp.status_code == CODE_NOT_FOUND, resp.text

    def test_answer_updates_adaptive_state(self, client, new_phone) -> None:
        """判分后画像窗口随之更新（答对计入 window_correct）。"""
        headers, student_id = self._fresh_login(client, new_phone)
        kp_id = make_bank_question_kp()
        qid = make_bank_question(
            type_="选择",
            content="自适应判分题：2 + 3 = ?",
            kp_id=kp_id,
            difficulty=3,
            correct_answer="B",
        )
# 主难度 3 池只有 qid + 1 道 seed（共 2 道），count=3 时主难度
        # 必抽 2 道（round(3*0.7)=2），qid 必然被抽中
        self._seed_bank(kp_id=kp_id, counts={3: 1})

        data = _draw(client, headers, kp_id=kp_id, adaptive=True, count=3)

        # 找该 kp 下抽到的题，答对一题
        target = next(q for q in data["questions"] if q["question_id"] == qid)
        assert_ok(
            client.post(
                "/api/practice/answer",
                json={
                    "session_id": data["session_id"],
                    "question_id": target["question_id"],
                    "selected_answer": "B",
                },
                headers=headers,
            )
        )
        with SessionLocal() as db:
            row = db.execute(
                select(StudentAdaptiveLevel).where(StudentAdaptiveLevel.student_id == student_id)
            ).scalar_one()
            assert row.window_correct == 1 and row.window_total == 1


#: 独立知识点 helper（避免与其它测试共享 kp 导致池混淆）
def make_bank_question_kp() -> str:
    from t04_helpers import make_kp

    return make_kp(f"自适应测试点-{uuid.uuid4().hex[:8]}")


__all__ = [
    "TestDifficultySteps",
    "TestWindowSettlement",
    "TestAdaptiveStore",
    "TestAdaptiveDrawIntegration",
    "make_bank_question_kp",
]