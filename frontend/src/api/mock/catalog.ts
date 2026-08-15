import type { Account, Brand, Site } from '@/types'

/** 账号全部使用脱敏标识，绝不出现真实邮箱/密码 */
export const mockAccounts: Account[] = [
  { id: 'acc-001', label: 'demo-na', alias: '演示北美账号', marketplaceIds: ['ATVPDKIKX0DER'] },
  { id: 'acc-002', label: 'demo-eu', alias: '演示欧洲账号', marketplaceIds: ['A1F83G8C2ARO7P', 'A1PA6795UKMFR9'] },
  { id: 'acc-003', label: 'demo-mx', alias: '演示墨西哥账号', marketplaceIds: ['A1AM78C64UM0Y8'] },
]

export const mockSites: Site[] = [
  { code: 'US', marketplaceId: 'ATVPDKIKX0DER', name: '美国站' },
  { code: 'UK', marketplaceId: 'A1F83G8C2ARO7P', name: '英国站' },
  { code: 'DE', marketplaceId: 'A1PA6795UKMFR9', name: '德国站' },
  { code: 'MX', marketplaceId: 'A1AM78C64UM0Y8', name: '墨西哥站' },
]

export const mockBrands: Brand[] = [
  { name: 'DEMO_HOME', packReady: true, lastUsedAt: '2026-08-09T14:22:00Z' },
  { name: 'DEMO_FLEX', packReady: true, lastUsedAt: '2026-08-09T14:22:00Z' },
  { name: 'DEMO_ALPHA', packReady: true, lastUsedAt: '2026-08-07T09:10:00Z' },
  { name: 'DEMO_CASE', packReady: true, lastUsedAt: '2026-08-07T09:10:00Z' },
  { name: 'DEMO_TECH', packReady: false, lastUsedAt: null },
  { name: 'DEMO_BEAM', packReady: true, lastUsedAt: '2026-08-05T16:40:00Z' },
  { name: 'DEMO_IOTA', packReady: true, lastUsedAt: '2026-08-03T11:05:00Z' },
  { name: 'DEMO_ARC', packReady: false, lastUsedAt: null },
]
