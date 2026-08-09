// 前端错误日志: 全局异常捕获 + localStorage 环形缓冲(最近 50 条)
// 出问题时可打开控制台或联系管理员导出
const LOG_KEY = 'kuaixuan_front_log'
const MAX = 50

export function logFront(level, msg, extra) {
  let line
  try {
    line = {
      t: new Date().toISOString(),
      lvl: level,
      msg: String(msg).slice(0, 500),
      extra: extra ? JSON.stringify(extra).slice(0, 500) : ''
    }
  } catch (e) { return }
  try {
    const arr = JSON.parse(localStorage.getItem(LOG_KEY) || '[]')
    arr.push(line)
    while (arr.length > MAX) arr.shift()
    localStorage.setItem(LOG_KEY, JSON.stringify(arr))
  } catch (e) { /* 存储不可用时仅 console */ }
  if (level === 'error') console.error(`[bid] ${msg}`, extra || '')
  else console.log(`[bid] ${msg}`, extra || '')
}

// 在 main.js 里调用一次即可
export function setupGlobalErrorCapture() {
  window.addEventListener('error', (e) => {
    logFront('error', `JS错误: ${e.message} @ ${e.filename}:${e.lineno}`, { stack: (e.error && e.error.stack || '').slice(0, 300) })
  })
  window.addEventListener('unhandledrejection', (e) => {
    const r = e.reason
    logFront('error', `未处理的Promise拒绝: ${r && r.message ? r.message : String(r)}`)
  })
}
