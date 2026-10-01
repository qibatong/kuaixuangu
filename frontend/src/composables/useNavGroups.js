/**
 * 导航信息架构（2026-09-27 v4.11.58 建立，v4.11.61 定稿）: 9 个平铺顶部 tab → 6 个一级分组
 *
 * 工单: 《快选产品优化总工单》批次一 + 批次二 布局设计稿 ——
 *       竞价 / 盘前资讯 / 盘中 / 复盘 / 自选 / 我的
 *
 * ★ 单一数据源。三处消费方全部从这里读，禁止各自再抄一份:
 *   1) components/NavBar.vue    —— PC 顶部一级分组入口
 *   2) components/GroupNav.vue  —— 二级 pill 行（组内切换二级页）
 *   3) components/AppTabBar.vue —— 手机端(≤768px) 底部固定 6 tab 栏
 *
 * 纪律（工单「不做」）:
 *   - ~~零后端改动、零新接口~~（见下方 v4.11.59 说明，已破例一次）；
 *   - **路由路径一律不变**（旧 URL 书签/分享不失效）；
 *   - 不做「按当前时间自动高亮分组」（二期）。
 *
 * ⚠️ 2026-09-27 v4.11.59 更新：上面第一条「零后端改动」**只在 v4.11.58 那一版成立**。
 * 本版新增「盘前资讯」需要新数据源（猫爪 apiname=news + 开盘啦 doc95/96/97/99），
 * 因此后端新增了 `services/news_feed.py` + `api/news.py` 三个接口。
 * 第二条「路由路径不变」**继续成立**：只新增 `/news` 一条，旧路径一条未动。
 *
 * ⚠️ 2026-09-27 v4.11.61 更新：盘前资讯由「竞价」组内二级页升为独立一级分组（6 组），
 * 且「竞价」组加 `hidePills: true`（组内 4 页与 / 页内联 tab 完全重复）。
 * 两条都只改本文件 + GroupNav 一行判断，路由路径依旧一条未动。
 */

/** 六个一级分组。entry = 点该分组时先落地的二级页。 */
export const NAV_GROUPS = [
  {
    key: 'auction',
    label: '竞价',
    icon: 'fa-bell',
    entry: '/',
    // 🔴 2026-09-27 v4.11.61 主人实测反馈：「这一行不用保留，和下面的选股什么的重复了」。
    //    `hidePills` = 本组**不渲染二级 pill 行**（GroupNav 会跳过），原因不是"少几页"，
    //    而是这 4 个二级页**已经在 / 页内部各有一套 tab**，pill 行是纯重复：
    //      · /           → 窄屏 .home-mob-toggle「选股 | 竞价异动」+ 左栏 .mode-tabs
    //                      「AI选股 | AI预测·金睛 | AI预测·火眼」
    //      · /auction    → 就是 / 右栏内嵌的那块（窄屏切「竞价异动」即见）
    //      · /aipick     → 就是左栏 mode-tab「AI预测·金睛」
    //      · /aipick-lgb → 就是左栏 mode-tab「AI预测·火眼」
    //    实测截图里「竞价异动 / AI预测·金睛 / AI预测·火眼」各出现两次，故去掉上面那一行。
    //    ⚠️ items 必须保留：NavBar 用 g.items 拼 title 悬浮提示；被移除的只是 pill 渲染。
    hidePills: true,
    items: [
      { label: '选股名单', path: '/' },
      // /auction 原本根本不在导航里，v4.11.58 时必须提到明面（工单第四节）
      { label: '竞价异动', path: '/auction' },
      // AI 预测·金睛 / 火眼是 VIP 付费功能，原先只嵌在首页左视图 tab 里，不能藏（工单第四节）
      { label: 'AI预测·金睛', path: '/aipick' },
      { label: 'AI预测·火眼', path: '/aipick-lgb' },
    ],
  },
  {
    // 2026-09-27 v4.11.61: 盘前资讯**升为一级分组**。
    //   主人原话：「盘前资讯是和竞价、盘中、这些放一行」——即与竞价/盘中同级，
    //   不再挂在「竞价」组内当二级页。
    //   位置：紧挨「竞价」之后（不占首位）。理由：底部第一个 tab 始终是 `/` 选股名单，
    //   这是本 App 的主功能入口，不该被让位；同时"竞价→盘前资讯→盘中"仍读得顺。
    //   若要挪到最前，只改本对象在数组里的次序即可（三处导航都从这里读）。
    key: 'news',
    label: '盘前资讯',
    icon: 'fa-newspaper-o',
    entry: '/news',
    // 组内只有 1 页 ⇒ GroupNav 本来就不渲染 pill 行；页内自带
    // 「7×24快讯 / 盘前精选 / 大V复盘」三个 tab（见 views/NewsView.vue）
    items: [{ label: '盘前资讯', path: '/news' }],
  },
  {
    key: 'intraday',
    label: '盘中',
    icon: 'fa-line-chart',
    entry: '/market',
    // 「大盘温度」不是独立路由 —— 它是 /market 页顶部内嵌的 SentimentPanel（工单 三.3），
    // 因此二级 pill 只列「板块」；页内有数据源切换（开盘啦强度榜 | 东财概念榜）。
    items: [{ label: '板块', path: '/market' }],
  },
  {
    key: 'review',
    label: '复盘',
    icon: 'fa-table',
    entry: '/ladder',
    items: [
      { label: '连板天梯', path: '/ladder' },
      { label: '龙虎榜', path: '/lhb' },
      { label: '异动监管', path: '/yidong' },
      { label: '大V复盘', path: '/bigv' },
      { label: '历史回看', path: '/history' },
      { label: '股性', path: '/temper' },
    ],
  },
  {
    key: 'pool',
    label: '自选',
    icon: 'fa-star',
    entry: '/pool',
    items: [{ label: '自选', path: '/pool' }],
  },
  {
    key: 'me',
    label: '我的',
    icon: 'fa-user',
    entry: '/member',
    items: [
      { label: '会员', path: '/member' },
      // 设置 / 改密 / 退出 沿用 NavBar 账号下拉里的现有入口，不重复列出
      { label: '管理', path: '/admin', adminOnly: true },
    ],
  },
]

