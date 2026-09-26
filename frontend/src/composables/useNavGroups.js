/**
 * 导航信息架构（2026-09-27 v4.11.58）: 9 个平铺顶部 tab → 5 个一级分组
 *
 * 工单: 快选前端信息架构改造工单 —— 竞价 / 盘中 / 复盘 / 自选 / 我的
 *
 * ★ 单一数据源。三处消费方全部从这里读，禁止各自再抄一份:
 *   1) components/NavBar.vue    —— PC 顶部一级分组入口
 *   2) components/GroupNav.vue  —— 二级 pill 行（组内切换二级页）
 *   3) components/AppTabBar.vue —— 手机端(≤768px) 底部固定 5 tab 栏
 *
 * 纪律（工单「不做」）:
 *   - 零后端改动、零新接口；
 *   - **路由路径一律不变**（16 条旧 URL 书签/分享不失效）；
 *   - 不做「按当前时间自动高亮分组」（二期）。
 *
 * ⚠️ 2026-09-27 v4.11.59 更新：上面第一条「零后端改动」**只在 v4.11.58 那一版成立**。
 * 本版新增「盘前资讯」需要新数据源（猫爪 apiname=news + 开盘啦 doc95/96/97/99），
 * 因此后端新增了 `services/news_feed.py` + `api/news.py` 三个接口。
 * 第二条「路由路径不变」**继续成立**：只新增 `/news` 一条，旧路径一条未动。
 */

/** 五个一级分组。entry = 点该分组时先落地的二级页。 */
export const NAV_GROUPS = [
  {
    key: 'auction',
    label: '竞价',
    icon: 'fa-bell',
    entry: '/',
    items: [
      { label: '选股名单', path: '/' },
      // 2026-09-27 v4.11.59: 盘前资讯（猫爪 news + 开盘啦头条/快讯/明天炒什么 + 大V复盘）。
      // 归「竞价」= 盘前时段：9:00 打开 App 的第一站。若要挪到「复盘」，
      // **只改这一行**（三个导航组件都从这里读，不会各改一遍漏一处）。
      { label: '盘前资讯', path: '/news' },
      // /auction 原本根本不在导航里，本次必须提到明面（工单第四节）
      { label: '竞价异动', path: '/auction' },
      // AI 预测·金睛 / 火眼是 VIP 付费功能，原先只嵌在首页左视图 tab 里，不能藏（工单第四节）
      { label: 'AI预测·金睛', path: '/aipick' },
      { label: 'AI预测·火眼', path: '/aipick-lgb' },
    ],
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
      { label: '涨停梯队', path: '/ladder' },
      { label: '历史回看', path: '/history' },
      { label: '股性', path: '/temper' },
      { label: '大V资讯', path: '/bigv' },
      { label: '异动监管', path: '/yidong' }, // 工单 三.5: 由「盘中」挪到「复盘」
      { label: '龙虎榜', path: '/lhb' },      // 工单 三.4: 从市场雷达拆出（17 点后才有数据）
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
  news: 'auction',
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
