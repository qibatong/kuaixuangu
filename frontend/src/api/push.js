// WebPush 订阅接口（2026-10-04 手机端真推送）
// 后端见 backend/app/api/push.py（同一天上线，改动两边一起看）
import { request } from './request'

/** 服务端 VAPID 公钥（订阅前必须拿到，浏览器用它识别"谁是发送方"） */
export function fetchVapidKey() {
  return request('/api/push/vapid-public-key')
}

/** 当前账号是否已订阅、有几个设备 */
export function fetchPushStatus() {
  return request('/api/push/status')
}

/**
 * 上报订阅。payload = { endpoint, keys: {p256dh, auth}, ua }
 * 直接传 `PushSubscription.toJSON()` 的结果即可。
 */
export function pushSubscribe(payload) {
  return request('/api/push/subscribe', { method: 'POST', body: payload })
}

/** 退订：传 endpoint = 退该设备；不传 = 该账号全部设备 */
export function pushUnsubscribe(payload) {
  return request('/api/push/unsubscribe', { method: 'POST', body: payload })
}
