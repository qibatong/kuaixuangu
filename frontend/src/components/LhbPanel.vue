<template>
  <div class="lhb-panel">
    <!-- ★ 2026-10-04 第三版：**放弃树图**，按主人给的通达信手机端龙虎榜截图定标（三张图）：
         图1 = 榜单列表（股票/机构/营业部 三个 tab + 日期回看 + 涨幅/净买入可排序）
         图2/3 = 个股详情（指标行 + 上榜理由 + 买入营业部表 / 卖出营业部表，红买绿卖、
                 每行「主方向金额在上、另一方向在下」，底部买卖总计）。
         与截图的差异（数据源没有的字段，如实不做）：T 标(沪深股通)、「3日」榜标、
         「昨日买N/昨卖N」绿标、席位「关联」标记、K线头图、「会不会上龙虎榜」浮窗。
         口径：净买入 = 列表 buyIn（已对拍：万科A 71384560元 = 详情页 7138.46万 ✓）。 -->

    <!-- ============ 列表态 ============ -->
    <div v-show="view === 'list'">
      <div v-if="loading" class="lhb-loading">
        <div class="spinner"></div>
        <div>{{ tipText }}</div>
      </div>
      <div v-else-if="!list.length" class="lhb-empty">
        暂无数据 —— 当日龙虎榜 <b>17:00 后陆续披露</b>（此前为空，不拿昨日榜单顶上）
      </div>

      <template v-else>
        <div class="lhb-toolbar">
          <span class="lhb-count">今日上榜数: <b>{{ list.length }}</b></span>
          <input
            v-model="dateSel" type="date" class="lhb-date"
            :max="todayStr" aria-label="选择日期回看历史龙虎榜"
            @change="onDateChange"
          >
        </div>

        <div class="lhb-tabs" role="tablist">
          <button
            v-for="t in TABS" :key="t.key" role="tab"
            class="lhb-tab" :class="{ active: tab === t.key }"
            :aria-selected="tab === t.key ? 'true' : 'false'"
            @click="switchTab(t.key)"
          >{{ t.label }}</button>
        </div>

        <!-- ---- tab: 股票（图1） ---- -->
        <div v-if="tab === 'stock'" class="lhb-table">
          <div class="lhb-thead">
            <span class="c-name">股票名称</span>
            <span class="c-board">风口概念</span>
            <button class="c-chg th-sort" @click="toggleSort('chg')">
              涨幅<i class="fa fa-caret-down" :class="{ flip: sortKey === 'chg' && sortDir === 'asc', dim: sortKey !== 'chg' }"></i>
            </button>
            <button class="c-net th-sort" @click="toggleSort('net')">
              净买入<i class="fa fa-caret-down" :class="{ flip: sortKey === 'net' && sortDir === 'asc', dim: sortKey !== 'net' }"></i>
            </button>
          </div>
          <div
            v-for="r in sortedStocks" :key="r.code" class="lhb-row"
            role="button" tabindex="0" @click="openDetail(r)" @keydown.enter="openDetail(r)"
          >
            <div class="c-name">
              <div class="rn">{{ r.name }}</div>
              <div class="rc">{{ r.code }}</div>
            </div>
            <div class="c-board" :title="r.board">{{ concept2(r.board) || '-' }}</div>
            <div class="c-chg">
              <div class="num" :class="cls(r.change)">{{ pct(r.change) }}</div>
              <div v-if="lbText(r.limitBoards)" class="lb">{{ lbText(r.limitBoards) }}</div>
            </div>
            <div class="c-net num" :class="cls(r.buyIn)">{{ netShort(r.buyIn) }}</div>
          </div>
        </div>

        <!-- ---- tab: 机构（按机构净买排序; 数据 = lhb-tags 端点按需汇总） ---- -->
        <div v-else-if="tab === 'inst'" class="lhb-table">
          <div v-if="instLoading" class="lhb-loading"><div class="spinner"></div><div>正在按代码汇总机构/游资席位…</div></div>
          <template v-else>
            <div class="lhb-thead">
              <span class="c-name">股票名称</span>
              <span class="c-chg inst-chg">涨幅</span>
              <span class="c-net">机构净买</span>
              <span class="c-net">游资净买</span>
            </div>
            <div
              v-for="r in instRows" :key="r.code" class="lhb-row"
              role="button" tabindex="0" @click="openDetail(r)" @keydown.enter="openDetail(r)"
            >
              <div class="c-name">
                <div class="rn">{{ r.name }}</div>
                <div class="rc">{{ r.code }}</div>
              </div>
              <div class="c-chg inst-chg num" :class="cls(r.change)">{{ pct(r.change) }}</div>
              <div class="c-net num" :class="cls(r.instNet)">{{ r.instNet ? netShort(r.instNet) : '-' }}</div>
              <div class="c-net num" :class="cls(r.hotNet)">{{ r.hotNet ? netShort(r.hotNet) : '-' }}</div>
            </div>
            <div v-if="!instRows.length" class="lhb-seats-empty">
              {{ instErr ? ('机构汇总失败: ' + instErr) : '当日无机构/游资席位数据' }}
              <span class="lhb-retry" @click="instLoaded = false; loadInst()"> 重试</span>
            </div>
          </template>
        </div>

        <!-- ---- tab: 营业部（席位聚合; 点行展开它买的票） ---- -->
        <div v-else class="lhb-table">
          <div v-if="seatLoading" class="lhb-loading">
            <div class="spinner"></div>
            <div>正在拉取营业部明细… {{ seatProgress }}/{{ seatTopN }}</div>
          </div>
          <template v-else>
            <div class="lhb-thead">
              <span class="c-seat">营业部（席位）</span>
              <span class="c-net">买入</span>
              <span class="c-net">卖出</span>
              <span class="c-net">净额</span>
            </div>
            <template v-for="k in seatRows" :key="k.name">
              <div class="lhb-row seat-row" role="button" tabindex="0" @click="toggleSeat(k.name)" @keydown.enter="toggleSeat(k.name)">
                <div class="c-seat">
                  <div class="rn">
                    <i class="fa fa-caret-down seat-caret" :class="{ open: seatOpen === k.name }"></i>
                    {{ k.name }}
                    <span v-if="k.hot" class="tag tag-hot">游资</span>
                    <span v-if="k.inst" class="tag tag-inst">机构</span>
                  </div>
                  <div class="rc">涉及 {{ k.stocks.length }} 只</div>
                </div>
                <div class="c-net num up">{{ netShort(k.buy) }}</div>
                <div class="c-net num down">{{ netShort(k.sell) }}</div>
                <div class="c-net num" :class="cls(k.buy - k.sell)">{{ netShort(k.buy - k.sell) }}</div>
              </div>
              <!-- 展开该席位的个股明细 -->
              <div v-if="seatOpen === k.name" class="seat-sub">
                <div v-for="s in k.stocks" :key="s.code" class="seat-sub-row">
                  <span class="ss-name" role="button" tabindex="0" @click.stop="openDetailByCode(s)">{{ s.name }}</span>
                  <span class="ss-num up">{{ wan2(s.buy) }}</span>
                  <span class="ss-num down">{{ wan2(s.sell) }}</span>
                  <span class="ss-num" :class="cls(s.buy - s.sell)">{{ wan2(s.buy - s.sell) }}</span>
                </div>
              </div>
            </template>
            <div v-if="!seatRows.length" class="lhb-seats-empty">席位明细未取到（上游接口波动，稍后重试）</div>
            <div class="lhb-foot">按净买入前 {{ seatTopN }} 只个股的营业部明细聚合（非全市场席位统计）</div>
          </template>
        </div>
      </template>
    </div>

    <!-- ============ 个股详情（图2/3） ============ -->
    <div v-if="view === 'detail'" class="lhb-detail">
      <div class="lhb-d-head">
        <button class="lhb-back" @click="backToList"><i class="fa fa-arrow-left"></i> 返回榜单</button>
        <span class="lhb-d-name">{{ sel.name }} <b>{{ sel.code }}</b></span>
        <span class="lhb-d-chg num" :class="cls(sel.change)">{{ pct(sel.change) }}</span>
      </div>

      <div v-if="detLoading" class="lhb-loading"><div class="spinner"></div><div>正在拉取营业部明细…</div></div>
      <div v-else-if="!detail" class="lhb-empty">营业部明细拉取失败，<span class="lhb-retry" @click="reloadDetail">点此重试</span></div>

      <template v-else>
        <div class="lhb-d-stats">
          <div class="st"><span class="st-label">流通市值</span><span class="st-val">{{ yi(detail.floatMv || sel.floatMv) }}</span></div>
          <div class="st"><span class="st-label">换手率</span><span class="st-val">{{ pctPlain(detail.turnover || sel.turnover) }}</span></div>
          <div class="st"><span class="st-label">今日净买入</span><span class="st-val num" :class="cls(detail.buyIn != null ? detail.buyIn : sel.buyIn)">{{ netShort(detail.buyIn != null ? detail.buyIn : sel.buyIn) }}</span></div>
          <div class="st"><span class="st-label">关联营业部数</span><span class="st-val">{{ seatCount }}</span></div>
        </div>

        <div v-if="reasonClean(detail.upReason)" class="lhb-d-reason">
          <span class="rs-label">上榜理由</span>{{ reasonClean(detail.upReason) }}
        </div>

        <!-- 买入营业部（按买入额降序; 行内 上=买(红) 下=卖(绿)） -->
        <div class="lhb-d-side">
          <div class="lhb-d-sidetitle"><span>买入营业部</span><span class="dim">金额(万)</span></div>
          <div v-for="(s, i) in detail.buyList" :key="'b' + i" class="lhb-d-seat">
            <div class="seat-name">
              {{ s.name || '-' }}
              <span v-if="s.inst" class="tag tag-inst">机构</span>
              <span v-if="s.hot" class="tag tag-hot">游资</span>
            </div>
            <div class="seat-nums">
              <span class="num up">{{ wan2(s.buy) }}</span>
              <span class="num down">{{ wan2(s.sell) }}</span>
            </div>
          </div>
          <div class="lhb-d-total">买入总计 <b class="num up">{{ wan2(detail.buyTotal) }}</b> 万元</div>
        </div>

        <!-- 卖出营业部（按卖出额降序; 行内 上=卖(绿) 下=买(红)） -->
        <div class="lhb-d-side">
          <div class="lhb-d-sidetitle"><span>卖出营业部</span><span class="dim">金额(万)</span></div>
          <div v-for="(s, i) in detail.sellList" :key="'s' + i" class="lhb-d-seat">
            <div class="seat-name">
              {{ s.name || '-' }}
              <span v-if="s.inst" class="tag tag-inst">机构</span>
              <span v-if="s.hot" class="tag tag-hot">游资</span>
            </div>
            <div class="seat-nums">
              <span class="num down">{{ wan2(s.sell) }}</span>
              <span class="num up">{{ wan2(s.buy) }}</span>
            </div>
          </div>
          <div class="lhb-d-total">卖出总计 <b class="num down">{{ wan2(detail.sellTotal) }}</b> 万元</div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
