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
      isAdmin: !!(session && session.is_admin)
    }
  },
  getters: {
    isLoggedIn: (s) => !!s.apiToken,
    // localStorage 数据按用户隔离的 key 前缀
    poolKey: (s) => 'kuaixuan_stock_pool_' + (s.username || 'guest'),
    filterKey: (s) => 'kuaixuan_locked_filter_' + (s.username || 'guest')
  },
  actions: {
    setSession(username, token, isAdmin) {
      this.session = { username, token, is_admin: isAdmin ? 1 : 0 }
      this.apiToken = token
      this.username = username
      this.isAdmin = !!isAdmin
      try { localStorage.setItem(SESSION_KEY, JSON.stringify(this.session)) } catch (e) { /* ignore */ }
    },
    clearSession() {
      this.session = null
      this.apiToken = ''
      this.username = ''
      this.isAdmin = false
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
