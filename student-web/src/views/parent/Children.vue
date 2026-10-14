<script setup lang="ts">
/**
 * 家长端：孩子列表页。
 *
 * 功能：
 *   - 展示已绑定孩子列表
 *   - 点击孩子进入详情
 *   - 绑定新孩子入口
 *   - 解绑孩子
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getChildren, unbindChild, type ChildBrief } from '@/api/family'
import { toMessage } from '@/api/http'

const router = useRouter()
const children = ref<ChildBrief[]>([])
const loading = ref(false)
const error = ref('')

async function loadChildren() {
  loading.value = true
  error.value = ''
  try {
    children.value = await getChildren()
  } catch (err) {
    error.value = toMessage(err, '加载失败')
  } finally {
    loading.value = false
  }
}

function goDetail(child: ChildBrief) {
  if (!child.studentId) return
  void router.push({ name: 'parent-child-detail', params: { sid: child.studentId } })
}

async function handleUnbind(studentId: string, e: Event) {
  e.stopPropagation()
  if (!confirm('确定要解绑这个孩子吗？')) return
  try {
    await unbindChild(studentId)
    await loadChildren()
  } catch (err) {
    error.value = toMessage(err, '解绑失败')
  }
}

onMounted(loadChildren)
</script>

<template>
  <div class="min-h-screen bg-slate-50 pb-8">
    <!-- 头部 -->
    <div class="bg-gradient-to-br from-brand-500 to-brand-600 px-5 py-6 text-white">
      <h1 class="text-xl font-extrabold">我的孩子</h1>
      <p class="mt-1 text-sm text-white/80">点击查看学习进展与错题</p>
    </div>

    <!-- 孩子列表 -->
    <div class="space-y-3 px-4 pt-4">
      <div
        v-for="child in children"
        :key="child.studentId"
        class="flex items-center gap-4 rounded-2xl bg-white p-4 shadow-soft transition active:scale-[0.98]"
        @click="goDetail(child)"
      >
        <div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-brand-100 text-lg">
          {{ child.nickname?.[0] || '学' }}
        </div>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <span class="font-bold text-ink">{{ child.nickname || '未知' }}</span>
            <span v-if="child.grade" class="rounded bg-brand-50 px-2 py-0.5 text-[10px] font-bold text-brand-600">
              {{ child.grade }}年级
            </span>
          </div>
          <p class="mt-0.5 text-xs text-slate-500">
            {{ child.stage === 'primary' ? '小学' : child.stage === 'junior' ? '初中' : child.stage === 'senior' ? '高中' : '' }}
            · 关系：{{ child.relation || '家长' }}
          </p>
        </div>
        <button
          type="button"
          class="shrink-0 rounded-lg px-3 py-1.5 text-xs font-bold text-red-400 transition active:bg-red-50"
          @click="handleUnbind(child.studentId, $event)"
        >
          解绑
        </button>
      </div>

      <!-- 空状态 -->
      <div v-if="!loading && children.length === 0" class="py-12 text-center">
        <p class="text-sm text-slate-400">还没有绑定孩子</p>
        <router-link
          :to="{ name: 'parent-bind' }"
          class="mt-4 inline-block rounded-xl bg-brand-500 px-6 py-2.5 text-sm font-bold text-white shadow-soft"
        >
          绑定孩子
        </router-link>
      </div>

      <!-- 加载/错误 -->
      <div v-if="loading" class="py-8 text-center text-sm text-slate-400">加载中…</div>
      <div v-if="error" class="rounded-2xl bg-red-50 p-3 text-center text-sm text-red-500">{{ error }}</div>
    </div>

    <!-- 绑定按钮（已有孩子时也显示） -->
    <div v-if="children.length > 0" class="mt-4 px-4">
      <router-link
        :to="{ name: 'parent-bind' }"
        class="block w-full rounded-xl bg-white py-3 text-center text-sm font-bold text-brand-600 shadow-soft"
      >
        + 绑定更多孩子
      </router-link>
    </div>
  </div>
</template>