// 龙虎榜表格视图（2026-10-04 第三版：按通达信手机端截图定标，弃树图）
// =====================================================================
// 结构: 列表(3 tab) ↔ 个股详情(按需 1 次明细请求)。
//   · 股票 tab  = 列表接口本身, 排序在前端做（涨幅 / 净买入）。
//   · 机构 tab  = 切到时才调 /api/kpl/lhb-tags（列表接口没有机构字段, 见该端点注释）。
//   · 营业部 tab = 切到时才对净买入前 N 只逐票拉明细再聚合（上游无批量口, 并发 6）。
//   · 详情     = 点行才拉该票明细 ⇒ 首屏只有 1 个请求（旧版首屏预拉 30 只明细, 太慢）。
// 🔴 不再用 echarts ⇒ 本组件零图表依赖（utils/echarts.js 仍被其它组件共用, 不动）。
import { computed, onMounted, ref } from 'vue'
import { kplLhb, kplLhbDetail, kplLhbTags } from '../api/kpl'
import { fmtPct } from '../utils/chart'

const TABS = [
  { key: 'stock', label: '股票' },
  { key: 'inst', label: '机构' },
  { key: 'seat', label: '营业部' },
]
const SEAT_TOP_N = 40     // 营业部聚合拉明细的个股数（净买入前 N）
const CONCURRENCY = 6

