/**
 * Vite 配置（AI数学教练 V4.1 学生端）。
 *
 * 启动：npm install && npm run dev  → http://localhost:5174
 * 所有 /api/* 请求由 dev server 代理到后端 FastAPI（http://localhost:8000），
 * 前端不直接持有任何 AI Key。
 */
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: true,
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
