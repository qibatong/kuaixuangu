<template>
  <div class="page-shell">
    <!-- 会员门禁(2026-08-27): AI 竞价预测仅 VIP/付费会员可用 -->
    <VipGate v-if="!user.isVipOrPaid" title="AI竞价预测" :required-level="1" />

    <template v-else>
      <div class="ap-panel">
        <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载预测报告...</div></div>
        <div v-else-if="empty" class="empty-state">暂无预测报告，交易日 9:30 前自动生成</div>
        <template v-else-if="hasData && !rows.length">
          <div class="empty-state ap-empty-rule">
            <div>当前规则下没有符合的股票</div>
            <button class="ap-empty-reset" @click="resetRule"><i class="fa fa-undo"></i> 恢复默认规则</button>
            <div class="ap-empty-hint">可放宽市值范围 / 降低竞价金额下限、提高竞价涨幅上限</div>
          </div>
        </template>

        <template v-else-if="rows.length">
          <div class="ap-report-head">
            <div class="ap-report-title-row">
              <div class="ap-report-title">
                <i class="fa fa-chart-line ap-report-icon"></i>
                AI 竞价选股 · 涨停概率预测
                <span class="ap-date-badge">{{ data.date }}</span>
                <span class="ap-count-chip">{{ shownCount }} 只</span>
              </div>
              <div class="ap-toolbar">
                <!-- 隐藏日期状态文字, 已去掉 -->
                <button
                  class="rot-date-btn"
                  title="选择日期回看历史报告"
                >
                  <i class="fa fa-calendar"></i> {{ selDate || latestDate || '选择日期' }}
                  <!-- 透明 input 覆盖整个按钮: 用户实际点到 input 本身触发原生日历, 兼顾桌面/iOS/Android, 不依赖 showPicker() -->
                  <input
                    type="date"
                    class="rot-date-hidden"
                    :max="maxDate"
                    v-model="selDate"
                    title="回看历史预测报告"
                    @change="loadReport"
                    @click="onDateInputClick"
                  >
                </button>
                <button class="rot-reset-btn" title="回到最新" @click="resetLatest()"><i class="fa fa-bolt"></i></button>
                <a
                  class="rot-open-btn"
                  :href="'/aipick/latest.html?token=' + encodeURIComponent(user.apiToken)"
                  target="_blank"
                  title="新窗口打开完整报告"
                ><i class="fa fa-external-link"></i> 完整报告</a>
              </div>
            </div>
            <div class="ap-rulebar">
              <span class="ap-rule-label">流通市值</span>
              <input class="ap-rule-in" type="number" inputmode="decimal" step="10" v-model="mvMin" placeholder="30">
              <span class="ap-rule-sep">~</span>
              <input class="ap-rule-in" type="number" inputmode="decimal" step="100" v-model="mvMax" placeholder="500">
              <span class="ap-rule-unit">亿</span>
              <span class="ap-rule-label">竞价金额≥</span>
              <input class="ap-rule-in" type="number" inputmode="decimal" step="100" v-model="amtMin" placeholder="2000">
              <span class="ap-rule-unit">万</span>
              <span class="ap-rule-label">竞价涨幅≤</span>
              <input class="ap-rule-in" type="number" inputmode="decimal" v-model="chgMax" placeholder="10">
              <span class="ap-rule-unit">%</span>
              <span class="ap-rule-n">{{ shownCount }} 只</span>
              <button class="ap-rule-reset" title="恢复默认规则" @click="resetRule"><i class="fa fa-undo"></i></button>
              <span class="ap-rule-tip">留空表示不限；回车即时过滤</span>
            </div>
            <div class="ap-report-sub">
              模型：快选・金睛 · 预测当日涨停概率 · 规则可自定义后实时过滤 · 仅供研究参考
            </div>
          </div>

          <div class="ap-table-scroll">
            <table class="stock-table ap-stock-table">
              <thead>
                <tr>
                  <th @click="toggleSort('#')" :class="thCls('#')">#</th>
                  <th @click="toggleSort('code')" :class="thCls('code')">代码</th>
                  <th @click="toggleSort('name')" :class="thCls('name')">名称</th>
                  <th @click="toggleSort('ai_prob')" :class="thCls('ai_prob')">AI涨停概率</th>
                  <th @click="toggleSort('bid_change')" :class="thCls('bid_change')">竞价涨幅</th>
                  <th @click="toggleSort('bid_amount')" :class="thCls('bid_amount')">竞价金额</th>
                  <th @click="toggleSort('circ_mv')" :class="thCls('circ_mv')">流通市值</th>
                  <th @click="toggleSort('bid_turnover')" :class="thCls('bid_turnover')">换手率</th>
                  <th class="ap-th" title="开盘啦概念">概念</th>
                  <th v-if="isLatest" @click="toggleSort('realtime')" :class="thCls('realtime')">实时涨幅</th>
                  <th v-else @click="toggleSort('day_change')" :class="thCls('day_change')">当日涨幅</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in rows" :key="r.code + i">
                  <td class="rank-col">{{ i + 1 }}</td>
                  <td class="code-click">{{ r.code }}</td>
                  <td class="name-col"><div class="name-main">{{ r.name }}</div></td>
                  <td><span class="score-badge" :class="probCls(r.ai_prob)">{{ (r.ai_prob * 100).toFixed(1) }}%</span></td>
                  <td :class="chgCls(r.bid_change)">{{ fmtChg(r.bid_change) }}</td>
                  <td>{{ fmtAmt(r.bid_amount) }}</td>
                  <td>{{ (r.circ_mv || 0).toFixed(1) }}亿</td>
                  <td>{{ (r.bid_turnover || 0).toFixed(2) }}%</td>
                  <td class="concept-col" :title="conceptFull(r) || '暂无概念'">{{ conceptText(r) }}</td>
                  <td v-if="isLatest" :class="chgCls(rt(r))">{{ rtText(r) }}</td>
                  <td v-else :class="chgCls(r.day_change)">{{ dayChgText(r) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="ap-note">数据仅供研究参考，不构成任何投资建议</div>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useUserStore } from '../stores/user'
import VipGate from '../components/VipGate.vue'
import { aipickDates, aipickData, aipickRealtime } from '../api/aipick'

const user = useUserStore()
const dates = ref([])
const latestDate = ref('')
const selDate = ref('')
const data = ref({ date: '', count: 0, top: [] })
const quotes = ref({})            // 实时行情: code -> {change}
const loading = ref(false)
const empty = ref(false)

// 日期选择上限(今天, 本地时区); 避免选到未来日期
function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const maxDate = todayStr()

// 日期器兜底: 透明 input 覆盖按钮时, 多数浏览器会点中 input 自动弹日历;
// 但部分 iOS Safari/WebView 对 opacity:0 的 input 不触发弹层, 这里用 showPicker() 显式拉起兜底.
function onDateInputClick(e) {
  const el = e.target
  if (el && typeof el.showPicker === 'function') {
    try { el.showPicker() } catch { /* picker 已打开或浏览器禁止, 忽略 */ }
  }
}

const isLatest = computed(() => !selDate.value)

// ===== 用户可调的规则过滤(默认与生成脚本一致) =====
const mvMin = ref(30)     // 市值下限(亿)
const mvMax = ref(500)    // 市值上限(亿)
const amtMin = ref(2000)  // 竞价金额下限(万)
const chgMax = ref(10)     // 竞价涨幅上限(%)

// 全量候选集(生成脚本保存过滤前结果); 旧报告无 all 时回退已过滤的 top
const base = computed(() => {
  const all = data.value.all
  if (Array.isArray(all) && all.length) return all
  return data.value.top || []
})

// 规则输入解析: 空串视为不限, 非法视为不限
function num(v) {
  if (v === null || v === undefined || String(v).trim() === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

// 按用户规则过滤 + 按 ai_prob 降序
const filtered = computed(() => {
  const rMin = num(mvMin.value), rMax = num(mvMax.value)
  const aMin = num(amtMin.value), cMax = num(chgMax.value)
  const arr = base.value.filter(r => {
    const mv = Number(r.circ_mv)
    const amt = Number(r.bid_amount)
    const chg = Number(r.bid_change)
    if (!Number.isFinite(mv)) return true
    if (rMin !== null && mv < rMin) return false
    if (rMax !== null && mv > rMax) return false
    if (aMin !== null && Number.isFinite(amt) && amt < aMin) return false
    if (cMax !== null && Number.isFinite(chg) && chg > cMax) return false
    return true
  })
  return arr.sort((a, b) => (Number(b.ai_prob) || 0) - (Number(a.ai_prob) || 0))
})

// 满足规则的候选总数(用于头部展示)
const shownCount = computed(() => filtered.value.length)

// 是否已有候选数据(区分"规则滤空"与"根本没有报告")
const hasData = computed(() => base.value.length > 0)

// ===== 列排序 =====
const sortKey = ref('ai_prob')   // 当前排序列; '#' 表示回到默认(概率降序)
const sortDir = ref('desc')      // asc / desc
const NUM_COLS = ['ai_prob', 'bid_change', 'bid_amount', 'circ_mv', 'bid_turnover', 'realtime', 'day_change']

// 取某行某列的排序值; day_change/realtime 可能为 null(未拉到) → 视为最小排后
function sortVal(r, k) {
  switch (k) {
    case 'code': return r.code
    case 'name': return r.name
    case 'ai_prob': return Number(r.ai_prob) || 0
    case 'bid_change': return Number(r.bid_change) || 0
    case 'bid_amount': return Number(r.bid_amount) || 0
    case 'circ_mv': return Number(r.circ_mv) || 0
    case 'bid_turnover': return Number(r.bid_turnover) || 0
    case 'day_change': return r.day_change == null ? null : Number(r.day_change)
    case 'realtime': { const q = quotes.value[r.code]; return (q && q.change != null) ? Number(q.change) : null }
    default: return 0
  }
}

function sortCompare(a, b) {
  const va = sortVal(a, sortKey.value), vb = sortVal(b, sortKey.value)
  let cmp
  if (va == null && vb == null) cmp = 0
  else if (va == null) cmp = 1
  else if (vb == null) cmp = -1
  else if (typeof va === 'string' && typeof vb === 'string') cmp = va.localeCompare(vb, 'zh')
  else cmp = (Number(va) || 0) - (Number(vb) || 0)
  return sortDir.value === 'asc' ? cmp : -cmp
}

function toggleSort(key) {
  if (key === '#') { sortKey.value = 'ai_prob'; sortDir.value = 'desc'; return }
  if (sortKey.value === key) { sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'; return }
  sortKey.value = key
  sortDir.value = NUM_COLS.includes(key) ? 'desc' : 'asc'
}
function thCls(key) {
  if (sortKey.value !== key) return 'ap-th'
  return sortDir.value === 'asc' ? 'ap-th sort-asc' : 'ap-th sort-desc'
}

// 展示行: 限制条数(兼顾性能与实时行情接口单次上限)
const MAX_ROWS = 150
// 展示行: 按当前排序键排序后截断前 MAX_ROWS
const rows = computed(() => {
  const arr = filtered.value.slice()
  if (sortKey.value && sortKey.value !== '#') arr.sort(sortCompare)
  return arr.slice(0, MAX_ROWS)
})

// 规则/展示行变化 → 增量补拉新增股票的实时行情(仅最新报告)
watch(filtered, () => { if (!selDate.value) scheduleRealtime() })

// 重置为默认规则
function resetRule() {
  mvMin.value = 30
  mvMax.value = 500
  amtMin.value = 2000
  chgMax.value = 10
  saveRules()   // 明确落盘(下方 watch 也会触发, 双保险)
}

// ===== 规则持久化(localStorage): 记住用户筛选, 再次进入直接套用 =====
const RULES_KEY = 'kx_aipick_rules'
// 默认规则(与生成脚本一致)。一次仅供"未自定义"用户跟随最新默认。
const NEW_DEFAULT = [30, 500, 2000, 10]
// 历史默认集合: 凡本地值等于任意历史默认(视为从未自定义) → 升级到最新默认
const HIST_DEFAULTS = [
  [30, 100, 3000, 7],
  [30, 500, 2000, 7],
]

function loadRules() {
  try {
    const raw = localStorage.getItem(RULES_KEY)
    if (!raw) return
    const s = JSON.parse(raw)
    const cur = [s.mvMin, s.mvMax, s.amtMin, s.chgMax]
    // 等于任一历史默认 → 认定为仍用默认, 迁移到最新默认并回写
    if (HIST_DEFAULTS.some(d => cur.every((v, i) => v === d[i]))) {
      [mvMin.value, mvMax.value, amtMin.value, chgMax.value] = NEW_DEFAULT
      saveRules()
      return
    }
    if (s.mvMin !== undefined) mvMin.value = s.mvMin
    if (s.mvMax !== undefined) mvMax.value = s.mvMax
    if (s.amtMin !== undefined) amtMin.value = s.amtMin
    if (s.chgMax !== undefined) chgMax.value = s.chgMax
  } catch (e) { /* 损坏/不可用则用默认 */ }
}

function saveRules() {
  try {
    localStorage.setItem(RULES_KEY, JSON.stringify({
      mvMin: mvMin.value, mvMax: mvMax.value,
      amtMin: amtMin.value, chgMax: chgMax.value,
    }))
  } catch (e) { /* 隐私模式等不可写, 忽略 */ }
}

// 任何规则变化都自动保存
watch([mvMin, mvMax, amtMin, chgMax], saveRules)

// 当前查看标签: 最新 vs 回看某天
const viewDateLabel = computed(() => {
  if (selDate.value) return `回看 ${selDate.value}`
  return `最新 ${latestDate.value || '--'}`
})

// ===== 格式化 =====
// 竞价金额: ≥1亿(10000万) 用"亿", 否则用"万"
function fmtAmt(v) {
  if (v == null) return '--'
  const n = Number(v)
  if (n >= 10000) return (n / 10000).toFixed(2) + '亿'
  return Math.round(n) + '万'
}
function fmtChg(v) {
  if (v == null) return '--'
  return (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%'
}
// 概念列: 默认显示前2个; title 悬浮显示全部(conceptFull), 不展示概念总数
const conceptText = (r) => {
  const c = Array.isArray(r.concepts) ? r.concepts : []
  if (c.length) return c.slice(0, 2).join('、')
  return r.concept || '--'
}
const conceptFull = (r) => {
  const c = Array.isArray(r.concepts) ? r.concepts : []
  if (c.length) return c.join('、')
  return r.concept || ''
}
// 实时涨幅
function rt(row) {
  const q = quotes.value[row.code]
  return q && q.change != null ? Number(q.change) : null
}
function rtText(row) {
  const v = rt(row)
  return v == null ? '--' : fmtChg(v)
}
// 历史日期的当日涨幅(由后端按该交易日收盘相对前收盘算出)
function dayChgText(row) {
  return row.day_change == null ? '--' : fmtChg(row.day_change)
}
function chgCls(v) {
  if (v == null) return ''
  return v >= 0 ? 'up' : 'down'
}
// AI涨停概率分级徽章颜色(和股性分徽章一致, 收敛红色滥用)
function probCls(prob) {
  const p = Number(prob || 0) * 100
  if (p >= 90) return 'score-high'
  if (p >= 70) return 'score-mid'
  if (p >= 50) return 'score-low'
  return 'score-dim'
}

// ===== 加载 =====
let rtTimer = null
let rtPending = null

async function loadDates() {
  try {
    const d = await aipickDates()
    dates.value = d.dates || []
    latestDate.value = dates.value[0] || ''
  } catch (e) { /* 403/网络由 request 处理 */ }
}

function resetLatest() {
  selDate.value = ''
  loadReport()
}

async function loadReport() {
  loading.value = true
  empty.value = false
  data.value = { date: '', count: 0, top: [] }
  quotes.value = {}
  fetchedCodes.clear()
  stopRealtime()
  try {
    const r = await aipickData(selDate.value)
    data.value = r.data || { date: '', count: 0, top: [] }
  } catch (e) {
    empty.value = true
  } finally {
    loading.value = false
    startRealtime()
  }
}

// ===== 实时涨幅 =====
// 报告日期是 9:25 竞价快照(历史数据), 此处动态拉取东财实时行情, 追加"实时涨幅"列, 每 30s 刷新.
// 已请求过行情的 code 集合: 只在初次和筛选后新增股票时补拉, 避免每次全量重拉.
const fetchedCodes = new Set()

async function refreshRealtime() {
  const codes = rows.value.map(r => r.code).filter(c => /^\d{6}$/.test(c))
  const missing = codes.filter(c => !fetchedCodes.has(c))
  if (!missing.length) return
  try {
    const r = await aipickRealtime(missing)
    if (r && r.quotes) {
      quotes.value = { ...quotes.value, ...(r.quotes || {}) }
      missing.forEach(c => { if (r.quotes && r.quotes[c]) fetchedCodes.add(c) })
    }
  } catch { /* 拉取失败保持现状 */ }
}

// 规则/日期变化导致展示行变化 → 增量补拉新增股票的实时行情(带 250ms 节流)
function scheduleRealtime() {
  if (rtPending) clearTimeout(rtPending)
  rtPending = setTimeout(() => { rtPending = null; refreshRealtime() }, 250)
}

// 最新报告才需要实时行情; 历史报告留竞价快照即可
function startRealtime() {
  stopRealtime()
  if (selDate.value) return
  refreshRealtime()
  rtTimer = setInterval(refreshRealtime, 30000)
}

function stopRealtime() {
  if (rtTimer) { clearInterval(rtTimer); rtTimer = null }
  if (rtPending) { clearTimeout(rtPending); rtPending = null }
}

onMounted(() => {
  loadRules()
  loadDates()
  loadReport()
})
onUnmounted(stopRealtime)
</script>

<style scoped>
.ap-panel {
  background: var(--bg-hover);
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  padding: 14px;
  overflow: hidden;
}
.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner {
  width: 28px; height: 28px;
  border: 3px solid rgba(var(--accent-rgb), 0.3);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 10px;
}
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); }
.ap-empty-rule { display: flex; flex-direction: column; align-items: center; gap: 14px; }
.ap-empty-rule > div:first-child { color: var(--text-secondary); font-weight: 600; }
.ap-empty-reset {
  padding: 9px 20px; border-radius: 8px;
  border: 1px solid var(--accent-border);
  background: var(--accent-bg);
  color: var(--accent-deep);
  cursor: pointer;
  font-size: 14px; font-weight: 600;
  display: inline-flex; align-items: center; gap: 6px;
}
.ap-empty-reset:hover { border-color: var(--accent); }
.ap-empty-hint { font-size: 12px; }

/* 报告头部: 标题左, 日期选择工具栏右, 一行排布, 小屏自动折行 */
.ap-report-head {
  display: flex; flex-direction: column; gap: 6px;
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-soft);
}
.ap-report-title-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap;
}
.ap-report-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-main);
  display: inline-flex; align-items: center; gap: 10px; flex-wrap: wrap;
}
.ap-report-icon { color: var(--accent); }
.ap-date-badge {
  display: inline-block;
  background: var(--accent-bg);
  border: 1px solid var(--accent-border);
  color: var(--accent-deep);
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 600;
}
.ap-count-chip {
  display: inline-block;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  color: var(--text-secondary);
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 12px;
}
.ap-toolbar {
  display: inline-flex; align-items: center; gap: 10px;
}
.ap-data-date { color: var(--text-muted); font-size: 13px; }
/* 透明 input 覆盖整个日期按钮: 保留下划线显示, opacity 0 让视觉只显示按钮文案,
   用户点击会直接命中 input, 原生弹日历. 不使用 width:0/left:-9999px(那会让 showPicker 失效). */
