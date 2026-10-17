<script setup lang="ts">
/**
 * 练习主页面（题库消费层 · F3）。
 *
 * 三态流转（状态机在 stores/practice.ts，本页只做编排与渲染）：
 *
 *   filter ──startPractice(成功)──► doing ──finishPractice(成功)──► result
 *     ▲                                                              │
 *     └──────────────── backToFilter / 「换个条件练」 ────────────────┘
 *                       result ──restartSame──► doing（同条件再来一组）
 *
 * 组件分工：
 *   PracticeFilterCard  挑条件
 *   BankQuestionCard    出题 + 收集选择（不碰答案）
 *   AnswerFeedbackBar   判分结论 + 引导主按钮 + 「看答案」次要入口（含二次确认）
 *   GuideChatPanel      AI 引导对话（T1 起与 AI 老师页共用同一个通用面板）
 *   PracticeResult      结算
 *
 * 诚实披露：抽题返回的 auto_relaxed / unmapped_count / pool_size 在答题态顶部
 * 挂一条常驻说明条，不折叠、不隐藏。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import tutorAvatar from '@/assets/tutor-yueyue-v2.png'
import GuideChatPanel from '@/components/guide/GuideChatPanel.vue'
import AnswerFeedbackBar from '@/components/practice/AnswerFeedbackBar.vue'
import BankQuestionCard from '@/components/practice/BankQuestionCard.vue'
import PracticeFilterCard from '@/components/practice/PracticeFilterCard.vue'
import PracticeResult from '@/components/practice/PracticeResult.vue'
import type { DrawParams } from '@/api/practice'
import {
  answeredCount,
  backToFilter,
  canReveal,
  chooseOption,
  closeGuide,
  currentFeedback,
  currentQuestion,
  currentReveal,
  currentSelected,
  finishAndExit,
  finishPractice,
  goNext,
  goPrev,
  goTo,
  isAllAnswered,
  markSelfCheck,
  openGuide,
  practiceState,
  restartSame,
  revealCurrent,
  sendGuide,
  startDeepLink,
  startPractice,
  submitCurrent,
  unjudgedCount,
  unmappedInSession,
  wrongCount,
} from '@/stores/practice'
import { checkStatus, consentState } from '@/stores/consent'

const router = useRouter()
const route = useRoute()

// ---- 派生态 ---------------------------------------------------------------

const question = computed(() => currentQuestion())
const feedback = computed(() => currentFeedback())
const reveal = computed(() => currentReveal())
const selected = computed<string>(() => currentSelected())
const revealVisible = computed<boolean>(() => canReveal())
const answered = computed<number>(() => answeredCount())
const allAnswered = computed<boolean>(() => isAllAnswered())

/** 本题是否已经判过分（判过就锁定选项，不许改）。 */
const locked = computed<boolean>(() => feedback.value !== null)

/** 还有没有下一题。 */
const hasNext = computed<boolean>(
  () => practiceState.index < practiceState.questions.length - 1,
)

/** 当前题的自评结论（只有 level=4 会用到）。 */
const selfCheck = computed<boolean | null>(() => {
  const q = question.value
  if (!q) return null
  const value = practiceState.selfCheck[q.question_id]
  return value === undefined ? null : value
})

/** 答题进度百分比。 */
const progressPct = computed<number>(() => {
  const total = practiceState.questions.length
  if (total === 0) return 0
  return Math.round((answered.value / total) * 100)
})

/** 本次抽到的题里没关联知识点的道数。 */
const unmapped = computed<number>(() => unmappedInSession())

/** 知识点直达时的知识点名；空串表示用户是自己挑的条件。 */
const deepLinkName = computed<string>(() => practiceState.deep_link?.kp_name ?? '')

/**
 * 深链来源后缀文案（T02 新增）。
 *
 * 错题本深链带 `source: 'mistakes'`，顶部回显「正在练：计算（来自你的错题）」，
 * 让孩子知道这不是随机题。画像页不传 source，后缀为空，行为与现状一致。
 */
