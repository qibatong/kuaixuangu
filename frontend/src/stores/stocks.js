// 主选股数据 store: 缓存结果 / 筛选条件 / 锁定状态 / 账号级偏好
import { defineStore } from 'pinia'
import { fetchStocks, fetchQuotes, getDefaultFilters, getPrefs, savePrefs,
         pingStocks, fetchStocksSpot, pingStocksSpot } from '../api/stocks'
import { fetchPickerSnapshot } from '../api/picker'
import { listBatches } from '../api/history'
import { showToast } from '../utils/toast'
import { isBefore930, isBeforeRelockEnd, isPickBlockedTime, isPickGateOn,
         PICK_BLOCK_MSG_TIME, todayBj } from '../utils/time'
import { useUserStore } from './user'
import { safeJsonGet, safeJsonSet, safeRemove } from '../utils/storage'
import { logFront } from '../utils/logger'
import { defaultFilterSettings, passLockedFilter, pickFromSnapshot,
         buildFilterParams as _buildFilterParams,
         defaultSpotFilterSettings, buildSpotFilterParams as _buildSpotFilterParams,
         passSpotFilter } from '../utils/filters'

// 兼容导出(历史引用方): 默认筛选参数
export { defaultFilterSettings, defaultSpotFilterSettings }

/**
 * @typedef {Object} StockItem 选股名单条目(后端 /api/stocks 返回, 前端实时 merge 后的形状)。
 * 字段按后端落库列 + 前端实时覆盖字段归类, 新增列时先在此登记, 避免靠记忆。
 * @property {string} code            股票代码(6 位)
 * @property {string} name            名称
 * @property {number} [probability]   评分(0-100, 降序为主排序)
 * @property {number} [confidence]    置信度
 * @property {number} [bidChange]     竞价涨幅(%)
 * @property {number} [bidAmt]        竞价额(元)
 * @property {number} [bidRatio]      竞价抢筹比
 * @property {number} [circulationMV] 流通市值(元)
 * @property {number} [warnType]      异动类型(0=无)
 * @property {number} [realChange]    实时涨幅(%)
 * @property {number} [entityChange]  实体涨幅(%)
 * @property {number} [volRatio]      量比
 * @property {number} [turnover]      换手率(%)
 * @property {number} [price]         现价(元)
 * @property {number} [mainNet]       盘中主力净额(元, 2026-09-20 起)
 * @property {string} [industry]      行业
 * @property {string} [concept]       概念
 * @property {number} [rank]          排名
 */

