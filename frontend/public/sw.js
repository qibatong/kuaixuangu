/**
 * 快选 Service Worker —— **离线壳**（2026-10-03 · 主人决策：先做 PWA 再谈 APK 封装）
 *
 * 🎯 目标：断网/弱网时打开不是白屏，而是能进 App 骨架（有图标、有导航、有本地偏好）；
 *    同时**绝不缓存任何行情数据**。
 *
 * 🔴 铁律一：**`/api/*` 一律 network-only，不进 Cache**。
 *    本项目的行情是**时点敏感**的（竞价 9:15/9:20/9:25 三时点、盘中实时价、昨日定格值），
 *    AGENTS.md 里那条「零值不得回退昨日」就是同类的血泪教训 —— 缓存一份旧行情给用户，
 *    比白屏更危险（用户会拿着过期的竞价数据下单）。行情错了是要赔钱的，页面慢一秒不是事。
 *    同理不缓存：`/aipick/`（VIP 鉴权静态报告）、`/download/`、任何非 GET 请求。
 *
 * 🔴 铁律二：**导航(HTML)走 network-first**，离线才回退缓存的 `/`。
 *    我们一天发好几版，若用 cache-first，用户会一直拿到旧 index.html ⇒
 *    引用不到新 hash 的资源 ⇒ 白屏/卡旧版。缓存的 index.html **只当离线兜底**，
 *    联网时永远以网络为准（nginx 侧 `location /` 也是 `no-store`，两边一致）。
 *
 * ✅ 可安全长缓存的只有**文件名带内容 hash 的静态资源**（`/assets/*`、自托管字体），
 *    nginx 对 `/assets/` 已是 `max-age=31536000, immutable` ⇒ 内容变文件名必变，cache-first 零风险。
 *
 * ⚠️ 本文件位于 `public/` ⇒ **原样拷贝到 dist 根**、不经过 Vite 打包、不带 hash
 *    ⇒ 更新它即刻生效（浏览器对 sw.js 本身不做缓存，且每次导航都会检查更新）。
 *    ⚠️ 部署后务必验 **content-type = application/javascript**（生产 nginx 的 SPA 回退
 *    曾把静态资源吞成 text/html ⇒ SW 注册失败且报错很难懂，见 AGENTS.md v4.11.84 教训）。
 */

const SHELL_CACHE = 'kx-shell-v1'      // 只存 App 骨架（index.html + 图标 + manifest）
const ASSET_CACHE = 'kx-assets-v1'     // 只存带 hash 的静态资源（/assets/*、字体）
const KEEP = [SHELL_CACHE, ASSET_CACHE]

/** 预缓存：够撑起"离线能打开 App"的最小集合（不预缓存所有 assets，避免安装时拖慢） */
const PRECACHE = [
  '/',
  '/manifest.json',
  '/favicon.png',
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png',
]

/** 一律不碰的请求（数据/鉴权/下载）——命中即放行给网络，不读不写缓存 */
function isNoCache(url) {
  const p = url.pathname
  return p.startsWith('/api/') || p.startsWith('/aipick/') ||
         p.startsWith('/download/') || p.startsWith('/__aipick_auth')
}

/** 可长缓存的静态资源：带 hash 的构建产物 + 自托管字体/图标 */
function isHashedAsset(url) {
  const p = url.pathname
  return p.startsWith('/assets/') || p.startsWith('/fonts/') ||
         /\.(?:css|js|mjs|woff2?|ttf|png|jpe?g|gif|svg|ico|webp)$/.test(p)
}

self.addEventListener('install', (e) => {
  // 预缓存失败不阻断安装（离线第一次装也能用，后续靠 runtime 缓存补）
  e.waitUntil(
    caches.open(SHELL_CACHE)
      .then((c) => c.addAll(PRECACHE))
      .catch(() => {})
      .then(() => self.skipWaiting())   // 立即接管，避免"关掉重开才生效"的困惑
  )
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !KEEP.includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  )
})

