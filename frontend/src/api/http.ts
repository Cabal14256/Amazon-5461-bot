/**
 * fetch 封装：统一 credentials、JSON 解析与错误规范化。
 * 401 时通过注册的处理器跳转 /login（由 router 模块注册，避免 http -> router 循环依赖）。
 */

import { API_BASE } from './client'

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    /** detail 为结构化对象时（如 422 preflight_blocked 的 {error, checks}）在此保留原值 */
    public rawDetail: unknown = null,
  ) {
    super(`HTTP ${status}: ${detail}`)
    this.name = 'ApiError'
  }
}

let unauthorizedHandler: (() => void) | null = null

/** 注册全局 401 处理器（跳登录页，带 redirect query） */
export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler
}

export interface ApiFetchOptions {
  method?: 'GET' | 'POST' | 'PATCH'
  body?: unknown
  query?: Record<string, string | number | null | undefined>
  /** 登录/会话探测接口的 401 由调用方处理，不触发全局跳登录页 */
  skipAuthRedirect?: boolean
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { method = 'GET', body, query, skipAuthRedirect = false } = options

  let url = `${API_BASE}${path}`
  if (query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== null && value !== undefined && value !== '') params.set(key, String(value))
    }
    const qs = params.toString()
    if (qs) url += `?${qs}`
  }

  const res = await fetch(url, {
    method,
    credentials: 'include',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }

  if (!res.ok) {
    const raw = (data as { detail?: unknown } | null)?.detail
    const detail = raw != null ? String(raw) : res.statusText || 'request_failed'
    if (res.status === 401 && !skipAuthRedirect) unauthorizedHandler?.()
    throw new ApiError(res.status, detail, raw ?? null)
  }
  return data as T
}

/** 纯文本响应变体（如 GET /api/repair-jobs/{id}/diff 返回 patch.diff 原文）；错误处理与 apiFetch 一致 */
export async function apiFetchText(path: string, options: ApiFetchOptions = {}): Promise<string> {
  const { method = 'GET', query, skipAuthRedirect = false } = options

  let url = `${API_BASE}${path}`
  if (query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== null && value !== undefined && value !== '') params.set(key, String(value))
    }
    const qs = params.toString()
    if (qs) url += `?${qs}`
  }

  const res = await fetch(url, { method, credentials: 'include' })
  const text = await res.text()

  if (!res.ok) {
    let detail = res.statusText || 'request_failed'
    try {
      const raw = (JSON.parse(text) as { detail?: unknown } | null)?.detail
      if (raw != null) detail = String(raw)
    } catch {
      /* 非 JSON 错误体，保留 statusText */
    }
    if (res.status === 401 && !skipAuthRedirect) unauthorizedHandler?.()
    throw new ApiError(res.status, detail)
  }
  return text
}
