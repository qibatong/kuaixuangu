// 简单内存缓存: 接口数据短时间缓存, 避免路由切换重复拉
const cache = new Map()
const inflight = new Map()

export function cacheGet(key, ttl = 120000) {
  const hit = cache.get(key)
  if (!hit) return null
  if (Date.now() - hit.time > ttl) {
    cache.delete(key)
    return null
  }
  return hit.data
}

export function cacheSet(key, data) {
  cache.set(key, { data, time: Date.now() })
}

export function cacheClear(prefix) {
  for (const k of cache.keys()) {
    if (!prefix || k.startsWith(prefix)) cache.delete(k)
  }
}

// 请求去重: 同时发相同 key 的请求复用同一个 Promise
export async function cachedFetch(key, fetcher, ttl = 120000) {
  const cached = cacheGet(key, ttl)
  if (cached) return cached
  if (inflight.has(key)) return inflight.get(key)
  const p = (async () => {
    try {
      const data = await fetcher()
      cacheSet(key, data)
      return data
    } finally {
      inflight.delete(key)
    }
  })()
  inflight.set(key, p)
  return p
}