const deepLinkSourceSuffix = computed<string>(() => {
  const source = practiceState.deep_link?.source
  if (source === 'mistakes') return '（来自你的错题）'
  if (source === 'profile') return '（来自你的画像）'
  return ''
})

/**
 * 深链进来但一道题都没抽到（后端 code=404）。
 *
 * 这时不复用筛选卡的通用红字，改渲染一张指名道姓的空态卡：孩子是冲着
 * 「练分数」来的，只说「换个条件试试」等于没解释。
 */
const deepLinkEmpty = computed<boolean>(
  () => practiceState.drawEmpty && deepLinkName.value !== '',
)

/**
 * 顶部诚实披露文案（没有要披露的就不渲染）。
 *
 * ⚠️ 深链场景的 auto_relaxed **不能**复用通用那句「老师自动放宽了筛选范围，
 * 这组题可能不完全符合你的选择」。查过后端 `practice_service.draw_session`：
 * 三级回退的 `common` 字典里始终带着 `kp_id`，`**common` 传给每一级
 * `_candidate_rows`，所以放宽**从不丢掉知识点过滤**——放宽的只是
 * 「是否只要人工核对过的题」和「是否回避最近 7 天做过的题」。
 * 也就是说指定知识点时抽到的题**必然还是这个知识点的**，说成「练了别的」是假话。
 *
 * T5/T10 重构期：本计算属性追加 ``relaxation_steps`` 逐行渲染（设计 §1.5），
 * 顺序就是后端追加的顺序，每条用「第 N 档：{label}（候选 {pool_size} 道）」。
 */
const disclosures = computed<string[]>(() => {
  const list: string[] = []
  const kp_name = deepLinkName.value
  const steps = practiceState.pool.relaxation_steps ?? []

  // P0-E：逐档披露（设计 §1.5 / §1.6.4）。只在实际放宽过的时候才显示当前档的 tag 行。
  if (steps.length > 0) {
    const top = steps[steps.length - 1]
    if (typeof top?.level === 'number' && top.level > 1) {
      list.push(`本次放宽到了第 ${top.level} 档`)
    }
    steps.forEach((step, idx) => {
      // 只显示有内容的档；既没放宽也没候选题的档跳过（不刷屏）。
      if ((step.applied ?? step.pool_size > 0) && step.label) {
        list.push(`第 ${step.level} 档：${step.label}（候选 ${step.pool_size} 道）`)
      }
      // 避免循环内未用报错（linter 友好）
      void idx
    })
  } else if (practiceState.pool.auto_relaxed) {
    // 老后端（仅有 auto_relaxed 布尔位、没 steps 时）的兜底文案
    if (kp_name) {
      list.push(
        `「${kp_name}」能自动判分的题不多，这次有一些是你最近刚练过的，又给你抽上来了`,
      )
    } else {
      list.push('你选的条件下题目不太够，老师自动放宽了筛选范围，这组题可能不完全符合你的选择')
    }
  }

  // 池子比想要的题量小：后端会给 `miss_count` > 0 标记，按设计 §1.6.4 显式提示
  if (practiceState.pool.miss_count > 0) {
    const size = practiceState.pool.actual_count ?? practiceState.pool.pool_size
    if (kp_name) {
      list.push(`「${kp_name}」现在只有 ${size} 道能自动判分的题，这次就先练这 ${size} 道`)
    } else {
      list.push(`这个条件下只凑齐了 ${size} 道题，比你要的少一些`)
    }
  } else if (
    practiceState.pool.pool_size > 0 &&
    practiceState.pool.requested_count > 0 &&
    practiceState.pool.pool_size < practiceState.pool.requested_count
  ) {
    // 兼容老后端：仅有 pool_size / requested_count，没 miss_count
    const size = practiceState.pool.pool_size
    if (kp_name) {
      list.push(`「${kp_name}」现在只有 ${size} 道能自动判分的题，这次就先练这 ${size} 道`)
    } else {
      list.push(`这个条件下只凑齐了 ${size} 道题，比你要的少一些`)
    }
  }

  if (unmapped.value > 0) {
    list.push(`本次有 ${unmapped.value} 道题暂未关联知识点，做对做错都不计入掌握度统计`)
  }
  if (practiceState.pool.unmapped_count > 0 && unmapped.value === 0) {
    list.push(
      `题库这个范围里还有 ${practiceState.pool.unmapped_count} 道题没关联知识点，这次没抽到它们`,
    )
  }
  return list
})

