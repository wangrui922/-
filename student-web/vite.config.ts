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
    // 试用：允许任意外部域名（localhost.run / cpolar 等穿透地址）访问 dev server。
    // 仅 dev 生效，生产构建走 Nginx/EdgeOne，不受影响。
    allowedHosts: true,
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // 关闭构建前的自动清空输出目录：本机安全删除拦截（safe-delete）对中文/空格路径
    // 处理异常会误报失败，关闭后由 vite 直接覆盖写入，便于本地 CI 跑通 build。
    emptyOutDir: false,
  },
})
