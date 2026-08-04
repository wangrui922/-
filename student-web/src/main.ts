/**
 * 学生端入口：createApp + router + mount。
 *
 * 启动命令：npm install && npm run dev（dev server 已把 /api 代理到 :8000）
 */
import { createApp } from 'vue'

import App from '@/App.vue'
import router from '@/router'
import '@/styles/tailwind.css'

createApp(App).use(router).mount('#app')