/** 当前题 options_status 决定了提交按钮是否要 disabled（P0-D）。
 *
 * 用类型守卫落到受约束的联合类型上，避免 BankQuestionCard 报错。
 */
const currentOptionsStatus = computed<'complete' | 'partial' | 'missing' | 'n/a'>(() => {
  const v = question.value?.options_status ?? 'n/a'
  return v === 'complete' || v === 'partial' || v === 'missing' || v === 'n/a'
    ? v
    : 'n/a'
})
const submitDisabledByOptions = computed<boolean>(
  () => currentOptionsStatus.value === 'partial' || currentOptionsStatus.value === 'missing',
)

/** 题号导航的「关卡宝石」样式：当前关=紫红闪烁，已通关=琥珀亮，未开=暗。 */
function dotClass(idx: number): string {
  const q = practiceState.questions[idx]
  const done = q ? Boolean(practiceState.feedback[q.question_id]) : false
  if (idx === practiceState.index)
    return 'bg-gradient-to-br from-brand-500 to-coral-400 text-white shadow-float ring-2 ring-white animate-pulse-ring'
  if (done) return 'bg-gradient-to-br from-amber-400 to-amber-500 text-white shadow'
  return 'bg-slate-100 text-slate-400'
}

/**
 * 结算庆祝等级（结果态外壳用）：按正确率给不同 emoji/文案。
 * accuracy 是 0~1，与 PracticeResult 内部口径一致。
 */
const resultTier = computed<{ emoji: string; title: string; sub: string }>(() => {
  const r = practiceState.result
  if (!r || typeof r.accuracy !== 'number') {
    return { emoji: '🎉', title: '这一关打完啦', sub: '' }
  }
  const pct = Math.round(r.accuracy * 100)
  if (pct >= 90) return { emoji: '🏆', title: '通关！神操作', sub: '几乎全对，稳得一批' }
  if (pct >= 70) return { emoji: '🌟', title: '过关！', sub: '大部分都拿下了' }
  if (pct >= 40) return { emoji: '💪', title: '闯过这关', sub: '错题老师都帮你收好啦' }
  return { emoji: '🌱', title: '这一关打完啦', sub: '难度不小，慢慢来' }
})

// ---- 事件 -----------------------------------------------------------------

function handleStart(params: DrawParams): void {
  void startPractice(params)
}

function handleSelect(key: string): void {
  chooseOption(key)
}

function handleSubmit(): void {
  void submitCurrent()
}

function handleGuide(): void {
  openGuide()
}

function handleGuideSend(work: string): void {
  void sendGuide(work)
}

function handleReveal(): void {
  void revealCurrent()
}

function handleSelfCheck(correct: boolean): void {
  markSelfCheck(correct)
}

function handleFinish(): void {
  void finishPractice()
}

function handleAgain(): void {
  void restartSame()
}

/**
 * T10：薄弱点一键再练。跳到 `/practice?kp_id=...&kp_name=...`，
 * 由 `consumeDeepLink()` 接住并直接调 `startDeepLink()` 开练。
 *
 * 这里**重新走一遍路由**而不是直调 `startPractice`：因为页面已经处于
 * `result` 阶段，状态需要先回到 `filter`/`doing` 才不会撞残留 state。
 */
