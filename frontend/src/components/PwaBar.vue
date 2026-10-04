<template>
  <!--
    PWA 提示条（2026-10-03 · 主人决策：先做 PWA，再谈 APK 封装）
    两件事、一个条：
      ① **安装到桌面**：安卓/桌面 Chrome 走 `beforeinstallprompt`（真按钮，一键装）；
         iOS Safari **没有该事件** ⇒ 只能给「分享 › 添加到主屏幕」的图文引导。
      ② **有新版本**：SW 拿到新版本后提示「刷新」—— 🔴 **绝不自动刷新**：
         竞价 9:15~9:30 是主战场，强行 reload 会把用户正在看的名单打断（同 AGENTS.md 里
         「盘中静默刷新」那条：可以静默拉数据，但**不能打断视图**）。
    · 只在 **≤768px** 显示（桌面浏览器有自带的安装入口，不再打扰）。
    · 关掉后 **7 天内不再出现**（localStorage）；安装成功后永不再现（standalone 判定）。
  -->
  <div v-if="visible" class="pwa-bar" role="region" aria-label="安装与新版本提示">
    <template v-if="mode === 'update'">
      <i class="fa fa-refresh" aria-hidden="true"></i>
      <span class="pwa-txt">有新版本</span>
      <button class="pwa-btn pwa-btn-main" @click="doReload">刷新</button>
      <button class="pwa-x" aria-label="稍后再说" @click="dismiss">✕</button>
    </template>

    <template v-else-if="mode === 'ios'">
      <!-- ⚠️ 不用 fa-apple：它**不在自托管 FA 子集里**（会渲染成空白，_verify/fa_guard.js 兜底）；
           改用在集内的 fa-mobile（真要加 fa-apple 得重跑 scripts/_kx_gen_fa_subset.py）。 -->
      <i class="fa fa-mobile" aria-hidden="true"></i>
      <span class="pwa-txt">装到桌面：点<b>分享</b> › <b>添加到主屏幕</b></span>
      <button class="pwa-x" aria-label="关闭" @click="dismiss">✕</button>
    </template>

    <template v-else>
      <i class="fa fa-download" aria-hidden="true"></i>
      <span class="pwa-txt">把快选装到桌面，全屏打开</span>
      <button class="pwa-btn pwa-btn-main" @click="doInstall">安装</button>
      <button class="pwa-x" aria-label="关闭" @click="dismiss">✕</button>
    </template>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'

const LS_KEY = 'kuaixuan.pwa.bar.dismiss'   // 存时间戳，7 天内不重复打扰
const SEVEN_DAYS = 7 * 24 * 3600 * 1000

const visible = ref(false)
const mode = ref('install')                 // install | ios | update
let deferred = null                         // beforeinstallprompt 事件（只能消费一次）

/** 已安装/已是独立窗口 ⇒ 永远不显示 */
function isStandalone() {
  try {
    return window.matchMedia('(display-mode: standalone)').matches ||
           window.navigator.standalone === true
  } catch (e) { return false }
}

function isIosSafari() {
  const ua = navigator.userAgent
  const ios = /iphone|ipad|ipod/i.test(ua)
  // 微信/QQ/App 内置 WebView 没有「添加到主屏幕」入口 ⇒ 不给引导（说了也做不到）
  const inApp = /micromessenger|qq\/|weibo|alipayclient/i.test(ua)
  return ios && !inApp
}

function dismissedRecently() {
  try {
    const ts = Number(localStorage.getItem(LS_KEY) || 0)
    return ts && (Date.now() - ts) < SEVEN_DAYS
  } catch (e) { return false }
}

// 2026-10-04 兜底(主人方案第4条): Capacitor 安卓壳(WebView)里「装到桌面」毫无意义
//   ⇒ 组件自身也永远不显示。App.vue 的 v-if="!isNativeApp && !isBare" 是第一层，这是第二层。
function inNativeShell() {
  try { return !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform()) } catch (e) { return false }
}

function refreshMode() {
  if (inNativeShell() || isStandalone() || dismissedRecently()) { visible.value = false; return }
  if (hasUpdate.value) { mode.value = 'update'; visible.value = true; return }
  if (deferred) { mode.value = 'install'; visible.value = true; return }
  if (isIosSafari()) { mode.value = 'ios'; visible.value = true; return }
  visible.value = false
}

