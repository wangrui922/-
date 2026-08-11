# 事故复盘：2026-08-04 `git checkout` 触发仓库整体丢失与完整恢复

| 项 | 内容 |
|---|---|
| 发生时间 | 2026-08-04 19:58（UTC 11:58） |
| 触发操作 | `git checkout develop && git merge --no-ff feature/epic6-parent-report` |
| 影响 | `.git` 全库 + `docs/` 全树 + `backend/tests/` 全树 + Epic6 全部 30 个文件从工作区消失 |
| 数据损失 | **0**（全部从回收站还原） |
| 恢复耗时 | 约 8 分钟 |
| 状态 | ✅ 已完全恢复，154 测试全绿，前端构建通过 |

---

## 1. 现象

执行合并命令后，Git 报出：

```
fatal: af1b5ec54c782c1e1f9f88374f7861751100f132 is not a valid object
fatal: stash failed
```

随后 `git status` 报 `not a git repository` —— `.git` 目录已不存在。工作区同时缺失
`docs/`、`backend/tests/`、`backend/app/api/*.py`、`backend/app/models/*.py`、
`student-web/src/views/` 下多数文件。

## 2. 根因

**AI 助手运行沙箱的「删除即进回收站」安全策略，拦截了 Git 的内部文件删除操作。**

Git 在 checkout / merge 过程中会高频创建并删除锁文件（`index.lock`、`HEAD.lock`、
`AUTO_MERGE.lock`、`packed-refs.lock`）。这些删除被沙箱改写为「移动到回收站」，
破坏了 Git 的状态机时序，导致 stash 保护失败；失败后的清理/回滚动作
进一步把整个 `.git` 目录（751 KB）也送进了回收站。

回收站元数据（`$I` 文件）证据链完整：

```
11:58:48  .git\AUTO_MERGE.lock      <- 锁文件被回收，Git 状态机开始崩溃
11:58:54  .git\index.lock
11:58:58  .git\index.stash.14776    <- stash 保护失败残留
11:58:59  .git  (751,098 B)         <- 整个仓库被回收
```

**结论：本项目不能在 AI 沙箱内执行会大量增删文件的 Git 命令。**

## 3. 恢复过程

1. 只读排查，确认 `.git` 与源码均在 D 盘回收站（未彻底删除）
2. 解析回收站 `$I` 元数据，筛出 11:58:30 之后、路径含 `cuotixiaolaoshi` 的 41 条目
3. **跳过所有 `.lock` 与 `index.stash.*`**（还原它们会让仓库被锁死）
4. 目录条目优先 `copytree(dirs_exist_ok=True)`，再补单文件 `copy2`，仅复制不删除
5. `git fsck` 校验：仅 2 个无害 dangling 对象，三个提交全部健在
6. 冷备份 `.git` 至 `D:\制作网页\_gitbackup_cuotixiaolaoshi_20260804_2005`
7. 用**零删除**方式完成合并（见下）
8. 回归验证：后端 154 passed / 0 failed，前端 `npm run build` EXIT=0

## 4. 安全的分支合并方式（本项目约定）

❌ **禁止**在 AI 沙箱内执行：`git checkout <branch>`、`git merge`、`git rebase`、
`git stash`、`git reset --hard` —— 这些会大量删除工作区文件或锁文件。

✅ **允许**（只写不删，零风险）：

```bash
# 快进当前分支到目标提交：只移动指针 + 刷新索引，不碰工作区文件
git reset <target-sha>

# 移动非当前分支指针：纯 refs 文件写入
git branch -f main develop

# 打标签、提交新增文件
git tag -a v4.2.0-mvp -m "..." develop
git add <new-file> && git commit -m "..."
```

若确实需要 `--no-ff` 合并提交或切换分支，**请在本机终端（PowerShell / Git Bash）
手动执行**，不要交给 AI 沙箱。

## 5. 防复发措施

| 措施 | 状态 |
|---|---|
| `.git` 冷备份目录 `_gitbackup_cuotixiaolaoshi_20260804_2005` | ✅ 已建 |
| 本文档写入 `docs/`，作为团队操作禁令 | ✅ |
| 项目长期记忆记录「沙箱 Git 禁令」 | ✅ |
| 建议：推送一份到远程仓库（Gitee / CNB），彻底摆脱单点风险 | ⏳ 待办 |

> 最强的防线不是备份脚本，是**远程仓库**。本地再多副本也扛不住一次误操作。
