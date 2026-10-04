// 用户会话 store (localStorage key 与旧版完全一致, 老用户数据无缝衔接)
// 「记住我」→ token 存 localStorage(30 天, 重开浏览器免登录)
// 不勾选   → token 存 sessionStorage(关闭浏览器标签即失效, 更安全)
import { defineStore } from 'pinia'

const SESSION_KEY = 'kuaixuan_session_v1'
const SESSION_KEY_TMP = 'kuaixuan_session_tmp'

function readSession() {
  try {
    const raw = localStorage.getItem(SESSION_KEY) || sessionStorage.getItem(SESSION_KEY_TMP) || 'null'
    return JSON.parse(raw)
  } catch (e) {
    return null
  }
}

// 旧版 key 迁移: 品牌改名(shunshi -> kuaixuan)
// 2026-10-04 ★ 这是**有时限的兼容代码**，不是永久设施：
//   品牌改名后旧用户浏览器里还留着 shunshi_session_v1；只要还有人没访问过新版，
//   这段就得在（删掉 ⇒ 那批用户被登出，需重新登录一次）。
//   迁移完的标志：上线后再无用户因缺 session 被登出 ⇒ 即可整体删掉本函数及
//   其在 state() 里的调用。**预定清理时间 2026-11-15**，已记入 docs/安卓APK封装评估-20261003.md 待办清单。
//   迁移成功后会主动 removeItem 旧 key ⇒ localStorage 里不再留老品牌痕迹。
function migrateSession() {
  try {
    if (!localStorage.getItem(SESSION_KEY) && localStorage.getItem('shunshi_session_v1')) {
      localStorage.setItem(SESSION_KEY, localStorage.getItem('shunshi_session_v1'))
    }
    // 🔴 只在新/旧 key 都存在时清理旧 key：万一上面的 setItem 因隐私模式失败，
    //    旧凭证还在，用户不至于立刻掉登录。
    if (localStorage.getItem(SESSION_KEY) && localStorage.getItem('shunshi_session_v1')) {
      localStorage.removeItem('shunshi_session_v1')
    }
  } catch (e) { /* ignore */ }
}

export const useUserStore = defineStore('user', {
  state: () => {
    migrateSession()
    const session = readSession()
    return {
      session,
      apiToken: (session && session.token) || '',
      username: (session && session.username) || '',
      isAdmin: !!(session && session.is_admin),
      expireAt: (session && session.expire_at) || 0,   // 0 = 永久会员
      expired: !!(session && session.expired),         // 已过期(非会员)
      memberLevel: (session && session.member_level) || 0   // 0=免费试用 1=付费会员 2=VIP老师
    }
  },
  getters: {
    isLoggedIn: (s) => !!s.apiToken,
    // 会员判断: 管理员永远有权限; VIP老师 永久权限; 其余要求未过期(expire_at=0 永久 或 未到到期时间)
    isMember: (s) => s.isAdmin || s.memberLevel === 2 || !s.expired,
    // 严格 VIP/付费 门禁(2026-08-17 主人需求): 仅管理员 + VIP(memberLevel=2) + 付费(memberLevel=1) 通过; 免费试用(0) 不可
    isVipOrPaid: (s) => s.isAdmin || s.memberLevel >= 1,
    // 会员等级标签
    memberLabel: (s) => s.memberLevel === 2 ? 'VIP' : s.memberLevel === 1 ? '付费会员' : (s.isAdmin ? '管理员' : '免费试用'),
    // 剩余试用天数(-1 表示永久, 仅提示用)
    memberDaysLeft: (s) => {
      if (s.isAdmin || s.memberLevel === 2 || !s.expireAt) return -1
      return Math.max(0, Math.ceil((s.expireAt - Date.now() / 1000) / 86400))
    },
    // localStorage 数据按用户隔离的 key 前缀
    poolKey: (s) => 'kuaixuan_stock_pool_' + (s.username || 'guest'),
    filterKey: (s) => 'kuaixuan_locked_filter_' + (s.username || 'guest')
  },
  actions: {
    // remember=true → localStorage(持久, 30 天); false → sessionStorage(临时会话)
    setSession(username, token, isAdmin, expireAt, expired, remember = true, memberLevel = 0) {
      this.session = { username, token, is_admin: isAdmin ? 1 : 0, expire_at: expireAt || 0, expired: expired ? 1 : 0, member_level: memberLevel || 0 }
      this.apiToken = token
      this.username = username
      this.isAdmin = !!isAdmin
      this.expireAt = expireAt || 0
      this.expired = !!expired
      this.memberLevel = memberLevel || 0
      try {
        const s = JSON.stringify(this.session)
        if (remember) {
          localStorage.setItem(SESSION_KEY, s)
          sessionStorage.removeItem(SESSION_KEY_TMP)
        } else {
          sessionStorage.setItem(SESSION_KEY_TMP, s)
          localStorage.removeItem(SESSION_KEY)
        }
      } catch (e) { /* ignore */ }
    },
    clearSession() {
      this.session = null
      this.apiToken = ''
      this.username = ''
      this.isAdmin = false
      this.expireAt = 0
      this.expired = false
      this.memberLevel = 0
      try {
        localStorage.removeItem(SESSION_KEY)
        sessionStorage.removeItem(SESSION_KEY_TMP)
      } catch (e) { /* ignore */ }
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
