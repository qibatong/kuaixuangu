// 自选股票池 store (localStorage 持久化, key 与旧版一致)
import { defineStore } from 'pinia'
import { useUserStore } from './user'

const LOCK_DURATION_MS = 10 * 60 * 60 * 1000   // 10 小时防刷新锁定

export const usePoolStore = defineStore('pool', {
  state: () => ({
    stockPool: [],
    autoPoolLockTime: null
  }),
  actions: {
    saveToStorage() {
      const user = useUserStore()
      try {
        localStorage.setItem(user.poolKey, JSON.stringify({ stocks: this.stockPool, autoPoolLockTime: this.autoPoolLockTime }))
      } catch (e) { /* ignore */ }
    },
    loadFromStorage() {
      const user = useUserStore()
      try {
        const r = localStorage.getItem(user.poolKey)
        if (!r) return
        const d = JSON.parse(r)
        const lockTime = d.autoPoolLockTime || null
        const stocks = d.stocks || []
        // 锁过期 (>10h): 无论池是否为空, 都清除存储项 (下一次 autoAdd 可正常自动收录)
        if (lockTime && Date.now() - lockTime > LOCK_DURATION_MS) {
          localStorage.removeItem(user.poolKey)
          this.stockPool = []
          this.autoPoolLockTime = null
          return
        }
        // 正常加载 (含用户手动清空后的"空池+锁"状态)
        this.stockPool = stocks
        this.autoPoolLockTime = lockTime
      } catch (e) { /* ignore */ }
    },
    syncToStorage() {
      // 注意: 不要在空池时清空 autoPoolLockTime, 因为 clearAll() 可能设了手动锁定
      this.saveToStorage()
    },
    addStocks(stks) {
      let added = 0
      stks.forEach(s => {
        if (!this.stockPool.some(x => x.code === s.code)) {
          this.stockPool.push({
            code: s.code, name: s.name,
            bidChange: s.bidChange, probability: s.probability, confidence: s.confidence,
            addTime: new Date().toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' }),
            addTimestamp: Date.now()
          })
          added++
        }
      })
      if (added) {
        // 用户手动添加股票 → 解除"手动清空"锁定, 允许下次自动收录
        this.autoPoolLockTime = null
        this.syncToStorage()
      }
      return added
    },
    removeStock(code) {
      this.stockPool = this.stockPool.filter(s => s.code !== code)
      this.syncToStorage()
    },
    clearAll() {
      if (!this.stockPool.length && !this.autoPoolLockTime) return
      this.stockPool = []
      // 关键: 设置锁定时间, 阻止 autoAdd 在用户手动清空后 10 小时内自动恢复自选
      this.autoPoolLockTime = Date.now()
      this.saveToStorage()
    },
    checkExpiry() {
      // 池非空且锁过期 → 清空自动收录的池 (用户手动清空的情况: 池空+锁有效, 不触发)
      if (this.autoPoolLockTime && this.stockPool.length && Date.now() - this.autoPoolLockTime > LOCK_DURATION_MS) {
        this.stockPool = []
        this.autoPoolLockTime = null
        this.saveToStorage()
      }
      // 池空但锁过期 (用户手动清空已超10小时) → 解锁, 允许下次自动收录
      if (this.autoPoolLockTime && !this.stockPool.length && Date.now() - this.autoPoolLockTime > LOCK_DURATION_MS) {
        this.autoPoolLockTime = null
        this.saveToStorage()
      }
    },
    // 9:30 前自动将选股前五名收录进池(诗人需求: 前3→前5)
    autoAdd(cachedStocks, isDataCached) {
      const now = Date.now()
      const h = new Date().getHours(), m = new Date().getMinutes()

      // 锁定检查: 若有锁定时间
      if (this.autoPoolLockTime) {
        if (this.stockPool.length) {
          // 池非空: 锁仍有效 → 不自动改
          if (now - this.autoPoolLockTime <= LOCK_DURATION_MS) return
          // 锁过期: 清空并解锁, 准备下一轮自动收录
          this.stockPool = []
          this.autoPoolLockTime = null
          this.saveToStorage()
        } else {
          // 池为空但有锁定时间 → 用户手动清空过
          if (now - this.autoPoolLockTime <= LOCK_DURATION_MS) {
            // 锁定仍有效 → 不自动恢复, 直接返回
            return
          }
          // 锁过期 → 允许下次自动收录
          this.autoPoolLockTime = null
        }
      }

      // 9:30 前自动收录
      if (h < 9 || (h === 9 && m < 30)) {
        if (isDataCached && cachedStocks.length) {
          const top = cachedStocks.slice(0, 5)
          if (top.length && !this.stockPool.length) {
            this.addStocks(top)
            this.autoPoolLockTime = Date.now()
            this.syncToStorage()
          }
        }
      }
    }
  }
})
