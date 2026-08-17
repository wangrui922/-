<script setup lang="ts">
/**
 * 错题本页（T03 · 3.5，T04 · F15 容器化 + 攻克）。
 *
 * 链路：GET /api/mistake/list（可带 state 筛选）→ 卡片列表；
 *      「标记已掌握」→ PUT /api/mistake/{id}/solve（T04 · B10 / OQ5）。
 *
 * 契约说明（与 api/chat.ts 保持一致，本页不改列表 API 层）：
 *   - listMistakes(state?) 不传 state 返回全部；返回 { items, total }。
 *   - MistakeItem.questionId 是 questions 表的 UUID。目前后端**没有**
 *     「按 questionId 取题面」的接口，所以这里只能把它当题号标识展示
 *     （「题目 #」+ 前 8 位），不假装能显示题干。
 *   - T02 起 MistakeItem 新增 kpId / kpName / grade（后端 T01 已落地），
 *     卡片渲染「🏷 知识点 · 年级」条 + 「练同类题」深链按钮（kpId 为空时
 *     整行不渲染）。深链复用画像页 RecommendationList.vue → Practice.vue
 *     的现成模式：router.push snake_case 直传，不转驼峰。
 *   - T04 起 SUPPORTS_MARK_SOLVED 已置 true（@/config/features），卡片对未掌握
 *     错题渲染「标记已掌握」按钮，调 markMistakeSolved（PUT .../solve），
 *     成功后用返回条目就地更新本地列表。
 *   - 顶栏与退出已下沉到 StudentLayout，本页只保留筛选 tab 与列表。
 *   - 「去练习」跳练习页筛选态（kpId 为空的错题用这个出口）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { listMistakes, markMistakeSolved, type MistakeItem, type MistakeState } from '@/api/chat'
import { SUPPORTS_MARK_SOLVED } from '@/config/features'
import { toMessage } from '@/api/http'

/** 筛选 tab 的取值：'all' 表示不传 state。 */
type FilterKey = MistakeState | 'all'

interface FilterTab {
  key: FilterKey
  label: string
}

const TABS: readonly FilterTab[] = [
  { key: 'all', label: '全部' },
  { key: 'unsolved', label: '未掌握' },
  { key: 'practicing', label: '练习中' },
  { key: 'mastered', label: '已掌握' },
]

const router = useRouter()

const items = ref<MistakeItem[]>([])
const total = ref<number>(0)
const activeKey = ref<FilterKey>('all')
const loading = ref<boolean>(false)
const errorMsg = ref<string>('')

/** 只有「加载完 + 没报错 + 真的没数据」才是空状态。 */
const isEmpty = computed<boolean>(
  () => !loading.value && !errorMsg.value && items.value.length === 0,
)

/** 正在标记「已掌握」的错题 ID（防止重复点击）。 */
const solvingId = ref<string>('')

/**
 * 拉取错题列表。
 *
 * @param key 筛选项，'all' 时不向后端传 state
 */
