// 邀请相关 API
import { request } from './request'

export function getInvite() {
  return request('/api/invite')
}

export function refreshInvite() {
  return request('/api/invite/refresh', { method: 'POST', body: {} })
}
