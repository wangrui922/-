"""AI 教学提示词必须覆盖小学、初中和高中。"""

from app.ai.prompts import exercise, ocr, tutor


def test_core_ai_prompts_cover_all_twelve_grades():
    for prompt in (ocr.PROMPT, tutor.PROMPT, exercise.PROMPT):
        assert "小学" in prompt
        assert "初中" in prompt
        assert "高中" in prompt
        assert "1-12" in prompt or "1 到 12" in prompt or "1至12" in prompt
