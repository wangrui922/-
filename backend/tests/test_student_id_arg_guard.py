"""守卫测试：``require_student_id`` 之类的取值 helper 必须传 ``current.sid``。

**背景（2026-10-20 生产事故）**：``app/api/progress.py`` 里 4 处写成
``require_student_id(current)``——把整个 ``CurrentUser`` 对象传进了期望
``sid`` 字符串的函数。``to_uuid`` 于是拿到一个 ``CurrentUser`` 实例，
``str()`` 出来是 ``"sub=... role=... sid=..."``，解析失败 → 抛 400
「student_id 非法」。

后果：``GET /api/progress/adaptive-plan``、``GET /api/progress``、
``DELETE /api/progress`` 三条端点**全线 400**（自适应复习计划功能在生产
完全不可用），而 ``/api/health`` 仍返回 200——只有打真实业务链路才暴露。

这类错误 Pydantic/类型检查都抓不到（``CurrentUser`` 实例传进
``Optional[str]`` 形参，运行期不报错、只在校验时炸），所以用**静态扫描**
当守卫：任何 ``require_student_id(current)``（缺 ``.sid``）出现即红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"

#: ``require_student_id(current)`` —— 缺 ``.sid``，传的是整个对象
BAD_CALL = re.compile(r"require_student_id\(\s*current\s*\)")
#: 正确形态：``require_student_id(current.sid)``
GOOD_CALL = re.compile(r"require_student_id\(\s*current\.sid\s*\)")


def _python_sources() -> list[Path]:
    return sorted(APP_DIR.rglob("*.py"))


def test_no_require_student_id_passed_whole_current_user():
    """任何 ``require_student_id(current)`` 都是 bug（必须是 ``current.sid``）。"""
    offenders: list[str] = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if BAD_CALL.search(line):
                offenders.append(f"{path.relative_to(APP_DIR)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "以下位置把整个 CurrentUser 对象传给了 require_student_id"
        "（应传 current.sid），会导致相关端点 400：\n  " + "\n  ".join(offenders)
    )


def test_progress_endpoints_use_sid_correctly():
    """``app/api/progress.py`` 的 4 处调用必须都带 ``.sid``。"""
    path = APP_DIR / "api" / "progress.py"
    text = path.read_text(encoding="utf-8")
    assert BAD_CALL.search(text) is None, "progress.py 仍存在 require_student_id(current)"
    # 该文件应有 4 处调用（读/删进度 + 自适应计划 + 其它进度端点）
    good = len(GOOD_CALL.findall(text))
    assert good >= 4, f"progress.py 只找到 {good} 处 require_student_id(current.sid)，预期 ≥4"


def test_require_student_id_rejects_current_user_object():
    """行为守卫：传整个 CurrentUser 必须抛 400，而不是静默通过。

    这条锁住「为什么这是个 bug」——即使将来有人改了 helper 的签名让它不再
    报错，这条测试也会提醒语义已经漂移。
    """
    from app.core.deps import CurrentUser
    from app.core.response import ApiError
    from app.services.learning_service import require_student_id

    user = CurrentUser(sub="sub-uuid", role="student", sid="5e956e94-bafb-485e-a4f6-7f02ee8ea91a")

    # 正确传法：取 .sid
    assert str(require_student_id(user.sid)) == "5e956e94-bafb-485e-a4f6-7f02ee8ea91a"

    # 错误传法：传整个对象 → 400
    with pytest.raises(ApiError) as exc:
        require_student_id(user)  # type: ignore[arg-type]
    assert "student_id" in str(exc.value)


def test_current_user_has_no_id_attribute():
    """``CurrentUser`` 没有 ``.id``——历史上写 ``user.id`` 会 500。

    与本文件同源的另一类取值错误，一并锁死。
    """
    from app.core.deps import CurrentUser

    user = CurrentUser(sub="sub-uuid", role="student", sid=None)
    assert not hasattr(user, "id")
    # CurrentUser 是 dataclass（不是 Pydantic model），字段走 __dataclass_fields__
    fields = set(getattr(CurrentUser, "__dataclass_fields__", {}) or {})
    assert fields >= {"sub", "role", "sid"}, f"CurrentUser 字段集异常：{fields}"
