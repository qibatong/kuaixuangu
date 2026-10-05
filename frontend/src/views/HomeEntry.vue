<script setup>
// 2026-10-05 (S1): 根路径「登录态分流入口」。
//
// 背景：此前 router 守卫把**所有**未登录访问（含 /）302 到 /login ⇒ 新用户第一眼只有登录框，
//   看不到产品是什么、能解决什么问题、值多少钱；实测 21 条路由中只有 /login 对匿名可见。
//
// 为什么必须在这里分流、而不是守卫直接放行到 StockView：
//   StockView 的 onMounted → init() 会立刻打 /api/prefs、/api/stocks?action=ping 等**需鉴权**接口，
//   匿名必然 401；而 api/request.js:79-90 的 401 分支会 clearSession() 并
//   `window.location.href = '/login'` ⇒ 用户会被硬跳走、还会看到一次白屏。
//   两个组件用 v-if 互斥挂载（不是 CSS 隐藏），未登录时 StockView 完全不挂载 ⇒ 零无效请求。
import { defineAsyncComponent } from 'vue'
import { useUserStore } from '../stores/user'

const user = useUserStore()
// 异步组件：登录后才加载 StockView 的 chunk，匿名首访不必下载选股页的代码
const StockView = defineAsyncComponent(() => import('./StockView.vue'))
const LandingView = defineAsyncComponent(() => import('./LandingView.vue'))
</script>

<template>
  <StockView v-if="user.isLoggedIn" />
  <LandingView v-else />
</template>
