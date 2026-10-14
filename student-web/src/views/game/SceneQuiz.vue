<script setup lang="ts">
/**
 * 生活场景题库（方向六 · 第二期）。
 *
 * 形态：场景选择页 → 选择场景后进入「限时挑战」并携带 scenario_type，
 * 由后端 speed/start 按场景类型抽题（复用限时挑战计分与结算）。
 *
 * 场景分类（对齐设计文档 3.4 与 questions.scenario_type）：
 *   shopping 购物算账 / cake 分蛋糕 / time 时间规划 / distance 路程计算
 */
import { useRouter } from 'vue-router'

const router = useRouter()

interface SceneItem {
  type: string
  title: string
  desc: string
  icon: string
  gradient: string
  tips: string[]
}

const scenes: SceneItem[] = [
  {
    type: 'shopping',
    title: '购物算账',
    desc: '买东西找零、比价、优惠券，做个精明的小买家',
    icon: '🛒',
    gradient: 'from-emerald-400 to-teal-600',
    tips: ['50 元买 3 支笔还剩多少？', '满减优惠怎么算最划算？'],
  },
  {
    type: 'cake',
    title: '分蛋糕',
    desc: '把披萨、蛋糕、糖果平均分给小伙伴',
    icon: '🍰',
    gradient: 'from-pink-400 to-rose-500',
    tips: ['8 个小朋友分 3 块蛋糕', '分数加法与平均分'],
  },
  {
    type: 'time',
    title: '时间规划',
    desc: '电影几点结束？作业要写多久？学会看时间',
    icon: '⏰',
    gradient: 'from-sky-400 to-blue-600',
    tips: ['14:30 开始 90 分钟结束？', '时刻与时间间隔计算'],
  },
  {
    type: 'distance',
    title: '路程计算',
    desc: '速度 × 时间 = 路程，出行问题轻松算',
    icon: '🚗',
    gradient: 'from-amber-400 to-orange-500',
    tips: ['60km/h 开 2.5 小时走多远？', '相遇与追及问题入门'],
  },
]

function goScene(scene: SceneItem): void {
  void router.push({
    name: 'speed-challenge',
    query: { scenario_type: scene.type, scene_label: scene.title },
  })
}

function goHome(): void {
  void router.push({ name: 'game-hub' })
}
</script>

<template>
  <div class="mx-auto max-w-xl p-4">
    <div class="mb-3 flex items-center justify-between">
      <button type="button" class="btn-ghost px-3 py-1.5 text-sm" @click="goHome">← 返回大厅</button>
      <h1 class="text-base font-extrabold text-ink">🏪 生活场景题库</h1>
      <span class="w-16" />
    </div>

    <p class="mb-4 text-sm text-ink-soft">
      把数学用到生活里！每个场景进入 60 秒限时挑战，答对得分、连击加分。
    </p>

    <div class="space-y-3">
      <button
        v-for="s in scenes"
        :key="s.type"
        type="button"
        class="tile group w-full text-left shadow-lg"
        :class="'bg-gradient-to-br ' + s.gradient"
        @click="goScene(s)"
      >
        <div class="flex items-start gap-3">
          <div class="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-white/25 text-3xl backdrop-blur-sm">
            {{ s.icon }}
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <p class="text-lg font-extrabold text-white">{{ s.title }}</p>
            </div>
            <p class="mt-0.5 text-sm text-white/85">{{ s.desc }}</p>
            <div class="mt-2 flex flex-wrap gap-1.5">
              <span
                v-for="tip in s.tips"
                :key="tip"
                class="rounded-full bg-white/20 px-2 py-0.5 text-[11px] font-medium text-white/90 backdrop-blur-sm"
              >{{ tip }}</span>
            </div>
          </div>
          <span class="mt-1 text-xl text-white/70 transition group-hover:translate-x-1">→</span>
        </div>
      </button>
    </div>
  </div>
</template>