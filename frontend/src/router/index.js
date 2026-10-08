import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

/**
 * 2026-09-27 v4.11.58 前端信息架构改造（工单《快选股前端信息架构改造工单》）:
 *   9 个平铺顶部 tab → 5 个一级分组（竞价 / 盘中 / 复盘 / 自选 / 我的）。
 *   - 每条用户路由补 meta.group + meta.order，供 NavBar / GroupNav / AppTabBar 统一渲染；
 *   - ★ 原有 16 条路径一律不变（书签 / 分享外链不失效），只新增 /lhb 一条；
 *   - 组定义唯一来源 = composables/useNavGroups.js。
 */
const router = createRouter({
  history: createWebHistory(),
  routes: [
    // ---------------- 盘前资讯（2026-09-27 v4.11.61: 由「竞价」组内二级页升为一级分组）----------------
    // 数据源：猫爪 news + 开盘啦 doc95 头条 / doc96 快讯 / doc97 明天炒什么 + 大V复盘
    { path: '/news', name: 'news', component: () => import('../views/NewsView.vue'), meta: { group: 'news', order: 0 } },

    // ---------------- 竞价 ----------------
    // 2026-10-05 (S1): 根路径改为「登录态分流入口」——
    //   已登录 → StockView(选股名单)；未登录 → LandingView(落地页: 价值主张/能力墙/权益价目/双 CTA)。
    //   🔴 目的: 此前未登录访问任意深链都会被守卫 302 到 /login ⇒ 新用户第一眼只有一个登录框,
    //      不知道产品是什么、值多少钱, 转化链条在最贵的一秒被切断（实测 21 条路由仅 /login 对匿名可见）。
    //   ⚠️ 必须在"进入 StockView 之前"分流: StockView 的 onMounted 会打 /api/* 与轮询,
    //      匿名触发 401 会被 request.js 直接 window.location.href='/login' 弹走（见 api/request.js:79-90）。
    { path: '/', name: 'stock', component: () => import('../views/HomeEntry.vue'), meta: { group: 'auction', order: 0 } },
    { path: '/auction', name: 'auction', component: () => import('../views/AuctionView.vue'), meta: { group: 'auction', order: 1 } },
    // 2026-10-03：《顺势而为竞价终极版》（数据来自后端 /api/his-pick）
    { path: '/his-pick', name: 'his-pick', component: () => import('../views/HisPickView.vue'), meta: { group: 'auction', order: 9 } },
    // 2026-10-06: 金睛/火眼归属从「竞价」组改挂「双脑竞价」组 —— 双脑竞价页「查看评分详情」
    //   跳过来时顶栏高亮仍停在「双脑竞价」，不再出现"从双脑竞价跳进了竞价"的错位感。
    //   路径 /aipick /aipick-lgb 一字不动（旧书签/外链不失效）。
    { path: '/aipick', name: 'aipick', component: () => import('../views/AipickView.vue'), meta: { group: 'chaozhi', order: 1 } },
    // 2026-09-25: 火眼(LightGBM) 平行链路独立页(与 /aipick 共用 AipickReport 组件, 只换 model)
    { path: '/aipick-lgb', name: 'aipick-lgb', component: () => import('../views/AipickLgbView.vue'), meta: { group: 'chaozhi', order: 2 } },

    // ---------------- 盘中 ----------------
    // /market = 板块（页顶内嵌大盘温度 SentimentPanel + 数据源切换: 开盘啦强度榜 | 东财概念榜）
    { path: '/market', name: 'market', component: () => import('../views/MarketView.vue'), meta: { group: 'intraday', order: 0 } },
    // 2026-10-01 主人: 首页宫格「题材库」格 → 开盘啦题材/板块榜（含成分股下钻）
    { path: '/theme', name: 'theme', component: () => import('../views/ThemeLibView.vue'), meta: { group: 'intraday', order: 1 } },
    // 2026-10-01 主人: 「双脑竞价」（原 AI预测）聚合页 —— 首页宫格第 5 格直跳此页
    // 2026-10-03 主人指令: 升为**一级分组**「双脑竞价」（放「竞价」左边），meta.group 改 'chaozhi'。
    //   ⚠️ 曾经的 noGroupNav 豁免随之取消 —— 它当时是为了挡住误归属「盘中」组的 pill 行；
    //      现在自成一组且组内只有 1 页 ⇒ GroupNav 的 items.length>1 判据本来就不渲染。
    { path: '/chaozhi', name: 'chaozhi', component: () => import('../views/ChaozhiView.vue'),
      meta: { group: 'chaozhi', order: 0 } },
    // 2026-09-27: 题材异动已并入 /market 的「东财概念榜」数据源（工单 三.4 方案 A）。
    // 路径保留 + 重定向，旧书签/外链不 404。
    { path: '/concept', name: 'concept', redirect: (to) => ({ name: 'market', query: { ...to.query, src: 'em' } }) },

    // ---------------- 复盘 ----------------
    { path: '/ladder', name: 'ladder', component: () => import('../views/LadderView.vue'), meta: { group: 'review', order: 0 } },
    { path: '/history', name: 'history', component: () => import('../views/HistoryView.vue'), meta: { group: 'review', order: 4 } },
    { path: '/temper', name: 'temper', component: () => import('../views/StockTemperView.vue'), meta: { group: 'review', order: 5 } },
    { path: '/bigv', name: 'bigv', component: () => import('../views/SummaryNewsView.vue'), meta: { group: 'review', order: 3 } },
    // 2026-10-01: 支持深链到指定 tab（首页宫格「异动计算器」格 → ?tab=calc）。
    //   YidongView 早就声明了 `initialTab` prop 并据此设初值 ⇒ 只需在这里把 query 接上。
    {
      path: '/yidong',
      name: 'yidong',
      component: () => import('../views/YidongView.vue'),
      props: (route) => ({ initialTab: String(route.query.tab || '') }),
      meta: { group: 'review', order: 2 },
    },
    // 2026-09-27: 龙虎榜从市场雷达拆出成独立页（它 17 点后才有数据，盘中看是空的）
    { path: '/lhb', name: 'lhb', component: () => import('../views/LhbView.vue'), meta: { group: 'review', order: 1 } },

    // ---------------- 自选 ----------------
    { path: '/pool', name: 'pool', component: () => import('../views/PoolView.vue'), meta: { group: 'pool', order: 0 } },

    // ---------------- 我的 ----------------
    { path: '/member', name: 'member', component: () => import('../views/MemberView.vue'), meta: { group: 'me', order: 0 } },
    // 2026-09-21 会员体系: 我的会员(等级/到期/配额/签到/邀请)
    { path: '/admin', name: 'admin', component: () => import('../views/AdminView.vue'), meta: { admin: true, group: 'me', order: 1 } },
    // 2026-10-04 主人需求「系统消息」：消息中心。入口 = 顶栏右上角铃铛（全站常驻），
    //   故**不进任何导航组**（进组就会在「我的」pill 行里多一个没人点的死项）。
    //   🔴 noGroupNav：它不属于任何组的二级页，不该渲染分组 pill 行。
    { path: '/messages', name: 'messages', component: () => import('../views/MessagesView.vue'),
      meta: { group: 'me', noGroupNav: true } },

    // ---------------- 不参与分组 ----------------
    // 2026-10-04 登录页独立布局: meta.bare ⇒ App.vue 不挂 NavBar/GroupNav/页脚/TabBar/PwaBar,
    //   配合 .auth-overlay 改不透明 ⇒ 手机端登录页全屏铺满, 不再像"浮在导航上的弹窗"。
    //   注册/忘记密码是 LoginView 内部页签(mode query), 同走本路由 ⇒ 不需要额外 meta。
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { bare: true } },
    // 2026-10-05 (S6): 合规静态页（用户协议/隐私政策/退款说明）—— 匿名可访问。
    //   三页共用一个 LegalView, 由 meta.doc 决定渲染哪一份文档（减少三个近乎重复的文件）。
    { path: '/terms', name: 'terms', component: () => import('../views/LegalView.vue'), meta: { doc: 'terms', bare: false } },
    { path: '/privacy', name: 'privacy', component: () => import('../views/LegalView.vue'), meta: { doc: 'privacy' } },
    { path: '/refund', name: 'refund', component: () => import('../views/LegalView.vue'), meta: { doc: 'refund' } },

    // 2026-09-21: 由 redirect '/' 改为独立 404 视图, 避免未知路径静默落首页造成困惑
    { path: '/:pathMatch(.*)*', name: 'notFound', component: () => import('../views/NotFoundView.vue') }
  ]
})

