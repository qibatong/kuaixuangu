// 异动监管代码集合: 首页选股 / 竞价异动 中标记"属于异动监管"的股票。
// 数据来自异动监管三个 tab 接口(异动实时 / 重点监控 / 多次异动)。
// 模块级单例: 任一页面刷新后全应用共享, 避免各组件重复请求。
import { ref } from 'vue'
import { kplYidongRealtime, kplYidongMonitor, kplYidongMulti } from '../api/kpl'

const ydCodes = ref(new Set())   // code -> true
const loading = ref(false)

// 抓取异动监管三个接口, 汇总所有 code; 接口可能要求会员(403), 静默容错
async function refreshYidongCodes() {
  if (loading.value) return
  loading.value = true
  try {
    const results = await Promise.all([
      kplYidongRealtime().catch(() => null),
      kplYidongMonitor().catch(() => null),
      kplYidongMulti().catch(() => null)
    ])
    const set = new Set()
    results.forEach(d => {
      const list = (d && d.list) || []
      list.forEach(it => {
        if (it && it.code) set.add(String(it.code))
      })
    })
    ydCodes.value = set
  } finally {
    loading.value = false
  }
}

// 是否属于异动监管
function isYidong(code) {
  if (code === null || code === undefined) return false
  return ydCodes.value.has(String(code))
}

export function useYidongMonitor() {
  return { ydCodes, loading, refreshYidongCodes, isYidong }
}