// 策略股票池 store (localStorage 持久化, key 与旧版一致)
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
        if (d.autoPoolLockTime && Date.now() - d.autoPoolLockTime > LOCK_DURATION_MS) {
          localStorage.removeItem(user.poolKey)
          return
        }
        this.stockPool = d.stocks || []
        this.autoPoolLockTime = d.autoPoolLockTime || null
      } catch (e) { /* ignore */ }
    },
    syncToStorage() {
      if (!this.stockPool.length) this.autoPoolLockTime = null
      this.saveToStorage()
    },
    addStocks(stks) {
      let added = 0
      stks.forEach(s => {
        if (!this.stockPool.some(x => x.code === s.code)) {
          this.stockPool.push({
            code: s.code, name: s.name,
            bidChange: s.bidChange, probability: s.probability, confidence: s.confidence,
            addTime: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
            addTimestamp: Date.now()
          })
          added++
        }
      })
      if (added) { this.syncToStorage() }
      return added
    },
    removeStock(code) {
      this.stockPool = this.stockPool.filter(s => s.code !== code)
      this.syncToStorage()
    },
    clearAll() {
      if (!this.stockPool.length) return
      this.stockPool = []
      this.autoPoolLockTime = null
      this.saveToStorage()
    },
    checkExpiry() {
      if (this.autoPoolLockTime && this.stockPool.length && Date.now() - this.autoPoolLockTime > LOCK_DURATION_MS) {
        this.stockPool = []
        this.autoPoolLockTime = null
        this.saveToStorage()
      }
    },
    // 9:30 前自动将选股前五名收录进池(诗人需求: 前3→前5)
    autoAdd(cachedStocks, isDataCached) {
      const now = new Date()
      const h = now.getHours(), m = now.getMinutes()
      if (this.autoPoolLockTime && this.stockPool.length) {
        if (Date.now() - this.autoPoolLockTime <= LOCK_DURATION_MS) return
        this.stockPool = []
        this.autoPoolLockTime = null
        this.saveToStorage()
      }
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
