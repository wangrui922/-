"""MathDial 教学属性 → 诊断式追问话术库（方向八 · AI 辅导体验微调）。

背景
----
* **MathDial**（EMNLP 2023）：教师-学生数学辅导对话数据集，带教学行为标注
  （追问展开 / 线索提示 / 讲解 / 肯定 / 反例等）。本文件把它的教学行为思想
  翻译成**分级话术表**，用于替掉「固定话术模板」里的生硬句。
* **universal-diagnostic-tutor-skill**（209★ 开源项目）：核心思路是
  **「先诊断，再教学」**——不要急着给提示 / 讲解，先让学生用一句话暴露思路，
  定位卡点后，再按错因给**最小**的干预。

定位
----
本文件只提供**句式骨架与行为约束**，不是可直接念出口的成品句：
主提示词（``prompts.tutor.PROMPT``）铁律是「看题说话，不套模板」，
因此模型的每一句都必须贴着当前题的**具体数字与情境**。话术表的作用是：
告诉模型「这类错因、这个教学环节，应该往哪个方向问」，并给出参考问法。

错因枚举的唯一真相源是 ``app.core.error_categories``（READ/METHOD/CALC/
KNOWLEDGE/CHECK），本文件不重写字面量。

依赖约束：纯常量 + 标准库，零 DB、零网络、零新依赖，可离线单测。
"""

from __future__ import annotations

from typing import Dict, Optional

from app.core.error_categories import (
    ERROR_CALC,
    ERROR_CHECK,
    ERROR_KNOWLEDGE,
    ERROR_METHOD,
    ERROR_READ,
    ERROR_UNCLASSIFIED,
    normalize_error,
)

# ==================================================================== 教学环节
#: 诊断：先让孩子开口说思路 / 复述题目 / 定位卡点（对应 MathDial 的追问展开）
TACTIC_DIAGNOSE: str = "diagnose"
#: 提示：给一条贴着题目的方向性线索，不给结论（对应 MathDial 的 cue/hint）
TACTIC_HINT: str = "hint"
#: 讲解：把概念 / 步骤讲透，讲完回扣追问（对应 MathDial 的 explanation）
TACTIC_ELABORATE: str = "elaborate"

TACTICS: tuple[str, ...] = (TACTIC_DIAGNOSE, TACTIC_HINT, TACTIC_ELABORATE)

_TACTIC_LABELS: Dict[str, str] = {
    TACTIC_DIAGNOSE: "诊断",
    TACTIC_HINT: "提示",
    TACTIC_ELABORATE: "讲解",
}