const view = ref('list')          // 'list' | 'detail'
const tab = ref('stock')
const loading = ref(true)
const list = ref([])
const serverDate = ref('')        // 后端解析后的真实交易日（非交易日会自动对齐）
const dateSel = ref('')
const sortKey = ref('net')        // 默认按净买入降序（与通达信一致）
const sortDir = ref('desc')

// 详情
const sel = ref({})               // 选中行的列表数据（详情头/指标行先用它, 明细回来补全）
const detail = ref(null)
const detLoading = ref(false)

// 机构 tab / 营业部 tab（各自惰性加载 + 缓存）
const instLoading = ref(false)
const instRows = ref([])
const instLoaded = ref(false)
const instErr = ref('')
const seatLoading = ref(false)
const seatProgress = ref(0)
const seatRows = ref([])
const seatOpen = ref('')
const seatLoaded = ref(false)

const seatTopN = SEAT_TOP_N
const todayStr = (() => {
  const g = new Date(Date.now() + 8 * 3600e3)
  return '%04d-%02d-%02d'.replace('%04d', g.getFullYear()).replace('%02d', String(g.getMonth() + 1).padStart(2, '0')).replace('%02d', String(g.getDate()).padStart(2, '0'))
})()
const tipText = computed(() => '正在加载龙虎榜…')
const seatCount = computed(() => (detail.value ? (detail.value.buyList || []).length + (detail.value.sellList || []).length : 0))