/** 离线兜底页：连缓存的 index.html 都没有（首次访问就断网）时给个体面的交代 */
function offlineResponse() {
  const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>快选 · 离线</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0a0a0e;color:#9aa;font:14px/1.7 system-ui,sans-serif;text-align:center;padding:24px}
b{display:block;color:#e8e8ef;font-size:16px;margin-bottom:8px}</style></head>
<body><div><b>当前无网络</b>快选需要联网获取行情数据<br>（行情不做离线缓存，避免给您过期数据）<br>
网络恢复后请下拉刷新</div></body></html>`
  return new Response(html, { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } })
}

self.addEventListener('fetch', (e) => {
  const req = e.request
  if (req.method !== 'GET') return                       // POST/PUT 不拦
  let url
  try { url = new URL(req.url) } catch (err) { return }
  if (url.origin !== self.location.origin) return        // 跨域不碰（本项目零外链）
  if (isNoCache(url)) return                             // 🔴 铁律一：数据/鉴权/下载放行

  // 导航请求（打开页面/刷新）：network-first，离线回退缓存的 index.html
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req)
        .then((res) => {
          // 🔴 2026-10-08: 只把 **SPA 壳** 写进 '/' 缓存。带扩展名的路径（如独立静态下载页
          //   /app.html）不是壳 —— 否则离线时 '/' 会回退成下载页，用户以为 App 打不开了。
          if (res && res.ok && !/\.[a-z0-9]+$/i.test(url.pathname)) {
            const copy = res.clone()
            caches.open(SHELL_CACHE).then((c) => c.put('/', copy)).catch(() => {})
          }
          return res
        })
        .catch(() => caches.match('/').then((hit) => hit || offlineResponse()))
    )
    return
  }

  // 静态资源：cache-first（文件名带 hash / immutable，内容变则文件名变）
  if (isHashedAsset(url)) {
    e.respondWith(
      caches.match(req).then((hit) => {
        if (hit) return hit
        return fetch(req).then((res) => {
          if (res && res.ok && res.type === 'basic') {
            const copy = res.clone()
            caches.open(ASSET_CACHE).then((c) => c.put(req, copy)).catch(() => {})
          }
          return res
        })
      })
    )
  }
})

/** 页面（PwaBar.vue）点「刷新」时调用：跳过等待并让页面重载 */
self.addEventListener('message', (e) => {
  if (e && e.data === 'SKIP_WAITING') self.skipWaiting()
})

// ==================================================================
// WebPush（2026-10-04 手机端真推送）
//
// 后端按 RFC 8291 加密后 POST 到浏览器给的 endpoint，浏览器解密后派 `push` 事件。
// 🔴 这里**只负责弹通知**：不做任何数据请求（SW 里没有登录态，且铁律一禁止碰 /api/）。
//    载荷里带的是 {title, body, url}，够用了 —— 想看详情就点通知进页面。
//
// 2026-10-04 说明（推送「要么接真、要么藏掉」→ 本轮选"藏掉"，但**只藏安卓**）：
//   · 安卓 Chrome / 壳内 WebView 的 WebPush 必须经 Google FCM，国内网络连不上
//     ⇒ subscribe 会抛 "Registration failed - push service error"
//     ⇒ 前端已在 `usePush.js` 里对安卓隐藏入口（不会再产生新订阅）。
//   · 本段**保留**：iPhone（Safari 加到主屏幕）走 Apple APNs 真能收到，
//     已订阅的设备要靠它弹通知；没有订阅就不会有 push 事件，留着无害。
//   TODO（接真推送时）：安卓侧换 @capacitor/push-notifications + 厂商通道后，
//     通知改由原生侧弹出，本段仍服务于 Web/iOS，不动。
// ==================================================================
self.addEventListener('push', (e) => {
  let data = {}
  try {
    data = e.data ? e.data.json() : {}
  } catch (err) {
    data = { title: '快选', body: e && e.data ? e.data.text() : '' }
  }
  const title = data.title || '快选'
  const opts = {
    body: data.body || '',
    icon: '/icon-192.png',
    badge: '/favicon.png',
    // 点击后要用：把目标地址挂在通知上
    data: { url: data.url || '/messages' },
    tag: data.tag || 'kx-notice',   // 同 tag 覆盖显示，避免刷屏
    renotify: true,
    // 紧急级别（会员到期/停服）要求用户显式处理，不自动消失
    requireInteraction: data.level === 'urgent',
  }
  e.waitUntil(self.registration.showNotification(title, opts))
})

self.addEventListener('notificationclick', (e) => {
  e.notification.close()
  const url = (e.notification.data && e.notification.data.url) || '/messages'
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if (new URL(c.url).origin === self.location.origin) {
          // 已有窗口 ⇒ 复用并导航过去（避免开一堆重复标签页）
          if (typeof c.navigate === 'function') {
            return c.navigate(url).then(() => c.focus()).catch(() => c.focus())
          }
          return c.focus()
        }
      }
      return self.clients.openWindow(url)
    })
  )
})