.rot-date-hidden {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
  border: 0;
  padding: 0;
  box-sizing: border-box;
}
/* 样式化的日期选择按钮, 和"最新"按钮视觉一致, 更精致 */
.rot-date-btn {
  position: relative;
  padding: 7px 14px; border-radius: 8px;
  border: 1px solid var(--border-soft);
  background: var(--bg-card);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
  display: inline-flex; align-items: center; gap: 6px;
  font-family: Consolas, Menlo, monospace;
  transition: all 0.15s;
  overflow: hidden;
}
.rot-date-btn:hover { color: var(--accent); border-color: var(--accent); }
.rot-date-btn .fa { opacity: 0.75; }
.rot-reset-btn {
  padding: 6px 10px; border-radius: 8px;
  border: 1px solid var(--border-soft);
  background: var(--bg-hover);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
}
.rot-reset-btn:hover { color: var(--accent); border-color: var(--accent); }
.rot-open-btn {
  padding: 6px 10px; border-radius: 8px;
  border: 1px solid var(--border-soft);
  background: var(--bg-hover);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
  text-decoration: none;
  display: inline-flex; align-items: center; gap: 4px;
  white-space: nowrap;
}
.rot-open-btn:hover { color: var(--accent); border-color: var(--accent); }

.ap-report-sub {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.8;
}
/* 用户可调规则过滤栏 */
.ap-rulebar {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px 8px;
  margin-bottom: 10px;
  padding: 8px 10px;
  background: var(--bg-main);
  border: 1px dashed var(--border-soft);
  border-radius: 8px;
}
.ap-rule-label { color: var(--text-muted); font-size: 12px; }
.ap-rule-in {
  width: 74px;
  padding: 5px 8px;
  border-radius: 6px;
  border: 1px solid var(--border-soft);
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 13px;
  font-family: Consolas, Menlo, monospace;
  text-align: center;
  color-scheme: dark;
}
body[data-bg="light"] .ap-rule-in { color-scheme: light; }
.ap-rule-in:focus { outline: none; border-color: var(--accent); }
.ap-rule-sep { color: var(--text-muted); }
.ap-rule-unit { color: var(--text-muted); font-size: 12px; }
.ap-rule-n {
  color: var(--accent-deep); font-weight: 700; font-size: 13px;
  background: var(--accent-bg); border: 1px solid var(--accent-border);
  padding: 2px 10px; border-radius: 12px; margin-left: 2px;
}
.ap-rule-reset {
  padding: 5px 10px; border-radius: 6px;
  border: 1px solid var(--border-soft);
  background: var(--bg-hover);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
}
.ap-rule-reset:hover { color: var(--accent); border-color: var(--accent); }
.ap-rule-tip { color: var(--text-muted); font-size: 11px; }
.ap-stock-table {
  /* 股性/连板页 stock-table 默认居中; AI预测表格数据统一居中展示 */
  text-align: center;
}
.ap-stock-table thead th,
.ap-stock-table tbody td {
  text-align: center;
}
/* 表头可点击排序 + 方向箭头 */
.ap-th { cursor: pointer; user-select: none; white-space: nowrap; }
.ap-th:hover { color: var(--accent); }
.ap-th.sort-asc::after { content: ' ↑'; color: var(--accent); font-size: 11px; }
.ap-th.sort-desc::after { content: ' ↓'; color: var(--accent); font-size: 11px; }
/* 概念列: 限宽省略, 悬浮(title)显示全部 */
.concept-col {
  max-width: 170px; min-width: 90px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--text-secondary);
}
/* 桌面无额外包装, 直接继承父容器宽. 手机端启用为唯一横向滚动容器(见 @media) */
.ap-table-scroll { }
.ap-note {
  color: var(--text-muted);
  font-size: 12px;
  margin-top: 14px;
  text-align: right;
}

