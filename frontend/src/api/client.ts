/**
 * 接口层入口。
 * 默认走真实 FastAPI 后端（/api，vite proxy -> 127.0.0.1:8080）；
 * 设 VITE_USE_MOCK=1（见 frontend/.env.development 注释）可切回纯 Mock 演示数据，
 * Mock 数据文件保留在 src/api/mock 下，页面与 store 两种模式均无需改动。
 */

/** 模拟网络延迟，让 loading 态可被感知（仅 Mock 模式使用） */
export function resolve<T>(data: T, delayMs = 120): Promise<T> {
  return new Promise((r) => setTimeout(() => r(structuredClone(data)), delayMs))
}

/** 真实后端的 baseURL（vite.config.ts 中已配置 /api proxy -> 127.0.0.1:8080） */
export const API_BASE = '/api'

/** Mock 演示模式开关：VITE_USE_MOCK=1 时为 true */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === '1'