#: 话术表：``(错因机器码, 教学环节) -> (行为指令, 参考问句列表)``
#:
#: 行为指令是给模型看的内部约束（不要念给孩子听）；参考问句里的数字/情境
#: 是占位示意，模型必须换成当前题的真实数据（``□`` 表示题目中的具体对象）。
_PHRASES: Dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {
    # -------------------------------------------------------- 未定位错因（通用）
    (ERROR_UNCLASSIFIED, TACTIC_DIAGNOSE): (
        "还没定位到具体错因，先让孩子说出卡在哪一步，再决定干预方式。",
        (
            "你先说说，这一步卡住你的是什么？",
            "你觉得这道题难在哪儿？是没读懂、不会算，还是不知道用什么方法？",
        ),
    ),
    (ERROR_UNCLASSIFIED, TACTIC_HINT): (
        "还没定位到具体错因，先给一条最小的方向性线索，让他自己走一步看看。",
        (
            "先别急，从题目已经告诉你的□开始，你能推出什么？",
            "这道题里哪个条件你还没用上？从它入手试试。",
        ),
    ),
    (ERROR_UNCLASSIFIED, TACTIC_ELABORATE): (
        "讲解时按「读题 → 方法 → 计算 → 检查」的通用顺序走，讲完让孩子复述思路，"
        "顺带确认他卡在哪个环节。",
        (
            "做题有四步：读懂题、选方法、算仔细、回头查。我们按这个顺序来，"
            "你刚才卡在哪一步了？",
            "我们把这道题的思考过程分成几步，你能说一说每一步该做什么吗？",
        ),
    ),
    # ------------------------------------------------------------------ 读题错
    (ERROR_READ, TACTIC_DIAGNOSE): (
        "先别讲思路，让孩子把题目口头复述一遍：问的是什么、给了哪些条件、"
        "哪些条件还没用上。",
        (
            "这道题让你求什么？你先用自己的话说说。",
            "题目里已经告诉你哪些信息了？哪些还没用上？",
            "你读下来，它问的是□还是□，拿得准吗？",
        ),
    ),
    (ERROR_READ, TACTIC_HINT): (
        "孩子读题偏差处先不给结论，抛一个「对比类」问题让他自己发现漏看了什么。",
        (
            "你再看看题目里「□」那句，和你刚才说的是一回事吗？",
            "如果题目问的是□，你觉得还缺哪个条件才能算？",
        ),
    ),
    (ERROR_READ, TACTIC_ELABORATE): (
        "讲解时把「题目的信息结构」讲清楚：谁和谁对应、所求与已知的关系，"
        "再让他自己复述一遍确认读懂了。",
        (
            "抓信息有个顺序：先圈数字、再找问题、最后看单位。按这个顺序你再读一遍，"
            "告诉我这次你圈到了什么。",
            "「□」和「□」在题目里是什么关系？想清楚这个，问题就清楚了。",
        ),
    ),
    # ------------------------------------------------------------------ 方法错
    (ERROR_METHOD, TACTIC_DIAGNOSE): (
        "先让孩子说出他打算用什么方法、为什么这么想，再判断是方向错了还是"
        "只是表达不清楚。",
        (
            "你准备怎么算这一步？说说你的想法。",
            "你选这个做法是因为看到了什么？",
            "你觉得「□」这个量在这里是变多了还是变少了？",
        ),
    ),
    (ERROR_METHOD, TACTIC_HINT): (
        "方法错时不纠正，抛一个「矛盾探测」问题，让他自己撞见思路里的漏洞。",
        (
            "按你这个做法，结果会比题目说的多还是少？为什么？",
            "「□」和「□」两个量，先算哪个才说得通？",
        ),
    ),
    (ERROR_METHOD, TACTIC_ELABORATE): (
        "讲解时讲「为什么这个方法是错的 / 对的」——带着数量关系的变化讲，"
        "讲完让孩子用一句话复述方法的适用条件。",
        (
            "这道题的核心关系是□，所以要先用□、再用□。你能不能用一句话说说，"
            "什么时候该用这个方法？",
            "刚才的做法错在把「增加」当成了「乘」，这两个不一样在哪？",
        ),
    ),
    # ------------------------------------------------------------------ 计算错
    (ERROR_CALC, TACTIC_DIAGNOSE): (
        "孩子方向没问题，先肯定方法，再只聚焦算出错的这一小步，不要重新讲方法。",
        (
            "你的思路对了，我们一起来查一步：□这一步你是怎么算的？",
            "方法没问题。你算到□的时候，心里是怎么算的？",
        ),
    ),
    (ERROR_CALC, TACTIC_HINT): (
        "计算错只给「自查线索」：提示检查进位 / 退位 / 约分 / 符号等具体工序，"
        "不给答案。",
        (
            "这一步是加法还是减法，进位有没有漏掉？",
            "你算到□时，符号 / 进退位这边再瞄一眼？",
        ),
    ),
    (ERROR_CALC, TACTIC_ELABORATE): (
        "讲解时演示**这一小步**的竖式 / 口诀即可，别把整道题重讲；"
        "讲完让他自己重算一遍并说出错在哪。",
        (
            "我们把这一小步拆开看：□加□，个位满十要进一，这里进了吗？"
            "你自己再算一遍。",
            "错在□这个地方多算了 / 少算了？你重算一次。",
        ),
    ),
    # ------------------------------------------------------------------ 知识点缺
    (ERROR_KNOWLEDGE, TACTIC_DIAGNOSE): (
        "先让孩子说出他卡在哪——是不记得公式 / 概念，还是不知道公式怎么用，"
        "再决定补哪一句。",
        (
            "你是不记得这个公式，还是不知道往哪儿套？",
            "这个知识点你学过吗？能说出大致意思吗？",
        ),
    ),
    (ERROR_KNOWLEDGE, TACTIC_HINT): (
        "知识点缺只补**一句**概念性提示（公式 / 定理 / 性质，不含最终答案），"
        "再引导他自己套用。",
        (
            "圆的面积公式是 π × r²，r 是半径。这道题里 r 是哪个数？",
            "加减法交换律是「交换两个数结果不变」，这道题能不能用上？",
        ),
    ),
    (ERROR_KNOWLEDGE, TACTIC_ELABORATE): (
        "讲解时把概念讲「为什么」而非只讲「是什么」，配一个最小例子，"
        "讲完用相似的小题验证他真的会用。",
        (
            "这个公式说明的是□和□的关系，比如□的情况就是这样。"
            "你照这个试试算这道题。",
            "它为什么叫□（性质）？因为□。理解了再来套这道题。",
        ),
    ),
    # ------------------------------------------------------------------ 检查漏
    (ERROR_CHECK, TACTIC_DIAGNOSE): (
        "孩子已经算出结果，先肯定已成立的部分，再让他自己复查有没有漏条件、"
        "漏单位、漏答。",
        (
            "你算到这一步是对的。题目里还有没有没用上的条件？",
            "你的结果带单位了吗？题目要你答的是哪一句？",
        ),
    ),
    (ERROR_CHECK, TACTIC_HINT): (
        "检查漏给「清单式」线索：单位、答句、未用条件，让孩子逐项自查，不给答案。",
        (
            "最后一步自查三件事：单位对不对、答句写没写、有没有条件没用上。",
            "你这道题是不是漏了□这个条件？",
        ),
    ),
    (ERROR_CHECK, TACTIC_ELABORATE): (
        "讲解时示范「验算习惯」：代入检查 / 单位检查 / 反向验证，"
        "讲完让他养成工序。",
        (
            "以后做完题加一道工序：把答案代回题目里看说理说不通。这次你试试。",
            "答案代回去：如果是□，那题目说的□能对上吗？",
        ),
    ),
}