// 2026-09-21: 路由级 <title>, 便于多标签区分/书签辨识/前进后退历史
// 2026-10-05 (S7): 补齐 4 个缺失键（theme/chaozhi/his-pick/messages）——
//   缺键时兜底值 '快选股' 会被拼成「**快选股 · 快选股 竞价选股**」(重复品牌词, 实测 4 条路由如此);
//   同时把 bigv 的「大V资讯」对齐到导航一级 pill 的「大V复盘」(useNavGroups.js:103),
//   避免"点的是大V复盘、标签写的是大V资讯"的自相矛盾。
const TITLES = {
  stock: '选股', pool: '自选', history: '历史回看', market: '板块',
  concept: '题材异动', ladder: '连板天梯', temper: '股性', yidong: '异动监管',
  bigv: '大V复盘', auction: '竞价异动', aipick: 'AI预测·金睛', admin: '管理后台',
  'aipick-lgb': 'AI预测·火眼', lhb: '龙虎榜', news: '盘前资讯',
  member: '我的会员',
  theme: '题材库', chaozhi: '双脑竞价', 'his-pick': '顺势而为', messages: '消息中心',
  terms: '用户协议', privacy: '隐私政策', refund: '退款说明',
}
// 2026-10-05 (S7): 未登录首屏（/，落地页）给一个能进搜索/分享卡片的标题，
//   不再让新用户第一眼看到「选股 · 快选股 竞价选股」这种内部术语。
const GUEST_TITLE = '快选股 · 竞价选股｜早 9:25 定格名单，一键导出通达信'
router.afterEach((to) => {
  if (to.name === 'login') { document.title = '登录 · 快选股'; return }
  if (to.name === 'notFound') { document.title = '页面不存在 · 快选股'; return }
  if (to.name === 'stock' && !useUserStore().isLoggedIn) { document.title = GUEST_TITLE; return }
  // 2026-09-28: 站点后缀随 tab 改名同步（AI选股 → 竞价选股）
  const t = TITLES[to.name]
  document.title = t ? `${t} · 快选股 竞价选股` : '快选股 · 竞价选股'
})

