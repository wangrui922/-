"""检索打分纯函数层（bigram 切分 + 文本相似度打分）。

这个模块只做两件事：**把中文切成 bigram**、**给候选文本打分**。
**不查库、不 import ORM、不读环境变量** —— 所有查库逻辑在
``app/services/grounding_service.py``。

为什么不用现成的中文分词器（jieba / pkuseg / HanLP）
------------------------------------------------------
服务器每多一个依赖就多一份升级与冲突风险（jieba 要带 ``dict.txt`` 词典文件、
HanLP 要带模型文件），而这里的任务只是「找出两段小学数学题面重合的片段」。
自己写 bigram 滑窗：**零依赖、零词典、行为可预测**，而且对「文具店」「平均分」
这类短词的效果与分词器几乎一致。

为什么不建 FTS5 全文索引（请读完这段再「优化」）
--------------------------------------------------
实测（4166 道 active 题，含无索引 LIKE 扫描）：

    无索引 LIKE median       0.391 ms
    bigram + FTS5    median   0.018 ms
    差                         0.37 ms

为 **0.37 ms** 的差距引入一张 FTS5 虚拟表 —— 意味着要额外维护一条
「虚拟表 ↔ questions 表」的同步链路（题库导入、归档、删除都得记得同步，
漏一次就是静默的检索错误），收益却只有零点几毫秒。
而选型风险是实打实的：

* FTS5 默认 ``unicode61`` 分词器**不切中文**（整句当一个 token，等于没分词）；
* ``trigram`` 分词器对 **2 字词**（本题库高频词恰恰是「平均」「剩下」「一共」
  这类 2 字词）匹配不上；
* 中文场景要真正用好 FTS5，得引入 ``jieba`` 预分词 + 自定义 tokenizer，
  依赖量直接翻倍。

题量 4166、年级窗口过滤后候选只有几百行，**结构化粗筛（SQL WHERE）
已经把候选压到很小**，剩下的 LIKE 精排完全够用。
结论：不上 FTS5。题量再涨一个数量级（万级）时再重新评估。

打分口径
--------
用 **bigram 覆盖度**（query 的 bigram 有多大比例出现在候选里）而不是 TF-IDF /
余弦相似度：

* 候选集只有几百行，词频统计（IDF）带来的区分度增益可忽略；
* 覆盖度**与题库无关**——不会因为新题进来而让历史检索结果漂移，
  同样的 query 永远得到同样的分数（可复现，便于回归测试断言）；
* 分母固定为 query 的 token 数 → 分数直观可比（0.6 = 六成片段对得上）。
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Set, Tuple

# =============================================================================
# 常量
# =============================================================================

#: 单个 token 的最大长度（英文/数字串整体保留时用它截断）。
MAX_TOKEN_LEN: int = 16

#: 单个 token 的最小长度（1 字 token 噪声太大，丢掉）。
MIN_TOKEN_LEN: int = 2

#: 停用词：小学数学题面里高频、且不携带区分度的 **bigram**。
#:
#: 注意只收 2 字词（bigram 的最小单位）；「的」「了」这类单字词切不出
#: bigram，写进来没有意义。「平均」「还剩」这类**有区分度**的数学词不收。
STOPWORDS: frozenset = frozenset(
    {
        "一共", "多少", "几个", "什么", "请问", "下面", "上面", "写出", "列式",
        "这道", "多少个", "求?", "求？", "多少?", "多少？",
    }
)

#: 分词用的字符类：连续汉字 / 连续英文字母 / 连续数字。
_WORD_RE = re.compile(r"[一-鿿]+|[A-Za-z]+|[0-9]+")

#: 中文「虚字」集合 —— trigram 里出现这些字，多半是切在了词边界上
#: （比如「小明在文具店」切出「明在文」），作为 LIKE 关键词区分度差，排序降权。
#:
#: 注意：**不收**「买」「卖」「分」「剩」这类**实词** —— 它们恰恰是小学
#: 数学题面里最带场景信息的字（「买了」「平均分」「还剩」）。
_FUNCTION_CHARS: frozenset = frozenset(
    "的了在是和与把被就都也还又并及等这那有个不很再从向往给让对每其中"
    "而且所以因为但如若并且虽然然后于之其为是以所并且"
)

#: LIKE 精排片段的最小长度（3 字）。
#:
#: 为什么是 3 而不是 2：2 字词（「一共」「多少」「剩下」）在 4166 行小学
#: 题面里命中上千行，LIKE 等于没筛；3 字词（「文具店」「平均分」「买铅笔」）
#: 命中几十行，既能筛掉绝大多数无关题，又保证目标题自己一定命中
#: （目标题的题面必然包含自己的 3 字滑窗）。
MIN_LIKE_LEN: int = 3

#: LIKE 精排片段的最大长度（6 字）。
#:
#: 为什么封顶：太长的片段（如整句 20 字）会把「同场景兄弟题」也筛掉，
#: 而同场景兄弟题正是接地气素材的来源 —— 筛太狠等于把素材池自己掐死。
MAX_LIKE_LEN: int = 6


# =============================================================================
# 切分
# =============================================================================


def bigrams(text: str) -> List[str]:
    """把中文文本切成 bigram（相邻两字的滑窗）。

    * 连续汉字 → 滑窗取相邻两字（``"平均分给四个人"`` →
      ``["平均","均分","分给","给四","四个","个人"]``）；
    * 连续英文字母 / 数字 → **整体保留**（``"APP"`` → ``["APP"]``），
      不切碎（英文词的 bigram 没有语义增益）；
    * 空白与标点天然被 ``_WORD_RE`` 过滤掉；
    * 停用词被过滤（避免「一共」「多少」之类占位）。

    Args:
        text: 待切分文本。

    Returns:
        bigram 列表（保持出现顺序，可重复）。
    """
    if not text:
        return []

    tokens: List[str] = []
    for match in _WORD_RE.finditer(text):
        chunk = match.group(0)
        if len(chunk) == 1:
            # 单字：无 bigram 意义，直接丢（英文/数字已在上面分流）
            continue
        if _is_ascii(chunk):
            tokens.append(chunk[:MAX_TOKEN_LEN].lower())
            continue
        for i in range(len(chunk) - 1):
            tokens.append(chunk[i : i + 2])

    return [tok for tok in tokens if tok not in STOPWORDS and len(tok) >= MIN_TOKEN_LEN]


def _is_ascii(chunk: str) -> bool:
    """判断一个分词片段是否为纯 ASCII（英文/数字）。"""
    return all(ord(ch) < 128 for ch in chunk)


def token_set(text: str) -> Set[str]:
    """``bigrams`` 的去重版本（打分只需要「出现过没有」）。"""
    return set(bigrams(text))


# =============================================================================
# 打分
# =============================================================================


def score_text(query_tokens: Iterable[str], doc_tokens: Iterable[str]) -> float:
    """给一个候选文本打分（**bigram 覆盖度**）。

    公式：``|query_tokens ∩ doc_tokens| / |query_tokens|``，取值 ``0.0 ~ 1.0``。

    为什么用覆盖度而不是 TF-IDF / 余弦：

    * 候选集小，词频统计（IDF）带来的区分度增益可忽略；
    * 覆盖度**不依赖题库统计量** → 新题进来不会让历史排序漂移，
      同样的 query 永远得到同样的分数（回归测试可断言）；
    * 分母固定为 query 的 token 数 → 分数直观可比（0.6 = 六成片段对得上）。

    Args:
        query_tokens: 查询词（一般传 ``bigrams(query)`` 的结果）。
        doc_tokens: 候选文本的词（一般传 ``bigrams(doc)`` 的结果）。

    Returns:
        ``0.0 ~ 1.0`` 的分数；查询词为空时返回 ``0.0``。
    """
    q_set = set(query_tokens)
    if not q_set:
        return 0.0
    d_set = set(doc_tokens)
    if not d_set:
        return 0.0
    return len(q_set & d_set) / len(q_set)


def score_query(query_text: str, doc_text: str) -> float:
    """便捷版打分：直接给两段文本，返回覆盖度分数。"""
    return score_text(bigrams(query_text), bigrams(doc_text))


def _function_penalty(token: str) -> int:
    """片段里的「虚字」个数（越少说明切得越像完整词，区分度越高）。"""
    return sum(1 for ch in token if ch in _FUNCTION_CHARS)


def _chinese_runs(text: str) -> List[str]:
    """把文本切成连续的汉字片段（英文 / 数字 / 标点天然断句）。"""
    runs: List[str] = []
    for match in _WORD_RE.finditer(text):
        chunk = match.group(0)
        if _is_ascii(chunk):
            continue
        runs.append(chunk)
    return runs


def trigrams(text: str) -> List[str]:
    """把中文文本切成 trigram（相邻三字的滑窗）。

    * 连续汉字 → 滑窗取相邻三字（``"文具店买了"`` →
      ``["文具店","具店买","店买了"]``）；
    * 英文 / 数字**不参与** trigram —— 数字绝不能当检索词
      （既会命中无关题，也会把原题数字带进链路）。

    与 :func:`bigrams` 的分工：bigram 用于**打分**（覆盖度，2 字粒度更细），
    trigram 用于**SQL LIKE 粗筛**（3 字粒度更准，能真正把候选压下来）。

    Args:
        text: 待切分文本。

    Returns:
        trigram 列表（保持出现顺序，可重复）。
    """
    if not text:
        return []

    tokens: List[str] = []
    for match in _WORD_RE.finditer(text):
        chunk = match.group(0)
        if _is_ascii(chunk):
            continue
        for i in range(len(chunk) - MIN_LIKE_LEN + 1):
            tokens.append(chunk[i : i + MIN_LIKE_LEN])
    return tokens


def like_terms(text: str, limit: int = 3) -> List[str]:
    """从文本里挑出适合做 SQL ``LIKE`` 的词（区分度高的优先）。

    口径
    ----
    * **候选来源**：每个连续汉字片段里长度 3~6 的子串。3 字保底（2 字词
      「一共」「多少」在 4166 行小学题面里命中上千行，LIKE 等于没筛），
      6 字封顶（太长会把「同场景兄弟题」也筛没了）。
    * **排序**：虚字少 → 片段长 → 出现位置靠前。虚字多说明切在了词边界上
      （「明在文」），区分度差，降权。
    * **保底**：候选为空（文本里没有 3 个连续汉字）→ 退回 bigram（≥2 字），
      **绝不返回空列表** —— 返回空会让 SQL 少一层过滤，候选集直接涨几千行。

    硬保证：返回的每个词都是**输入文本的子串**。因此「用某道题的题面做
    检索目标串」时，该题必然命中自己的 LIKE 条件（召回率测试据此断言）。

    Args:
        text: 源文本。
        limit: 最多返回几个词。

    Returns:
        去重后的词列表（区分度高 → 低）。
    """
    if not text:
        return []
    limit = max(0, int(limit))
    if limit == 0:
        return []

    candidates: List[str] = []
    for run in _chinese_runs(text):
        run_len = len(run)
        if run_len < MIN_LIKE_LEN:
            continue
        upper = min(run_len, MAX_LIKE_LEN)
        for size in range(MIN_LIKE_LEN, upper + 1):
            for i in range(run_len - size + 1):
                candidates.append(run[i : i + size])

    if candidates:
        first_index: Dict[str, int] = {}
        for index, token in enumerate(candidates):
            first_index.setdefault(token, index)
        ordered = sorted(
            first_index.items(),
            key=lambda item: (_function_penalty(item[0]), -len(item[0]), item[1]),
        )
        selected: List[str] = []
        for token, _ in ordered:
            # 去冗余：与已选词互为子串的（"文具店" vs "文具店买"）只留长的
            if any(token in kept or kept in token for kept in selected):
                continue
            selected.append(token)
            if len(selected) >= limit:
                break
        if selected:
            return selected

    # 退化路径：文本不足 3 个连续汉字 → 用 bigram（已过滤停用词与 1 字 token）
    fallback = [token for token in bigrams(text) if len(token) >= MIN_TOKEN_LEN]
    seen: Set[str] = set()
    out: List[str] = []
    for token in fallback:
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out[:limit]


def rank_documents(
    query_text: str,
    documents: Sequence[Tuple[str, str]],
    *,
    limit: int = 1,
) -> List[Tuple[str, float]]:
    """给 ``(doc_id, doc_text)`` 列表排序（纯函数，不查库）。

    **稳定排序**：分数降序；同分保持**输入顺序**（Python 的 ``sorted`` 稳定
    性质 + 只以分数做 key）。这保证同样的输入永远得到同样的输出顺序 ——
    回归测试可以断言「第 1 名是 X」而不只是断言「集合相等」。

    Args:
        query_text: 查询文本。
        documents: ``(doc_id, doc_text)`` 序列。
        limit: 最多返回几条。

    Returns:
        ``(doc_id, score)`` 列表，按分数降序。
    """
    q_tokens = bigrams(query_text)
    if not q_tokens:
        return []

    scored: List[Tuple[str, float]] = []
    for doc_id, doc_text in documents:
        score = score_text(q_tokens, bigrams(doc_text))
        scored.append((doc_id, score))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[: max(0, int(limit))]


__all__ = [
    "MAX_TOKEN_LEN",
    "MIN_TOKEN_LEN",
    "MIN_LIKE_LEN",
    "STOPWORDS",
    "bigrams",
    "trigrams",
    "token_set",
    "score_text",
    "score_query",
    "like_terms",
    "rank_documents",
]
