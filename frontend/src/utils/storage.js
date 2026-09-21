// localStorage 统一封装(2026-09-21): 集中 JSON 序列化 + try/catch 样板。
// key **原样传入**(不强制加前缀) —— 保持既有存储 key 兼容, 避免用户已锁定的数据读不到。
// 未来新增存储统一走这里, 便于容量/版本治理与异常留痕。

/**
 * 读 JSON 值; 不存在/解析失败返回 fallback(默认 null)。
 * @param {string} key
 * @param {*} [fallback]
 */
export function safeJsonGet(key, fallback = null) {
  try {
    const raw = localStorage.getItem(key)
    if (raw == null) return fallback
    return JSON.parse(raw)
  } catch (e) {
    return fallback
  }
}

/**
 * 写 JSON 值; 成功返回 true, 失败(配额满/禁用)返回 false 且不抛。
 * @param {string} key
 * @param {*} value
 */
export function safeJsonSet(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
    return true
  } catch (e) {
    return false
  }
}

/**
 * 删 key; 成功返回 true。
 * @param {string} key
 */
export function safeRemove(key) {
  try {
    localStorage.removeItem(key)
    return true
  } catch (e) {
    return false
  }
}