async function fetchMistakes(key: FilterKey): Promise<void> {
  activeKey.value = key
  loading.value = true
  errorMsg.value = ''

  try {
    const result = await listMistakes(key === 'all' ? undefined : key)
    items.value = result.items ?? []
    total.value = Number(result.total ?? items.value.length)
  } catch (error) {
    errorMsg.value = toMessage(error, '错题本加载失败，请重试')
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

/** 点 tab：同一个 tab 重复点也允许刷新。 */
function handleFilter(key: FilterKey): void {
  if (loading.value) return
  void fetchMistakes(key)
}

/** 重试按钮。 */
function handleRetry(): void {
  void fetchMistakes(activeKey.value)
}

/**
 * 把后端时间串格式化成 YYYY-MM-DD（原生 Date，不引第三方库）。
 *
 * @param value ISO 时间串，可能为 null
 */
function formatDate(value: string | null): string {
  if (!value) return '日期未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    // 后端若给了非标准串，退化成直接截前 10 位（通常正好是 YYYY-MM-DD）
    return value.slice(0, 10) || '日期未知'
  }
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/**
 * 题目标识：questionId 是 UUID，没有取题面的接口，只展示前 8 位当题号。
 *
 * @param questionId 题目 UUID
 */
function questionLabel(questionId: string): string {
  const short = (questionId || '').slice(0, 8)
  return short ? `题目 #${short}` : '题目 #未知'
}

/**
 * 去练习：跳练习页的筛选态，**不带**任何深链参数。
 *
 * 仅用于 kpId 为空的错题（无法知识点直达）或已掌握错题的「去练习」按钮。
 */
function handlePractice(): void {
  void router.push({ name: 'practice' })
}

/**
 * 练同类题：带 kp_id + kp_name + grade 深链到练习页，落地即开练。
 *
 * 复用画像页 RecommendationList.vue → Practice.vue 的现成深链模式：
 * query 键名 snake_case 直传（kp_id / kp_name / grade），与后端
 * `/practice/session` 的入参同名，全链路不转驼峰。
 *
 * grade 为 null 时不塞进 query（Practice.vue 的 consumeDeepLink 读到
 * 空值会当 undefined 处理，drawSession 不发给后端，等同不收窄年级）。
 * source: 'mistakes' 让练习页顶部显示「（来自你的错题）」来源提示。
 *
 * @param item 错题条目（调用方保证 kpId 非空）
 */
function handlePracticeKp(item: MistakeItem): void {
  if (!item.kpId) return
  const query: Record<string, string> = {
    kp_id: item.kpId,
    kp_name: item.kpName ?? '',
    source: 'mistakes',
  }
  if (typeof item.grade === 'number' && Number.isFinite(item.grade)) {
    query.grade = String(item.grade)
  }
  void router.push({ name: 'practice', query })
}

/**
 * 标记一道错题为已掌握（攻克）。
 * 成功后用后端返回的最新条目覆盖本地，保持列表与服务器一致。
 */
async function handleSolve(item: MistakeItem): Promise<void> {
  if (!SUPPORTS_MARK_SOLVED || item.solved || solvingId.value) return
  solvingId.value = item.id
  try {
    const updated = await markMistakeSolved(item.id)
    const idx = items.value.findIndex((it) => it.id === item.id)
    if (idx >= 0) items.value[idx] = { ...items.value[idx], ...updated }
  } catch (error) {
    errorMsg.value = toMessage(error, '标记失败，请重试')
  } finally {
    solvingId.value = ''
  }
}

onMounted(() => {
  void fetchMistakes('all')
})
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-x-hidden bg-transparent">
    <!-- 筛选 tab -->
    <nav class="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-white/60 bg-white/70 px-4 py-2 backdrop-blur-md">
      <button
        v-for="tab in TABS"
        :key="tab.key"
        type="button"
        class="shrink-0 rounded-full px-3 py-1.5 text-sm font-medium transition"
        :class="
          activeKey === tab.key
            ? 'bg-brand-50 text-brand-600'
            : 'text-slate-500 active:bg-slate-100'
        "
        :disabled="loading"
        @click="handleFilter(tab.key)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <!-- 列表区 -->
    <main class="flex-1 space-y-3 overflow-y-auto overflow-x-hidden px-4 py-4">
      <p v-if="loading" class="card text-sm text-slate-500">正在翻错题本…</p>

      <template v-else>
        <p v-if="errorMsg" class="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-500">
          {{ errorMsg }}
        </p>
        <button v-if="errorMsg" type="button" class="btn-ghost w-full" @click="handleRetry">
          重新加载
        </button>

        <!-- 空状态 -->
        <div v-if="isEmpty" class="card text-sm leading-relaxed text-slate-500">
          <p class="mb-2 font-medium text-slate-700">这里还是空的 🎉</p>
          <p>还没有错题，做错题 AI 老师会帮你收进来。</p>
          <p class="mt-2 text-xs text-slate-400">
            换个筛选看看，或者去 AI 老师那儿拍一道不会的题。
          </p>
        </div>

        <!-- 错题卡片 -->
        <article v-for="item in items" :key="item.id" class="card space-y-3">
          <div class="flex flex-wrap items-center gap-2">
            <span class="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">
              {{ questionLabel(item.questionId) }}
            </span>
            <span
              v-if="item.errorType"
              class="rounded-full bg-amber-50 px-2.5 py-1 text-xs text-amber-600"
            >
              {{ item.errorType }}
            </span>
            <span
              class="rounded-full px-2.5 py-1 text-xs font-medium"
              :class="item.solved ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-500'"
            >
              {{ item.solved ? '已掌握' : '未掌握' }}
            </span>
          </div>

          <p class="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-800">
            {{ item.errorReason || 'AI 老师还没写下错因，去练一练它会补上。' }}
          </p>

          <p class="text-xs text-slate-400">
            收录于 {{ formatDate(item.createdAt) }}
            <span v-if="item.solved && item.solvedAt"> · 攻克于 {{ formatDate(item.solvedAt) }}</span>
          </p>

          <!-- 知识点条 + 操作按钮 -->
          <div class="space-y-2 border-t border-dashed border-slate-200 pt-3">
            <!-- 知识点条：kpId 为空时整行不渲染，也不显示「练同类题」按钮 -->
            <div
              v-if="item.kpId"
              class="flex items-center justify-between gap-2"
            >
              <span class="min-w-0 truncate text-xs text-slate-500">
                🏷 {{ item.kpName }}<template v-if="item.grade !== null && item.grade !== undefined"> · {{ item.grade }}年级</template>
              </span>
              <button
                type="button"
                class="btn-ghost shrink-0 text-sm"
                @click="handlePracticeKp(item)"
              >
                练同类题 →
              </button>
            </div>

            <button
              v-if="SUPPORTS_MARK_SOLVED && !item.solved"
              type="button"
              class="btn-primary w-full"
              :disabled="solvingId === item.id"
              @click="handleSolve(item)"
            >
              {{ solvingId === item.id ? '标记中…' : '标记已掌握' }}
            </button>
            <button v-else type="button" class="btn-ghost w-full" @click="handlePractice">
              去练习
            </button>
          </div>
        </article>
      </template>
    </main>
  </div>
</template>
