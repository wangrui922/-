<!-- 存档说明：本文件为用户于 2026-08-04 贴出的《AI数学教练 V4 数据库详细设计 + API接口设计》早期/简版草稿。
     仓库内已存在更完整、由架构师高见远撰写的权威版 docs/v4-db-api.md（15 张表、pgvector、双方言细节、腾讯云部署约定等）。
     本草稿与之存在系统性差异：路由前缀（/api/v1/ vs /api/）、错因（自由文本 vs 已统一的 5 类枚举）、家长访问（家长账号 vs D7 分享链接）。
     保留此草稿仅供追溯"早期设想"，实际开发以 docs/v4-db-api.md + docs/epic6-design.md 为准。 -->

# 《AI数学教练 V4 数据库详细设计 + API接口设计》

---

# 一、版本目标

> 给开发团队一份可以直接开始搭建后端的数据和接口设计文档。

本设计解决：

1. 数据如何存储
2. 前后端如何通信
3. AI Agent如何调用
4. 未来商业功能如何扩展

设计原则：

> 第一版商业系统不追求复杂微服务，采用“模块化单体 + AI服务”的架构。

推荐技术：

* 后端：FastAPI
* 数据库：PostgreSQL
* 缓存：Redis
* 文件存储：对象存储
* AI服务：Agent服务层
* 向量数据库：pgvector

---

# 二、整体数据架构

```text
                 用户

                  │

              前端应用

                  │

              API接口层

                  │

        ┌─────────┴─────────┐

        业务服务层          AI服务层

        │                   │

  用户/学习/题库       Agent系统

        │                   │

        └─────────┬─────────┘

                  │

              PostgreSQL

                  │

        ┌─────────┴─────────┐

      结构数据          向量数据

      用户信息          学习记忆

      错题记录          孩子画像

      题库              AI记忆
```

---

# 三、数据库详细设计

---

# 1. 用户体系

## users 用户表

用途：

保存所有账号基础信息。

表：

```
users
```

字段：

| 字段            | 类型       | 说明   |
| ------------- | -------- | ---- |
| id            | uuid     | 用户ID |
| phone         | varchar  | 手机号  |
| email         | varchar  | 邮箱   |
| password_hash | varchar  | 密码   |
| role          | enum     | 角色   |
| avatar        | varchar  | 头像   |
| status        | int      | 账号状态 |
| created_at    | datetime | 创建时间 |
| updated_at    | datetime | 更新时间 |

角色：

```
student
parent
admin
```

---

# 2. 学生信息表

## students

用途：

保存学生基础资料。

字段：

| 字段           | 类型       | 说明    |
| ------------ | -------- | ----- |
| id           | uuid     | 学生ID  |
| user_id      | uuid     | 关联用户  |
| name         | varchar  | 姓名    |
| grade        | varchar  | 年级    |
| school_stage | varchar  | 小学/初中 |
| target       | text     | 学习目标  |
| created_at   | datetime | 创建时间  |

示例：

```json
{
"name":"小明",
"grade":"五年级",
"target":"提高应用题能力"
}
```

---

# 3. 家长关系表

## parent_student_relation

用途：

解决一个家长管理多个孩子的问题。

字段：

| 字段         | 说明   |
| ---------- | ---- |
| parent_id  | 家长ID |
| student_id | 学生ID |
| relation   | 关系   |

示例：

```
爸爸 → 小明
妈妈 → 小红
```

---

# 4. 题目表

## questions

用途：

保存题目基础信息。

字段：

| 字段         | 说明   |
| ---------- | ---- |
| id         | 题目ID |
| content    | 题目文字 |
| image_url  | 题目图片 |
| subject    | 学科   |
| grade      | 年级   |
| difficulty | 难度   |
| source     | 来源   |
| created_at | 创建时间 |

难度：

```
1 入门

2 基础

3 标准

4 提升

5 挑战
```

---

# 5. 知识点表

## knowledge_points

用途：

建立数学知识图谱。

字段：

| 字段          | 说明    |
| ----------- | ----- |
| id          | 知识点ID |
| name        | 知识点名称 |
| parent_id   | 父节点   |
| description | 说明    |
| level       | 层级    |

示例：

```
数学

 └── 分数

      └── 通分

          └── 异分母加法
```

---

# 6. 题目知识关联表

## question_knowledge

用途：

一个题目对应多个知识点。

字段：

| 字段           |
| ------------ |
| question_id  |
| knowledge_id |
| weight       |

示例：

```
一道应用题：

应用题能力 70%

计算能力 30%
```

---

# 7. 学生能力画像表

## student_mastery

用途：

记录学生对知识点的掌握程度。

字段：

| 字段            | 说明    |
| ------------- | ----- |
| id            | 记录ID  |
| student_id    | 学生ID  |
| knowledge_id  | 知识点ID |
| mastery_score | 掌握度   |
| confidence    | 可信度   |
| last_update   | 更新时间  |

示例：

```json
{
"knowledge":"分数通分",
"mastery":0.65
}
```

---

# 8. 错题记录表

## mistakes

用途：

保存孩子错误原因。

字段：

| 字段             | 说明   |
| -------------- | ---- |
| id             | ID   |
| student_id     | 学生   |
| question_id    | 题目   |
| answer         | 学生答案 |
| correct_answer | 正确答案 |
| error_type     | 错误类型 |
| error_reason   | 错误原因 |
| status         | 掌握状态 |
| created_at     | 时间   |