// 路由守卫: 除白名单外均需登录; 已登录访问 /login 跳回主页; /admin 需管理员
// 2026-10-05 (S1/S6): 新增匿名白名单 —— 落地首屏 / 与三张合规静态页。
//   · /（name=stock）: 匿名不再被弹到 /login, 由 HomeEntry.vue 在组件层分流到 LandingView,
//     因此**不会**挂载 StockView、不会发出任何需鉴权的 API 请求（避免 401 硬跳转）。
//   · /terms /privacy /refund: 合规页必须能匿名打开（注册前要能点开看）。
const PUBLIC_ROUTES = new Set(['stock', 'terms', 'privacy', 'refund'])
router.beforeEach((to) => {
  const user = useUserStore()
  if (to.name !== 'login' && !PUBLIC_ROUTES.has(to.name) && !user.isLoggedIn) {
    // 2026-08-18 修复: 跳 /login 时保留原始 query(如 ?reset=TOKEN),
    // 否则忘记密码邮件链接点击后 reset 参数丢失 → 只显示登录框而不是设置新密码
    return { name: 'login', query: { redirect: to.fullPath, ...to.query } }
  }
  if (to.name === 'login' && user.isLoggedIn) {
    return { name: 'stock' }
  }
  // 明确标记"非管理员"的用户禁止进 /admin(老 session 无标记则放行, 由后端 403 兜底)
  if (to.meta && to.meta.admin && user.isLoggedIn && !user.isAdmin) {
    return { name: 'stock' }
  }
  return true
})

export default router
