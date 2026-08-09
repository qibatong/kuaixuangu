// 主选股数据 store: 缓存结果 / 筛选条件 / 锁定状态 / 账号级偏好
import { defineStore } from 'pinia'
import { fetchStocks, getPrefs, savePrefs } from '../api/stocks'
import { showToast } from '../utils/toast'
import { isBefore930 } from '../utils/time'
import { useUserStore } from './user'

function bjDateStr() {
  const d = new Date(Date.now() + 8 * 3600 * 1000)
  return d.toISOString().slice(0, 10)
}

export const defaultFilterSettings = {
  stSuspend: true,
  markets: ['hs', 'cyb', 'kcb'],
  limitUp: true,
  bidGt: 7,
  probLt: 65,
  confLt: 65,
  floatMvFloor: 30,
  floatMvGt: 1000,
  priceGt: 300,
  bidAmtFloor: 3000
}

export const useStocksStore = defineStore('stocks', {
  state: () => ({
    cachedStocks: [],
    isDataCached: false,
    realTimeRefreshUsed: false,
    before930: true,
    // 当前工作筛选条件
    filterSettings: { ...defaultFilterSettings },
    // 账号级筛选偏好(后端 users 表, 跨设备一致)
    userFilterPrefs: null,
    isFilterLocked: false
  }),
  actions: {
    // ---- 筛选参数 ----
    buildFilterParams() {
      const f = this.filterSettings
      return {
        stSuspend: f.stSuspend ? '1' : '0',
        limitUp: f.limitUp ? '1' : '0',
        markets: f.markets.join(','),
        bidGt: f.bidGt,
        probLt: f.probLt,
        confLt: f.confLt,
        floatMvFloor: f.floatMvFloor,
        floatMvGt: f.floatMvGt,
        priceGt: f.priceGt,
        bidAmtFloor: f.bidAmtFloor
      }
    },

    // ---- 账号级偏好 ----
    async loadUserPrefs() {
      try {
        const data = await getPrefs()
        if (data.settings && typeof data.settings === 'object') {
          this.userFilterPrefs = data.settings
        }
      } catch (e) { /* 忽略, 用默认 */ }
    },
    saveUserPrefs() {
      try { savePrefs(this.filterSettings).catch(() => {}) } catch (e) { /* ignore */ }
    },

    // ---- 锁定逻辑 (localStorage, key 与旧版一致) ----
    loadLockedFilter() {
      const user = useUserStore()
      try {
        const raw = localStorage.getItem(user.filterKey)
        if (!raw) return null
        const data = JSON.parse(raw)
        if (data && data.locked && data.settings) return data
        return null
      } catch (e) { return null }
    },
    saveLockedFilter() {
      const user = useUserStore()
      try {
        localStorage.setItem(user.filterKey, JSON.stringify({ locked: true, settings: this.filterSettings }))
      } catch (e) { /* ignore */ }
    },
    clearLockedFilter() {
      const user = useUserStore()
      try { localStorage.removeItem(user.filterKey) } catch (e) { /* ignore */ }
    },

    // ---- 初始化筛选状态 ----
    initFilterFromStorage() {
      const locked = this.loadLockedFilter()
      if (locked && locked.settings) {
        this.isFilterLocked = true
        this.filterSettings = { ...locked.settings }
      } else if (this.userFilterPrefs) {
        this.filterSettings = { ...defaultFilterSettings, ...this.userFilterPrefs }
      } else {
        this.filterSettings = { ...defaultFilterSettings }
      }
    },

    // ---- 竞价结论快照(9:30 后保留抢筹标记) ----
    // lock(9:30前)时保存 竞价结论; refresh(9:30后)时用快照覆盖 qiangchou/bidRatio,
    // 避免收盘数据把"竞价抢筹"结论冲掉
    snapshotKey() {
      const user = useUserStore()
      return 'kuaixuan_bid_snapshot_' + (user.username || 'guest') + '_' + bjDateStr()
    },
    saveBidSnapshot(list) {
      try {
        const snap = {}
        ;(list || []).forEach((it) => {
          snap[it.code] = { qiangchou: it.qiangchou ? 1 : 0, bidRatio: it.bidRatio, accel: it.accel }
        })
        localStorage.setItem(this.snapshotKey(), JSON.stringify(snap))
      } catch (e) { /* ignore */ }
    },
    loadBidSnapshot() {
      try {
        const raw = localStorage.getItem(this.snapshotKey())
        return raw ? JSON.parse(raw) : null
      } catch (e) { return null }
    },
    applyBidSnapshot(list) {
      const snap = this.loadBidSnapshot()
      if (!snap) return list
      return (list || []).map((it) => {
        const s = snap[it.code]
        if (s) return { ...it, qiangchou: s.qiangchou, bidRatio: s.bidRatio, accel: s.accel, _snapshot: true }
        return it
      })
    },

    // ---- 数据操作 ----
    async fetchAndCache() {
      if (this.isDataCached) return
      // 9:30 前锁定最新竞价数据(落库); 9:30 后拉取/更新实时数据
      const action = isBefore930() ? 'lock' : 'refresh'
      const data = await fetchStocks(action, this.buildFilterParams())
      let list = data.list
      if (action === 'lock') {
        this.saveBidSnapshot(list)          // 保存竞价抢筹结论
      } else {
        list = this.applyBidSnapshot(list)  // 9:30 后保留竞价结论
      }
      this.cachedStocks = list
      this.isDataCached = true
      this.before930 = data.before930
      this.realTimeRefreshUsed = false
      showToast('✅ 选股完成', 'success')
    },
    async updateRealTimeOnly() {
      if (!this.isDataCached) { await this.fetchAndCache(); return }
      const data = await fetchStocks('refresh', this.buildFilterParams())
      this.cachedStocks = this.applyBidSnapshot(data.list)   // 保留竞价抢筹结论
      this.before930 = data.before930
      this.realTimeRefreshUsed = true
      showToast('✅ 实时涨幅更新完成', 'success')
    },
    async reLockData() {
      if (!isBefore930()) { showToast('❌ 9:30后禁止重新选股', 'error'); return }
      this.isDataCached = false
      this.cachedStocks = []
      this.realTimeRefreshUsed = false
      await this.fetchAndCache()
    },
    async applyCustomFilter() {
      if (this.isFilterLocked) {
        showToast(' 筛选条件已锁定，无法手动应用', 'error')
        return
      }
      const data = await fetchStocks('filter', this.buildFilterParams())
      this.cachedStocks = isBefore930() ? data.list : this.applyBidSnapshot(data.list)
      this.isDataCached = true
      this.before930 = data.before930
      this.saveUserPrefs()
      showToast('✅ 筛选条件已更新', 'success')
    },
    toggleFilterLock() {
      this.isFilterLocked = !this.isFilterLocked
      if (this.isFilterLocked) {
        this.saveLockedFilter()
        showToast(' 筛选条件已锁定，后续选股将使用此设置', 'success')
      } else {
        this.clearLockedFilter()
        showToast(' 筛选条件已解锁，可自由编辑', 'success')
      }
      this.saveUserPrefs()
    },
    resetFilterToDefault() {
      if (this.isFilterLocked) {
        showToast(' 筛选已锁定，请先解锁再重置', 'error')
        return
      }
      const locked = this.loadLockedFilter()
      if (locked && locked.settings) {
        this.filterSettings = { ...locked.settings }
        showToast('✅ 已恢复为上次锁定的条件', 'success')
      } else if (this.userFilterPrefs) {
        this.filterSettings = { ...defaultFilterSettings, ...this.userFilterPrefs }
        showToast('✅ 已恢复为你的默认筛选条件', 'success')
      } else {
        this.filterSettings = { ...defaultFilterSettings }
        showToast('✅ 筛选条件已恢复默认', 'success')
      }
      this.saveUserPrefs()
    }
  }
})
