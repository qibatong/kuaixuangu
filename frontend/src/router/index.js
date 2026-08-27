import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'stock', component: () => import('../views/StockView.vue') },
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
    { path: '/history', name: 'history', component: () => import('../views/HistoryView.vue') },
    { path: '/market', name: 'market', component: () => import('../views/MarketView.vue') },
    { path: '/pool', name: 'pool', component: () => import('../views/PoolView.vue') },
    { path: '/ladder', name: 'ladder', component: () => import('../views/LadderView.vue') },
    { path: '/yidong', name: 'yidong', component: () => import('../views/YidongView.vue') },
    { path: '/auction', name: 'auction', component: () => import('../views/AuctionView.vue') },
    { path: '/temper', name: 'temper', component: () => import('../views/StockTemperView.vue') },
    { path: '/admin', name: 'admin', component: () => import('../views/AdminView.vue'), meta: { admin: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
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