function handleRePractice(payload: { kp_id: string; kp_name: string }): void {
  void router
    .push({
      name: 'practice',
      query: {
        kp_id: payload.kp_id,
        kp_name: payload.kp_name,
      },
    })
    .then(() => {
      // consumeDeepLink() 在 onMounted 跑了，但路由 query 变化不会自动再跑；这里手补一次
      consumeDeepLink()
    })
}

function handleBack(): void {
  backToFilter()
}

function handleGoMistakes(): void {
  void router.push({ name: 'mistakes' })
}

// ---- 中途退出 --------------------------------------------------------------

/** 退出二次确认弹窗是否打开。 */
const quitConfirmOpen = ref<boolean>(false)

/** 已作答的题里会被收进错题本的道数（口径与后端 finish 的 C4 一致）。 */
const quitWrong = computed<number>(() => wrongCount())

/** 已作答的题里老师没法自动判分（level=4）的道数。 */
const quitUnjudged = computed<number>(() => unjudgedCount())

/**
 * 退出确认的正文。
 *
 * level=4 的题 is_correct 恒为 false，后端**照样**会把它收进错题本，
 * 所以入本条数必须把它算进去（否则弹窗承诺的数和实际入本的数对不上）；
 * 但铁律「level=4 不许说成你做错了」也要守住，于是有 level=4 时分开讲。
 */
const quitMessage = computed<string>(() => {
  const done = answered.value
  const wrong = quitWrong.value
  const unjudged = quitUnjudged.value

  if (wrong === 0) {
    return `你已经做了 ${done} 道题，全都答对了。要保存这次练习吗？`
  }
  if (unjudged > 0) {
    const judged = wrong - unjudged
    const head =
      judged > 0
        ? `你已经做了 ${done} 道题，其中 ${judged} 道做错了，还有 ${unjudged} 道老师没法自动判分`
        : `你已经做了 ${done} 道题，其中 ${unjudged} 道老师没法自动判分`
    return `${head}。要保存这次练习吗？这 ${wrong} 道都会收进错题本。`
  }
  return `你已经做了 ${done} 道题，其中 ${wrong} 道做错了。要保存这次练习吗？错题会自动收进错题本。`
})

/**
 * 中途退出。
 *
 * 一道题都没做就直接走人，不打扰；做过题就必须问一句——作答在 `/answer`
 * 时已经落库、掌握度已经受影响，此时不结算丢掉的是「错题入本」，
 * 而那正是本产品的核心价值，静默丢掉最伤。
 */
function handleQuit(): void {
  if (answered.value === 0) {
    backToFilter()
    return
  }
  practiceState.finishError = ''
  quitConfirmOpen.value = true
}

/** 保存并退出：结算成功（错题入本）后才回筛选态；失败留在弹窗里可重试。 */
async function handleSaveAndQuit(): Promise<void> {
  const ok = await finishAndExit()
  if (ok) quitConfirmOpen.value = false
}

/** 直接退出：不结算，本次的错题不入本。 */
function handleQuitAnyway(): void {
  quitConfirmOpen.value = false
  backToFilter()
}

/** 关掉弹窗，继续做题。 */
function handleQuitCancel(): void {
  quitConfirmOpen.value = false
}

// ---- 知识点直达 ------------------------------------------------------------

/**
 * 从 route query 读知识点直达参数并**直接开练**。
 *
 * query 里的键名保持 `kp_id` / `kp_name` / `grade`（snake_case），与后端
 * `/practice/session` 的入参同名，全链路不转驼峰。
 * 读到 kp_id 就不停在筛选态——孩子在画像页或错题本已经点过一次了，
 * 再让他挑一遍条件等于把他自己的选择还给他。
 *
 * `grade` 是 P0-3 止血参数（T02 新增）：错题本深链同时带 grade 收窄到
 * 「同年级 × 同知识点」。画像页不传 grade，这里读出来是 undefined，
 * drawSession 不会发给后端，行为与现状完全一致。
 * `source` 仅用于顶部来源提示文案（「来自你的错题」），不参与抽题。
 */
