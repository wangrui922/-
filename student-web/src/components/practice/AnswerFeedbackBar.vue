<script setup lang="ts">
/**
 * 单题反馈条（题库消费层 · F3）—— 两条产品铁律的主战场。
 *
 * 铁律 1「首轮绝不给答案」在本组件的落法：
 *   - 做错时唯一的**主按钮**是「让老师带我想一想」（h-14 大按钮，brand 主色，占满宽度）；
 *   - 「看答案」是页脚一行**灰色小字次要入口**，点了先弹二次确认，
 *     确认文案明确劝退（「直接看答案，这道题就练不到了」），默认焦点在「再想想」；
 *   - 只有 canReveal（已作答 && 判定不正确）时才渲染这个入口，
 *     严格对齐后端 reveal 的 400 / 403 约束，不让孩子撞墙；
 *   - ⚠️ **判分 reason 在答错时不原样展示**（纵深防御第二道，**故意冗余**）：
 *     答错（level=1）时这里换成自己的中性文案，只有**答对**或 **level=4**
 *     才回显后端 reason。
 *
 *     后端现状（2026-08-08 修复答错分支，2026-08-09 收敛答对分支）：`JudgeOutcome`
 *     把说明拆成了对外 / 对内两个字段——答错时 `reason` 收敛为不含答案的
 *     `judge_engine.CHOICE_WRONG_REPLY`（「这道选错了，我们一起看看是哪一步想岔了」）
 *     与 `JUDGE_WRONG_REPLY`（「判断反了，我们一起把题目再捋一遍」）；答对时收敛为
 *     `CHOICE_CORRECT_REPLY`（「答对了，你选的这项就是正确答案」）与
 *     `JUDGE_CORRECT_REPLY`（「判断准确，这道你想清楚了」）——**全分支都不含键位 /
 *     符号**。标准答案退到 `audit_reason`，而 `JudgeOutcome.to_dict()` 刻意不含该字段，
 *     只进日志。
 *
 *     **所以这道闸门现在拦不到任何东西——但绝不能删。** 历史上它是唯一一层，
 *     后端一穿帮（`reason` 曾明文写「你选了 A，标准答案是 B」）F12 一开就绕过了
 *     「首轮不给答案」。两层独立，任一层被改坏另一层还能兜住。
 *     想看答案必须显式走 `/reveal`（且只对做错的题开放）。
 *
 *     回显的两个分支（答对 / level=4）**现在都**不含答案：level=4 的四条兜底文案
 *     （选项缺失 / 标准答案无法识别 / 作答无法判定 / 题型不支持）均为纯提示；
 *     答对时的 reason 也已收敛为不含键位 / 符号的正向反馈（如
 *     「答对了，你选的这项就是正确答案」/「判断准确，这道你想清楚了」），满足
 *     「reason 全分支不泄题」的不变量。
 *
 * 铁律 2 之「level=4 不许说成你错了」：
 *   - level === 4 时 is_correct 恒为 false，但这里走**独立的琥珀色分支**，
 *     文案是「这题老师没法自动判，你自己核对一下」，并给自评入口；
 *   - 绝不复用「答错了」的红色 UI 和文案。
 */
import { computed, ref, watch } from 'vue'

import { JUDGE_LEVEL_UNKNOWN, type RevealResult, type SubmitResult } from '@/api/practice'

const props = defineProps<{
  /** 判分结论；null 表示这题还没提交 */
  result: SubmitResult | null
  /** 已取回的参考答案；null 表示还没看过 */
  reveal: RevealResult | null
  /** 「看答案」入口是否允许出现（由 store 的 canReveal 决定） */
  canReveal: boolean
  revealing: boolean
  revealError: string
  /** 孩子对 level=4 题目的自评结论，null 表示还没评 */
  selfCheck: boolean | null
  /** 是否还有下一题 */
  hasNext: boolean
  /** 引导抽屉是否正在请求（防重复点击） */
  guideLoading: boolean
  /**
   * 选项完整性（T8/P0-D）。partial/missing 时在结论条加一段「不计分」补充
   * —— 设计 §1.6.5：判分后给一句解释，与 BankQuestionCard 的 amber 提示对齐。
   */
  optionsStatus?: 'complete' | 'partial' | 'missing' | 'n/a'
}>()