/* AI 涨停概率徽章(复用股性页 score-badge 体系 + 我们的自定义级别色) */
.score-badge {
  display: inline-block;
  min-width: 60px;
  text-align: center;
  padding: 3px 8px;
  border-radius: 8px;
  font-weight: 700;
  font-size: 12.5px;
  font-family: Consolas, monospace;
}
.score-high {
  background: var(--accent-bg2);
  color: var(--accent-deep);
  border: 1px solid var(--accent-border);
}
.score-mid {
  background: rgba(255, 160, 40, 0.12);
  color: #e08820;
  border: 1px solid rgba(255, 160, 40, 0.4);
}
.score-low {
  background: rgba(255, 215, 0, 0.10);
  color: #c79100;
  border: 1px solid rgba(255, 215, 0, 0.4);
}
.score-dim {
  background: var(--bg-input);
  color: var(--text-muted);
  border: 1px solid var(--border-soft);
}

@media (max-width: 768px) {
  /* 报告头部: 标题/徽章/工具栏三段式折行, 减少视觉噪音 */
  .ap-report-head { margin-bottom: 10px; padding-bottom: 10px; }
  .ap-report-title-row { gap: 8px; }
  .ap-report-title {
    font-size: 15px;
    gap: 8px;
    row-gap: 6px;
    width: 100%;
    flex: 1 1 100%;
  }
  .ap-report-icon { margin-right: 2px; }
  .ap-date-badge, .ap-count-chip {
    font-size: 11.5px;
    padding: 2px 8px;
  }
  .ap-report-sub {
    font-size: 11.5px;
    line-height: 1.6;
    padding-top: 2px;
  }
  /* 工具栏: 独立一行, 按钮/日期触控面积 ≥ 40px, 左对齐方便单手 */
  .ap-toolbar {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
    gap: 8px;
  }
  .ap-data-date {
    width: 100%;
    font-size: 12px;
    padding-left: 2px;
  }
  /* 移动端日期按钮和"最新"按钮同为 40px 触控高, 并排分布 */
  .rot-date-btn {
    flex: 1 1 auto;
    min-width: 0;
    height: 40px;
    padding: 6px 12px;
    font-size: 13px;
    justify-content: center;
    gap: 6px;
  }
  .rot-reset-btn {
    flex: 0 0 auto;
    min-width: 40px;
    width: 40px;
    height: 40px;
    padding: 0;
    font-size: 13px;
    display: inline-flex; align-items: center; justify-content: center;
  }

  /* 单一横向滚动策略(经验 1573633): 外层.表格包装器 作为唯一 x 滚动容器, 表格保持 min-width 避免列挤压 */
  .ap-panel {
    padding: 10px 8px;
  }
  .ap-table-scroll {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    margin: 0 -8px;
    padding: 0 8px 4px;
  }
  .ap-stock-table {
    min-width: 1080px;     /* 9 列 + 徽章需要更多宽度, 防止字段换行或被压瘪 */
    font-size: 12px;
  }
  .ap-stock-table thead th {
    position: sticky; top: 0; z-index: 1;
    padding: 8px 6px;
    white-space: nowrap;
    font-size: 12px;
  }
  .ap-stock-table tbody td {
    padding: 8px 6px;
    white-space: nowrap;
  }
  .ap-stock-table tbody tr { height: 44px; } /* 触控友好行高 */

  /* 手机端收紧徽章, 保留最小可识别宽度 */
  .score-badge {
    min-width: 52px;
    padding: 2px 6px;
    font-size: 11.5px;
    border-radius: 6px;
  }
  .ap-note {
    margin-top: 10px;
    font-size: 11px;
    text-align: left;
  }
  /* 手机端规则栏紧凑换行, 输入框等宽 */
  .ap-rulebar { padding: 8px 6px; gap: 6px; }
  .ap-rule-in { width: 64px; padding: 5px 6px; font-size: 12.5px; }
  .ap-rule-tip { display: none; }
  .ap-rule-n { font-size: 12px; }
}

/* 极窄屏 (<= 390px, 比如 iPhone SE/小屏安卓): 工具栏自适应全屏宽度 */
@media (max-width: 390px) {
  .rot-date-btn { flex: 1 1 60%; }
  .rot-reset-btn { flex: 0 0 auto; width: 40px; }
}
</style>