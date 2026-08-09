import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'stock', component: () => import('../views/StockView.vue') },
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },
    { path: '/history', name: 'history', component: () => import('../views/HistoryView.vue') },
    { path: '/invite', name: 'invite', component: () => import('../views/InviteView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

// 路由守卫: 除 /login 外均需登录; 已登录访问 /login 跳回主页
router.beforeEach((to) => {
  const user = useUserStore()
  if (to.name !== 'login' && !user.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && user.isLoggedIn) {
    return { name: 'stock' }
  }
  return true
})

export default router