/**
 * 路由 name → 分组 key。作为 meta.group 缺失时的兜底判定，
 * 避免新增路由时忘了写 meta 就掉出所有分组（导航高亮全灭）。
 */
const NAME_TO_GROUP = {
  stock: 'auction', auction: 'auction', aipick: 'auction', 'aipick-lgb': 'auction',
  news: 'news',
  market: 'intraday', concept: 'intraday',
  ladder: 'review', history: 'review', temper: 'review', bigv: 'review',
  yidong: 'review', lhb: 'review',
  pool: 'pool',
  member: 'me', admin: 'me',
}

/** 当前路由属于哪一组；不属于任何组（/login、/404）返回 ''。 */
export function groupKeyOfRoute(route) {
  if (!route) return ''
  const byMeta = route.meta && route.meta.group
  if (byMeta) return byMeta
  return NAME_TO_GROUP[route.name] || ''
}

/** 按 key 取分组定义。 */
export function groupByKey(key) {
  return NAV_GROUPS.find((g) => g.key === key) || null
}


/**
 * 手机底部 tab 栏的 4 格（2026-10-01 主人拍板：**首页 / 竞价 / 盘中 / 我的**）
 *
 * 为什么与 `NAV_GROUPS`(6 组) 分开：
 *   · 桌面顶部导航空间足够 ⇒ **保持 6 组不变**（本次桌面零改动、零风险）；
 *   · 手机底部只留 4 格，并把 `/auction`（竞价异动）从「竞价组二级页」**升为一级**，
 *     `/` 明确叫「首页」—— 二者同属 auction 组，靠 `tabbarKeyOfRoute` 按**路径**区分高亮。
 *   · 🔴 代价（记账见 docs/移动端规划-参考短线侠-20261001.md §〇）：盘前资讯/复盘/自选三组
 *     不再进底部栏 ⇒ 那些页面的入口由**首页宫格**（components/QuickGrid.vue，可编辑候选池）承担；
 *     第 7 格搜索也去掉（原话「上面已经搜索了」）⇒ 顶栏搜索改为**移动端常驻**。
 */
export const TABBAR_TABS = [
  // 图标用 fa-th-large：自托管 FA 子集(108 个)里**没有 fa-home**，重做子集属独立任务
  { key: 'home', label: '首页', icon: 'fa-th-large', path: '/' },
  { key: 'auc', label: '竞价', icon: 'fa-bell', path: '/auction' },
  { key: 'intraday', label: '盘中', icon: 'fa-line-chart', path: '/market' },
  { key: 'me', label: '我的', icon: 'fa-user', path: '/member' },
]

/**
 * 底部栏高亮哪一格：**路径优先**（首页/竞价同组必须拆开），其余落到分组判定。
 * 没有对应格子的页面（/ladder、/lhb、/news、/pool、/history…）返回 '' ⇒ 四格都不高亮
 * —— 它们是「从宫格进」的二级页，不冒充一级 tab。
 */
export function tabbarKeyOfRoute(route) {
  if (!route || !route.path) return ''
  const p = route.path
  if (p === '/' || p.indexOf('/aipick') === 0) return 'home'
  if (p.indexOf('/auction') === 0) return 'auc'
  const g = groupKeyOfRoute(route)
  if (g === 'intraday') return 'intraday'
  if (g === 'me') return 'me'
  return ''
}
