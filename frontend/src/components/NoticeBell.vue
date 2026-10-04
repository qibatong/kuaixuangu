<template>
  <!--
    右上角「系统消息」铃铛（2026-10-04）
    主人需求原话：「系统消息比如系统更新提醒，会员到期提醒，等等」

    · 内容两类（与后端 api/notices.py 同口径）：
        broadcast 站方广播（系统更新提醒等，管理员后台发布）
        account   账户事件（会员到期 / 今日次数用尽，实时推导、**不可标已读**）
    · 红点 = 后端返回的 unread；点铃铛进 /messages 消息中心（已读在那里标）。
    · 🔴 未登录**不渲染**（v-if，不是 CSS 隐藏）—— 未登录时接口必然 401，
      按主人偏好「隐藏组件必须用 v-if 不挂载」，省掉无谓请求与 401 噪音。
    · 轮询 120s：系统公告/到期提醒都不是秒级事件；且本组件全站只挂一份（NavBar），
      不存在多实例重复轮询。页面卸载必须清 interval，否则路由来回切会堆积定时器。
  -->
  <router-link
    v-if="user.isLoggedIn"
    to="/messages"
    class="notice-bell"
    :class="{ 'has-unread': unread > 0 }"
    :title="unread > 0 ? ('系统消息：' + unread + ' 条未读') : '系统消息'"
    :aria-label="unread > 0 ? ('系统消息，' + unread + ' 条未读') : '系统消息'"
  >
    <i class="fa fa-bell" aria-hidden="true"></i>
    <span v-if="unread > 0" class="nb-badge">{{ unread > 99 ? '99+' : unread }}</span>
  </router-link>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useUserStore } from '../stores/user'
import { fetchNotices } from '../api/notices'

const user = useUserStore()
const unread = ref(0)
let timer = null

async function load() {
  // 失败一律静默：红点是个"锦上添花"的提示，不该因为接口抖动就弹错误打断看盘。
  try {
    const d = await fetchNotices()
    if (d && d.ok) unread.value = Number(d.unread || 0)
  } catch (e) { /* 静默 */ }
}

onMounted(() => {
  if (!user.isLoggedIn) return
  load()
  timer = setInterval(load, 120000)
})

onUnmounted(() => {
  if (timer) { clearInterval(timer); timer = null }
})
</script>

<style scoped>
.notice-bell {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 1px solid var(--border-soft);
  background: var(--bg-panel-solid);
  color: var(--text-secondary);
  text-decoration: none;
  flex: 0 0 auto;
}
.notice-bell:hover { color: var(--accent-text); border-color: var(--accent-border); }
.notice-bell:active { transform: scale(0.92); }
.notice-bell.has-unread { color: var(--accent-text); border-color: var(--accent-border); }

/* 未读角标：右上角红色小圆，99 封顶 */
.nb-badge {
  position: absolute;
  top: -4px;
  right: -5px;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  box-sizing: border-box;
  border-radius: 8px;
  background: var(--accent);
  color: var(--accent-on);
  font-size: 10px;
  line-height: 15px;
  text-align: center;
  font-weight: 600;
}
</style>
