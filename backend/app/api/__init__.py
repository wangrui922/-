"""API 路由汇总。

本文件是「路由注册中心」：各业务模块在 ``app/api/`` 下独立建文件并暴露
``router``，然后在这里用 ``include_router`` 收口。保持单一聚合点，方便
``main.py`` 只挂载一个 ``api_router``，也方便后续按版本分组。

当前已注册：
    * health   —— 探活接口（无鉴权）。
    * auth     —— 手机号验证码登录 / 刷新令牌（T02·2.1）。
    * student  —— 学生档案读写（T02·2.2，受保护）。
    * chat     —— AI 拍题引导与多轮对话（T03·3.4）。
    * mistake  —— 错题本记录与三态列表（T03·3.4）。
    * exercise —— 修复题 / 迁移题生成（T03·3.4）。
    * file     —— 题目照片上传与读取（T03·3.4）。
    * profile  —— 学习画像看板：掌握度 / 错因分布 / 练习推荐（T04·B8）。
    * report   —— 家长周报告：生成 / 公开分享 / 历史列表（Epic6 · T4/T5）。
    * practice —— 题库练习：抽题 / 判分 / 看答案 / 结算 / AI 引导（题库消费层·B4）。

后续里程碑（task / parent / admin 等）只需在此追加
``api_router.include_router(xxx.router)`` 即可。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    admin,
    auth,
    billing,
    chat,
    cron,
    dna,
    exercise,
    family,
    file,
    game,
    health,
    knowledge,
    mistake,
    practice,
    profile,
    report,
    student,
    tasks,
    tts,
    progress,
    manim,
    geogebra,
    review,
)

#: 所有业务路由统一挂在 ``/api`` 前缀下
api_router = APIRouter(prefix="/api", tags=["api"])

# ---- 已注册路由器 ----------------------------------------------------------
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(family.router)
api_router.include_router(student.router)

# ---- 管理后台（admin-only）--------------------------------------------------
api_router.include_router(admin.router)

# ---- V6.5 Sprint 2 · 学生 DNA 聚合（画像 + 掌握 + 事件，DNA-005）-------------
api_router.include_router(dna.router)

# ---- 知识树（Sprint 1 · KG-003~005）：详情 / 前置 / 关系，公开只读 ---------
api_router.include_router(knowledge.router)

# ---- T03 · 3.4 教学与错题 API ----------------------------------------------
api_router.include_router(chat.router)
api_router.include_router(mistake.router)
api_router.include_router(exercise.router)
api_router.include_router(file.router)

# ---- T04 · 学习画像看板 -----------------------------------------------------
api_router.include_router(profile.router)

# ---- Epic6 · 家长周报（生成 / 公开分享 / 历史）------------------------------
api_router.include_router(report.router)

# ---- V4.2 · 订阅/权益系统 ---------------------------------------------------
api_router.include_router(billing.router)

# ---- 题库消费层 · 练习闭环（抽题 / 判分 / 看答案 / 结算 / 引导）--------------
api_router.include_router(practice.router)

# ---- 方向六 · 游戏化学习（限时挑战 / 回合战斗 / 徽章 / 排行榜）--------------
api_router.include_router(game.router)

# ---- AI 异步任务队列（Celery）-----------------------------------------------
api_router.include_router(tasks.router)

# ---- 云端语音合成（TTS）：读题 / 讲题更甜的嗓音 ----------------------------
api_router.include_router(tts.router)

# ---- 定时任务触发（供 SCF 云函数 / 外部调度器调用）------------------------
api_router.include_router(cron.router)
api_router.include_router(progress.router)
api_router.include_router(manim.router)
api_router.include_router(geogebra.router)

# ---- V6.5 · FSRS 科学复习卡片流（Review.vue 独立复习页）----------------------
api_router.include_router(review.router)



__all__ = ["api_router"]
