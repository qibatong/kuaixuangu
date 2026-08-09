// 历史相关 API
import { request } from './request'

export function queryHistory(params, page = 1, pageSize = 100) {
  return request('/api/history/query', { query: { ...params, page, pageSize } })
}

export function listBatches(batchId) {
  const q = batchId ? { batch: batchId } : {}
  return request('/api/history', { query: q })
}