const emit = defineEmits<{
  (e: 'guide'): void
  (e: 'reveal'): void
  (e: 'self-check', correct: boolean): void
  (e: 'next'): void
  (e: 'finish'): void
}>()

/** 二次确认面板是否展开。 */
const confirmingReveal = ref<boolean>(false)

/** 机器判不了（level=4）——**不是**「你错了」。 */
const isUnjudged = computed<boolean>(() => props.result?.level === JUDGE_LEVEL_UNKNOWN)

/** 真的答对了。 */
const isRight = computed<boolean>(() => props.result?.is_correct === true)

/** 真的答错了（排除 level=4 的情形）。 */
const isWrong = computed<boolean>(
  () => props.result !== null && props.result.is_correct === false && !isUnjudged.value,
)

/** 顶部结论条的配色。 */
const bannerClass = computed<string>(() => {
  if (isRight.value) return 'bg-green-50 text-green-700'
  if (isUnjudged.value) return 'bg-amber-50 text-amber-800'
  return 'bg-rose-50 text-rose-700'
})

/** 顶部结论条的主文案（level=4 单独一套，绝不说「错」）。 */
const bannerTitle = computed<string>(() => {
  if (isRight.value) return '答对了，漂亮！'
  if (isUnjudged.value) return '这题老师没法自动判，你自己核对一下'
  return '这道题没做对，别急'
})

/**
 * 结论条的副文案。
 *
 * 防泄题闸门（纵深防御第二道，故意冗余，**不要因为后端修好了就删**）：
 * 答错时不回显后端 reason，换成自己的中性引导语；答对 / level=4 的 reason
 * 不含答案，原样展示。
 *
 * 后端自 2026-08-08 起答错的 reason 已不含标准答案（退到只进日志的
 * audit_reason），所以这层当前拦不到东西——留着是为了后端万一回退时还有人兜底。
 * 详见文件顶部注释。
 */
const bannerDetail = computed<string>(() => {
  if (isUnjudged.value) {
    return '你的答案格式老师这边识别不了（不代表你写错了）。先自己检查一遍，或者让老师带你把思路顺一顺。'
  }
  if (isWrong.value) {
    return '先别急着看答案。想想你是哪一步拐错了弯？让老师带你顺一遍，你自己找出来印象最深。'
  }
  return props.result?.reason ?? ''
})

/** 自评状态提示。 */
const selfCheckHint = computed<string>(() => {
  if (props.selfCheck === true) return '你标记了「我做对了」，这题就先这样'
  if (props.selfCheck === false) return '你标记了「我做错了」，要不要让老师带你想一想？'
  return ''
})

// 换题时收起二次确认面板，避免残留到下一题
watch(
  () => props.result,
  () => {
    confirmingReveal.value = false
  },
)

function handleGuide(): void {
  if (props.guideLoading) return
  emit('guide')
}

/** 点「看答案」小字：先展开二次确认，不直接请求。 */
function askReveal(): void {
  confirmingReveal.value = true
}

/** 二次确认里的「还是想看」。 */
function confirmReveal(): void {
  confirmingReveal.value = false
  emit('reveal')
}

/** 二次确认里的「再想想」（默认希望走这条）。 */
function cancelReveal(): void {
  confirmingReveal.value = false
}
</script>