// 2026-09-20 性能优化: 原地更新辅助 —— 字段值真正变化才赋值。
// 用于 updateRealTimeOnly 等高频轮询路径: 保留对象引用(避免全表 patch + 重排),
// 仅在字段变化时触发最小粒度响应式更新(消除高频刷新抖动)。
function _assignIf(obj, key, val) {
  if (val !== undefined && obj[key] !== val) obj[key] = val
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
    // 选股策略: auction(竞价) / spot(盘中实时)。
    // 2026-09-09 spot 曾随重构下线(整链零调用); 2026-09-28 v4.11.75 重建。
    strategy: 'auction',
    // 当前工作筛选条件
    filterSettings: { ...defaultFilterSettings },
    // ---- 盘中实时(spot)独立状态: 2026-09-28 v4.11.75 ----
    // 🔴 为什么必须有独立字段、不能复用 cachedStocks:
    //   cachedStocks 承载的是**竞价定格名单**(9:25 落库/可锁定/可回放/进自选池/被推送)。
    //   spot 是"此刻的答案" —— 不落批次、不锁定、不推送、下一次请求就变。
    //   两者混用会让「顶栏冻结标注条 / 自选池自动收录 / 历史回看」全部语义错乱。
    spotStocks: [],
    spotCached: false,
    spotLoading: false,
    // 盘中筛选条件(与竞价 filterSettings 分开存, 互不覆盖)
    spotFilterSettings: { ...defaultSpotFilterSettings },
    // 盘中是否可用(探测端点; null=未探测)
    spotAvailable: null,
    // 盘中名单的数据时刻(秒级时间戳, 来自后端 dataTime)
    spotDataAt: 0,
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
    // 2026-09-16 选股闸门(主人拍板: 开盘日竞价时段不支持选股)。
    // 🔴 2026-09-30 口径修正: 原注释写"9:00-9:26"与常量不符 —— 实际拦截段 =
    //   `PICK_BLOCK_FROM ~ PICK_BLOCK_TO` = **09:15:00 ~ 09:26:30**(utils/time.js;
    //   09:15 = 竞价开始, 09:26:30 = 当日 9:25 定格首采时刻)。
    //   **09:00 那一刀是另一件事**: 属 serve_date 的"逻辑交易日起点"(09:00 起当日没数据
    //   也不许回退上一交易日), 它**不拦选股**, 只决定"看哪一天的数"。
    // true = 当前被拦(09:15:00~09:26:30, 或后端回 blocked=当日定格未落库); msg 供 UI 展示。
    // 由 StockView 的闸门定时器在解禁后自动清除并重新选股(见 refreshPickGate)。
    pickBlocked: false,
    pickBlockedMsg: '',
    // 2026-09-17 开关联动: 后端 `pick_window_guard` 的实际值(true=闸门启用)。
    // 默认 true = 与后端默认值同口径(探测失败时保守沿用, 不会误放行)。
    // 由 _loadPickGateEnabled() 通过 /api/stocks?action=ping 探测并刷新。
    pickGateEnabled: true,
    pickGateCheckedTs: 0,      // 上次探测开关的时间(60s 节流, 避免 20s 定时器反复发请求)
    // 2026-09-18 (v4.11.29) 定格数据来源日期: 盘前/非交易日按设计仍出名单(用上一交易日
    // 9:25 定格)。原供顶栏「当前为 X 日定格数据」标注条使用, 避免被误当成当日名单
    // (主人 9/18 反馈「刷出来是昨天的数据」即这类误解)。
    // 🔴 2026-09-30 主人指令: 那条标注条**已删除** ⇒ 这两个字段当前**无人消费**。
    //   保留原因: 后端 /api/stocks 仍在下发; 将来若要恢复标注条可直接取用, 不必回头改后端。
    freezeDate: '',            // 后端 /api/stocks 返回的 freezeDate(名单所用定格日期)
    freezeIsToday: true,       // == 当日?(标注条已删 ⇒ 暂无消费方)
    // 2026-09-21 会员体系: 选股配额超限(后端 429 code=quota_exceeded) → 页面显示开通引导。
    // quotaInfo 透传 {feature, feature_label, limit, used} 供 VipGate 配额模式渲染。
    quotaExceeded: false,
    quotaInfo: null,
  }),
  actions: {
    // ---- 筛选参数(磁盘上两套: 竞价 filterSettings / 盘中 spotFilterSettings) ----
    // ★ 2026-09-28 v4.11.80 第三步: 新增**当前策略**透出 —— 锁定的「AI选股」tab 也走 spot
    //   后, 全链路(引擎/参数/前端列)都必须跟着 `this.strategy` 走, 不能各写一处。
    //   ⚠️ 语义辨析: 这里是"选股策略"(auction/spot), 与"左视图 tab" 是两个维度 ——
    //     tab1「AI选股」与 tab2「盘中实时」都可以是 spot(见 StockView.switchTab)。
    get isSpotStrategy() {
      return this.strategy === 'spot'
    },
    buildFilterParams() {
      return _buildFilterParams(this.filterSettings)
    },
    // 当前策略对应的筛选参数 —— 唯一分支点, 避免"传了 spot 参数却用竞价门槛"。
    buildActiveFilterParams() {
      return this.isSpotStrategy ? this.buildSpotFilterParams() : this.buildFilterParams()
    },

    // ---- 策略切换(2026-09-09 命名消歧: setMode → setStrategy) ----
    // 2026-09-28 v4.11.75: 放开 spot —— 后端已重建独立端点 /api/stocks_spot。
    setStrategy(m) {
      if (m !== 'auction' && m !== 'spot') return
      this.strategy = m
    },

    // ---- 盘中实时(spot) ----
    // 2026-09-28 v4.11.75 新增。与竞价链路**严格隔离**:
    //   · 不落批次 / 不推送 / 不参与 9:26 定格 —— 后端该端点本身就不做这些。
    //   · **不受 pick_window_guard 闸门限制** —— 盘中选股本就是"看当下",
    //     09:15:00~09:26:30 那套"当日定格尚未产生"的理由对 spot 不成立。
    //   · **不做本地快照预筛**(不像竞价 P3 的 pickFromSnapshot): 竞价用的是 9:25 定格、
    //     全天恒定; spot 的现涨/量比/换手每次请求都在变, 本地缓存立刻过期 ⇒ 必须真打网络。
    buildSpotFilterParams() {
      return _buildSpotFilterParams(this.spotFilterSettings)
    },

    /** 探测盘中选股是否可用(不受交易时段限制)。失败 → 保持可重试(false)。 */
    async loadSpotAvailable() {
      try {
        const d = await pingStocksSpot()
        this.spotAvailable = !!(d && d.available)
      } catch (e) {
        this.spotAvailable = false
      }
      return this.spotAvailable
    },

    /**
     * 拉取实时动态选股名单。
     * @param {boolean} [silent] true = 不弹成功 toast(30s 轮询用)
     * @returns {Promise<number>} 名单条数
     */
    async fetchSpotList({ silent = false } = {}) {
      this.spotLoading = true
      try {
        const data = await fetchStocksSpot('filter', this.buildSpotFilterParams())
        if (!data || data.ok === false) {
          // 后端如实报错(如行情取数失败) → 抛出交给调用方提示, 不静默吞
          throw new Error((data && data.msg) || '盘中选股失败')
        }
        this.spotStocks = (data.list || []).slice()
        this.spotCached = true
        this.spotDataAt = data.dataTime || Math.floor(Date.now() / 1000)
        this.spotAvailable = true
        if (!silent) showToast('✅ 实时动态选股完成（' + this.spotStocks.length + ' 只）', 'success')
        return this.spotStocks.length
      } finally {
        this.spotLoading = false
      }
    },

    /** 重置盘中筛选条件到默认(不改竞价的那份)。 */
    async resetSpotFilterToDefault() {
      this.spotFilterSettings = { ...defaultSpotFilterSettings }
      return this.fetchSpotList()
    },

    /**
     * 本地预筛(纯计算, 不发请求) —— 给 UI 做"预期条数"提示用。
     * ⚠️ 只是乐观预估: 真名单以 fetchSpotList 的后端返回为准。
     * 判据与后端 apply_spot_filters 同口径(见 utils/filters.passSpotFilter)。
     */
    previewSpotCount() {
      return (this.spotStocks || []).filter((it) => passSpotFilter(it, this.spotFilterSettings)).length
    },

    // ---- 2026-09-16 选股闸门(交易日 09:15:00~09:26:30 不支持选股) ----
    markPickBlocked(msg) {
      this.pickBlocked = true
      this.pickBlockedMsg = msg || PICK_BLOCK_MSG_TIME
    },
    clearPickBlocked() {
      this.pickBlocked = false
      this.pickBlockedMsg = ''
    },
    // ---- 2026-09-21 配额超限标记 ----
    markQuotaExceeded(info) {
      this.quotaExceeded = true
      this.quotaInfo = info || null
    },
    clearQuotaExceeded() {
      this.quotaExceeded = false
      this.quotaInfo = null
    },
    /**
     * 闸门定时器调用(StockView 每 20s 一次)。
     * 目的: ① **开关联动** —— 后端 `pick_window_guard=0` 时立即放行。此前前端置灰是
     *          纯时间判断、不看开关, 于是关开关只关了后端, 前端 9:00-9:26 依旧置灰且
     *          连自动加载都不发请求(9/17 早盘用户 9:15-9:26 完全点不动的根因);
     *       ② 9:26 到点**自动解禁** —— 否则用户在 9:10 打开页面会一直停在禁用态,
     *          必须手动刷新才行;
     *       ③ 解禁后可能撞上"当日定格尚未落库"(后端回 blocked), fetchAndCache 会重新
     *          标记, 下一轮再试, 天然形成等待重试。
     * @param {boolean} [force] 强制探测开关(首屏 init 用, 不受 60s 节流限制)
     * @returns {boolean} 是否处于禁用态
     */
    async refreshPickGate(force = false) {
      // 非闸门时段且未被标记 → 无事可做, 也不发探测请求(省流量)
      if (!force && !isPickBlockedTime() && !this.pickBlocked) return false

      const enabled = await this._loadPickGateEnabled(force)
      const blocked = enabled && isPickBlockedTime()

      if (!blocked) {
        // 已放行: 若此前被标记(开关关闭 / 时间维到点 / 后端快照维), 清除并触发一次选股
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

    /**
     * 探测后端闸门开关(`settings.pick_window_guard`)。
     * 60s 节流(定时器 20s 一轮, 不必每轮都问); force=true 绕过节流(首屏必探)。
     * 探测失败 → 沿用上次值(默认 true, 保守不误放行)。
     */
    async _loadPickGateEnabled(force = false) {
      const now = Date.now()
      if (!force && this.pickGateCheckedTs && now - this.pickGateCheckedTs < 60000) {
        return this.pickGateEnabled
      }
      try {
        const d = await pingStocks()
        if (d && typeof d.pickGateEnabled === 'boolean') {
          this.pickGateEnabled = d.pickGateEnabled
        }
        this.pickGateCheckedTs = now
      } catch (e) { /* 探测失败: 沿用上次值 */ }
      return this.pickGateEnabled
    },

    /**
     * 闸门是否正在生效(开关启用 + 处于禁用时段)。三处入口判断统一走它。
     * 语义落在 utils/time.isPickGateOn(纯函数, 有单测), 此处只做 store 取值。
     * @param {Date} [bj] 可注入"北京时间视图"的 Date(测试用), 默认取当前
     */
    _pickGateOn(bj) {
      return isPickGateOn(this.pickGateEnabled, bj)
    },

    // ---- 账号级偏好 ----
    async loadUserPrefs() {
      try {
        const data = await getPrefs()
        if (data.settings && typeof data.settings === 'object') {
          this.userFilterPrefs = data.settings
        }
      } catch (e) { logFront('warn', '账号偏好加载失败, 用默认', e) }
    },
    // 全局默认筛选参数(管理员后台可调): 未自定义偏好的用户使用
    async loadGlobalDefaults() {
      try {
        const d = await getDefaultFilters()
        if (d.defaults && typeof d.defaults === 'object') {
          this.globalDefaults = { ...defaultFilterSettings, ...d.defaults }
        }
      } catch (e) { logFront('warn', '全局默认筛选加载失败, 用内置默认', e) }
    },
    saveUserPrefs() {
      // 2026-09-28 v4.11.75: 竞价 + 盘中两套条件**合并后**存。
      //   后端 users.py 的偏好白名单本就含这 6 个盘中参数(第 636~637 行),
      //   但此前前端从未传过它们 ⇒ 白名单里的键永远是默认值。此处补上最后一段断路。
      //   ⚠️ 合并顺序: 先竞价再盘中 —— 盘中那份含 6 个专属键, 会覆盖掉竞价里同名(没有)的;
      //      共用门槛两边同值(initFilterFromStorage 已对齐), 谁覆盖都一样。
      try {
        savePrefs({ ...this.filterSettings, ...this.spotFilterSettings }).catch(() => {})
      } catch (e) { /* ignore */ }
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
      safeJsonSet(user.filterKey, { locked: true, settings: this.filterSettings })
    },
    clearLockedFilter() {
      const user = useUserStore()
      safeRemove(user.filterKey)
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
      // 2026-09-28 v4.11.75: 盘中(spot)条件**独立恢复**。
      //   共用门槛(市值/价格/评分/市场)沿用上面那份竞价的结果 —— 后端两套过滤本就复用同一批
      //   门槛, 用户不必在两处各调一次; 只有 6 个盘中专属参数取自己的偏好(缺则内置默认)。
      //   ⚠️ 顺序必须在 filterSettings 定稿之后, 否则拿到的是初始 state 里的旧值。
      const spotPrefs = this.userFilterPrefs || {}
      const spotOnly = {}
      for (const k of Object.keys(defaultSpotFilterSettings)) {
        if (spotPrefs[k] !== undefined) spotOnly[k] = spotPrefs[k]
      }
      this.spotFilterSettings = {
        ...this.filterSettings,          // 共用门槛(含 locked 用户被锁定的那套)
        ...defaultSpotFilterSettings,    // 盘中专属默认
        ...spotOnly                       // 盘中专属的账号偏好覆盖
      }
      // 2026-08-25: 偏好/默认已就位, 允许 FilterPanel 渲染最终勾选状态(避免先勾选后取消闪烁)
      this.filterReady = true
    },

    // ---- 竞价锁定名单快照(9:30 后保持名单不变, 只更新实时行情) ----
    // lock(9:30前)时保存完整名单; 9:30 后 refresh/筛选 都基于此名单 merge 实时数据,
    // 避免"早盘跌出/午后复现"等名单漂移(竞价结论应恒定)
    snapshotKey() {
      const user = useUserStore()
      return 'kuaixuan_bid_snapshot_' + (user.username || 'guest') + '_' + todayBj()
    },
    saveBidSnapshot(list) {
      // 完整名单 + 竞价专属结论(bidRatio/accel)
      safeJsonSet(this.snapshotKey(), list || [])
    },
    loadBidSnapshot() {
      return safeJsonGet(this.snapshotKey(), null)
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
            price: lt.price,
            mainNet: lt.mainNet ?? null,   // 2026-09-20: 盘中主力净额列(后端 fundflow_kp)
            _snapshot: true
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
      const today = todayBj()
      // 优先级: 用户当天手动锁定批次 > 9:26 系统统一批次
      // 2026-08-18: 9:26 自动应用后所有人看到同一份统一结果(auto_applied 兜底)
      // 2026-08-22 主人需求: 9:30 后刷新页面必须保留用户当天**手动锁定**名单,
      // 不被统一批次覆盖(名单固定, 符合"9:30 后仅更新实时行情、不重选")。
      // 故手动 lock 批次优先; 仅当日未手动锁定时才用统一批次保证一致性。
      //
      // 2026-09-18 (v4.11.29) 🔴 定格前批次**必须排除**:
      //   早于当日 9:25 定格落库时刻写入的批次, 其竞涨幅/竞价额取自**上一交易日** 9_25
      //   (当日定格尚未落库 → load_day_bid_change 自动回退), 而竞涨幅占评分权重 34%
      //   ⇒ 名单与评分双双失真。今天(9/18)主人看到的"刷出来是昨天的数据"就是
      //   09:15 锁定的 #1674 在下午被这里回显: 5 只票竞涨幅逐位 = 昨日值(黑猫 3.35,
      //   今日实为 1.00)。排除后页面走 refresh 现取, 拿到当日定格名单。
      //   定格前的名单仍可在「历史回看」里查到, 不丢。
      //   🔴 判据由后端 `freeze_ready` 给出(它才拿得到 snapshot_bid 的落库时刻) ——
      //   前端**不要**自己用 "09:25:36" 之类的固定时刻猜: 系统批次(#9_25)由落库事件
      //   触发, 实测只比落库晚 3 秒, 固定时刻会把它(合法名单)误杀。
      // ★ 2026-09-28 v4.11.80 第三步: 字段名订正 `freezeReady` → `freeze_ready`。
      //   🔴 这是**既有 bug**(早于本轮): `list_batches()` 返回的键是 **snake_case**
      //     (实测字段表: action/auto_applied/batch_date/batch_time/filters/freeze_ready/
      //      id/markets/stock_count/ts/user_id —— 后端**不做** camelCase 转换),
      //     故 `x.freezeReady` 恒为 undefined ⇒ `freezeReady(x)` 恒 false ⇒ 本函数
      //     **一直返回 []**, 前端悄悄回退本地快照(`loadBidSnapshot`)。
      //     同一判据在 utils/batches.js:37 写的就是 `b.freeze_ready`(正确) —— 两处口径
      //     此前不一致, 以正确的那处为准。
      //   ⚠️ 保留 `x.freezeReady` 作为**兼容读法**: 万一将来后端改为 camelCase(或中间层
      //     加了转换), 两种写法都能命中, 不会因一次字段名变更再次静默失效。
      const freezeReady = (x) => x.batch_date === today &&
        (x.freeze_ready === true || x.freezeReady === true)
      // ★ 2026-09-28 v4.11.80 第三步(勘察风险3): **按策略区分批次**。
      //   背景: 两个 tab 都能 lock 之后, 「今天最近一次手动 lock」可能属于另一套策略 ——
      //   若 spot 名单被回显进竞价列表(或反之), 顶栏定格标注/列语义全错。
      //   判据 = 批次 filters 里的 `_strategy` 标记(后端 save_batch 写入; 旧批次没有该键)。
      //   🔴 兼容规则必须写死在这里: **旧批次(无标记)一律视为 auction** ——
      //     该键是 v4.11.80 才引入的, 此前所有批次都是竞价产物; 若把"无标记"判成
      //     "不匹配任意策略", 老用户的当日锁定名单会**再也回显不出来**(静默退化)。
      const batchStrategy = (x) => {
        try {
          const fl = typeof x.filters === 'string' ? JSON.parse(x.filters || '{}') : (x.filters || {})
          return fl._strategy || 'auction'
        } catch (e) { return 'auction' }
      }
      const wantStrategy = this.strategy                       // 当前要取哪一套
      const userLock = batches.find((x) => x.action === 'lock' && !x.auto_applied &&
        freezeReady(x) && batchStrategy(x) === wantStrategy)
      const autoB = batches.find((x) => x.auto_applied && freezeReady(x))
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
        concept: s.concept, rank: s.rank
      }))
      // 标记是否系统统一批次(9:26 自动应用): 统一批次不随用户筛选条件过滤, 保证全用户一致。
      // 仅当实际命中 auto_applied 批次(isAuto)时才为 true; 命中手动锁定批次则为 false(需按条件过滤)
      stocks.autoApplied = isAuto
      // 2026-09-18: 回显批次同样标注定格来源 —— 命中当日定格后批次 → freezeIsToday=true;
      // 命中历史日期批次 → 原会出顶栏标注条(**该条 2026-09-30 已删**)。
      // freezeReady 已保证不会命中定格前的当日批次。
      stocks.freezeDate = b.batch_date || ''
      stocks.freezeIsToday = (b.batch_date === today)
      return stocks
    },
    // ---- 数据操作 ----
    async fetchAndCache(force = false) {
      if (this.isDataCached) return
      // 2026-09-16 选股闸门: 交易日禁用时段直接不发请求(后端另有时间+快照双闸门兜底)
      // 2026-09-17: 改判 _pickGateOn() —— 后端开关关闭时不再置灰/不再短路(即时放行)
      if (this._pickGateOn()) { this.markPickBlocked(PICK_BLOCK_MSG_TIME); return }
      // 9:30 前锁定最新竞价数据(落库); 9:30 后保持锁定名单, 只更新实时行情
      // force=true: 主动重锁(绕过当日幂等); 页面加载自动 lock 不带 force → 后端幂等直读
      // 2026-09-20 主人拍板「重新选股放开到10点」: 9:30-10:00 仅**主动点锁定**(force)
      // 才发 action=lock(后端重算+落库); 页面自动加载仍 refresh 直读 —— 防止打开页面
      // 就触发落库+推送。2026-09-24 上限再放宽到 **15:00(收盘)** 且**开放周末** ⇒
      // 15:00 盘后一律 refresh(后端快照池条件不再命中, 恢复拒绝重选)。
      const action = (isBefore930() || (force && isBeforeRelockEnd())) ? 'lock' : 'refresh'
      // ★ 2026-09-28 v4.11.80 第三步: strategy **不再写死 'auction'** —— 取当前策略。
      //   「AI选股」tab 现在也走 spot(主人需求「AI竞价出来数据就锁定」) ⇒ 点锁定时
      //   用 spot 引擎算名单并落批次, 之后 9:30 后照旧走 mergeSpotIntoLocked 不被洗掉。
      //   ⚠️ 参数必须与 strategy 配套(buildActiveFilterParams): 传 spot 参数配竞价门槛
      //     会让 chgGt/volRatioFloor 静默失效(后端 apply_filters 不消费它们)。
      const strategy = this.strategy
      let data
      try {
        data = await fetchStocks(action, this.buildActiveFilterParams(), strategy,
                                 force && action === 'lock')
      } catch (e) {
        // 后端快照维拦截(≥9:26 但当日 9:25 定格尚未落库): 标记等待, 不算失败
        if (e && e.blocked) { this.markPickBlocked(e.msg || PICK_BLOCK_MSG_TIME); return }
        // 配额超限(2026-09-21 会员体系): 429 code=quota_exceeded → 标记配额, 由页面显示开通引导
        if (e && (e.code === 'quota_exceeded' || e.status === 429)) {
          this.markQuotaExceeded({
            feature: e.feature || 'picker',
            feature_label: e.feature_label || '选股快照',
            limit: e.limit || 0,
            used: e.used || 0,
            reason: e.reason || '',
          })
          return
        }
        throw e
      }
      this.clearPickBlocked()
      // 2026-09-18: 记录名单所用定格日期(后端透出)。盘前/非交易日 = 上一交易日 →
      // 顶栏标注条曾据此显示(避免"上一交易日名单被当成当日名单");
      // 🔴 **该条 2026-09-30 已按主人指令删除** ⇒ 此处赋值现仅供将来恢复时取用。
      if (data.freezeDate) {
        this.freezeDate = data.freezeDate
        this.freezeIsToday = data.freezeIsToday !== false
      }
      // ★ 2026-09-28 v4.11.80 第三步: spot 名单**不回填 cachedStocks**。
      //   原因(与 state 里 spotStocks 的隔离注释同源): cachedStocks 承载竞价定格名单,
      //   是「顶栏冻结标注条 / 自选池自动收录 / 历史回看」的数据源; spot 名单混进去会让
      //   这些语义全部错乱(spot 无 bidChange, 自选池字段对不上)。
      //   ⇒ strategy=spot 时统一写 spotStocks/spotCached/spotDataAt 那一组字段。
      if (strategy === 'spot') {
        this.spotStocks = (data.list || []).slice()
        this.spotCached = true
        this.spotDataAt = data.dataTime || Math.floor(Date.now() / 1000)
        this.spotAvailable = true
        this.isDataCached = true          // 保留原语义: "本次取数已完成", 不表示这里存的是竞价名单
        this.before930 = data.before930
        this.realTimeRefreshUsed = false
        showToast(`✅ 实时动态选股名单已${action === 'lock' ? '锁定' : '更新'}（${this.spotStocks.length} 只）`, 'success')
        return
      }
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
      // ★ 2026-09-28 v4.11.80 第三步: spot 的"刷新"不是"只换现涨" —— spot 评分本身就吃
      //   实时涨幅/量比/换手, 只覆盖展示字段而不重算会得到"名单停在上一刻评分"的错误结果。
      //   ⇒ spot 直接整份重拉(走 applyCustomFilter 的 spot 分支, 与 tab2 同口径)。
      if (this.isSpotStrategy) {
        if (!silent) showToast('正在刷新实时动态选股名单…', 'success')
        await this.applyCustomFilter()
        return
      }
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
      // 2026-09-20 性能优化: 由「map 全量重建对象」改为「原地更新 + 字段级 diff」。
      //   旧实现每轮把每个 item 展开成新对象 → cachedStocks 数组引用变化 →
      //   sortedStocks 重新 sort + 全表逐格 patch, 列表上千行时 30s 刷新肉眼可见抖动。
      //   原地 mutate(数组/item 引用不变)后, Vue 只 patch 真正变化的单元格, 列表稳定不抖动。
      const cur = this.cachedStocks || []
      for (const it of cur) {
        const lt = listMap[it.code]
        const rt = rtMap[it.code]
        if (lt) {
          _assignIf(it, 'realChange', lt.realChange)
          _assignIf(it, 'entityChange', lt.entityChange)
          _assignIf(it, 'probability', lt.probability)
          _assignIf(it, 'confidence', lt.confidence)
          _assignIf(it, 'price', lt.price)
          _assignIf(it, 'mainNet', lt.mainNet)   // 2026-09-20: 盘中主力净额列
        } else if (rt) {
          _assignIf(it, 'realChange', rt.realChange)
          _assignIf(it, 'entityChange', rt.entityChange)
          _assignIf(it, 'volRatio', rt.volRatio)
          _assignIf(it, 'turnover', rt.turnover)
          _assignIf(it, 'price', rt.price)
        }
        // 全市场都没有: 保留原值(不动)
      }
      // 2026-09-20 主人拍板「10点前不再锁定」: 后端 9:30-10:00 会随盘中主力净流入
      // 动态修正评分(只加不减), 按新 probability 降序**原地**重排(数组引用不变,
      // 与上面的防抖设计兼容); 用户点了列头自定义排序时 StockTable.sortedStocks
      // 仍以 sortState 为准, 不受影响。
      if (this.cachedStocks && this.cachedStocks.length > 1) {
        this.cachedStocks.sort((a, b) => (b.probability ?? -1) - (a.probability ?? -1))
      }
      this.before930 = data.before930
      this.realTimeRefreshUsed = true
      if (!silent) showToast('✅ 实时涨幅更新完成', 'success')
    },
    async reLockData() {
      // 2026-09-20 主人拍板「ai选股放开到10点 · 10点之前不再锁定」: 重新选股(锁定)
      // 截止由 9:30 放宽到 10:00; 2026-09-24 再放宽到 **15:00(收盘)** 并**开放周末**
      // (isBeforeRelockEnd, 与后端 api/stocks 快照池 lock 条件同口径)。
      if (!isBeforeRelockEnd()) { showToast('❌ 15:00后禁止重新选股', 'error'); return }
      // 2026-09-16 选股闸门(与"9:30后禁止重选"同为时段规则)
      // 2026-09-17: 改判 _pickGateOn()(开关关闭时即时放行)
      // ★ 2026-09-28: spot 跳过闸门(看实时, 与 9:25 定格无关; 与后端同口径)
      if (!this.isSpotStrategy && this._pickGateOn()) {
        this.markPickBlocked(PICK_BLOCK_MSG_TIME)
        showToast('⏳ ' + PICK_BLOCK_MSG_TIME, 'error')
        return
      }
      this.isDataCached = false
      // spot 名单存 spotStocks(见 fetchAndCache 分流注释), 清空时按策略清对应那份
      if (this.isSpotStrategy) {
        this.spotStocks = []
        this.spotCached = false
      } else {
        this.cachedStocks = []
      }
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
        logFront('warn', '本地快照不可用, 回退后端筛选', e)
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
      // 2026-09-17: 改判 _pickGateOn()(开关关闭时即时放行)
      // ★ 2026-09-28 v4.11.80 第三步: spot 策略**跳过闸门** —— 闸门理由是"当日 9:25
      //   定格尚未落库", spot 看实时、不依赖定格, 该理由不成立(与后端 `strategy != spot`
      //   跳闸门同口径)。FilterPanel.apply 的 spot 分支走 fetchSpotList, 这里是第二道入口
      //   (重置按钮等), 必须一并放行, 否则会出现"参数行是盘中、点重置却被拦"。
      const isSpot = this.isSpotStrategy
      if (!isSpot && this._pickGateOn()) {
        this.markPickBlocked(PICK_BLOCK_MSG_TIME)
        showToast('⏳ ' + PICK_BLOCK_MSG_TIME, 'error')
        return
      }
      // 2026-09-12 P3: 快照在手 → **本地筛选**(改条件秒出, 零网络往返; 实时价另拉一次)。
      // 拿不到快照(未开启/物化表不可用/非 VIP) → 走原后端筛选路径, 行为与 P3 前一致。
      // ⚠️ spot **不走本地快照**: 快照是 9:25 定格(全天恒定), 而 spot 的现涨/量比/换手
      //   每次请求都在变, 本地筛会立刻过期(见 state 里 spotStocks 的注释)。
      if (!isSpot && await this.loadSnapshot()) {
        const picked = pickFromSnapshot(this.snapshot, this.filterSettings)
        const list = await this._attachQuotes(picked)
        this.cachedStocks = list
        this.isDataCached = true
        this.saveUserPrefs()
        showToast('⚡ 本地筛选完成（' + list.length + ' 只）', 'success')
        return
      }
      const data = await fetchStocks('filter', this.buildActiveFilterParams(), this.strategy)
      if (isSpot) {
        // spot 结果进 spotStocks(与 fetchAndCache 同口径), 不碰竞价 cachedStocks
        this.spotStocks = (data.list || []).slice()
        this.spotCached = true
        this.spotDataAt = data.dataTime || Math.floor(Date.now() / 1000)
        this.spotAvailable = true
        this.isDataCached = true
        this.before930 = data.before930
        this.saveUserPrefs()
        showToast('✅ 盘中筛选条件已更新（' + this.spotStocks.length + ' 只）', 'success')
        return
      }
      // 筛选重算 = 按当前条件重新筛, 直接用后端新名单(不是锁定名单)
      // 锁定名单恒定仅用于"刷新实时涨幅", 不影响筛选重算(否则改条件永远同一批)
      // 2026-09-07: slice 一份新引用 + 立即赋值(双保险触发响应); 之前 nextTick 里赋值
      // 已切到同步(实测 this.$nextTick 在 Pinia store 中不存在 → 上版本报错
      // "this.$nextTick is not a function")
      const newList = (data.list || []).slice()
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