const sortedStocks = computed(() => {
  const key = sortKey.value === 'chg' ? 'change' : 'buyIn'
  const dir = sortDir.value === 'asc' ? 1 : -1
  return [...list.value].sort((a, b) => ((Number(a[key]) || -Infinity) - (Number(b[key]) || -Infinity)) * dir)
})

/** 元 → 万/亿混合（图1: 7138万 / 1.38亿 / -5594万） */
function netShort(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2).replace(/\.?0+$/, '') + '亿'
  if (Math.abs(n) >= 1e4) return Math.round(n / 1e4) + '万'
  return String(Math.round(n))
}
/** 元 → 万元两位（图2: 21278.65） */
function wan2(v) {
  const n = Number(v)
  return Number.isFinite(n) ? (n / 1e4).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '-'
}
/** 元 → 亿两位（流通市值 413.86亿） */
function yi(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || !n) return '-'
  return (n / 1e8).toFixed(2) + '亿'
}
function pct(v) { return fmtPct(v === '' || v == null ? null : v) }
/** 换手率等"非涨跌"百分比: 不带 + 号（fmtPct 是给涨跌幅用的） */
function pctPlain(v) {
  const n = Number(v)
  return Number.isFinite(n) && v !== '' && v != null ? n.toFixed(2) + '%' : '-'
}
/** 上榜理由: 上游 upReason 是 python repr 字符串("['无价格涨跌幅限制']") ⇒ 去壳拼文本 */
function reasonClean(v) {
  if (!v) return ''
  const s = String(v).trim().replace(/^\[\s*['"]?/, '').replace(/['"]?\s*\]$/, '')
  return s.split(/['"],\s*['"]/).map((x) => x.trim()).filter(Boolean).join('、') || String(v)
}
function cls(v) { const n = Number(v); return n > 0 ? 'up' : n < 0 ? 'down' : '' }
/** 概念取前两个（分隔符与 AuctionView.conceptText 同源: 、,，） */
function concept2(b) {
  if (!b) return ''
  return String(b).split(/[、,，]/).map((s) => s.trim()).filter(Boolean).slice(0, 2).join(' ')
}
/** 连板标签：>=2 → N连板; 1 → 首板; 0 → 无（截图中"昨日首板"类无数据, 不做） */
function lbText(n) {
  const k = Number(n) || 0
  return k >= 2 ? k + '连板' : k === 1 ? '首板' : ''
}

function toggleSort(key) {
  if (sortKey.value === key) sortDir.value = sortDir.value === 'desc' ? 'asc' : 'desc'
  else { sortKey.value = key; sortDir.value = 'desc' }
}

async function load(date) {
  loading.value = true
  try {
    const d = await kplLhb(date || '')
    list.value = (d && d.list) || []
    serverDate.value = (d && (d.date || d.resolvedDate)) || ''
    if (serverDate.value) dateSel.value = serverDate.value
  } catch (e) {
    list.value = []
  } finally {
    loading.value = false
  }
  instLoaded.value = false; instRows.value = []
  seatLoaded.value = false; seatRows.value = []
  if (tab.value === 'inst') loadInst()
  else if (tab.value === 'seat') loadSeats()
}
function onDateChange() { if (dateSel.value && dateSel.value !== serverDate.value) load(dateSel.value) }

function switchTab(key) {
  tab.value = key
  if (key === 'inst' && !instLoaded.value) loadInst()
  if (key === 'seat' && !seatLoaded.value) loadSeats()
}

async function loadInst() {
  instLoading.value = true
  instErr.value = ''
  try {
    const codes = list.value.map((r) => r.code)   // ⚠️ kplLhbTags 收**数组**(内部自己 join)
    const d = await kplLhbTags(codes, serverDate.value)
    const tags = (d && d.tags) || {}
    instRows.value = list.value
      .map((r) => ({ ...r, instNet: Number((tags[r.code] || {}).instNet) || 0, hotNet: Number((tags[r.code] || {}).hotNet) || 0 }))
      .sort((a, b) => b.instNet - a.instNet)
    instLoaded.value = true
  } catch (e) {
    instRows.value = []
    instErr.value = (e && e.message) || String(e)
  } finally {
    instLoading.value = false
  }
}

/** 限并发映射（保持顺序） */
async function mapLimit(items, limit, fn) {
  const out = new Array(items.length)
  let i = 0
  const workers = Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, async () => {
    while (i < items.length) { const idx = i++; out[idx] = await fn(items[idx], idx) }
  })
  await Promise.all(workers)
  return out
}

async function loadSeats() {
  seatLoading.value = true
  seatProgress.value = 0
  try {
    const tops = sortedStocks.value.slice(0, SEAT_TOP_N)
    const rows = await mapLimit(tops, CONCURRENCY, async (s) => {
      let det = null
      try { det = ((await kplLhbDetail(s.code)) || {}).detail || null } catch (e) { det = null }
      seatProgress.value++
      return { code: s.code, name: s.name, det }
    })
    const m = new Map()
    for (const r of rows) {
      if (!r.det) continue
      for (const x of [...(r.det.buyList || []), ...(r.det.sellList || [])]) {
        if (!x.name) continue
        if (!m.has(x.name)) m.set(x.name, { name: x.name, buy: 0, sell: 0, inst: false, hot: false, stocks: [] })
        const k = m.get(x.name)
        k.buy += Number(x.buy) || 0
        k.sell += Number(x.sell) || 0
        k.inst = k.inst || !!x.inst
        k.hot = k.hot || !!x.hot
        if (!k.stocks.some((s) => s.code === r.code)) {
          k.stocks.push({ code: r.code, name: r.name, buy: Number(x.buy) || 0, sell: Number(x.sell) || 0 })
        }
      }
    }
    seatRows.value = [...m.values()].sort((a, b) => (b.buy - b.sell) - (a.buy - a.sell))
    seatLoaded.value = true
  } finally {
    seatLoading.value = false
  }
}

function toggleSeat(name) { seatOpen.value = seatOpen.value === name ? '' : name }

async function openDetail(row) {
  sel.value = row || {}
  view.value = 'detail'
  detail.value = null
  await fetchDetail()
}
function openDetailByCode(s) {
  const row = list.value.find((r) => r.code === s.code)
  if (row) openDetail(row)
}
async function fetchDetail() {
  detLoading.value = true
  try {
    const d = await kplLhbDetail(sel.value.code, serverDate.value)
    detail.value = (d && d.detail) || null
  } catch (e) {
    detail.value = null
  } finally {
    detLoading.value = false
  }
}
function reloadDetail() { fetchDetail() }
function backToList() { view.value = 'list' }

onMounted(() => { load('') })
</script>

<style scoped>
.lhb-panel { color: var(--text-main); }
.lhb-loading, .lhb-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; min-height: 220px; color: var(--text-muted); font-size: 0.875rem;
}
.lhb-empty b { color: var(--warn-amber); }
.lhb-retry { color: var(--accent); cursor: pointer; text-decoration: underline; }
.lhb-foot { margin-top: 8px; color: var(--text-muted); font-size: 0.75rem; }
.lhb-seats-empty { font-size: 0.8125rem; color: var(--text-muted); padding: 10px 6px; }

/* ---- 工具行: 上榜数 + 日期（图1 顶部） ---- */
.lhb-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.lhb-count { font-size: 0.875rem; color: var(--text-secondary); }
.lhb-count b { color: var(--up-strong); font-size: 1rem; }
.lhb-date {
  margin-left: auto; background: var(--bg-input); color: var(--text-main);
  border: 1px solid var(--border-soft); border-radius: 6px; padding: 4px 8px; font-size: 0.8125rem;
  color-scheme: dark;
}
body[data-bg="light"] .lhb-date { color-scheme: light; }

/* ---- 三个 tab（图1: 股票/机构/营业部, 红色高亮当前） ---- */
.lhb-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border-soft); margin-bottom: 2px; }
.lhb-tab {
  background: none; border: none; cursor: pointer;
  padding: 8px 14px; font-size: 0.9375rem; color: var(--text-secondary);
  border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.lhb-tab:hover { color: var(--text-main); }
.lhb-tab.active { color: var(--up-strong); border-bottom-color: var(--up-strong); font-weight: 600; }

/* ---- 表格 ---- */
.lhb-table { display: flex; flex-direction: column; }
.lhb-thead {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 8px; font-size: 0.8125rem; color: var(--text-muted);
  border-bottom: 1px solid var(--border-soft);
  position: sticky; top: 0; z-index: 2;
  background: var(--bg-panel-solid);
}
.lhb-row {
  display: flex; align-items: center; gap: 8px; padding: 10px 8px;
  border-bottom: 1px solid var(--border-soft); cursor: pointer;
}
.lhb-row:hover { background: var(--bg-hover); }
.num { font-variant-numeric: tabular-nums; }
.c-name { flex: 0 0 30%; min-width: 0; }
.c-name .rn { font-size: 0.9375rem; font-weight: 600; color: var(--text-main); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.c-name .rc { font-size: 0.75rem; color: var(--text-dim); margin-top: 1px; }
.c-board { flex: 1 1 24%; min-width: 0; font-size: 0.8125rem; color: var(--accent-text, #e0a0a0); line-height: 1.5; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.c-chg { flex: 0 0 24%; text-align: center; }
.c-chg .num { font-size: 0.9375rem; font-weight: 600; }
.c-chg .lb { font-size: 0.6875rem; color: var(--accent-text, #e0a0a0); margin-top: 2px; }
.c-net { flex: 0 0 22%; text-align: right; font-size: 0.9375rem; font-weight: 600; }
.c-chg.inst-chg { flex: 0 0 18%; }
.th-sort {
  background: none; border: none; cursor: pointer; padding: 0;
  font-size: 0.8125rem; color: var(--text-muted); font-weight: 400;
  display: inline-flex; align-items: center; gap: 3px;
}
.th-sort:hover { color: var(--text-main); }
.th-sort .fa { font-size: 0.6875rem; }
.th-sort .fa.flip { transform: rotate(180deg); }
.th-sort .fa.dim { opacity: 0.4; }

/* ---- 营业部 tab ---- */
.c-seat { flex: 1 1 auto; min-width: 0; }
.c-seat .rn { font-size: 0.8125rem; font-weight: 500; color: var(--text-main); line-height: 1.4; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.c-seat .rc { font-size: 0.6875rem; color: var(--text-dim); margin-top: 1px; }
.c-seat ~ .c-net { flex: 0 0 20%; }
.seat-caret { color: var(--text-dim); transition: transform .15s; }
.seat-caret.open { transform: rotate(0deg); }
.seat-caret:not(.open) { transform: rotate(-90deg); }
.seat-sub { background: var(--bg-card); border-bottom: 1px solid var(--border-soft); padding: 4px 12px 8px 24px; }
.seat-sub-row { display: flex; align-items: center; gap: 8px; padding: 5px 0; font-size: 0.8125rem; border-bottom: 1px dashed var(--border-soft); }
.seat-sub-row:last-child { border-bottom: none; }
.ss-name { flex: 1 1 auto; min-width: 0; color: var(--text-secondary); cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ss-name:hover { color: var(--accent); }
.ss-num { flex: 0 0 88px; text-align: right; font-variant-numeric: tabular-nums; }
.seat-row .c-net { font-size: 0.8125rem; }

/* ---- 详情（图2/3） ---- */
.lhb-d-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.lhb-back {
  background: none; border: 1px solid var(--border-soft); border-radius: 6px;
  color: var(--text-secondary); cursor: pointer; padding: 5px 10px; font-size: 0.8125rem;
  display: inline-flex; align-items: center; gap: 5px;
}
.lhb-back:hover { color: var(--text-main); border-color: var(--accent); }
.lhb-d-name { font-size: 1.0625rem; font-weight: 700; color: var(--text-main); }
.lhb-d-name b { color: var(--text-dim); font-weight: 400; font-size: 0.8125rem; margin-left: 4px; }
.lhb-d-chg { margin-left: auto; font-size: 1.0625rem; font-weight: 700; }
.lhb-d-stats { display: flex; flex-wrap: wrap; gap: 6px 0; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--bg-panel); padding: 8px 4px; margin-bottom: 8px; }
.lhb-d-stats .st { flex: 1 1 50%; display: flex; align-items: baseline; justify-content: space-between; padding: 4px 12px; min-width: 0; }
.st-label { font-size: 0.8125rem; color: var(--text-muted); }
.st-val { font-size: 0.9375rem; font-weight: 600; color: var(--text-main); }
.lhb-d-reason {
  border: 1px solid var(--border-soft); border-left: 3px solid var(--up-strong);
  border-radius: 6px; background: var(--bg-panel); padding: 8px 12px;
  font-size: 0.875rem; color: var(--text-main); margin-bottom: 10px;
}
.rs-label { color: var(--text-muted); margin-right: 10px; }
.lhb-d-side { border: 1px solid var(--border-soft); border-radius: 8px; background: var(--bg-panel); margin-bottom: 10px; overflow: hidden; }
.lhb-d-sidetitle {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; font-size: 0.875rem; font-weight: 600; color: var(--text-main);
  background: var(--bg-card); border-bottom: 1px solid var(--border-soft);
}
.lhb-d-sidetitle .dim { font-size: 0.75rem; color: var(--text-dim); font-weight: 400; }
.lhb-d-seat {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  border-bottom: 1px solid var(--border-soft);
}
.lhb-d-seat:last-of-type { border-bottom: none; }
.seat-name { flex: 1 1 auto; min-width: 0; font-size: 0.875rem; color: var(--text-main); line-height: 1.4; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.seat-nums { flex: 0 0 auto; display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.seat-nums .num { font-size: 0.9375rem; font-weight: 600; }
.lhb-d-total { padding: 8px 12px; text-align: right; font-size: 0.8125rem; color: var(--text-muted); background: var(--bg-card); }
.lhb-d-total b { font-size: 0.9375rem; }

/* ---- 标签（机构=金 / 游资=橙红; 全走 token, 不添裸值） ---- */
.tag { display: inline-block; font-size: 0.625rem; padding: 0 4px; border-radius: 3px; margin-left: 4px; vertical-align: middle; }
.tag-inst { background: var(--bg-hover); color: var(--star); border: 1px solid var(--border-soft); }
.tag-hot { background: var(--bg-hover); color: var(--up-strong); border: 1px solid var(--border-soft); }

@media (max-width: 768px) {
  .lhb-toolbar { flex-wrap: wrap; }
  .lhb-count { font-size: 0.8125rem; }
  .c-name { flex: 0 0 34%; }
  .c-chg { flex: 0 0 22%; }
  .c-net { flex: 0 0 20%; font-size: 0.875rem; }
  .lhb-tab { padding: 8px 10px; }
  .lhb-d-stats .st { flex: 1 1 100%; }
  .seat-sub { padding-left: 12px; }
  .ss-num { flex: 0 0 76px; }
}
</style>