# ==================================================================== 对外接口
def tactic_label(tactic: str) -> str:
    """教学环节中文标签，未知回退到「诊断」。"""
    return _TACTIC_LABELS.get(tactic, _TACTIC_LABELS[TACTIC_DIAGNOSE])


def pick_phrase(
    error_type: Optional[str],
    tactic: str,
    variant: int = 0,
) -> str:
    """取指定错因 × 环节的参考问句（循环取变体，越权自动回退）。

    Args:
        error_type: 错因机器码（``READ`` / ``METHOD`` / ``CALC`` /
            ``KNOWLEDGE`` / ``CHECK``）；``None`` / 非法值收敛失败时回退到
            ``READ`` 同款通用诊断问句。
        tactic: 教学环节（``TACTIC_*``）；非法时回退到 ``diagnose``。
        variant: 变体序号，按 ``variant % len(问句数)`` 取模，避免越界。

    Returns:
        一句参考问句（贴题数字需由模型替换）。
    """
    code = normalize_error(error_type)
    if code not in _PHRASES:
        code = ERROR_READ
    tactic = tactic if tactic in TACTICS else TACTIC_DIAGNOSE
    _, samples = _PHRASES[(code, tactic)]
    return samples[variant % len(samples)] if samples else ""


def tactic_instruction(
    error_type: Optional[str],
    tactic: str,
) -> str:
    """取指定错因 × 环节的**行为指令**（给模型看的内部约束）。

    Args:
        error_type: 错因机器码；非法值收敛失败时回退 ``READ``。
        tactic: 教学环节；非法值回退 ``diagnose``。

    Returns:
        行为指令文本（不以句号结尾，供拼接）。
    """
    code = normalize_error(error_type)
    if code not in _PHRASES:
        code = ERROR_READ
    tactic = tactic if tactic in TACTICS else TACTIC_DIAGNOSE
    instruction, _ = _PHRASES[(code, tactic)]
    return instruction


#: 「先诊断再教学」总则（对齐 universal-diagnostic-tutor-skill 的核心思路）。
#: 作为附加 system 指令注入 tutor 提示，出现在具体话术之前。
DIAGNOSE_FIRST_INSTRUCTION: str = (
    "【先诊断再教学】孩子说错了 / 卡住了，不要立刻讲。第一步先用一句话让他"
    "自己说出思路（怎么想的、卡在哪、题目求什么），等你确定了他的真实卡点，"
    "再按错因给最小的提示或讲解。宁可多问一句，不要猜着教。"
)

__all__ = [
    "TACTIC_DIAGNOSE",
    "TACTIC_HINT",
    "TACTIC_ELABORATE",
    "TACTICS",
    "DIAGNOSE_FIRST_INSTRUCTION",
    "tactic_label",
    "pick_phrase",
    "tactic_instruction",
]