状态：

```
未掌握

训练中

已掌握
```

---

# 9. AI对话记录表

## ai_sessions

用途：

记录一次完整学习过程。

字段：

| 字段          | 说明   |
| ----------- | ---- |
| id          | 会话ID |
| student_id  | 学生   |
| question_id | 题目   |
| start_time  | 开始时间 |
| end_time    | 结束时间 |
| result      | 结果   |

---

## ai_messages

用途：

保存AI和学生聊天内容。

字段：

| 字段         | 说明      |
| ---------- | ------- |
| session_id | 会话ID    |
| role       | 角色      |
| content    | 内容      |
| agent_type | Agent类型 |
| created_at | 时间      |

角色：

```
student

assistant

system
```

Agent：

```
TutorAgent

DiagnosisAgent

ExerciseAgent
```

---

# 10. 学习任务表

## learning_tasks

用途：

保存每日学习计划。

字段：

| 字段         |
| ---------- |
| student_id |
| task_type  |
| content    |
| status     |
| deadline   |

任务类型：

```
错题复习

专项训练

测试
```

---

# 11. 学习记录表

## learning_records

用途：

统计成长数据。

字段：

| 字段             |
| -------------- |
| student_id     |
| duration       |
| question_count |
| correct_count  |
| score_change   |
| created_at     |

---

# 12. 会员订阅表

## subscriptions

字段：

| 字段         |
| ---------- |
| user_id    |
| plan       |
| start_time |
| end_time   |
| status     |

---

# 13. AI长期记忆表

## ai_memory

用途：

保存孩子长期学习特征。

字段：

| 字段          |
| ----------- |
| student_id  |
| memory_type |
| content     |
| embedding   |
| created_at  |

类型：

```
学习习惯

错误模式

教学偏好

性格特点
```

---

# 四、API接口设计

统一返回格式：

请求：

```json
{
"data":{}
}
```

返回：

```json
{
"code":0,
"message":"success",
"data":{}
}
```

---

# 五、用户接口

## 注册

POST

```
/api/v1/auth/register
```

请求：

```json
{
"phone":"138xxxx",
"password":"123456",
"role":"student"
}
```

返回：

```json
{
"user_id":"xxx",
"token":"xxx"
}
```

---

## 登录

POST

```
/api/v1/auth/login
```

---

# 六、学生接口

## 获取学生画像

GET

```
/students/{id}/profile
```

返回：

```json
{
"math_score":78,

"weak_points":[
"应用题",
"分数"
]
}
```

---

# 七、错题接口

## 保存错题

POST

```
/mistakes
```

请求：

```json
{
"student_id":"001",
"question_id":"100",
"answer":"x=5",
"error_type":"移项错误"
}
```

---

## 获取错题列表

GET

```
/mistakes/list
```

参数：

```
student_id

status

knowledge
```

---

# 八、AI老师接口

## 开始学习

POST

```
/ai/session/start
```

请求：

```json
{
"student_id":"001",

"question":"x+5=8"
}
```

返回：

```json
{
"session_id":"abc",

"analysis":{
"knowledge":"方程",
"possible_error":"移项"
},

"message":
"我们先看看题目要求什么"
}
```

---

## 学生回答

POST

```
/ai/session/chat
```

请求：

```json
{
"session_id":"abc",

"message":"我要把5移过去"
}
```

返回：

```json
{
"agent":"TutorAgent",

"next_question":
"移过去以后符号会变化吗？"
}
```

---

# 九、练习生成接口

POST

```
/ai/exercise/generate
```

请求：

```json
{
"student_id":"001",

"knowledge":"一元一次方程",

"error":"移项错误",

"count":2
}
```

返回：

```json
{
"questions":[

{
"title":"2+x=8",
"type":"修复题"
},

{
"title":"3x+5=14",
"type":"迁移题"
}

]
}
```

---

# 十、报告接口

POST

```
/report/generate
```

请求：

```json
{
"student_id":"001",

"period":"week"
}
```

返回：

```json
{
"progress":"+12%",

"weak":"应用题",

"suggestion":
"继续训练数量关系"
}
```

---

# 十一、AI Agent调用协议

所有Agent统一：

输入：

```json
{
"context":{},
"task":""
}
```

输出：

```json
{
"result":{},
"confidence":0.95
}
```

---

# 十二、Agent调用链

整体流程：

```
学生提交题目

↓

Question Agent

↓

Diagnosis Agent

↓

Tutor Agent

↓

Exercise Agent

↓

学习画像更新

↓

Report Agent
```

---

# 十三、V4开发顺序

严格按照：

```
1. 数据库建表

↓

2. 后端API

↓

3. AI Agent服务

↓

4. 学生端

↓

5. 家长端

↓

6. 后台

↓

7. 支付系统
```

---

# 十四、最终验收标准

一个学生：

注册

↓

上传数学题

↓

AI分析

↓

AI教学

↓

生成练习

↓

保存错题

↓

更新能力画像

↓

生成家长报告

↓

持续学习30天

---

这份文档作为：

《AI数学教练 V4 后端设计基础文档》

后续开发：

数据库、API、AI Agent、前端页面均以此为依据。
