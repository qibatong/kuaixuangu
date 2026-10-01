import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

/**
 * 2026-09-27 v4.11.58 前端信息架构改造（工单《快选前端信息架构改造工单》）:
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
    { path: '/', name: 'stock', component: () => import('../views/StockView.vue'), meta: { group: 'auction', order: 0 } },
    { path: '/auction', name: 'auction', component: () => import('../views/AuctionView.vue'), meta: { group: 'auction', order: 1 } },
    { path: '/aipick', name: 'aipick', component: () => import('../views/AipickView.vue'), meta: { group: 'auction', order: 2 } },
    // 2026-09-25: 火眼(LightGBM) 平行链路独立页(与 /aipick 共用 AipickReport 组件, 只换 model)
    { path: '/aipick-lgb', name: 'aipick-lgb', component: () => import('../views/AipickLgbView.vue'), meta: { group: 'auction', order: 3 } },

    // ---------------- 盘中 ----------------
    // /market = 板块（页顶内嵌大盘温度 SentimentPanel + 数据源切换: 开盘啦强度榜 | 东财概念榜）
    { path: '/market', name: 'market', component: () => import('../views/MarketView.vue'), meta: { group: 'intraday', order: 0 } },
    // 2026-10-01 主人: 首页宫格「题材库」格 → 开盘啦题材/板块榜（含成分股下钻）
    { path: '/theme', name: 'theme', component: () => import('../views/ThemeLibView.vue'), meta: { group: 'intraday', order: 1 } },
    // 2026-10-01 主人: 「超智研判」（原 AI预测）聚合页 —— 首页宫格第 5 格直跳此页
    // ⚠️ `noGroupNav`: 本页是独立聚合页，**不显示**所在分组(盘中)的二级 pill 行
    //   （主人 2026-10-01:「超智页面上面怎么有盘中/板块/题材库，这个不应该显示在这里」）
    { path: '/chaozhi', name: 'chaozhi', component: () => import('../views/ChaozhiView.vue'),
      meta: { group: 'intraday', order: 2, noGroupNav: true } },
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

    // ---------------- 不参与分组 ----------------
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
    // 2026-09-21: 由 redirect '/' 改为独立 404 视图, 避免未知路径静默落首页造成困惑
    { path: '/:pathMatch(.*)*', name: 'notFound', component: () => import('../views/NotFoundView.vue') }
  ]
})

// 2026-09-21: 路由级 <title>, 便于多标签区分/书签辨识/前进后退历史
const TITLES = {
  stock: '选股', pool: '自选', history: '历史回看', market: '板块',
  concept: '题材异动', ladder: '连板天梯', temper: '股性', yidong: '异动监管',
  bigv: '大V资讯', auction: '竞价异动', aipick: 'AI预测·金睛', admin: '管理后台',
  'aipick-lgb': 'AI预测·火眼', lhb: '龙虎榜', news: '盘前资讯',
  member: '我的会员',
}
router.afterEach((to) => {
  if (to.name === 'login') { document.title = '登录 · 快选'; return }
  if (to.name === 'notFound') { document.title = '页面不存在 · 快选'; return }
  // 2026-09-28: 站点后缀随 tab 改名同步（AI选股 → 竞价选股）
  document.title = `${TITLES[to.name] || '快选'} · 快选 竞价选股`
})

// 路由守卫: 除 /login 外均需登录; 已登录访问 /login 跳回主页; /admin 需管理员
router.beforeEach((to) => {
  const user = useUserStore()
  if (to.name !== 'login' && !user.isLoggedIn) {
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
