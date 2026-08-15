// 后端返回无时区的本地时间（"2026-08-10 14:36:12" 或带 T），
// 浏览器对这类字符串的解析不一致（可能按 UTC），统一按本地时间显式构造。
export function parseServerTime(t: string): Date | null {
  const m = t.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/)
  if (m && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(t)) {
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] ?? 0))
  }
  const d = new Date(t)
  return Number.isNaN(d.getTime()) ? null : d
}
