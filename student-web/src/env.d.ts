/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 生产构建时注入的后端基址（如 https://api.xxx.com）。未设置则走相对 /api（开发代理）。 */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'

  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}