const hasUpdate = ref(false)

function doInstall() {
  if (!deferred) { visible.value = false; return }
  deferred.prompt()
  deferred.userChoice.then((choice) => {
    // 装没装成都收起，别纠缠
    deferred = null
    visible.value = false
    try { localStorage.setItem(LS_KEY, String(Date.now())) } catch (e) { /* 无 storage */ }
  }).catch(() => { visible.value = false })
}

function dismiss() {
  visible.value = false
  try { localStorage.setItem(LS_KEY, String(Date.now())) } catch (e) { /* 无 storage */ }
}

/** 「刷新」：先让等待中的 SW 接管，controllerchange 后再 reload（一次性，防重复刷新） */
let reloading = false
function doReload() {
  navigator.serviceWorker?.getRegistration?.().then((reg) => {
    reg?.waiting?.postMessage('SKIP_WAITING')
    reloading = true
    window.location.reload()
  }).catch(() => { window.location.reload() })
}
function onControllerChange() {
  // SW 主动接管（install 里 skipWaiting）时，只在新版本提示已点过刷新才 reload，
  // 否则静默不打断 —— 🔴 盘中不能因为发版就把用户的屏幕刷掉。
  if (reloading) return
  hasUpdate.value = true
  refreshMode()
}

function onBeforeInstall(e) {
  e.preventDefault()      // 阻止浏览器自带的迷你安装条，用我们自己的
  deferred = e
  refreshMode()
}

/** main.js 注册 SW 后发现新版本会派这个事件（见 main.js 注释） */
function onSwUpdate() {
  hasUpdate.value = true
  refreshMode()
}

onMounted(() => {
  window.addEventListener('beforeinstallprompt', onBeforeInstall)
  window.addEventListener('kx:sw-update', onSwUpdate)
  navigator.serviceWorker?.addEventListener?.('controllerchange', onControllerChange)
  // 晚于本组件挂载才注册完的情况：主动查一次是否已有 waiting
  navigator.serviceWorker?.getRegistration?.().then((reg) => {
    if (reg?.waiting) onSwUpdate()
  }).catch(() => {})
  refreshMode()
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeinstallprompt', onBeforeInstall)
  window.removeEventListener('kx:sw-update', onSwUpdate)
  navigator.serviceWorker?.removeEventListener?.('controllerchange', onControllerChange)
})
</script>

<style scoped>
/* 只在 ≤768 显示（断点复用既有 768，见 _verify/breakpoint_guard.js） */
.pwa-bar {
  display: none;
  position: fixed;
  left: 8px;
  right: 8px;
  /* 让开底部 tabbar(56px) + 安全区：见 App.vue 与 AppTabBar.vue 的 56px 常量 */
  bottom: calc(64px + env(safe-area-inset-bottom));
  z-index: 1200;
  align-items: center;
  gap: var(--s2);
  padding: var(--s2) var(--s2);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-lg);
  background: var(--bg-input);
  box-shadow: var(--sh-2);
  font-size: var(--fs-xs);
}
@media (max-width: 768px) {
  .pwa-bar { display: flex; }
}
.pwa-txt { flex: 1 1 auto; color: var(--text-secondary); line-height: 1.4; }
.pwa-txt b { color: var(--text-main); font-weight: 600; }
.pwa-bar i { flex: 0 0 auto; color: var(--qg-orange-a); }
.pwa-btn {
  flex: 0 0 auto;
  border: 1px solid var(--qg-orange-a);
  border-radius: var(--r-pill);
  background: transparent;
  color: var(--qg-orange-a);
  padding: var(--s1) var(--s3);
  font-size: var(--fs-xs);
  cursor: pointer;
}
.pwa-btn:hover { background: rgba(255, 180, 0, 0.1); }
.pwa-x {
  flex: 0 0 auto;
  border: none;
  background: transparent;
  color: var(--text-muted);
  font-size: var(--fs-sm);
  line-height: 1;
  padding: 2px var(--s1);
  cursor: pointer;
}
.pwa-x:hover { color: var(--text-main); }
</style>
