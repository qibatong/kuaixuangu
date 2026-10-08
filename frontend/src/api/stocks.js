// 选股相关 API
import { request } from './request'
import { useUserStore } from '../stores/user'

// 2026-09-09 命名消歧: 策略参数 mode → strategy(与后端同步)。
// 语义: 选股策略(auction=竞价因子表 / spot=盘中实时因子表), 与内部时段 PickMode 无关。
export function fetchStocks(action, filterParams, strategy = 'auction', force = false) {
  // force=true: 主动重锁(绕过当日幂等, 9:25 后同参自动 lock 会直读当日批次)
  return request('/api/stocks', {
    query: { action, strategy, ...(force ? { force: 1 } : {}), ...filterParams }
  })
}

// 2026-09-17: 选股闸门开关探测。
// 背景: 前端置灰原为**纯时间判断**, 不看后端开关 → `pick_window_guard=0` 只关了后端,
// 前端 9:00-9:26 仍置灰、连自动加载都不发请求(9/17 早盘该时段 0 请求的根因)。
// ping 走鉴权之后 / 闸门之前, 天然不受闸门影响, 是状态探测的最佳落点。
export function pingStocks() {
  return request('/api/stocks', { query: { action: 'ping', strategy: 'auction' } })
}

// 2026-09-28 v4.11.75: 盘中实时选股(spot) —— **独立端点** /api/stocks_spot。
//
// 为什么不走 fetchStocks(..., 'spot') 那个 strategy 参数:
//   后端**有意**没把 spot 挂回 /api/stocks?strategy=spot —— 那个路由已长到 71K，
//   内含 9:26 定格 / pick_window_guard / 当日幂等等**只对竞价成立**的逻辑，
//   复用会要求每个分支重判"这逻辑对 spot 适用吗"，极易漏判。故后端新开了独立端点，
//   前端也走独立函数，语义一一对应。
//
// 与竞价的关键差异(调这个接口前必须知道):
//   · **无 9:26 闸门** —— 盘中随时可调。竞价那套 pickBlocked/09:15:00~09:26:30 置灰不适用
//     (后端该端点也没有 pick_window_guard 判断)。
//   · **不落批次、不推送、不参与定格** —— 每次都是当下重算，返回的只是"此刻的答案"。
//   · 实时字段(realChange/volRatio/turnover)每次都在变 ⇒ 前端**不做本地快照预筛**
//     (与竞价 P3 的 pickFromSnapshot 不同：竞价用的是 9:25 定格，全天恒定，可本地筛)。
//
// ★ 2026-09-28 v4.11.80 更新(上面「后端有意没把 spot 挂回 /api/stocks」已**不再成立**):
//   主人需求「AI竞价出来数据就锁定」= 让**锁定这条链路**也能用 spot 算法，于是后端已
//   放行 `/api/stocks?strategy=spot`(开关 `spot_lock_enabled`，关掉即回到仅 auction)。
//   两个入口由此分工明确、**并存不冲突**:
//     · /api/stocks_spot           —— 「看当下」：不落批次/不锁定/不推送，这份文件本函数
//     · /api/stocks?strategy=spot  —— 「要锁定」：走 lock/filter/落库/快照全套，由
//                                      fetchStocks(action, params, 'spot', force) 调用
//   两者共用同一套 spot 引擎(compute_score_spot + apply_spot_filters)，只是工艺不同。
//   ⇒ 本函数(独立端点)继续由「盘中实时」tab 使用；「AI选股」tab 若走 spot 锁定，
//     则改走 fetchStocks(..., 'spot')，**不要**把两者混成一个入口。
export function fetchStocksSpot(action, filterParams) {
  return request('/api/stocks_spot', {
    query: { action, ...filterParams }
  })
}

// 盘中实时可用性探测(不受交易时段限制, 与竞价 ping 的语义区分开)。
export function pingStocksSpot() {
  return request('/api/stocks_spot', { query: { action: 'ping' } })
}

export function stockChart(code, period = 'day') {
  return request('/api/stock/chart', { query: { code, period } })
}

// 2026-09-27: 个股详情(为什么选它) —— 评分构成拆解/题材/异动风险/历史战绩/连板。
// 与 stockChart 互补: chart 只给分时/K线, detail 给决策信息。
export function stockDetail(code) {
  return request('/api/stock/detail', { query: { code } })
}

// 2026-09-27 v4.11.63 全市场股票快速搜索(《快选股移动端追加清单》§三「🔍 跳股」)。
// 输入：代码 / 中文名片段 / 拼音首字母，三种可混用。
// 返回 { ok, q, count, list:[{ code, name, board, py }] }。
// ★ 清单原文写「纯前端、零后端改动」，但实测本仓**没有任何全市场名录接口**，
//   且拼音首字母要 GBK 编码器（前端没有）⇒ 该项落到后端 services/stock_search.py，
//   数据全部来自本地 SQLite（snapshot_bid 最新 9_25 定格，零网络）。
export function stocksSearch(q, limit = 20) {
  return request('/api/stocks/search', { query: { q, limit } })
}

// 2026-09-05 B 方案(拆分独立行情接口): /api/stocks 不再下发全市场 spotMap,
// 前端对"不在返回名单的锁定票"等少量 code 按需取实时价。codes 为 6 位代码数组。
export function fetchQuotes(codes) {
  if (!codes || !codes.length) return Promise.resolve({ ok: true, quotes: {} })
  return request('/api/quotes', { query: { codes: codes.join(',') } })
}

// 2026-09-04 prefs 双读合并: 首屏 App.useTheme.load() + StockView/PoolView.loadUserPrefs()
// 都调 getPrefs → 截图实测同一次打开请求 2 次(140ms+1.17s 撞首屏并发排队)。
// getPrefs 做 30s 记忆化 + 在途请求去重: 同一页面生命周期内仅发一次网络请求,
// 其余调用命中内存缓存(主题/筛选偏好低频变化, 30s 新鲜度足够; savePrefs 后立即失效)。
let _prefsCache = null
let _prefsTs = 0
let _prefsInflight = null
const PREFS_TTL = 30 * 1000

export function getPrefs() {
  // 2026-09-30 v4.11.83 实机体检修复: 未登录时不发请求 —— 登录页首屏 App.useTheme.load() 会先触发本函数,
  //   而 /api/prefs 需鉴权 ⇒ 实机抓包固定报一次 401(控制台一条 error, 对用户无意义)。
  //   返回空对象即可(主题/偏好有本地兜底 kuaixuan_bg, 登录后 saveSession 会再拉一次)。
  if (!useUserStore().isLoggedIn) return Promise.resolve({})
  const now = Date.now()
  if (_prefsCache && now - _prefsTs < PREFS_TTL) {
    return Promise.resolve(_prefsCache)
  }
  if (_prefsInflight) return _prefsInflight   // 并发去重: 复用同一在途请求
  _prefsInflight = request('/api/prefs')
    .then(d => { _prefsCache = d; _prefsTs = Date.now(); return d })
    .finally(() => { _prefsInflight = null })
  return _prefsInflight
}

function invalidatePrefs() {
  _prefsCache = null
  _prefsTs = 0
}

export function getDefaultFilters() {
  // 全局默认筛选参数(管理员后台可调), 未自定义偏好的用户使用
  return request('/api/prefs/defaults')
}

export function savePrefs(settings) {
  invalidatePrefs()   // 保存后清缓存, 下次 getPrefs 拿最新
  return request('/api/prefs', { method: 'POST', body: { settings } })
}
