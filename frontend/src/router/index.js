import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'stock', component: () => import('../views/StockView.vue') },
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
    { path: '/history', name: 'history', component: () => import('../views/HistoryView.vue') },
    { path: '/invite', name: 'invite', component: () => import('../views/InviteView.vue') },
    { path: '/admin', name: 'admin', component: () => import('../views/AdminView.vue'), meta: { admin: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

// 路由守卫: 除 /login 外均需登录; 已登录访问 /login 跳回主页; /admin 需管理员
router.beforeEach((to) => {
  const user = useUserStore()
  if (to.name !== 'login' && !user.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
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
