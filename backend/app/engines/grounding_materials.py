"""接地气素材包引擎（纯函数层 · A-1 期）。

把「一道题 + 一个知识点」加工成**可以直接塞进 tutor system 提示**的四段素材包：

    GroundingMaterials
    ├── peer_misconception: str   # 同伴视角句 —— 本期恒空（语料库属 A-2）
    ├── analogy: str              # 生活类比句 —— 本期有内容
    ├── kp_anchor: str            # 知识点释义 —— 本期有内容
    └── personal_error: str       # 个体错因 —— 本期恒空（无个体数据）

三条铁律（本文件的全部价值都在这上面）：

1. **缺哪段就不编**：拿不到内容就留空串，**绝不写占位语**（「暂无相关素材」
   「你可以参考…」这类）。空段在渲染时整段不出现。
2. **不送数字**：原题的数字送进 prompt = 泄题。所以情境骨架与知识点释义
   都要过数字剥离（见 ``strip_numbers`` / ``build_kp_anchor``）。
3. **红线过滤**：渲染出去的任何一段都必须过
   《规格-同伴视角语料库-20261029.md》的 6 条硬红线（禁真实姓名/班级/校名、
   禁把错误指向「你」、禁 LaTeX、禁初中高中、禁贬义词、单句 ≤40 字且不自称）。

纯函数层铁律（与 ``app/engines`` 其余模块一致）：

* **禁 import sqlalchemy / Session / FastAPI**，**禁读环境变量**；
* 阈值一律走**模块常量 + 函数默认参数**注入，不进 ``engines/config.py``
  （那 31 个键被 ``tests/engines/test_config_golden.py`` 逐字写死，与并行
  开发中的其它分支共用，改了必冲突）；
* 一切入参都是 ``str`` / ``Optional[str]``，不依赖 ORM 对象。

素材来源的分工（A-1 期定稿）：

* ``analogy`` 的素材源是 **``questions.content``（题面）**，不是
  ``answers.analysis``——实测小学 analysis 平均仅 18.6 字、40 字以上只有 9 条、
  含因果标记 0 条，撑不起「接地气讲解」；题面含生活场景词 53.9%、人物名 11.9%、
  第二人称 7.5%，这才是接地气素材的来源。
* ``answers.distractor_analysis`` **不可用**：它是 JSON 选项数组（干扰项数值），
  不是分析文本。
* ``kp_anchor`` 的素材源是 ``knowledge_points.description``。

A-2 期（同伴视角语料库 026 迁移）落地时，**只需替换
``build_peer_misconception`` 的数据来源**，四段结构、渲染与注入链路一行都不用改。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# =============================================================================
# 常量
# =============================================================================

#: 渲染文本（注入 tutor system 提示的那一整段）的字符数上限。
#:
#: 为什么用**字符数**而不是 token 数：PM 的验收标准是「单次 token 增量 ≤1.5k」，
#: 而中文场景下 1 个字符 ≈ 0.6~1.5 token 不定（取决于分词器），用字符数断言
#: 是**更硬**的上界——字符数超了，token 数一定超。
MAX_MATERIAL_CHARS: int = 1500

#: 情境骨架（scene anchor）的字符数上限。
MAX_ANCHOR_CHARS: int = 20

#: 单段素材（analogy / kp_anchor / …）的字符数上限。
MAX_SEGMENT_CHARS: int = 120

#: 单句字数上限（《规格》红线⑥）。
MAX_SENTENCE_CHARS: int = 40

#: 注入文本的内部标注（沿用 ``app/ai/agents/tutor.py`` 里
#: ``_ERROR_HINT_TEMPLATE`` 的既有风格：明确告诉模型「这段不是给孩子听的」）。
INTERNAL_HINT: str = "（内部提示，不要念给孩子听）"

#: 注入文本里给模型的用法说明（每句 ≤ ``MAX_SENTENCE_CHARS``）。
_HINT_LINES: Tuple[str, ...] = (
    "下面是可以参考的接地气素材，缺哪段就不说哪段。",
    "不要把素材原样念给孩子，也不要把素材里的说法当成结论。",
)

# =============================================================================
# 四段素材包
# =============================================================================


@dataclass(frozen=True)
class GroundingMaterials:
    """一段接地气讲解素材（四段）。

    四段全部默认空串 —— **空即不编**：拿不到内容就留空，渲染时整段不出现。
    """

    peer_misconception: str = ""
    analogy: str = ""
    kp_anchor: str = ""
    personal_error: str = ""

    def is_empty(self) -> bool:
        """四段是否全空（全空 → 不该往提示词里注入任何东西）。

        空白串（只有空格 / 换行）也算空 —— 渲染层按 ``strip()`` 过滤，
        这里必须与渲染层**同口径**，否则会出现「is_empty 说有内容、
        渲染出来却什么都没有」的不一致。
        """
        return not any(
            part.strip()
            for part in (
                self.peer_misconception,
                self.analogy,
                self.kp_anchor,
                self.personal_error,
            )
        )

    def sections(self) -> List[Tuple[str, str]]:
        """返回 ``(段标签, 段内容)`` 列表，只含**非空**段。

        顺序固定：同伴提醒 → 生活类比 → 知识点 → 个人错因。顺序即优先级，
        渲染与测试都依赖它保持稳定。
        """
        pairs: Tuple[Tuple[str, str], ...] = (
            ("同伴提醒", self.peer_misconception),
            ("生活类比", self.analogy),
            ("知识点", self.kp_anchor),
            ("个人错因", self.personal_error),
        )
        return [(label, text) for label, text in pairs if text.strip()]


# =============================================================================
# 数字剥离（硬安全约束：素材里不许出现原题数字）
# =============================================================================

#: 数字后面常跟的**单位/量词**。剥数字时要把单位一起带走，否则会留下
#: 光秃秃的「支铅笔」「个苹果」这种半截话。
_UNIT_WORDS: Tuple[str, ...] = (
    "元", "角", "分", "度", "个", "只", "支", "本", "块", "张", "片", "颗",
    "粒", "棵", "朵", "双", "份", "群", "把", "台", "件", "组", "排", "遍",
    "趟", "回", "次", "下", "米", "厘米", "毫米", "千米", "公斤", "千克",
    "吨", "克", "毫升", "升", "天", "周", "月", "年", "岁", "人", "倍",
    "页", "条", "根", "位", "%", "％",
)

#: 单位表按**长度降序**拼成 alternation（长的必须排在前面）。
#:
#: 为什么：「3厘米」如果先匹配「米」，会留下光秃秃的「厘」；按长度降序
#: 排，「厘米」先命中，「3厘米」整段消失。
_UNIT_ALTERNATION: str = "|".join(
    sorted(_UNIT_WORDS, key=lambda word: (-len(word), word))
)

#: 「数字 + 单位」整体（单位可选）。数字被剥掉时单位一并去掉。
_NUM_WITH_UNIT = re.compile(
    r"[0-9]+(?:\.[0-9]+)?\s*(?:" + _UNIT_ALTERNATION + r")?"
)

#: 中文数字 + **明确量词**（如「三支」「二十个」）——只在后面紧跟量词时算数值。
#:
#: 三个细节：
#: * **消费**量词（「三个苹果」→「苹果」，不留光秃秃的「个」）；
#: * ``(?!形)`` 守卫：「三角形」的「三角」不是数量（角是几何概念，
#:   量词表里的「角」指的是钱币单位「三毛钱」），不能剥；
#: * ``(?!数)`` 守卫：「两位数」「三位数」的「两位」「三位」是**知识点名字**
#:   的一部分，不是数量，剥掉会毁掉语义。
_CN_NUM_WITH_UNIT = re.compile(
    r"[零一二三四五六七八九十百千万亿两]{1,4}(?:" + _UNIT_ALTERNATION + r")(?!形|数)"
)

#: 落单的中文数字（前面不是量词语境、后面也不接量词）。
#:
#: ⚠️ 必须保护「**词内数字**」：「三角形」的「三」、「两位数」的「两」是
#: **词的一部分**，剥掉就变成「角形」「位数」—— 语义毁了。
#: 所以剥之前先把 :data:`_LEXICAL_NUMBER_TERMS` 里的词**遮罩**掉，
#: 剥完再还原。
_CN_NUM_LONE = re.compile(r"[零一二三四五六七八九十百千万亿两]")

#: **词内数字**白名单：这些词里的中文数字是**术语的一部分**，不是数量。
#:
#: 覆盖小学数学里最常见的几类：
#:
#: * 图形类：「三角形」「四边形」「五边形」「六边形」；
#: * 数位类：「一位数」「两位数」「三位数」「四位数」；
#: * 运算类：「四则运算」。
_LEXICAL_NUMBER_TERMS: Tuple[str, ...] = (
    "三角形",
    "四边形",
    "五边形",
    "六边形",
    "四则运算",
    "一位数",
    "两位数",
    "三位数",
    "四位数",
)

#: 遮罩用的占位符（**私有区字符**，Unicode 码位 U+E000 起）。
#:
#: ⚠️ 占位符**绝不能含阿拉伯数字**：数字剥离会把它一起剥掉
#: （「\x000\x01」里的「0」被当成数字删掉，词就还原不回来了）。
#:
#: ⚠️ 占位符必须是**至少 2 个字符**：``build_kp_anchor`` 有「子句长度 < 2
#: 就丢弃」的规则（剥完只剩虚词的子句没有信息量），单字符占位符会被
#: 当成垃圾子句丢掉 ——「四则运算」曾因此整段消失。
_MASK_BASE: int = 0xE000

#: 阿拉伯数字字符集（测试与调用方断言用）。
DIGIT_CHARS: str = "0123456789"

#: 中文数字字符集（测试与调用方断言用）。
#:
#: 注意**不含「零」**：「零花钱」「零数」这类词里的「零」是词的一部分，
#: 不是数量，剥掉会毁掉语义。
CN_NUMERAL_CHARS: str = "一二三四五六七八九十百千万亿两"


def _mask_lexical_number_terms(text: str) -> Tuple[str, Dict[str, str]]:
    """把「词内数字」词（三角形 / 两位数 …）遮罩成占位符。

    占位符用**私有区单字符**（U+E000 起），保证：
    * 不含阿拉伯数字（否则会被数字剥离误伤）；
    * 不含中文（否则会被 ``_CN_NUM_LONE`` 误伤）；
    * 不被 ``_RESIDUAL_SEP`` 吃掉。

    Returns:
        ``(遮罩后的文本, {占位符: 原词})``
    """
    mapping: Dict[str, str] = {}
    masked = text
    for index, term in enumerate(_LEXICAL_NUMBER_TERMS):
        if term not in masked:
            continue
        token = chr(_MASK_BASE + index) + chr(_MASK_BASE + 0x40 + index)
        mapping[token] = term
        masked = masked.replace(term, token)
    return masked, mapping


def _unmask_lexical_number_terms(text: str, mapping: Dict[str, str]) -> str:
    """还原 :func:`_mask_lexical_number_terms` 遮罩掉的词。"""
    for token, term in mapping.items():
        text = text.replace(token, term)
    return text


def strip_numbers(text: Optional[str], *, keep_cn_numbers: bool = False) -> str:
    """把文本里的数字剥掉（泄题防线）。

    Args:
        text: 原始文本，可为 ``None``。
        keep_cn_numbers: 是否保留中文数字。知识点释义（``kp_anchor``）传
            ``True``——「三角形」「两位数乘除法」里的中文数字是**词的一部分**，
            删了语义就毁了；题面骨架（``analogy``）传 ``False``——那里的
            「三个」「24」都是原题的数量，必须删干净。

    Returns:
        剥离数字后的文本（空白已归一）。
    """
    if not text:
        return ""
    masked, mapping = _mask_lexical_number_terms(text)
    out = _NUM_WITH_UNIT.sub("", masked)
    out = _CN_NUM_WITH_UNIT.sub("", out)
    if not keep_cn_numbers:
        out = _CN_NUM_LONE.sub("", out)
    out = _unmask_lexical_number_terms(out, mapping)
    out = re.sub(r"[ \t　]+", " ", out)
    # 中文之间的空格是「数字被删掉」留下的空洞（「小明有 5 个苹果」→「小明有 苹果」），
    # 直接去掉，否则渲染出来是半截话。
    out = re.sub(r"(?<=[一-鿿]) (?=[一-鿿])", "", out)
    return out.strip()


# =============================================================================
# 场景骨架表
# =============================================================================
#
# 为什么不直接把题面切一段出来当锚点：
#   实测小学题面里 3165/3285 条含阿拉伯数字，题面普遍带「小明」「二（1）班」
#   「A/老师带你口算闯关…」这类尾巴。直接切片会得到「文具店买了 支铅笔，共花了 元。」
#   这种半截话 —— 既不接地气，又可能漏出数字或班级名。
#
# 所以：**先识别场景，再映射到规范化骨架**。骨架来自下面这张**显式常量表**，
# 每条都是人工审过的话：不含数字、不含姓名班级、不含初高中内容、不含贬义词。
# 题面提供「这是哪一类场景」这个信号，句子本身由表提供 —— 这也是
# 「从题面抽情境骨架」的落地形式：抽的是**场景**，不是原文切片。

#: 场景识别的**优先级层级**（数字越小优先级越高）。
#:
#: 为什么要分层：题面里往往同时出现多个场景词（「在文具店买了 3 支铅笔」里有
#: 「文具店」「买了」），必须有**确定的取舍规则**，否则同一道题在不同次运行里
#: 可能抽出不同的骨架，回归测试无法断言。
#:
#: 分层依据是「哪一层更能代表这道题的生活场景」：
#:
#: 1. **操作结构**（分给 / 平均分 / 送给）—— 题目在干什么，最本质；
#: 2. **具体场所与道具**（文具店 / 超市 / 钟表 / 火车）—— 最接地气的载体；
#: 3. **交易动作**（买了 / 带了 / 花了 / 还剩）—— 购物场景的兜底；
#: 4. **食物**（蛋糕 / 苹果 / 西瓜 …）—— **只在有「分给」类操作时生效**
#:    （「分蛋糕给同学」成立；「妈妈买了 3 千克苹果」里就**不**成立）；
#: 5. **人物与校园背景**（妈妈 / 同学 / 运动会）—— 最弱，只在没有更好的线索时用。
TIER_OPERATION: int = 1
TIER_OBJECT: int = 2
TIER_TRANSACTION: int = 3
TIER_FOOD: int = 4
TIER_PERSON: int = 5

#: 优先级遍历顺序（与层级常量一一对应）。
TIER_ORDER: Tuple[int, ...] = (
    TIER_OPERATION,
    TIER_OBJECT,
    TIER_TRANSACTION,
    TIER_FOOD,
    TIER_PERSON,
)

#: 场景关键词 → (层级, 场景类别, 规范化骨架)。
#:
#: 关键词全部取自**真实题面**（小学 active 题里实测出现过的词），不是拍脑袋编的：
#: 「买了」225 次 / 「还剩」150 次 / 「平均分」241 次 / 「文具店」44 次 /
#: 「超市」38 次 / 「蛋糕」106 次 / 「妈妈」100 次 / 「同学」26 次 …
SCENE_SKELETONS: Dict[str, Tuple[int, str, str]] = {
    # —— 第一层：操作结构 ——
    "平均分": (TIER_OPERATION, "food", "把东西平均分给同学"),
    "分给": (TIER_OPERATION, "food", "把东西分给同学"),
    "送给": (TIER_OPERATION, "food", "把东西送给同学"),
    # —— 第二层：具体场所与道具 ——
    "文具店": (TIER_OBJECT, "shopping", "去文具店买文具"),
    "超市": (TIER_OBJECT, "shopping", "去超市挑东西"),
    "商店": (TIER_OBJECT, "shopping", "去商店挑东西"),
    "菜市场": (TIER_OBJECT, "shopping", "去菜市场买菜"),
    "书店": (TIER_OBJECT, "shopping", "去书店挑书"),
    "购物": (TIER_OBJECT, "shopping", "去商店挑东西"),
    "促销": (TIER_OBJECT, "shopping", "看商场促销"),
    "玩具": (TIER_OBJECT, "game", "分玩具给同学"),
    "卡片": (TIER_OBJECT, "game", "数卡片上的图案"),
    "拼图": (TIER_OBJECT, "game", "拼图慢慢拼"),
    "积木": (TIER_OBJECT, "game", "用积木搭起来"),
    "跳绳": (TIER_OBJECT, "game", "跳绳先数好节拍"),
    "风筝": (TIER_OBJECT, "game", "放风筝要看风"),
    "足球": (TIER_OBJECT, "game", "踢球先看位置"),
    "篮球": (TIER_OBJECT, "game", "投篮先看位置"),
    "钟表": (TIER_OBJECT, "time", "看钟表认时间"),
    "时钟": (TIER_OBJECT, "time", "看钟表认时间"),
    "秒针": (TIER_OBJECT, "time", "看钟表认时间"),
    "分针": (TIER_OBJECT, "time", "看钟表认时间"),
    "时针": (TIER_OBJECT, "time", "看钟表认时间"),
    "分钟": (TIER_OBJECT, "time", "看钟表认时间"),
    "火车": (TIER_OBJECT, "distance", "坐火车按站数"),
    "公交": (TIER_OBJECT, "distance", "坐公交按站数"),
    "车站": (TIER_OBJECT, "distance", "坐车按站数"),
    "自行车": (TIER_OBJECT, "distance", "骑车看路程"),
    "路程": (TIER_OBJECT, "distance", "分小段量路程"),
    "旅游": (TIER_OBJECT, "distance", "出去玩看路线"),
    "旅行": (TIER_OBJECT, "distance", "出去玩看路线"),
    "公园": (TIER_OBJECT, "distance", "去公园看路线"),
    "钱包": (TIER_OBJECT, "money", "数钱包里的钱"),
    "种子": (TIER_OBJECT, "nature", "看着种子发芽"),
    "发芽": (TIER_OBJECT, "nature", "看着种子发芽"),
    "向日葵": (TIER_OBJECT, "nature", "看着向日葵长高"),
    "蚂蚁": (TIER_OBJECT, "nature", "看蚂蚁搬家"),
    "蝴蝶": (TIER_OBJECT, "nature", "看蝴蝶飞来飞去"),
    "树": (TIER_OBJECT, "nature", "看小树长高"),
    "手工": (TIER_OBJECT, "craft", "做手工"),
    "折纸": (TIER_OBJECT, "craft", "折纸慢慢折"),
    "画画": (TIER_OBJECT, "craft", "画画先想好画什么"),
    "剪纸": (TIER_OBJECT, "craft", "剪纸慢慢剪"),
    "涂色": (TIER_OBJECT, "craft", "涂色先涂大片"),
    # —— 第三层：交易动作 ——
    "买了": (TIER_TRANSACTION, "shopping", "去商店挑东西再结账"),
    "带了": (TIER_TRANSACTION, "shopping", "带着钱去商店买东西"),
    "花了": (TIER_TRANSACTION, "money", "数口袋里的钱"),
    "还剩": (TIER_TRANSACTION, "count", "看看手里还剩多少"),
    # —— 第四层：食物（仅在有「分给」类操作时生效）——
    "蛋糕": (TIER_FOOD, "food", "分蛋糕给同学"),
    "面包": (TIER_FOOD, "food", "分面包给同学"),
    "巧克力": (TIER_FOOD, "food", "分巧克力给同学"),
    "牛奶": (TIER_FOOD, "food", "分牛奶给大家"),
    "果汁": (TIER_FOOD, "food", "倒果汁分给大家"),
    "苹果": (TIER_FOOD, "food", "分苹果给小朋友"),
    "西瓜": (TIER_FOOD, "food", "切西瓜分给大家"),
    "香蕉": (TIER_FOOD, "food", "分香蕉给大家"),
    "橘子": (TIER_FOOD, "food", "分橘子给大家"),
    "草莓": (TIER_FOOD, "food", "分草莓给大家"),
    "糖果": (TIER_FOOD, "food", "分糖果给同学"),
    "水果": (TIER_FOOD, "food", "分水果给大家"),
    "蔬菜": (TIER_FOOD, "food", "分蔬菜给大家"),
    "白菜": (TIER_FOOD, "food", "分白菜给大家"),
    "米饭": (TIER_FOOD, "food", "盛饭分给大家"),
    "面条": (TIER_FOOD, "food", "煮面条分给大家"),
    "饺子": (TIER_FOOD, "food", "包饺子分给大家"),
    "鸡蛋": (TIER_FOOD, "food", "分鸡蛋给大家"),
    "饼干": (TIER_FOOD, "food", "分饼干给大家"),
    # —— 第五层：人物与校园背景 ——
    "妈妈": (TIER_PERSON, "family", "帮妈妈做家务"),
    "爸爸": (TIER_PERSON, "family", "跟爸爸收拾东西"),
    "爷爷": (TIER_PERSON, "family", "帮爷爷做家务"),
    "奶奶": (TIER_PERSON, "family", "帮奶奶做家务"),
    "哥哥": (TIER_PERSON, "family", "跟哥哥做事情"),
    "姐姐": (TIER_PERSON, "family", "跟姐姐做事情"),
    "弟弟": (TIER_PERSON, "family", "帮弟弟做事情"),
    "妹妹": (TIER_PERSON, "family", "帮妹妹做事情"),
    "同学": (TIER_PERSON, "school", "跟同学分东西"),
    "小朋友": (TIER_PERSON, "school", "跟小朋友分东西"),
    "教室": (TIER_PERSON, "school", "在教室做作业"),
    "黑板": (TIER_PERSON, "school", "在黑板上写"),
    "板报": (TIER_PERSON, "school", "和伙伴画板报"),
    "运动会": (TIER_PERSON, "school", "参加运动会"),
    "春游": (TIER_PERSON, "school", "跟伙伴出门玩"),
    "值日": (TIER_PERSON, "school", "轮着做值日"),
    "排队": (TIER_PERSON, "school", "排好队按次序来"),
    "比赛": (TIER_PERSON, "school", "参加比赛"),
    "小组": (TIER_PERSON, "school", "小组商量着来"),
    "游戏": (TIER_PERSON, "game", "玩游戏要先看规则"),
}

#: 骨架 → 场景类别（``build_analogy`` 反查用）。
_CATEGORY_OF_SKELETON: Dict[str, str] = {
    skeleton: category for _, category, skeleton in SCENE_SKELETONS.values()
}

#: 场景类别 → 生活类比句式模板（``{anchor}`` 会被替换成情境骨架）。
#:
#: 模板里的每个字都过一遍红线：不含「你」、不含姓名/班级/校名、不含 LaTeX、
#: 不含初中高中内容、不含贬义词、不自称「老师/客服/AI」。
ANALOGY_TEMPLATES: Dict[str, str] = {
    "shopping": "就像{anchor}那样，先想清楚要什么，再看还剩多少。",
    "food": "就像{anchor}那样，先算清要分给几个人。",
    "family": "就像{anchor}那样，先把要做的活儿按顺序排好。",
    "school": "就像{anchor}那样，先看清题目问的是什么。",
    "game": "就像{anchor}那样，先把步骤看明白再动手。",
    "time": "就像{anchor}那样，先分清几点几分再算间隔。",
    "distance": "就像{anchor}那样，分成小段慢慢数过去。",
    "money": "就像{anchor}那样，先分清每份是多少再加起来。",
    "craft": "就像{anchor}那样，先照着步骤慢慢来。",
    "nature": "就像{anchor}那样，得慢慢等它长。",
    "count": "就像{anchor}那样，先看原来有多少再减掉用掉的。",
}


def _strip_tails(text: str) -> str:
    """砍掉题面里的推广尾巴（题库导入时带进来的话）。

    实测最长题面 574 字，尾巴形如「A/老师带你口算闯关…快扫描右侧二维码…」、
    「举一反三练一练完成练习后，在APP中进行"拍照批改"…」。
    """
    cut = len(text)
    for pat in ("A/", "老师带你口算闯关", "举一反三", "练一练", "拍照批改", "快扫描", "扫码"):
        idx = text.find(pat)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut]


def _first_matching_word(text: str, tier: int) -> str:
    """找出某一层级里在文本中**出现位置最早**的场景词（没有则空串）。"""
    best_word = ""
    best_pos = -1
    for word, (word_tier, _category, _skeleton) in SCENE_SKELETONS.items():
        if word_tier != tier:
            continue
        pos = text.find(word)
        if pos == -1:
            continue
        if best_pos == -1 or pos < best_pos:
            best_word, best_pos = word, pos
    return best_word


def extract_scene_anchor(content: Optional[str]) -> str:
    """从题面里抽「情境骨架」（≤20 字）。

    做法：砍推广尾巴 → 分层识别场景关键词 → 映射到**规范化骨架**。

    分层规则：按 :data:`TIER_ORDER` 逐层扫描，每层内取**出现位置最早**的
    那个词；某层没有命中就下沉到下一层。

    * 第 1 层「操作结构」优先：「把 12 个苹果平均分给 4 个小朋友」里
      「平均分」比「苹果」更能代表这道题；
    * 第 2 层「场所与道具」优先于第 3 层「交易动作」：
      「在文具店买了 3 支铅笔」抽「文具店」比抽「买了」更具体；
    * 第 4 层「食物」**只在第 1 层命中时才生效**（「分蛋糕给同学」成立，
      「妈妈买了 3 千克苹果」里就**不**成立）—— 否则会凭空造出
      「分苹果给小朋友」这种题面里没有的情节。

    为什么返回规范化骨架而不是题面原文切片：题面里普遍带数字（「3 支铅笔」）、
    姓名（「小明」）和班级（「二（1）班」），直接切片会既漏数字又漏班级红线。
    规范化骨架是显式常量表里的短句，**结构上就不可能带数字或身份信息**。

    Args:
        content: 题面（``questions.content``）。可为 ``None``。

    Returns:
        情境骨架；题面为空 / 剥完没东西 / 认不出场景时返回 ``""``。
    """
    if not content:
        return ""

    text = _strip_tails(content)
    if not text.strip():
        return ""

    matched_operation = False
    for tier in TIER_ORDER:
        best_word = _first_matching_word(text, tier)
        if not best_word:
            continue
        if tier == TIER_FOOD and not matched_operation:
            # 食物只在「分给」类操作成立时才用（见函数 docstring）
            continue
        if tier == TIER_OPERATION:
            matched_operation = True
            # 「分给 / 平均分」+ 出现具体食物 → 用更具体的食物骨架
            # （「分蛋糕给同学」比「把东西分给同学」更接地气）。
            # ⚠️ 只对**分配类**操作生效：「送给」不算（「把苹果送给妹妹」
            # 不是「分苹果给同学」）。
            if best_word in ("分给", "平均分"):
                food_word = _first_matching_word(text, TIER_FOOD)
                if food_word:
                    return SCENE_SKELETONS[food_word][2][:MAX_ANCHOR_CHARS]
        return SCENE_SKELETONS[best_word][2][:MAX_ANCHOR_CHARS]

    return ""


def build_analogy(scene_anchor: Optional[str]) -> str:
    """用生活类比句式把情境骨架包装成一句接地气的话。

    Args:
        scene_anchor: 情境骨架（``extract_scene_anchor`` 的产物）。

    Returns:
        类比句；锚点为空、或不是本模块认可的骨架 → ``""``（**不编造**）。

    Note:
        未知锚点返回空串而不是套个通用模板 —— 通用模板会把任意文本塞进
        提示词，那是「编造」而不是「接地气」。
    """
    anchor = (scene_anchor or "").strip()
    if not anchor:
        return ""

    category = _CATEGORY_OF_SKELETON.get(anchor)
    if not category:
        return ""

    template = ANALOGY_TEMPLATES.get(category)
    if not template:
        return ""

    sentence = template.replace("{anchor}", anchor).strip()
    if len(sentence) > MAX_SENTENCE_CHARS:
        return ""
    return sentence


# =============================================================================
# 知识点释义
# =============================================================================

#: 子句切分（顿号/逗号/句号都是边界）。
_CLAUSE_SPLIT = re.compile(r"[、，,。；;！!？?]")

#: 剥数字后残留的孤立分隔符（「2/3/5」→ 「///」）。
_RESIDUAL_SEP = re.compile(r"[/、，,。；;！!？?\s]+")

#: 子句**开头**要剥掉的虚词（「的和与及」在句首没有意义）。
#:
#: 注意开头可以剥「和」——句首的「和」几乎不可能是实词连接。
_LEADING_FILLER = re.compile(r"^[的了和与及]+")

#: 子句**结尾**要剥掉的虚词。
#:
#: ⚠️ 这里**绝不能**剥「和」：「三角形内角和」「多边形的内角和」的结尾
#: 「和」是**实词的一部分**，剥掉就变成「三角形内角」「多边形的内角」——
#: 语义被毁了。只剥「了」「的」这类纯虚词。
_TRAILING_FILLER = re.compile(r"[了的地]+$")

#: 子句结尾的**系词**（「…是」「…等于」）。
#:
#: 数字被剥掉后常留下悬空的系词：「三角形内角和是 180 度」→「三角形内角和是」。
#: 系词一定是虚词，剥掉不损失语义。
_TRAILING_COPULA = re.compile(r"(?:等于|是|为)+$")


def build_kp_anchor(description: Optional[str]) -> str:
    """把知识点释义加工成可直接注入的一段话。

    加工动作：

    1. **剥掉数字 + 单位**（``180度`` → 整段删、``2/3/5 的`` → ``的``）——
       知识点释义里的数字可能是**当前这道题的答案**（问「三角形内角和多少度」
       而释义写着「三角形内角和180度」时，直接注入等于送答案）。实测
       327 条释义里 15 条含阿拉伯数字。
    2. 剥完只剩虚词/单位的子句整句丢弃（「180度」→「度」没有信息量）。
    3. 保留中文数字（「三角形」「两位数」里的数字是**词的一部分**，删了语义就毁了）。
    4. 限长。

    Args:
        description: ``knowledge_points.description``，可为 ``None``。

    Returns:
        知识点释义；空描述 / 剥完没东西 → ``""``（**不编造**）。
    """
    if not description:
        return ""

    raw = description.strip()
    if not raw:
        return ""

    kept: List[str] = []
    masked, mapping = _mask_lexical_number_terms(raw)
    for clause in _CLAUSE_SPLIT.split(masked):
        piece = clause.strip()
        if not piece:
            continue
        cleaned = _NUM_WITH_UNIT.sub("", piece)
        cleaned = _RESIDUAL_SEP.sub(" ", cleaned).strip()
        cleaned = _LEADING_FILLER.sub("", cleaned).strip()
        cleaned = _TRAILING_COPULA.sub("", cleaned).strip()
        cleaned = _TRAILING_FILLER.sub("", cleaned).strip()
        if len(cleaned) < 2:
            continue
        kept.append(cleaned)

    if not kept:
        return ""

    text = _unmask_lexical_number_terms("、".join(kept), mapping)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return text[:MAX_SEGMENT_CHARS]


# =============================================================================
# 6 条硬红线（机器可扫）
# =============================================================================

#: 常见姓氏表（真实姓名检测用；取常用的 60 个，够覆盖绝大多数真实人名）。
_COMMON_SURNAMES: str = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏窦章苏潘葛范彭郎鲁韦马苗方俞任袁柳鲍史唐费廉岑薛雷贺倪"
    "汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元顾孟平黄萧尹姚邵汪祁毛"
)

#: 虚构角色名白名单（题面里常见的「小明」「乐乐」等，**不是**真实姓名）。
_FICTIONAL_NAMES: frozenset = frozenset(
    {
        "小明", "小红", "小刚", "小丽", "小华", "小玲", "小芳", "小青", "小兰",
        "小英", "小美", "小军", "明明", "天天", "乐乐", "亮亮", "丽丽", "圆圆",
        "宁宁", "聪聪", "皮皮", "豆豆", "壮壮", "冬冬", "兰兰", "方方", "平平",
        "宝宝", "丫丫", "小燕", "小龙", "小林", "小云", "小玉",
    }
)

#: 班级指代（「全班」「同班」是集体，不指向具体学生，**不**算违规）。
_CLASS_SAFE_PREFIX: Tuple[str, ...] = ("全", "同", "本", "上班", "晚", "课", "这", "该", "插")

#: 班级：``二（1）班`` / ``三年级2班`` / ``我班``。
_CLASS_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"[0-9一二三四五六七八九十]{1,3}\s*[(（]\s*[0-9一二三四五六七八九十]{1,3}\s*[)）]\s*班"),
    re.compile(r"[0-9一二三四五六七八九十]{1,3}\s*年级?\s*[0-9一二三四五六七八九十]{0,3}\s*班"),
    re.compile(r"[一-鿿]{1,3}班(?!级|课|里|长)"),
)

#: 校名 / 小区名。
_SCHOOL_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"[一-鿿]{2,4}(?:小学|中学|学校|大学)"),
    re.compile(r"[一-鿿]{2,4}小区"),
    re.compile(r"[一-鿿]{2,4}校区"),
)

#: 人称称谓（用于判定「姓 + 名 + 称谓」这类真实姓名指向）。
_NAME_TITLES: Tuple[str, ...] = (
    "同学", "老师", "小朋友", "阿姨", "叔叔", "爷爷", "奶奶", "先生", "女士",
)

#: 真实姓名检测：**必须有「人称上下文」**，否则「周长」「方向」「面积」这类
#: 数学词会被误判成人名（实测 KP 释义里就有「周长」）。
#:
#: 判定式（命中其一即算真实姓名指向）：
#:   (1) 「姓 + 名」            例：姓张的
#:   (2) 「姓 + 名 + 称谓」     例：张伟同学 / 李老师
#:   (3) 「称谓 + 姓 + 名」     例：同学小张
_NAME_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"姓\s*[一-鿿]{1,2}"),
    re.compile(
        "[" + _COMMON_SURNAMES + r"][一-鿿]{1,2}(?:" + "|".join(_NAME_TITLES) + r")"
    ),
    re.compile(
        "(?:" + "|".join(_NAME_TITLES) + r")\\s*[" + _COMMON_SURNAMES + r"][一-鿿]{1,2}"
    ),
)

#: 把错误指向「你」。
_BLAME_YOU_PATTERN = re.compile(
    r"(?:你|您)(?:又|还|总是|老是|怎么|真)?(?:错|不对|不行|不会|不懂|没|笨|算错|做错|错得)"
)

#: LaTeX 源码。
#:
#: 覆盖：行内公式 ``$...$``、LaTeX 命令（``\frac`` / ``\sqrt`` / ``\times``）、
#: 行间公式定界符、上下标（``x^2`` / ``a_{i}``）。
#: 注意 ``√3`` / ``5/13`` / ``×`` / ``÷`` 是**允许的**纯文本写法（见《规格》）。
_LATEX_PATTERN = re.compile(
    r"\$[^$]*\$"
    r"|\\[A-Za-z]+"
    r"|\\[\[\]\(\)\{\}]"
    r"|[\^_]\{?"
)

#: 初中 / 高中内容关键词。
_JUNIOR_HIGH_WORDS: Tuple[str, ...] = (
    "初中", "高中", "中考", "高考", "初一", "初二", "初三", "高一", "高二", "高三",
    "七年级", "八年级", "九年级", "十年级", "十一年级", "十二年级",
    "一元二次", "二次方程", "一次函数", "二次函数", "方程组", "全等三角形",
    "相似三角形", "勾股定理", "扇形", "物理", "化学", "生物", "地理", "历史",
    "政治", "文言文", "绝对值", "相反数", "科学记数法", "因式分解",
    "立体几何", "投影", "浮力", "密度", "电压",
)

#: 评价性贬义词（「差」单字太常见——「相差」「差不多」不是贬义，故不收）。
_DEMEANING_WORDS: Tuple[str, ...] = (
    "笨", "傻", "蠢", "呆子", "没脑子", "脑子有问题", "废", "废物", "垃圾",
    "糟糕", "差劲", "太差", "很差", "错了别做", "白痴", "弱智", "没出息",
)

#: 「老师/客服/AI」自称。
_SELF_REFERENCE_PATTERN = re.compile(
    r"(?:我是|作为|来自)\s*(?:一名|一个|位)?\s*(?:老师|客服|AI|ai|人工智能|助手|小助手)"
)

#: 连用感叹号。
_MULTI_EXCLAMATION = re.compile(r"[!！]{2,}|!\s*!")


def _check_real_name(text: str) -> bool:
    """红线①：真实姓名 / 班级 / 校名 / 小区名。"""
    for pattern in _CLASS_PATTERNS:
        for match in pattern.finditer(text):
            frag = match.group(0)
            if frag.startswith(_CLASS_SAFE_PREFIX):
                continue
            return True
    for pattern in _SCHOOL_PATTERNS:
        if pattern.search(text):
            return True
    for pattern in _NAME_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(0)
            if name in _FICTIONAL_NAMES:
                continue
            if any(fiction in name for fiction in _FICTIONAL_NAMES):
                continue
            return True
    return False


def _check_blame_you(text: str) -> bool:
    """红线②：把错误指向「你」。"""
    return bool(_BLAME_YOU_PATTERN.search(text))


def _check_latex(text: str) -> bool:
    """红线③：LaTeX 源码。"""
    return bool(_LATEX_PATTERN.search(text))


def _check_junior_high(text: str) -> bool:
    """红线④：初中 / 高中内容。"""
    return any(word in text for word in _JUNIOR_HIGH_WORDS)


def _check_demeaning(text: str) -> bool:
    """红线⑤：评价性贬义词。"""
    return any(word in text for word in _DEMEANING_WORDS)


def _check_sentence_shape(text: str) -> bool:
    """红线⑥：单句 ≤40 字、不连用感叹号、不自称老师/客服/AI。"""
    if _MULTI_EXCLAMATION.search(text):
        return True
    if _SELF_REFERENCE_PATTERN.search(text):
        return True
    for sentence in re.split(r"[。；;！!？?]", text):
        if len(sentence.strip()) > MAX_SENTENCE_CHARS:
            return True
    return False


#: 6 条红线的检查器（顺序与《规格》一致）。
RED_LINE_CHECKERS: Tuple[Tuple[str, "callable"], ...] = (
    ("禁真实姓名/班级/校名", _check_real_name),
    ("禁把错误指向「你」", _check_blame_you),
    ("禁 LaTeX", _check_latex),
    ("禁初中高中内容", _check_junior_high),
    ("禁评价性贬义词", _check_demeaning),
    ("单句≤40字/不连用感叹号/不自称", _check_sentence_shape),
)


def find_red_line_violations(text: Optional[str]) -> List[str]:
    """返回文本违反的硬红线名称列表（空列表 = 干净）。

    Args:
        text: 待检文本。

    Returns:
        违反的红线名称；文本为空或干净时返回 ``[]``。
    """
    if not text or not text.strip():
        return []
    return [name for name, checker in RED_LINE_CHECKERS if checker(text)]


def passes_red_lines(text: Optional[str]) -> bool:
    """文本是否通过全部 6 条硬红线。"""
    return not find_red_line_violations(text)


# =============================================================================
# 素材包组装与渲染
# =============================================================================


def build_peer_misconception(corpus_sentence: Optional[str] = None) -> str:
    """同伴视角句 —— **A-1 期恒返回空串**。

    原因（不是偷懒，是产品约束）：库内同伴视角语料实测 **0 条**；PRD 要求的
    首批 ≥200 条需要运营人工种子 + 人工抽检，工程师无法自证。更重要的是，
    **用模板假装同伴说话正是 PRD 要防的编造风险**（「好多同学都会……」这种话
    没有真实语料支撑，就是在编）。

    A-2 期（同伴视角语料库 + 026 迁移 + 人工放行闸门）落地时，只需把
    ``corpus_sentence`` 换成从语料库检索到的**已放行**句子，本函数签名与
    四段结构都不用改。

    Args:
        corpus_sentence: A-2 期传入的已放行语料句；A-1 期不传。

    Returns:
        ``""``（恒定）。
    """
    return ""


def build_personal_error(error_text: Optional[str] = None) -> str:
    """个体错因 —— **A-1 期恒返回空串**。

    原因：本期没有可靠的个体错因数据源（个体错因需要学习事件流水与错题
    归因对齐），编造一段「上次就是这样错的」既没有依据，也与《规格》
    「未放行内容不得进入线上讲解」的闸门口径冲突。

    Args:
        error_text: A-2 期传入的个体错因文本；A-1 期不传。

    Returns:
        ``""``（恒定）。
    """
    return ""


def build_grounding_materials(
    *,
    content: Optional[str] = None,
    kp_description: Optional[str] = None,
    peer_sentence: Optional[str] = None,
    personal_error_text: Optional[str] = None,
) -> GroundingMaterials:
    """把题面 + 知识点释义加工成四段素材包。

    Args:
        content: 题面（``questions.content``），analogy 的素材源。
        kp_description: 知识点释义（``knowledge_points.description``）。
        peer_sentence: 同伴视角句（A-1 期不传）。
        personal_error_text: 个体错因（A-1 期不传）。

    Returns:
        四段素材包；拿不到内容的段为空串。
    """
    anchor = extract_scene_anchor(content)
    return GroundingMaterials(
        peer_misconception=build_peer_misconception(peer_sentence),
        analogy=build_analogy(anchor),
        kp_anchor=build_kp_anchor(kp_description),
        personal_error=build_personal_error(personal_error_text),
    )


def render_materials_for_prompt(materials: GroundingMaterials) -> str:
    """把素材包拼成可注入 tutor system 提示的文本。

    规则：

    * **四段全空 → 返回空串**（调用方据此完全不追加后缀）；
    * 任何一段没过 6 条硬红线 → **整段丢弃**（宁缺毋滥，不做「修补式」改写，
      改写 = 编造）；
    * 带上「（内部提示，不要念给孩子听）」标注（沿用 tutor 既有风格）；
    * 全文 ≤ ``MAX_MATERIAL_CHARS``（按行截断，不切出半个句子）。

    Args:
        materials: 四段素材包。

    Returns:
        渲染文本；无任何可用素材时返回 ``""``。
    """
    if materials is None or materials.is_empty():
        return ""

    lines: List[str] = []
    for label, text in materials.sections():
        if not passes_red_lines(text):
            continue
        lines.append(f"【{label}】{text}")

    if not lines:
        return ""

    head = "\n".join((INTERNAL_HINT, *_HINT_LINES))
    rendered = "\n".join([head, *lines])

    if len(rendered) <= MAX_MATERIAL_CHARS:
        return rendered

    out: List[str] = []
    total = 0
    for line in rendered.split("\n"):
        if total + len(line) + 1 > MAX_MATERIAL_CHARS:
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


# =============================================================================
# 年级窗口（年级过滤的硬上限住在这里）
# =============================================================================

#: 素材注入的**年级硬上限**：1-6 年级（小学）。
#:
#: 为什么做成**不可配置**的模块常量：内测目标就是 1-6 年级，而题库里躺着
#: 881 条初高中语料（g7:158 / g8:172 / g9:187 / g10:113 / g11:121 / g12:130），
#: 一旦漏进小学孩子的提示词就是事故。运营能调的只有**年级窗口宽度**，
#: 「上限是 6」这条不变量不给调。
PRIMARY_MAX_GRADE: int = 6


def grade_window(student_grade: Optional[int], width: int = 1) -> Tuple[int, ...]:
    """算出允许注入素材的年级窗口。

    * 上限 = ``min(学生年级 + width, PRIMARY_MAX_GRADE)``；
    * 下限 = ``max(1, 学生年级 - width)``；
    * 学生年级未知 → 返回 1~6 全段（仍**绝不**越过 6）。

    Args:
        student_grade: 学生年级（1-6）；``None`` 表示未知。
        width: 窗口宽度（半宽），默认 1。

    Returns:
        允许的年级元组（升序）。
    """
    if student_grade is None:
        return tuple(range(1, PRIMARY_MAX_GRADE + 1))
    upper = min(int(student_grade) + max(0, int(width)), PRIMARY_MAX_GRADE)
    lower = max(1, int(student_grade) - max(0, int(width)))
    if upper < lower:
        return ()
    return tuple(range(lower, upper + 1))



#: 从文本里抽「数字 token」——用于断言「素材里不含原题数字」。
#:
#: 两类 token：
#:
#: * **阿拉伯数字串**：``"12"`` / ``"3.5"``；
#: * **中文数量词**：中文数字 + **量词**（如「三支」「两个」），
#:   且排除「三角形」「两位数」这类**词内数字**（那是术语名，不是数量）。
#:
#: 口径与 :func:`strip_numbers` **完全一致**（复用同一个正则）——
#: 断言口径和实际剥离口径必须同源，否则「断言说没数字、实际剥了」就漏了。
def extract_number_tokens(text: Optional[str]) -> List[str]:
    """抽出文本里的「数字 token」（阿拉伯数字串 + 中文数量词）。

    用途：调用方（如服务层与测试）可以拿它跟素材文本比对，
    断言「素材里不含原题的任何数字」。

    Args:
        text: 待检文本。

    Returns:
        数字 token 列表（保持出现顺序，可重复）。
    """
    if not text:
        return []
    masked, _mapping = _mask_lexical_number_terms(text)
    tokens: List[str] = []
    for match in re.finditer(r"[0-9]+(?:\.[0-9]+)?", masked):
        tokens.append(match.group(0))
    for match in _CN_NUM_WITH_UNIT.finditer(masked):
        tokens.append(match.group(0))
    return tokens

__all__ = [
    "GroundingMaterials",
    "MAX_MATERIAL_CHARS",
    "MAX_ANCHOR_CHARS",
    "MAX_SEGMENT_CHARS",
    "MAX_SENTENCE_CHARS",
    "INTERNAL_HINT",
    "SCENE_SKELETONS",
    "ANALOGY_TEMPLATES",
    "RED_LINE_CHECKERS",
    "PRIMARY_MAX_GRADE",
    "strip_numbers",
    "extract_number_tokens",
    "extract_scene_anchor",
    "build_analogy",
    "build_kp_anchor",
    "build_peer_misconception",
    "build_personal_error",
    "build_grounding_materials",
    "render_materials_for_prompt",
    "find_red_line_violations",
    "passes_red_lines",
    "grade_window",
]
