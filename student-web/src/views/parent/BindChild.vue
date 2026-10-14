<script setup lang="ts">
/**
 * 绑定孩子页：输入孩子手机号 + 关系，调用 /api/family/bind。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { bindChild } from '@/api/family'
import { toMessage } from '@/api/http'

const router = useRouter()

const phone = ref('')
const relation = ref('爸爸')
const loading = ref(false)
const error = ref('')
const success = ref('')

const phoneValid = computed(() => /^1[3-9]\d{9}$/.test(phone.value.trim()))

async function handleBind() {
  if (!phoneValid.value) {
    error.value = '请输入正确的手机号'
    return
  }
  loading.value = true
  error.value = ''
  success.value = ''
  try {
    await bindChild(phone.value.trim(), relation.value)
    success.value = '绑定成功！'
    setTimeout(() => {
      void router.replace({ name: 'parent-children' })
    }, 1000)
  } catch (err) {
    error.value = toMessage(err, '绑定失败，请确认手机号对应的学生账号存在')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen bg-slate-50">
    <div class="bg-gradient-to-br from-brand-500 to-brand-600 px-5 py-6 text-white">
      <h1 class="text-xl font-extrabold">绑定孩子</h1>
      <p class="mt-1 text-sm text-white/80">输入孩子的注册手机号</p>
    </div>

    <div class="px-4 pt-4">
      <div class="rounded-2xl bg-white p-5 shadow-soft">
        <div class="space-y-4">
          <div>
            <label class="mb-1 block text-xs font-semibold text-slate-500">孩子手机号</label>
            <input
              v-model="phone"
              type="tel"
              maxlength="11"
              placeholder="请输入孩子注册时的手机号"
              class="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-brand-400 focus:bg-white focus:ring-2 focus:ring-brand-100"
            >
          </div>

          <div>
            <label class="mb-1 block text-xs font-semibold text-slate-500">关系</label>
            <select
              v-model="relation"
              class="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-brand-400 focus:bg-white focus:ring-2 focus:ring-brand-100"
            >
              <option>爸爸</option>
              <option>妈妈</option>
              <option>爷爷</option>
              <option>奶奶</option>
              <option>外公</option>
              <option>外婆</option>
              <option>其他</option>
            </select>
          </div>

          <button
            type="button"
            class="w-full rounded-xl bg-brand-500 py-3 text-sm font-bold text-white shadow-soft transition active:bg-brand-600 disabled:bg-slate-200 disabled:text-slate-400"
            :disabled="!phoneValid || loading"
            @click="handleBind"
          >
            {{ loading ? '绑定中…' : '确认绑定' }}
          </button>

          <p v-if="success" class="text-center text-sm text-brand-600">{{ success }}</p>
          <p v-if="error" class="text-center text-sm text-red-500">{{ error }}</p>
        </div>
      </div>
    </div>
  </div>
</template>