function consumeDeepLink(): void {
  const rawId = route.query.kp_id
  const rawName = route.query.kp_name
  const rawGrade = route.query.grade
  const rawSource = route.query.source
  const kp_id = typeof rawId === 'string' ? rawId.trim() : ''
  if (!kp_id) return

  const kp_name = typeof rawName === 'string' ? rawName.trim() : ''

  // grade: query 里是字符串，转成 number；空 / NaN → undefined（不传给后端）
  let grade: number | undefined
  if (typeof rawGrade === 'string' && rawGrade.trim()) {
    const n = Number(rawGrade)
    grade = Number.isFinite(n) ? n : undefined
  }

  const source =
    typeof rawSource === 'string' && rawSource === 'mistakes'
      ? 'mistakes'
      : typeof rawSource === 'string' && rawSource === 'profile'
        ? 'profile'
        : undefined

  // 把 query 从地址栏摘掉：避免用户在练习页刷新 / 点「练别的」后又被拽回同一个知识点
  void router.replace({ name: 'practice' })
  void startDeepLink({ kp_id, kp_name, grade, source })
}

/**
 * 「练别的」：退出知识点直达，回到筛选态自己挑。
 *
 * 复用 handleQuit——它同样是一条「离开答题态」的路径，做过题就必须先问一句
 * 要不要保存，否则从这个出口走会静默丢掉错题入本，跟「退出练习」一个后果。
 */
function handleExitDeepLink(): void {
  handleQuit()
}

onMounted(() => {
  void bootstrapConsent()
})

/**
 * 挂载启动顺序：监护人同意态未知时，先补一次预查再放行深链开练。
 *
 * 路由守卫通常已在导航前异步查过（绝大多数情况到这里已拿到结论）；
 * 这里是刷新页面 / 守卫未覆盖路径的兜底。预查**不阻塞渲染**——筛选态
 * 页面照常可见，只是深链自动开练等这一个快查落地：未同意时 store 会
 * 主动弹引导窗，不用孩子先撞一次 403 再弹窗。
 */
async function bootstrapConsent(): Promise<void> {
  if (consentState.status === 'unknown') {
    await checkStatus()
  }
  consumeDeepLink()
}

// 离开页面时把抽屉收掉，避免遮罩残留在别的页面上
onBeforeUnmount(() => {
  closeGuide()
})
</script>

