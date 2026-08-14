// 用户会话 store (localStorage key 与旧版完全一致, 老用户数据无缝衔接)
import { defineStore } from 'pinia'

const SESSION_KEY = 'kuaixuan_session_v1'

// 旧版 key 迁移: 品牌改名(shunshi -> kuaixuan)
function migrateSession() {
  try {
    if (!localStorage.getItem(SESSION_KEY) && localStorage.getItem('shunshi_session_v1')) {
      localStorage.setItem(SESSION_KEY, localStorage.getItem('shunshi_session_v1'))
    }
  } catch (e) { /* ignore */ }
}

export const useUserStore = defineStore('user', {
  state: () => {
    migrateSession()
    let session = null
    try { session = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null') } catch (e) { session = null }
    return {
      session,
      apiToken: (session && session.token) || '',
      username: (session && session.username) || '',
      isAdmin: !!(session && session.is_admin),
      expireAt: (session && session.expire_at) || 0,   // 0 = 永久会员
      expired: !!(session && session.expired)          // 已过期(非会员)
    }
  },
  getters: {
    isLoggedIn: (s) => !!s.apiToken,
    // 会员判断: 管理员永远有权限; 其余要求未过期(expire_at=0 永久 或 未到到期时间)
    isMember: (s) => s.isAdmin || !s.expired,
    // 剩余试用天数(-1 表示永久, 仅提示用)
    memberDaysLeft: (s) => {
      if (s.isAdmin || !s.expireAt) return -1
      return Math.max(0, Math.ceil((s.expireAt - Date.now() / 1000) / 86400))
    },
    // localStorage 数据按用户隔离的 key 前缀
    poolKey: (s) => 'kuaixuan_stock_pool_' + (s.username || 'guest'),
    filterKey: (s) => 'kuaixuan_locked_filter_' + (s.username || 'guest')
  },
  actions: {
    setSession(username, token, isAdmin, expireAt, expired) {
      this.session = { username, token, is_admin: isAdmin ? 1 : 0, expire_at: expireAt || 0, expired: expired ? 1 : 0 }
      this.apiToken = token
      this.username = username
      this.isAdmin = !!isAdmin
      this.expireAt = expireAt || 0
      this.expired = !!expired
      try { localStorage.setItem(SESSION_KEY, JSON.stringify(this.session)) } catch (e) { /* ignore */ }
    },
    clearSession() {
      this.session = null
      this.apiToken = ''
      this.username = ''
      this.isAdmin = false
      this.expireAt = 0
      this.expired = false
      try { localStorage.removeItem(SESSION_KEY) } catch (e) { /* ignore */ }
    },
    // 旧 key 迁移: 改名前的股票池/锁定条件数据
    migrateLegacyKeys() {
      try {
        const user = this.username || 'guest'
        const oldPool = 'shunshi_stock_pool_' + user
        const oldFilter = 'shunshi_locked_filter_' + user
        if (!localStorage.getItem(this.poolKey) && localStorage.getItem(oldPool)) {
          localStorage.setItem(this.poolKey, localStorage.getItem(oldPool))
        }
        if (!localStorage.getItem(this.filterKey) && localStorage.getItem(oldFilter)) {
          localStorage.setItem(this.filterKey, localStorage.getItem(oldFilter))
        }
      } catch (e) { /* ignore */ }
    }
  }
})
