// 主选股数据 store: 缓存结果 / 筛选条件 / 锁定状态 / 账号级偏好
import { defineStore } from 'pinia'
import { fetchStocks, getDefaultFilters, getPrefs, savePrefs } from '../api/stocks'
import { listBatches } from '../api/history'
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

// 锁定名单按"当前筛选条件"过滤: 条件不允许的票直接移除(不显示)。
// 竞价/盘中两套条件语义不同:
//   · 竞价模式(auction): 只用竞价条件(剔除昨日涨停/竞价涨幅/市值/价格/竞价金额)——与后端 apply_filters 一致
//   · 盘中模式(spot):    追加实时维度(涨幅区间/量比/换手)——与后端 apply_spot_filters 一致
// 避免把盘中条件误用到竞价名单(晓程场景: 量比0.79<1 是盘中条件, 竞价模式不应据此剔除)。
function passLockedFilter(it, rt, f, mode) {
  if (f.limitUp) {
    const concept = it.concept || ''
    if (concept.includes('昨日涨停') || concept.includes('昨日连板')) return false
  }
  if (it.bidChange > f.bidGt) return false                 // 竞价涨幅过高剔除
  // 盘中模式才检查实时维度(量比/换手/涨幅区间); 竞价模式不查(后端 apply_filters 也不查)
  if (mode === 'spot' && rt) {
    const rc = rt.realChange
    if (rc !== undefined && rc !== null) {
      if (rc < f.chgFloor || rc > f.chgGt) return false    // 实时涨幅区间
    }
    if (f.volRatioFloor > 0 && rt.volRatio < f.volRatioFloor) return false  // 量比下限
    if (f.turnoverFloor > 0 && rt.turnover < f.turnoverFloor) return false  // 换手下限
    if (f.turnoverGt > 0 && rt.turnover > f.turnoverGt) return false        // 换手上限
  }
  if (it.circulationMV < f.floatMvFloor) return false      // 市值过小
  if (it.circulationMV > f.floatMvGt) return false         // 市值过大
  const price = rt ? rt.price : it.price
  if (price && price > f.priceGt) return false             // 股价过高
  if (it.bidAmt < f.bidAmtFloor) return false              // 竞价金额过低
  return true
}

// 盘中实时模式筛选参数(独立于竞价)
// 默认值基于盘中评分表满分区间 + 全市场实测(约72只候选):
//   涨幅3~9.5%(健康区间,评分满分档) 量比>=2(显著放量) 换手2~20%(活跃度)
export const defaultSpotFilterSettings = {
  stSuspend: true,
  markets: ['hs', 'cyb', 'kcb'],
  limitUp: true,
  spotExcludeZT: false,     // 不剔除已涨停(默认保留涨停股, 看封单强度)
  chgFloor: 3,              // 实时涨幅下限(去弱势, 3%以下启动信号弱)
  chgGt: 9.5,               // 实时涨幅上限(高于此归涨停股, 由 spotExcludeZT 控制)
  volRatioFloor: 2,         // 量比下限(显著放量确认)
  turnoverFloor: 2,         // 换手率下限(活跃度)
  turnoverGt: 20,           // 换手率上限(防出货)
  probLt: 60,
  confLt: 60,
  floatMvFloor: 20,
  floatMvGt: 1000,
  priceGt: 300
}

