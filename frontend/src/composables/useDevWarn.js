// 「异动风险」标签集合：给**选股名单/表格**标出按交易所口径算出的异动/停牌风险。
//
// 与 `useYidongMonitor` 的分工（两者语义不同，**互不覆盖**）：
//   useYidongMonitor = 开盘啦/交易所**已经公布**的监管名单（严重异动/重点监控）
//   本模块           = 我们自己**按 5.4.2 口径算出来**的「风险预告」（会不会越线）
// 所以表格里是两个独立徽章：橙色「严重异动」（已公布） vs 红/黄「异动风险」（我方预算）。
//
// 级别（后端 services/dev_risk.warn_of，★ 两级互斥且都可达）：
//   red    = 今天**已经**越过任一条偏离值异动线
//   yellow = 尚未越线，但明日涨停（或所需涨幅 ≤ 涨停）即首次越线，或已进入「临近」带
//
// 数据源 `/api/dev/tomorrow`（盘后 15:45 由 kx-worker 全市场扫描落 dev_risk_daily）。
// 模块级单例：任一页面拉一次，全应用共享。
import { onMounted, ref } from 'vue'
import { devTomorrow } from '../api/dev'

const Label = { red: '异动风险', yellow: '异动临近' }
const Tip = {
  red: '今日已越过 3/10/30 日涨跌幅偏离值异动线，注意异常波动核查风险',
  yellow: '明日涨停（或再小幅上涨）即越过偏离值异动线，或已临近阈值',
}

const warnMap = ref(new Map())      // code -> { level, msg }
const loading = ref(false)
const failed = ref(false)
const date = ref('')
let lastTs = 0
const TTL_MS = 10 * 60 * 1000

// 拉取明日预警名单（默认只含 red/yellow）。
// ★ 不要把「空列表」当成「已加载」—— 结果表当天没生成时会返回空，必须按 TTL 重试，
//   否则页面会把「今天还没有结果」永久显示成「今天没有风险」。
async function refreshDevWarn() {
  if (loading.value) return
  loading.value = true
  failed.value = false
  try {
    const d = await devTomorrow()
    const map = new Map()
    const list = (d && d.list) || []
    for (const it of list) {
      const c = String((it && it.code) || '')
      const lvl = String((it && it.warn_level) || '').trim()
      if (c && (lvl === 'red' || lvl === 'yellow')) {
        map.set(c, { level: lvl, msg: (it && it.warn_msg) || Tip[lvl] || '' })
      }
    }
    warnMap.value = map
    date.value = (d && d.date) || ''
  } catch (e) {
    failed.value = true
  } finally {
    lastTs = Date.now()
    loading.value = false
  }
}

// 按 TTL 惰性加载：已加载 10 分钟内不重复请求；结果为空时 1 分钟后允许重试。
function ensureDevWarn() {
  if (loading.value) return
  const stale = warnMap.value.size ? TTL_MS : 60 * 1000
  if (!lastTs || Date.now() - lastTs > stale) refreshDevWarn()
}

// 返回 '' | 'red' | 'yellow'
function devWarn(code) {
  if (code === null || code === undefined) return ''
  const it = warnMap.value.get(String(code))
  return it ? it.level : ''
}

function devWarnLabel(code) {
  const lvl = devWarn(code)
  return lvl ? Label[lvl] || '' : ''
}

function devWarnTitle(code) {
  if (code === null || code === undefined) return ''
  const it = warnMap.value.get(String(code))
  if (!it) return ''
  return it.msg || Tip[it.level] || ''
}

export function useDevWarn() {
  return { warnMap, loading, failed, date, refreshDevWarn, ensureDevWarn,
           devWarn, devWarnLabel, devWarnTitle }
}

/**
 * 组件内挂载即惰性拉取（★ 必须放 onMounted，不能放 setup 顶层）：
 *   SSR/单测里 setup 会执行、onMounted 不会 —— 顶层拉取会在 Node 里发真实 fetch。
 */
export function useDevWarnAutoLoad() {
  const api = useDevWarn()
  onMounted(() => { api.ensureDevWarn() })
  return api
}