<template>
  <section v-if="result" class="card space-y-4">
    <!-- 结论条 -->
    <div class="rounded-2xl px-4 py-3" :class="bannerClass">
      <p class="text-base font-bold">{{ bannerTitle }}</p>
      <p v-if="bannerDetail" class="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed">
        {{ bannerDetail }}
      </p>
      <!-- P0-D：选项不完整时判分后给一句补充（设计 §1.6.5） -->
      <p
        v-if="optionsStatus === 'partial' || optionsStatus === 'missing'"
        class="mt-2 text-xs leading-relaxed text-amber-800"
      >
        这题不计分，因为选项不完整
      </p>
    </div>

    <!-- level=4 专属：自评入口 -->
    <div v-if="isUnjudged" class="space-y-2">
      <p class="text-sm font-medium text-slate-700">自己核对完，告诉老师结果：</p>
      <div class="flex gap-3">
        <button
          type="button"
          class="h-12 flex-1 rounded-xl border text-base font-medium transition"
          :class="
            selfCheck === true
              ? 'border-green-500 bg-green-50 text-green-700'
              : 'border-slate-200 bg-white text-slate-600 active:bg-slate-50'
          "
          @click="emit('self-check', true)"
        >
          我做对了
        </button>
        <button
          type="button"
          class="h-12 flex-1 rounded-xl border text-base font-medium transition"
          :class="
            selfCheck === false
              ? 'border-amber-500 bg-amber-50 text-amber-700'
              : 'border-slate-200 bg-white text-slate-600 active:bg-slate-50'
          "
          @click="emit('self-check', false)"
        >
          我做错了
        </button>
      </div>
      <p v-if="selfCheckHint" class="text-xs leading-relaxed text-slate-500">
        {{ selfCheckHint }}
      </p>
    </div>

    <!-- 主按钮：做错 / 判不了 时唯一的显眼出口就是「让老师带我想一想」 -->
    <button
      v-if="!isRight"
      type="button"
      class="btn-primary h-14 w-full text-lg"
      :disabled="guideLoading"
      @click="handleGuide"
    >
      {{ guideLoading ? '老师正在想…' : '让老师带我想一想' }}
    </button>

    <!-- 答对了也可以再听一遍思路（次要，不抢主流程） -->
    <button
      v-else
      type="button"
      class="btn-ghost w-full"
      :disabled="guideLoading"
      @click="handleGuide"
    >
      {{ guideLoading ? '老师正在想…' : '听老师讲讲这题的思路' }}
    </button>

    <!-- 已取回的参考答案 -->
    <div v-if="reveal" class="space-y-2 rounded-2xl bg-slate-50 p-4">
      <p class="text-sm font-bold text-slate-700">参考答案</p>
      <p class="whitespace-pre-wrap break-words text-base leading-relaxed text-slate-800">
        {{ reveal.correct_answer || '这道题暂时没有录入标准答案' }}
      </p>
      <template v-if="reveal.analysis">
        <p class="pt-1 text-sm font-bold text-slate-700">解析</p>
        <p class="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-600">
          {{ reveal.analysis }}
        </p>
      </template>
    </div>

    <p v-if="revealError" class="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-500">
      {{ revealError }}
    </p>

    <!-- 二次确认面板（点了小字才展开） -->
    <div
      v-if="confirmingReveal"
      class="space-y-3 rounded-2xl border border-amber-200 bg-amber-50 p-4"
    >
      <p class="text-base font-bold text-amber-800">真的要直接看答案吗？</p>
      <p class="text-sm leading-relaxed text-amber-700">
        直接看答案，这道题就练不到了。要不要先让老师带你想一想？
      </p>
      <div class="flex gap-3">
        <button
          type="button"
          class="h-12 flex-1 rounded-xl bg-brand-500 text-base font-medium text-white transition active:bg-brand-600"
          @click="cancelReveal"
        >
          再想想
        </button>
        <button
          type="button"
          class="h-12 flex-1 rounded-xl border border-amber-300 bg-white text-base text-amber-700 transition active:bg-amber-100"
          :disabled="revealing"
          @click="confirmReveal"
        >
          {{ revealing ? '取答案中…' : '还是想看' }}
        </button>
      </div>
    </div>

    <!-- 底部：翻页 + 「看答案」次要入口（灰色小字，不做成按钮） -->
    <div class="flex items-center justify-between gap-3 border-t border-dashed border-slate-200 pt-3">
      <button
        v-if="canReveal && !reveal && !confirmingReveal"
        type="button"
        class="text-sm text-slate-400 underline underline-offset-4 transition active:text-slate-600"
        @click="askReveal"
      >
        看答案
      </button>
      <span v-else />

      <button
        v-if="hasNext"
        type="button"
        class="btn-ghost shrink-0 px-6"
        @click="emit('next')"
      >
        下一题
      </button>
      <button v-else type="button" class="btn-ghost shrink-0 px-6" @click="emit('finish')">
        交卷看结果
      </button>
    </div>
  </section>
</template>
