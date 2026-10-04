import { ref } from 'vue'
import { fetchPushStatus, fetchVapidKey, pushSubscribe, pushUnsubscribe } from '../api/push'

/**
 * WebPush 开关（2026-10-04 手机端真推送）。
 *
 * ★ 默认是**关**的：推送是打扰型能力，必须用户主动点开（主人一贯偏好：新能力默认关）。
 *   iOS Safari 还要求先"添加到主屏幕"才允许订阅 —— 这种环境直接显示不支持，不给开关。
 *
 * 🔴 两个前提，缺一不可：
 *   1. **https**（Service Worker / Push 只在安全上下文可用）—— 现网 www.kuaixuangu.cn 满足
 *   2. **Service Worker 已注册**（main.js 里注册 /sw.js）
 *
 * 注意：`applicationServerKey` 必须是 **Uint8Array**，不是 base64 字符串，
 * 直接把公钥字符串传进去会抛 TypeError（这是 WebPush 最常见的坑）。
 */

const supported = ref(false)
const subscribed = ref(false)
const busy = ref(false)
const message = ref('')

/** base64url → Uint8Array（浏览器 subscribe 要求的格式） */
function urlBase64ToUint8Array(b64) {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4)
  const raw = atob(b64.replace(/-/g, '+').replace(/_/g, '/') + pad)
  const out = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

export function usePush() {
  const refresh = async () => {
    supported.value =
      typeof window !== 'undefined' &&
      'serviceWorker' in navigator &&
      'PushManager' in window &&
      'Notification' in window
    if (!supported.value) return
    try {
      const r = await fetchPushStatus()
      const d = (r && r.data) || r || {}
      subscribed.value = !!(d && (d.subscribed || d.devices > 0))
    } catch (e) {
      subscribed.value = false
    }
  }

  const enable = async () => {
    if (busy.value) return false
    busy.value = true
    message.value = ''
    try {
      const perm = await Notification.requestPermission()
      if (perm !== 'granted') {
        message.value = '未授权通知权限（需在浏览器设置里允许通知）'
        return false
      }
      const reg = await navigator.serviceWorker.ready
      const kr = await fetchVapidKey()
      const key = (kr && ((kr.data && kr.data.key) || kr.key)) || ''
      if (!key) throw new Error('取不到推送公钥')

      let sub = await reg.pushManager.getSubscription()
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(key),
        })
      }
      const body = sub.toJSON()
      body.ua = navigator.userAgent
      await pushSubscribe(body)
      subscribed.value = true
      return true
    } catch (e) {
      message.value = friendlyPushError((e && e.message) || '')
      return false
    } finally {
      busy.value = false
    }
  }

  // 2026-10-04 实测（主人手机 Android Chrome）：subscribe 阶段浏览器抛
  // "Registration failed - push service error" ⇒ 原文直接展示用户看不懂。
  //   根因：Android Chrome 的 WebPush 必须经 Google FCM，国内网络连不上 ⇒ 注册失败。
  //   这是**网络环境限制**，不是代码 bug；iOS(Safari+加到主屏幕) 走 APNs 不受此限。
  //   把高频错误映射成中文 + 指路；未识别的保留原文（附中文前缀）。
  function friendlyPushError(raw) {
    if (/push service error|Registration failed/i.test(raw))
      return '注册推送失败：Android 浏览器需连接 Google 推送服务，国内网络通常连不上（iPhone 加到主屏幕后可用）。网络环境限制，非账号问题。'
    if (/permission|NotAllowed/i.test(raw))
      return '未授权通知权限（浏览器设置 → 通知 → 允许本站）'
    if (/InvalidAccessError|applicationServerKey/i.test(raw))
      return '推送公钥校验失败（服务端配置问题，请联系管理员）'
    return raw ? '开启失败：' + raw : '开启失败'
  }

  const disable = async () => {
    if (busy.value) return false
    busy.value = true
    message.value = ''
    try {
      const reg = await navigator.serviceWorker.ready
      const sub = await reg.pushManager.getSubscription()
      if (sub) {
        const endpoint = sub.endpoint
        await sub.unsubscribe()
        await pushUnsubscribe({ endpoint })
      } else {
        await pushUnsubscribe({})
      }
      subscribed.value = false
      return true
    } catch (e) {
      message.value = (e && e.message) || '关闭失败'
      return false
    } finally {
      busy.value = false
    }
  }

  return { supported, subscribed, busy, message, refresh, enable, disable }
}
