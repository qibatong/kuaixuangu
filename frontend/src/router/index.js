import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'stock', component: () => import('../views/StockView.vue') },
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
    { path: '/history', name: 'history', component: () => import('../views/HistoryView.vue') },
    { path: '/market', name: 'market', component: () => import('../views/MarketView.vue') },
    { path: '/concept', name: 'concept', component: () => import('../views/ConceptView.vue') },
    { path: '/pool', name: 'pool', component: () => import('../views/PoolView.vue') },
    { path: '/ladder', name: 'ladder', component: () => import('../views/LadderView.vue') },
    { path: '/yidong', name: 'yidong', component: () => import('../views/YidongView.vue') },
    { path: '/auction', name: 'auction', component: () => import('../views/AuctionView.vue') },
    { path: '/temper', name: 'temper', component: () => import('../views/StockTemperView.vue') },
    { path: '/aipick', name: 'aipick', component: () => import('../views/AipickView.vue') },
    { path: '/bigv', name: 'bigv', component: () => import('../views/SummaryNewsView.vue') },
    { path: '/admin', name: 'admin', component: () => import('../views/AdminView.vue'), meta: { admin: true } },
    // 2026-09-21: 由 redirect '/' 改为独立 404 视图, 避免未知路径静默落首页造成困惑
    { path: '/:pathMatch(.*)*', name: 'notFound', component: () => import('../views/NotFoundView.vue') }
  ]
})

// 2026-09-21: 路由级 <title>, 便于多标签区分/书签辨识/前进后退历史
const TITLES = {
  stock: '选股', pool: '自选', history: '历史回看', market: '市场雷达',
  concept: '题材异动', ladder: '涨停梯队', temper: '股性', yidong: '异动监管',
  bigv: '大V资讯', auction: '竞价异动', aipick: 'AI预测', admin: '管理后台',
}
router.afterEach((to) => {
  if (to.name === 'login') { document.title = '登录 · 快选'; return }
  if (to.name === 'notFound') { document.title = '页面不存在 · 快选'; return }
  document.title = `${TITLES[to.name] || '快选'} · 快选 AI选股`
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