export const useStocksStore = defineStore('stocks', {
  state: () => ({
    cachedStocks: [],
    isDataCached: false,
    realTimeRefreshUsed: false,
    before930: true,
    // 当前模式: auction(竞价) / spot(盘中实时)
    mode: 'auction',
    // 盘中实时结果(独立缓存, 避免切换模式互相覆盖)
    spotStocks: [],
    isSpotCached: false,
    // 当前工作筛选条件
    filterSettings: { ...defaultFilterSettings },
    // 全局默认筛选参数(管理员后台可调), 未自定义偏好的用户使用
    globalDefaults: null,
    // 盘中筛选条件
    spotFilterSettings: { ...defaultSpotFilterSettings },
    // 账号级筛选偏好(后端 users 表, 跨设备一致)
    userFilterPrefs: null,
    isFilterLocked: false
  }),
  actions: {
    // ---- 筛选参数 ----
    buildFilterParams() {
      if (this.mode === 'spot') return this.buildSpotFilterParams()
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

    // ---- 盘中筛选参数 ----
    buildSpotFilterParams() {
      const f = this.spotFilterSettings
      return {
        stSuspend: f.stSuspend ? '1' : '0',
        limitUp: f.limitUp ? '1' : '0',
        markets: f.markets.join(','),
        chgFloor: f.chgFloor,
        chgGt: f.chgGt,
        volRatioFloor: f.volRatioFloor,
        turnoverFloor: f.turnoverFloor,
        turnoverGt: f.turnoverGt,
        spotExcludeZT: f.spotExcludeZT ? '1' : '0',
        probLt: f.probLt,
        confLt: f.confLt,
        floatMvFloor: f.floatMvFloor,
        floatMvGt: f.floatMvGt,
        priceGt: f.priceGt
      }
    },

    // ---- 模式切换 ----
    setMode(m) {
      if (m !== 'auction' && m !== 'spot') return
      this.mode = m
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
    // 全局默认筛选参数(管理员后台可调): 未自定义偏好的用户使用
    async loadGlobalDefaults() {
      try {
        const d = await getDefaultFilters()
        if (d.defaults && typeof d.defaults === 'object') {
          this.globalDefaults = { ...defaultFilterSettings, ...d.defaults }
        }
      } catch (e) { /* 忽略, 用内置默认 */ }
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
      // 默认基准: 管理员后台配置的全局默认(优先) > 前端内置默认
      const base = this.globalDefaults || { ...defaultFilterSettings }
      if (locked && locked.settings) {
        this.isFilterLocked = true
        this.filterSettings = { ...locked.settings }
      } else if (this.userFilterPrefs) {
        this.filterSettings = { ...base, ...this.userFilterPrefs }
      } else {
        this.filterSettings = { ...base }
      }
    },

    // ---- 竞价锁定名单快照(9:30 后保持名单不变, 只更新实时行情) ----
    // lock(9:30前)时保存完整名单; 9:30 后 refresh/筛选 都基于此名单 merge 实时数据,
    // 避免"早盘跌出/午后复现"等名单漂移(竞价结论应恒定)
    snapshotKey() {
      const user = useUserStore()
      return 'kuaixuan_bid_snapshot_' + (user.username || 'guest') + '_' + bjDateStr()
    },
    saveBidSnapshot(list) {
      try {
        // 完整名单 + 竞价专属结论(qiangchou/bidRatio/accel)
        localStorage.setItem(this.snapshotKey(), JSON.stringify(list || []))
      } catch (e) { /* ignore */ }
    },
    loadBidSnapshot() {
      try {
        const raw = localStorage.getItem(this.snapshotKey())
        return raw ? JSON.parse(raw) : null
      } catch (e) { return null }
    },
    // 9:30 后: 锁定名单 + 全市场实时行情(spotMap) 合并。
    // 关键语义修正: 用"当前筛选条件"对锁定名单重新过滤——
    //   · 被当前条件剔除(如勾了剔除昨日涨停, 而该票是昨日涨停) → 直接移除, 不显示
    //   · 条件放行但行情不在榜 → 保留 + 标"已跌出"
    // 避免"涨停票却显示已跌出"的荒谬现象(剔除条件 ≠ 行情跌出)。
    // 权威名单优先取"当天 lock 批次"(后端落库), 失败退回本地快照。
    async mergeSpotIntoLocked(spotList, spotMap, filterSettings) {
      let locked = null
      let hasLockBatch = false
      try {
        locked = await this.loadLockedBatchFromServer()
        hasLockBatch = Array.isArray(locked) && locked.length > 0
      } catch (e) { /* 后端失败则退回本地 */ }
      // 只有"当天确实 lock 过"才用本地快照兜底; 否则快照可能混入 filter 结果, 导致假"已跌出"
      if (!hasLockBatch) {
        const snap = this.loadBidSnapshot()
        if (Array.isArray(snap) && snap.length) locked = snap   // 新格式完整名单
      }
      if (!Array.isArray(locked) || !locked.length) {
        // 无锁定名单(9:30 后首次打开且当天未 lock) → 实时名单(标 snapshot)
        return this.applyBidSnapshot(spotList || [])
      }
      const listMap = {}
      ;(spotList || []).forEach((s) => { listMap[s.code] = s })
      const fs = filterSettings || this.filterSettings
      const out = []
      locked.forEach((it) => {
        // 1) 当前筛选条件不允许 → 直接移除(哈药场景: 剔除昨日涨停)
        if (!passLockedFilter(it, (spotMap || {})[it.code], fs, this.mode)) return
        const lt = listMap[it.code]              // 在过滤名单里 → 有完整实时评分
        const rt = (spotMap || {})[it.code]      // 全市场实时行情
        if (lt) {
          // 2) 在榜: 更新实时字段 + 实时评分
          out.push({
            ...it,
            realChange: lt.realChange, entityChange: lt.entityChange,
            probability: lt.probability, confidence: lt.confidence,
            price: lt.price, _snapshot: true, _offline: false
          })
          return
        }
        if (rt) {
          // 3) 条件放行但行情不在榜(真跌出) → 保留 + 标记
          out.push({
            ...it,
            realChange: rt.realChange, entityChange: rt.entityChange,
            volRatio: rt.volRatio, turnover: rt.turnover,
            price: rt.price, _snapshot: true, _offline: true
          })
          return
        }
        // 4) 全市场都没有(极端) → 保留, 无实时值
        out.push({
          ...it,
          realChange: null, entityChange: null,
          _staleReal: it.realChange, _staleEntity: it.entityChange,
          _snapshot: true, _offline: true
        })
      })
      return out
    },
    // 从后端读当天 lock 批次(权威锁定名单): 返回完整名单数组, 无则 []
    async loadLockedBatchFromServer() {
      const d = await listBatches()
      const batches = d.batches || []
      const today = bjDateStr()
      // 找当天最新一条 action=lock 的批次(后端按 ts 倒序, 第一条最新)
      const b = batches.find((x) => x.action === 'lock' && x.batch_date === today)
      if (!b) return []
      const detail = await listBatches(b.id)
      return (detail.stocks || []).map((s) => ({
        code: s.code, name: s.name,
        probability: s.probability, confidence: s.confidence,
        bidChange: s.bid_change, realChange: s.real_change,
        entityChange: s.entity_change, warnType: s.warn_type,
        bidAmt: s.bid_amt, bidRatio: s.bid_ratio,
        circulationMV: s.circulation_mv, industry: s.industry,
        concept: s.concept, rank: s.rank
      }))
    },
    applyBidSnapshot(list) {
      const snap = this.loadBidSnapshot()
      if (!snap) return list
      // 兼容旧格式(dict: code -> {qiangchou,bidRatio,accel}) 与 新格式(完整名单数组)
      if (Array.isArray(snap)) {
        // 新格式: 名单本身即锁定名单, 直接用(9:30 后无锁定名单时退化用实时名单)
        return (list || []).map((it) => {
          const s = snap.find((x) => x.code === it.code)
          if (s) return { ...it, qiangchou: s.qiangchou, bidRatio: s.bidRatio, accel: s.accel, _snapshot: true }
          return it
        })
      }
      return (list || []).map((it) => {
        const s = snap[it.code]
        if (s) return { ...it, qiangchou: s.qiangchou, bidRatio: s.bidRatio, accel: s.accel, _snapshot: true }
        return it
      })
    },

    // ---- 数据操作 ----
    async fetchAndCache() {
      if (this.isDataCached) return
      // 9:30 前锁定最新竞价数据(落库); 9:30 后保持锁定名单, 只更新实时行情
      const action = isBefore930() ? 'lock' : 'refresh'
      const data = await fetchStocks(action, this.buildFilterParams())
      if (action === 'lock') {
        this.saveBidSnapshot(data.list)          // 保存完整竞价锁定名单(含抢筹结论)
        this.cachedStocks = data.list
      } else {
        // 9:30 后: 锁定名单不变, 只把实时行情 merge 进名单(防"早盘跌出/午后复现")
        this.cachedStocks = await this.mergeSpotIntoLocked(data.list, data.spotMap, this.filterSettings)
      }
      this.isDataCached = true
      this.before930 = data.before930
      this.realTimeRefreshUsed = false
      showToast('✅ 选股完成', 'success')
    },
    async updateRealTimeOnly() {
      if (!this.isDataCached) { await this.fetchAndCache(); return }
      const data = await fetchStocks('refresh', this.buildFilterParams())
      // 刷新实时涨幅: 基于当前列表更新实时字段, 不回到锁定名单
      // (改过筛选条件后点刷新, 应在当前新名单上更新, 而不是跳回早上 lock 的名单)
      const listMap = {}
      ;(data.list || []).forEach((s) => { listMap[s.code] = s })
      const sm = data.spotMap || {}
      this.cachedStocks = (this.cachedStocks || []).map((it) => {
        const lt = listMap[it.code]
        const rt = sm[it.code]
        if (lt) {
          return { ...it, realChange: lt.realChange, entityChange: lt.entityChange,
                   probability: lt.probability, confidence: lt.confidence, price: lt.price, _offline: false }
        }
        if (rt) {
          return { ...it, realChange: rt.realChange, entityChange: rt.entityChange,
                   volRatio: rt.volRatio, turnover: rt.turnover, price: rt.price, _offline: true }
        }
        return { ...it, _offline: true }
      })
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
    async     applyCustomFilter() {
      if (this.mode === 'spot') return this.applySpotFilter()
      if (this.isFilterLocked) {
        showToast(' 筛选条件已锁定，无法手动应用', 'error')
        return
      }
      const data = await fetchStocks('filter', this.buildFilterParams())
      // 筛选重算 = 按当前条件重新筛, 直接用后端新名单(不是锁定名单)
      // 锁定名单恒定仅用于"刷新实时涨幅", 不影响筛选重算(否则改条件永远同一批票)
      this.cachedStocks = data.list || []
      this.isDataCached = true
      this.before930 = data.before930
      this.saveUserPrefs()
      showToast('✅ 筛选条件已更新', 'success')
    },

    // ---- 盘中实时模式 ----
    async fetchSpot() {
      if (this.isSpotCached) return
      const data = await fetchStocks('refresh', this.buildFilterParams(), 'spot')
      this.spotStocks = data.list || []
      this.isSpotCached = true
      this.before930 = data.before930
      showToast('✅ 盘中选股完成', 'success')
    },
    async updateSpotRealTime() {
      const data = await fetchStocks('refresh', this.buildFilterParams(), 'spot')
      this.spotStocks = data.list || []
      this.isSpotCached = true
      this.before930 = data.before930
      this.realTimeRefreshUsed = true
      showToast('✅ 实时刷新完成', 'success')
    },
    async applySpotFilter() {
      const data = await fetchStocks('refresh', this.buildFilterParams(), 'spot')
      this.spotStocks = data.list || []
      this.isSpotCached = true
      this.before930 = data.before930
      this.saveUserPrefs()
      showToast('✅ 盘中筛选条件已更新', 'success')
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
