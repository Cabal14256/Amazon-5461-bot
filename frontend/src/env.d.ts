/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** =1 时前端走纯 Mock 演示数据（见 frontend/.env.development） */
  readonly VITE_USE_MOCK?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
