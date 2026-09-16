// 主选股数据 store: 缓存结果 / 筛选条件 / 锁定状态 / 账号级偏好
import { defineStore } from 'pinia'
import { fetchStocks, fetchQuotes, getDefaultFilters, getPrefs, savePrefs } from '../api/stocks'
import { fetchPickerSnapshot } from '../api/picker'
import { listBatches } from '../api/history'
import { showToast } from '../utils/toast'
import { isBefore930, isPickBlockedTime, PICK_BLOCK_MSG_TIME } from '../utils/time'
import { useUserStore } from './user'
import { defaultFilterSettings, passLockedFilter, pickFromSnapshot,
         buildFilterParams as _buildFilterParams } from '../utils/filters'

// 兼容导出(历史引用方): 默认筛选参数
export { defaultFilterSettings }

function bjDateStr() {
  const d = new Date(Date.now() + 8 * 3600 * 1000)
  return d.toISOString().slice(0, 10)
}

// 盘中实时模式筛选参数(独立于竞价)
// 默认值基于盘中评分表满分区间 + 全市场实测(约72只候选):
//   涨幅3~9.5%(健康区间,评分满分档) 量比>=2(显著放量) 换手2~20%(活跃度)

export const useStocksStore = defineStore('stocks', {
  state: () => ({
    cachedStocks: [],
    isDataCached: false,
    realTimeRefreshUsed: false,
    before930: true,
    // 2026-08-18: 当前名单是否来自 9:26 系统统一批次(auto_applied)
    // 统一批次: 不过滤/不剔除, 所有用户看到同一份完整名单
    isAutoAppliedList: false,
    // 选股策略: auction(竞价)。2026-09-09 盘中实时(spot)已下线, 前端无入口、后端零调用
    strategy: 'auction',
    // 当前工作筛选条件
    filterSettings: { ...defaultFilterSettings },
    // 全局默认筛选参数(管理员后台可调), 未自定义偏好的用户使用
    globalDefaults: null,
    // 盘中筛选条件
    // 账号级筛选偏好(后端 users 表, 跨设备一致)
    userFilterPrefs: null,
    isFilterLocked: false,
    // 2026-09-12 P3: 当日全市场预计算快照(本地筛选用)。懒加载: 首次点「应用」才拉,
    // 拿不到(接口未开/物化表不可用/非 VIP)就静默回退后端筛选, 60s 内不重试。
    snapshot: null,
    snapshotDate: '',
    snapshotFailTs: 0,
    // 2026-08-25: 偏好/全局默认异步加载完成前为 false, 防止 FilterPanel 先用内置默认(limitUp=true)
    // 渲染勾选、随后被用户偏好(limitUp=false)覆盖导致"先勾选后取消"闪烁
    filterReady: false,
    // 2026-09-16 选股闸门(主人拍板: 开盘日 9:00-9:26 不支持选股)。
    // true = 当前被拦(9:00-9:26, 或后端回 blocked=当日定格未落库); msg 供 UI 展示。
    // 由 StockView 的闸门定时器在解禁后自动清除并重新选股(见 refreshPickGate)。
    pickBlocked: false,
    pickBlockedMsg: ''
  }),
  actions: {
    // ---- 筛选参数(盘中/竞价共用 filterSettings) ----
    buildFilterParams() {
      return _buildFilterParams(this.filterSettings)
    },

    // ---- 策略切换(2026-09-09 命名消歧: setMode → setStrategy) ----
    setStrategy(m) {
      if (m !== 'auction') return   // 2026-09-09: spot(盘中实时)已下线
      this.strategy = m
    },

    // ---- 2026-09-16 选股闸门(9:00-9:26 不支持选股) ----
    markPickBlocked(msg) {
      this.pickBlocked = true
      this.pickBlockedMsg = msg || PICK_BLOCK_MSG_TIME
    },
    clearPickBlocked() {
      this.pickBlocked = false
      this.pickBlockedMsg = ''
    },
    /**
     * 闸门定时器调用(StockView 每 20s 一次)。
     * 目的: 9:26 到点**自动解禁** —— 否则用户在 9:10 打开页面会一直停在禁用态,
     * 必须手动刷新才行。解禁后又可能撞上"当日定格尚未落库"(后端 blocked),
     * 此时 fetchAndCache 会重新标记, 下一轮再试, 天然形成等待重试。
     * @returns {boolean} 是否处于禁用态
     */
    async refreshPickGate() {
      if (!isPickBlockedTime()) {
        // 时间维已开放: 若当前仍被标记(时间维到点 / 后端快照维), 触发一次选股
        if (this.pickBlocked) {
          this.clearPickBlocked()
          if (!this.isDataCached) {
            try { await this.fetchAndCache() } catch (e) { /* 下一轮重试 */ }
          }
        }
        return this.pickBlocked
      }
      this.markPickBlocked(PICK_BLOCK_MSG_TIME)
      return true
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
    // 2026-08-25: 运行时迁移(语义反转: limitUp/stSuspend 旧=剔除, 新=只看).
    //   后端 DB 的迁移在 init_db 时完成; localStorage 值是按用户旧偏好保存的旧语义,
    //   需做一次取反. 以 mig_filter_sem_flip_v2 标记(存 localStorage)为幂等守卫.
    loadLockedFilter() {
      const user = useUserStore()
      try {
        const raw = localStorage.getItem(user.filterKey)
        if (!raw) return null
        const data = JSON.parse(raw)
        if (!(data && data.locked && data.settings)) return null
        const migKey = 'mig_filter_sem_flip_v2'
        // 标记与锁定数据绑定: 按 user 粒度, 避免多用户共享同一标记
        const userMigKey = migKey + '_' + (user.username || 'guest')
        if (!localStorage.getItem(userMigKey)) {
          let changed = false
          const s = data.settings
          for (const k of ['limitUp', 'stSuspend']) {
            if (typeof s[k] === 'boolean') {
              s[k] = !s[k]
              changed = true
            }
          }
          if (changed) {
            try {
              localStorage.setItem(user.filterKey, JSON.stringify({ ...data, settings: s }))
              data.settings = s
            } catch (e) { /* 写入失败也继续用取反后的内存值 */ }
          }
          try { localStorage.setItem(userMigKey, '1') } catch (e) {}
        }
        return data
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
      // 2026-08-25: 偏好/默认已就位, 允许 FilterPanel 渲染最终勾选状态(避免先勾选后取消闪烁)
      this.filterReady = true
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
    // 9:30 后: 锁定名单 + 实时行情合并(2026-09-05 起行情按需走 /api/quotes, 不再收全市场 spotMap)。
    // 关键语义修正: 用"当前筛选条件"对锁定名单重新过滤——
    //   · 被当前条件剔除(如勾了剔除昨日涨停, 而该票是昨日涨停) → 直接移除, 不显示
    //   · 条件放行但行情不在榜 → 保留 + 标"已跌出"
    // 避免"涨停票却显示已跌出"的荒谬现象(剔除条件 ≠ 行情跌出)。
    // 权威名单优先取"当天 lock 批次"(后端落库), 失败退回本地快照。
    async mergeSpotIntoLocked(spotList, filterSettings) {
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
        // 无任何锁定名单(自动应用失败 / 新号 / 过期等)且已过 9:30:
        // 首次结果冻结为当日快照, 之后刷新页面只更新实时行情、不再换名单,
        // 避免每次打开列表都随实时名单漂移(满足"9:30 后名单固定, 重选需等次日")。
        const first = spotList || []
        this.saveBidSnapshot(first)
        return first
      }
      const listMap = {}
      ;(spotList || []).forEach((s) => { listMap[s.code] = s })
      const fs = filterSettings || this.filterSettings
      // 2026-09-05 B 方案: /api/stocks 不再下发全市场 spotMap。唯二需要实时价的场景是
      // 1) 在榜票(lt=listMap[code] 后端已内联实时价); 2) 跌出但保留显示的锁定票(rt)。
      // 故只对"不在返回名单(实时榜)的锁定 code"按需调 /api/quotes 拉少量实时价。
      const missing = locked.map((it) => it.code).filter((c) => !listMap[c])
      let rtMap = {}
      if (missing.length) {
        try {
          const qres = await fetchQuotes(missing)
          rtMap = (qres && qres.quotes) || {}
        } catch (e) { /* 行情取失败: 跌出票退化为无实时值, 下轮自愈 */ rtMap = {} }
      }
      const quoteOf = (code) => listMap[code] || rtMap[code]   // 在榜取完整 item, 跌出取行情
      const out = []
      locked.forEach((it) => {
        const rt = quoteOf(it.code)
        if (!locked.autoApplied && !passLockedFilter(it, rt, fs)) return
        const lt = listMap[it.code]              // 在过滤名单里 → 有完整实时评分
        if (lt) {
          // 2) 在榜: 更新实时字段 + 实时评分
          out.push({
            ...it,
            realChange: lt.realChange, entityChange: lt.entityChange,
            probability: lt.probability, confidence: lt.confidence,
            price: lt.price, _snapshot: true
          })
          return
        }
        if (rt) {
          // 3) 条件放行但行情不在榜(真跌出) → 保留(2026-08-18 主人澄清:
          //    只去掉"已跌出实时榜"标签, 股票要保留显示)
          out.push({
            ...it,
            realChange: rt.realChange, entityChange: rt.entityChange,
            volRatio: rt.volRatio, turnover: rt.turnover,
            price: rt.price, _snapshot: true
          })
          return
        }
        // 4) 全市场都没有(极端) → 保留, 无实时值
        out.push({
          ...it,
          realChange: null, entityChange: null,
          _staleReal: it.realChange, _staleEntity: it.entityChange,
          _snapshot: true
        })
      })
      this.isAutoAppliedList = !!locked.autoApplied
      return out
    },
    // 从后端读当天 lock 批次(权威锁定名单): 返回完整名单数组, 无则 []
    async loadLockedBatchFromServer() {
      const d = await listBatches()
      const batches = d.batches || []
      const today = bjDateStr()
      // 优先级: 用户当天手动锁定批次 > 9:26 系统统一批次
      // 2026-08-18: 9:26 自动应用后所有人看到同一份统一结果(auto_applied 兜底)
      // 2026-08-22 主人需求: 9:30 后刷新页面必须保留用户当天**手动锁定**名单,
      // 不被统一批次覆盖(名单固定, 符合"9:30 后仅更新实时行情、不重选")。
      // 故手动 lock 批次优先; 仅当日未手动锁定时才用统一批次保证一致性。
      const userLock = batches.find((x) => x.action === 'lock' && x.batch_date === today && !x.auto_applied)
      const autoB = batches.find((x) => x.auto_applied && x.batch_date === today)
      const isAuto = !userLock && !!autoB
      const b = userLock || autoB
      if (!b) return []
      const detail = await listBatches(b.id)
      const stocks = (detail.stocks || []).map((s) => ({
        code: s.code, name: s.name,
        probability: s.probability, confidence: s.confidence,
        bidChange: s.bid_change, realChange: s.real_change,
        entityChange: s.entity_change, warnType: s.warn_type,
        bidAmt: s.bid_amt, bidRatio: s.bid_ratio,
        circulationMV: s.circulation_mv, industry: s.industry,
        concept: s.concept, rank: s.rank,
        qiangchou: s.qiangchou,   // 2026-09-01: 锁定名单回看也显示抢筹标(落库保存)
        // 2026-09-09: 抢筹细分(类型+幅度)同样落库, 回看时还原, 不随最新交易日串味
        qcType: s.qcType || '', qcAmt: s.qcAmt ?? null, qcChg: s.qcChg ?? null,
        qcLast: s.qcLast ?? null, qcText: s.qcText || '', qcFallback: s.qcFallback || 0
      }))
      // 标记是否系统统一批次(9:26 自动应用): 统一批次不随用户筛选条件过滤, 保证全用户一致。
      // 仅当实际命中 auto_applied 批次(isAuto)时才为 true; 命中手动锁定批次则为 false(需按条件过滤)
      stocks.autoApplied = isAuto
      return stocks
    },
    // ---- 数据操作 ----
    async fetchAndCache(force = false) {
      if (this.isDataCached) return
      // 2026-09-16 选股闸门: 交易日 9:00-9:26 直接不发请求(后端另有时间+快照双闸门兜底)
      if (isPickBlockedTime()) { this.markPickBlocked(PICK_BLOCK_MSG_TIME); return }
      // 9:30 前锁定最新竞价数据(落库); 9:30 后保持锁定名单, 只更新实时行情
      // force=true: 主动重锁(绕过当日幂等); 页面加载自动 lock 不带 force → 后端幂等直读
      const action = isBefore930() ? 'lock' : 'refresh'
      let data
      try {
        data = await fetchStocks(action, this.buildFilterParams(), 'auction', force && action === 'lock')
      } catch (e) {
        // 后端快照维拦截(≥9:26 但当日 9:25 定格尚未落库): 标记等待, 不算失败
        if (e && e.blocked) { this.markPickBlocked(e.msg || PICK_BLOCK_MSG_TIME); return }
        throw e
      }
      this.clearPickBlocked()
      if (action === 'lock') {
        this.saveBidSnapshot(data.list)          // 保存完整竞价锁定名单(含抢筹结论)
        this.cachedStocks = data.list
      } else {
        // 2026-09-05 主人需求(多用户反馈): 休市/当日无批次时, 后端已回退**最近交易日**
        // 同参批次直读(= 关闭前选出的名单, 服务端已完成实时行情覆盖) → 直接采用,
        // 不再走 mergeSpotIntoLocked(其 loadLockedBatchFromServer 只查当日 lock 会落空)。
        if (data.reusedDate) {
          this.cachedStocks = data.list || []
          this.isDataCached = true
          this.before930 = data.before930
          this.realTimeRefreshUsed = false
          showToast('✅ 已载入 ' + data.reusedDate + ' 的选股名单', 'success')
          return
        }
        // 2026-09-05 修复: 用户刚点「应用」(filter)改了筛选 → 后端 refresh 直读命中的正是
        // 该 filter 批次(source='filter'), data.list 即当前筛选的权威名单(含实时覆盖, 与后端
        // 直读路径 merge 一致)。必须直接采用 data.list, 否则 mergeSpotIntoLocked 会按更早的
        // **锁定批次**重建名单、丢弃用户刚应用的 filter 名单(如勾选"昨涨停"后 filter 含该票,
        // 但 lock 批次早先按未勾生成 → 刷新后票凭空消失)。
        // 仅当后端按 lock/auto 批次返回(source≠'filter')时, 才回退到"锁定名单不变, 只 merge 实时"
        // 的定格语义。
        if (data.source === 'filter') {
          this.cachedStocks = data.list || []
        } else {
          // 9:30 后: 锁定名单不变, 只把实时行情 merge 进名单(防"早盘跌出/午后复现")
          this.cachedStocks = await this.mergeSpotIntoLocked(data.list, this.filterSettings)
        }
      }
      this.isDataCached = true
      this.before930 = data.before930
      this.realTimeRefreshUsed = false
      // 2026-09-02 当日幂等: 后端命中当日同参锁定批次时 idempotent=true → 提示已载入不误导用户
      showToast(data.idempotent ? '✅ 已载入今日锁定名单' : '✅ 选股完成', 'success')
    },
    async updateRealTimeOnly({ silent = false } = {}) {
      this.realTimeRefreshUsed = true
      if (!this.isDataCached) { await this.fetchAndCache(); return }
      const data = await fetchStocks('refresh', this.buildFilterParams())
      // 刷新实时涨幅: 基于当前列表更新实时字段, 不回到锁定名单
      // (改过筛选条件后点刷新, 应在当前新名单上更新, 而不是跳回早上 lock 的名单)
      const listMap = {}
      ;(data.list || []).forEach((s) => { listMap[s.code] = s })
      // 2026-09-05 B 方案: /api/stocks 不再下发全市场 spotMap。仅对当前列表里"不在返回名单"
      // 的跌出票按需拉实时价; 在榜票用 lt(后端已内联实时价)。
      const missing = (this.cachedStocks || []).map((it) => it.code).filter((c) => !listMap[c])
      let rtMap = {}
      if (missing.length) {
        try {
          const qres = await fetchQuotes(missing)
          rtMap = (qres && qres.quotes) || {}
        } catch (e) { rtMap = {} }
      }
      // 2026-08-18 主人澄清: 跌出实时榜的**保留显示**(只去标签), 名单不减少
      this.cachedStocks = (this.cachedStocks || []).map((it) => {
        const lt = listMap[it.code]
        const rt = rtMap[it.code]

        if (lt) {
          return { ...it, realChange: lt.realChange, entityChange: lt.entityChange,
                   probability: lt.probability, confidence: lt.confidence, price: lt.price }
        }
        if (rt) {
          return { ...it, realChange: rt.realChange, entityChange: rt.entityChange,
                   volRatio: rt.volRatio, turnover: rt.turnover, price: rt.price }
        }
        return it    // 全市场都没有: 保留原值
      })
      this.before930 = data.before930
      this.realTimeRefreshUsed = true
      if (!silent) showToast('✅ 实时涨幅更新完成', 'success')
    },
    async reLockData() {
      if (!isBefore930()) { showToast('❌ 9:30后禁止重新选股', 'error'); return }
      // 2026-09-16 选股闸门(与"9:30后禁止重选"同为时段规则)
      if (isPickBlockedTime()) {
        this.markPickBlocked(PICK_BLOCK_MSG_TIME)
        showToast('⏳ ' + PICK_BLOCK_MSG_TIME, 'error')
        return
      }
      this.isDataCached = false
      this.cachedStocks = []
      this.realTimeRefreshUsed = false
      // force=true: 用户主动点「锁定」→ 绕过当日幂等, 强制重算并落新批次
      await this.fetchAndCache(true)
    },
    // ---- 2026-09-12 P3: 本地筛选(全市场预计算快照) ----
    /**
     * 懒加载当日全市场评分快照。true = 可用(可本地筛选)。
     *
     * 为什么懒加载而不是首屏加载: 快照约 0.5MB, 首屏多一次大响应会拖慢首页且
     * 在接口未开启时纯属浪费(多数请求会落空)。用户点「应用」时才需要它。
     * 失败(未开启 / 物化表不可用 / 非 VIP 403 / 网络)一律静默回退后端筛选,
     * 并记 60s 冷却 —— 否则用户连点几次「应用」会打出一串注定失败的请求。
     */
    async loadSnapshot() {
      if (this.snapshot && this.snapshot.length) return true
      if (Date.now() - this.snapshotFailTs < 60000) return false
      try {
        const d = await fetchPickerSnapshot()
        if (d && d.enabled && Array.isArray(d.list) && d.list.length) {
          this.snapshot = d.list
          this.snapshotDate = d.date || ''
          return true
        }
        this.snapshotFailTs = Date.now()
        return false
      } catch (e) {
        this.snapshotFailTs = Date.now()
        return false
      }
    },

    /**
     * 本地名单补实时行情(现价/现涨/实体/量比/换手)。
     * 拿不到行情的票保留定格值(现价退化为定格竞价价), **不写 0** ——
     * "未知"必须与"实测 0"区分(P0-3 同一原则), 前端显示「—」。
     */
    async _attachQuotes(rows) {
      if (!rows.length) return rows
      let q = {}
      try {
        const res = await fetchQuotes(rows.map((r) => r.code))
        q = (res && res.quotes) || {}
      } catch (e) { q = {} }
      return rows.map((it) => {
        const rt = q[it.code]
        if (!rt) return { ...it, price: it.auctionPrice ?? null }
        return {
          ...it,
          name: rt.name || it.name,
          price: rt.price ?? it.auctionPrice ?? null,
          realChange: rt.realChange ?? null,
          entityChange: rt.entityChange ?? null,
          volRatio: rt.volRatio ?? null,
          turnover: rt.turnover ?? null
        }
      })
    },
    async applyCustomFilter() {
      if (this.isFilterLocked) {
        showToast(' 筛选条件已锁定，无法手动应用', 'error')
        return
      }
      // 2026-09-16 选股闸门: 禁用时段不允许应用(按钮已置灰, 此处为兜底)
      if (isPickBlockedTime()) {
        this.markPickBlocked(PICK_BLOCK_MSG_TIME)
        showToast('⏳ ' + PICK_BLOCK_MSG_TIME, 'error')
        return
      }
      // 2026-09-12 P3: 快照在手 → **本地筛选**(改条件秒出, 零网络往返; 实时价另拉一次)。
      // 拿不到快照(未开启/物化表不可用/非 VIP) → 走原后端筛选路径, 行为与 P3 前一致。
      if (await this.loadSnapshot()) {
        const picked = pickFromSnapshot(this.snapshot, this.filterSettings)
        const list = await this._attachQuotes(picked)
        this.cachedStocks = list
        this.isDataCached = true
        this.saveUserPrefs()
        showToast('⚡ 本地筛选完成（' + list.length + ' 只）', 'success')
        return
      }
      const data = await fetchStocks('filter', this.buildFilterParams())
      // 筛选重算 = 按当前条件重新筛, 直接用后端新名单(不是锁定名单)
      // 锁定名单恒定仅用于"刷新实时涨幅", 不影响筛选重算(否则改条件永远同一批)
      // 2026-09-07: slice 一份新引用 + 立即赋值(双保险触发响应); 之前 nextTick 里赋值
      // 已切到同步(实测 this.$nextTick 在 Pinia store 中不存在 → 上版本报错
      // "this.$nextTick is not a function")。console.log 便于复现核对长度与 code
      const newList = (data.list || []).slice()
      console.log('[filter] cachedStocks len=', newList.length,
                  '| 前 3:', newList.slice(0, 3).map(s => s.code))
      this.cachedStocks = newList
      this.isDataCached = true
      this.before930 = data.before930
      this.saveUserPrefs()
      // 2026-09-07: toast 带条数(主人反馈"点应用没更新股池"——可据此判断后端是否生效)
      showToast('✅ 筛选条件已更新（' + newList.length + ' 只）', 'success')
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
    async resetFilterToDefault() {
      if (this.isFilterLocked) {
        showToast(' 筛选已锁定，请先解锁再重置', 'error')
        return
      }
      // 2026-09-05(主人两次确认): 重置 = 恢复**管理员后台设置的全局默认**参数 + 立即重新选股。
      // 旧实现只改表单不重选, 且恢复到'上次锁定/保存'条件(当前已等于保存值时'点了没变化'),
      // 用户多次反馈'重置按钮没效果'; 后改为内置默认, 主人再确认应跟管理员后台参数一致,
      // 故优先 globalDefaults(后端 getDefaultFilters, StockView 初始化已加载), 未有则回内置默认。
      this.filterSettings = { ...(this.globalDefaults || defaultFilterSettings) }
      try {
        await this.applyCustomFilter()   // 内部按 strategy 分支(auction→filter 重算 / spot→refresh), 自带成功 toast
      } catch (e) {
        showToast('已恢复默认条件, 重新选股失败', 'error')
      }
    }
  }
})
