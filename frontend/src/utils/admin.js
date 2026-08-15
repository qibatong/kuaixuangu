// 管理端工具函数(纯函数, 可单测)

// expireState: 用户会员到期状态 → 'forever'(永久) | 'expired'(已过期) | 'active'(有效)
export function expireState(u) {
  if (!u || !u.expire_at) return 'forever'
  return Date.now() / 1000 > u.expire_at ? 'expired' : 'active'
}