<template>
  <div class="h-full overflow-y-auto overflow-x-hidden bg-transparent">
    <div class="mx-auto w-full max-w-app space-y-4 px-4 py-4">
      <!-- ① 筛选态 -->
      <template v-if="practiceState.phase === 'filter'">
        <!-- 深链进来却一道题都没抽到：指名道姓地解释，别只说「换个条件试试」 -->
        <section
          v-if="deepLinkEmpty"
          class="card space-y-3 border border-amber-200 bg-amber-50/60"
        >
          <h2 class="text-base font-bold text-amber-900">
            「{{ deepLinkName }}」这个知识点暂时练不了
          </h2>
          <p class="text-sm leading-relaxed text-amber-800">
            题库里「{{ deepLinkName }}」的题目现在还没有能让老师自动判分的
            （要么是没录标准答案，要么是解答题、填空题这类需要人工看的）。
            老师不会拿别的知识点凑数糊弄你，所以这次先空着。
          </p>
          <p class="text-sm leading-relaxed text-amber-800">
            你可以先在下面挑个别的条件练一练，等这个知识点的题补齐了再回来。
          </p>
        </section>

        <!-- 正在挑题（深链自动开练时页面不该是空白的） -->
        <p v-if="practiceState.drawing" class="card text-center text-base text-slate-500">
          正在给你挑题…
        </p>

        <!-- 闯关地图入口：小玥老师带你选关卡 -->
        <section class="relative overflow-hidden rounded-3xl bg-gradient-to-br from-brand-500 via-brand-500 to-coral-400 p-5 text-white shadow-float animate-fade-up">
          <div class="pointer-events-none absolute -right-8 -top-10 h-28 w-28 rounded-full bg-white/15" />
          <div class="pointer-events-none absolute -bottom-10 -left-6 h-32 w-32 rounded-full bg-white/10" />
          <div class="relative flex items-center gap-3">
            <img :src="tutorAvatar" alt="小玥老师" class="h-14 w-14 shrink-0 animate-float drop-shadow-md" />
            <div class="min-w-0">
              <h2 class="text-lg font-extrabold">🗺️ 选择你的挑战关卡</h2>
              <p class="mt-0.5 text-sm text-white/85">挑好条件，出发去闯关吧！</p>
            </div>
          </div>
          <svg class="pointer-events-none absolute right-5 top-4 h-6 w-6 animate-twinkle" viewBox="0 0 24 24"><path d="M12 2l2.4 6.6L21 9l-5.4 4 1.8 7L12 16.8 6.6 20l1.8-7L3 9l6.6-.4z" fill="#FFE08A"/></svg>
          <svg class="pointer-events-none absolute right-12 top-12 h-4 w-4 animate-twinkle" style="animation-delay:.6s" viewBox="0 0 24 24"><path d="M12 2l2.4 6.6L21 9l-5.4 4 1.8 7L12 16.8 6.6 20l1.8-7L3 9l6.6-.4z" fill="#FFFFFF"/></svg>
        </section>

        <PracticeFilterCard
          :loading="practiceState.drawing"
          :error="deepLinkEmpty ? '' : practiceState.drawError"
          @start="handleStart"
        />
      </template>

      <!-- ② 答题态 -->
      <template v-else-if="practiceState.phase === 'doing'">
        <!-- 知识点直达回显：让孩子随时看得见自己在练哪个点，并留一个「练别的」出口 -->
        <section
          v-if="deepLinkName"
          class="flex items-center justify-between gap-3 rounded-2xl bg-brand-50 px-4 py-3"
        >
          <p class="min-w-0 truncate text-sm font-semibold text-brand-700">
            正在练：{{ deepLinkName }}{{ deepLinkSourceSuffix }}
          </p>
          <button
            type="button"
            class="shrink-0 rounded-full bg-white px-3 py-1.5 text-xs font-medium text-brand-600 transition active:bg-brand-100"
            @click="handleExitDeepLink"
          >
            练别的
          </button>
        </section>

        <!-- 进度条（闯关轨道） -->
        <section class="card space-y-3">
          <div class="flex items-center justify-between gap-3">
            <div class="flex min-w-0 items-center gap-2">
              <span class="shrink-0 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-bold text-amber-700">🏆 闯关中</span>
              <p class="truncate text-base font-bold text-slate-800">
                已完成 {{ answered }} / {{ practiceState.questions.length }} 关
              </p>
            </div>
            <button
              type="button"
              class="shrink-0 text-sm text-slate-400 transition active:text-slate-600"
              @click="handleQuit"
            >
              退出
            </button>
          </div>
          <!-- 星光轨道 -->
          <div class="relative h-3 overflow-hidden rounded-full bg-slate-100">
            <div
              class="h-full rounded-full bg-gradient-to-r from-brand-400 to-coral-400 transition-all duration-500"
              :style="{ width: `${progressPct}%` }"
            />
          </div>
          <!-- 关卡宝石节点 -->
          <div class="flex flex-wrap gap-2">
            <button
              v-for="(q, idx) in practiceState.questions"
              :key="q.question_id"
              type="button"
              class="relative h-9 w-9 rounded-full text-sm font-bold transition"
              :class="dotClass(idx)"
              @click="goTo(idx)"
            >
              {{ idx + 1 }}
            </button>
          </div>
        </section>

        <!-- 诚实披露条 -->
        <section v-if="disclosures.length" class="space-y-2">
          <p
            v-for="(text, idx) in disclosures"
            :key="idx"
            class="rounded-2xl bg-amber-50 px-4 py-3 text-sm leading-relaxed text-amber-800"
          >
            {{ text }}
          </p>
        </section>

        <!-- 题卡 -->
        <BankQuestionCard
          v-if="question"
          :question="question"
          :index="practiceState.index"
          :total="practiceState.questions.length"
          :selected="selected"
          :locked="locked"
          @select="handleSelect"
        />

        <!-- 未提交：提交按钮 -->
        <template v-if="question && !feedback">
          <p
            v-if="practiceState.submitError"
            class="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-500"
          >
            {{ practiceState.submitError }}
          </p>
          <button
            type="button"
            class="btn-primary h-14 w-full text-lg"
            :disabled="practiceState.submitting || !selected || submitDisabledByOptions"
            @click="handleSubmit"
          >
            {{ practiceState.submitting ? '提交中…' : submitDisabledByOptions ? '这道题选项不完整，做完不算分' : '⚔️ 提交这道题' }}
          </button>
          <div class="flex gap-3">
            <button
              type="button"
              class="btn-ghost flex-1"
              :disabled="practiceState.index === 0"
              @click="goPrev"
            >
              上一题
            </button>
            <button type="button" class="btn-ghost flex-1" :disabled="!hasNext" @click="goNext">
              ⏭ 先跳过
            </button>
          </div>
        </template>

        <!-- 已提交：反馈条 -->
        <AnswerFeedbackBar
          v-if="feedback"
          :result="feedback"
          :reveal="reveal"
          :can-reveal="revealVisible"
          :revealing="practiceState.revealing"
          :reveal-error="practiceState.revealError"
          :self-check="selfCheck"
          :has-next="hasNext"
          :guide-loading="practiceState.guide.loading"
          :options-status="currentOptionsStatus"
          @guide="handleGuide"
          @reveal="handleReveal"
          @self-check="handleSelfCheck"
          @next="goNext"
          @finish="handleFinish"
        />

        <!-- 结算失败提示：不管从哪个入口交的卷，都在这儿说 -->
        <p
          v-if="practiceState.finishError"
          class="rounded-2xl bg-red-50 px-4 py-3 text-sm leading-relaxed text-red-500"
        >
          {{ practiceState.finishError }}
        </p>

        <!-- 交卷：最后一题的反馈条里已经有出口了，就不重复出一遍 -->
        <section v-if="!(feedback && !hasNext)" class="card space-y-2">
          <p v-if="!allAnswered" class="text-sm text-slate-500">
            还有 {{ practiceState.questions.length - answered }} 道没做，也可以先交卷看结果
          </p>
          <button
            type="button"
            class="w-full"
            :class="allAnswered ? 'btn-primary h-14 text-lg' : 'btn-ghost'"
            :disabled="practiceState.finishing"
            @click="handleFinish"
          >
            {{ practiceState.finishing ? '正在结算…' : '交卷看结果' }}
          </button>
        </section>
      </template>

      <!-- ③ 结果态 -->
      <template v-else>
        <!-- 「再来一组」抽题失败 / 进行中时的反馈（否则点了没反应） -->
        <p v-if="practiceState.drawing" class="card text-center text-base text-slate-500">
          正在挑下一组题…
        </p>
        <p
          v-else-if="practiceState.drawError"
          class="rounded-2xl bg-red-50 px-4 py-3 text-sm leading-relaxed text-red-500"
        >
          {{ practiceState.drawError }}
        </p>

        <div v-else class="space-y-4">
          <!-- 通关庆祝横幅 -->
          <section class="relative overflow-hidden rounded-3xl bg-gradient-to-br from-brand-500 via-brand-500 to-coral-400 p-5 text-center text-white shadow-float animate-pop">
            <div class="pointer-events-none absolute -right-8 -top-10 h-28 w-28 rounded-full bg-white/15" />
            <div class="pointer-events-none absolute -bottom-10 -left-6 h-32 w-32 rounded-full bg-white/10" />
            <div class="relative">
              <div class="text-4xl">{{ resultTier.emoji }}</div>
              <h2 class="mt-1 text-xl font-extrabold">{{ resultTier.title }}</h2>
              <p v-if="resultTier.sub" class="mt-0.5 text-sm text-white/85">{{ resultTier.sub }}</p>
            </div>
            <svg class="pointer-events-none absolute left-4 top-4 h-6 w-6 animate-twinkle" viewBox="0 0 24 24"><path d="M12 2l2.4 6.6L21 9l-5.4 4 1.8 7L12 16.8 6.6 20l1.8-7L3 9l6.6-.4z" fill="#FFE08A"/></svg>
            <svg class="pointer-events-none absolute right-6 bottom-3 h-5 w-5 animate-twinkle" style="animation-delay:.6s" viewBox="0 0 24 24"><path d="M12 2l2.4 6.6L21 9l-5.4 4 1.8 7L12 16.8 6.6 20l1.8-7L3 9l6.6-.4z" fill="#FFFFFF"/></svg>
          </section>

          <PracticeResult
            :result="practiceState.result"
            :loading="practiceState.finishing"
            :error="practiceState.finishError"
            @again="handleAgain"
            @back="handleBack"
            @retry="handleFinish"
            @mistakes="handleGoMistakes"
            @re-practice="handleRePractice"
          />
        </div>
      </template>
    </div>

    <!-- 中途退出二次确认：默认保存，「保存并退出」是主按钮 -->
    <div
      v-if="quitConfirmOpen"
      class="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 px-4 pb-6 sm:items-center sm:pb-0"
      @click.self="handleQuitCancel"
    >
      <section class="w-full max-w-sm space-y-4 rounded-3xl bg-white p-5 shadow-xl">
        <h2 class="text-lg font-bold text-slate-800">要保存这次练习吗？</h2>
        <p class="text-sm leading-relaxed text-slate-600">{{ quitMessage }}</p>
        <p class="rounded-xl bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-500">
          你做过的每道题都已经记下来了。不保存的话，这次的错题就不会进错题本。
        </p>

        <p
          v-if="practiceState.finishError"
          class="rounded-xl bg-red-50 px-3 py-2 text-sm leading-relaxed text-red-500"
        >
          {{ practiceState.finishError }}
        </p>

        <div class="space-y-2">
          <button
            type="button"
            class="btn-primary h-14 w-full text-base"
            :disabled="practiceState.finishing"
            @click="handleSaveAndQuit"
          >
            {{ practiceState.finishing ? '正在保存…' : '保存并退出' }}
          </button>
          <button
            type="button"
            class="btn-ghost w-full"
            :disabled="practiceState.finishing"
            @click="handleQuitCancel"
          >
            继续做题
          </button>
          <button
            type="button"
            class="w-full py-2 text-sm text-slate-400 transition active:text-slate-600"
            :disabled="practiceState.finishing"
            @click="handleQuitAnyway"
          >
            不保存，直接退出
          </button>
        </div>
      </section>
    </div>

    <!-- AI 引导抽屉（全局挂载，答题态用） -->
    <GuideChatPanel
      :open="practiceState.guide.open"
      :loading="practiceState.guide.loading"
      :error="practiceState.guide.error"
      :messages="practiceState.guide.messages"
      :ask-final-confirm="practiceState.guide.ask_final_confirm"
      :stem="question?.stem ?? ''"
      @close="closeGuide"
      @send="handleGuideSend"
    />
  </div>
</template>
