<template>
  <div class="page-shell">
    <div class="page-back" @click="$router.push('/')"><i class="fa fa-arrow-left"></i> 返回选股</div>

    <div class="mrk-head">
      <span class="mrk-title"><i class="fa fa-radar"></i> 市场雷达</span>
      <span class="mrk-sub">板块强度排行 · 盘中人气热榜 · 龙虎榜（三数据源）</span>
      <span class="mrk-time">{{ bjTime }}</span>
    </div>

    <div class="mrk-tabs">
      <button class="mrk-tab" :class="{ active: tab === 'board' }" @click="switchTab('board')">
        <i class="fa fa-th-large"></i> 板块强度
      </button>
      <button class="mrk-tab" :class="{ active: tab === 'history' }" @click="switchTab('history')">
        <i class="fa fa-history"></i> 板块轮动历史
      </button>
      <button class="mrk-tab" :class="{ active: tab === 'hot' }" @click="switchTab('hot')">
        <i class="fa fa-fire"></i> 人气热榜
      </button>
      <button class="mrk-tab" :class="{ active: tab === 'lhb' }" @click="switchTab('lhb')">
        <i class="fa fa-list-alt"></i> 龙虎榜
      </button>
    </div>

    <!-- 板块强度 -->
    <div v-if="tab === 'board'" class="mrk-panel">
      <div class="rot-toolbar">
        <span class="rot-tip"><i class="fa fa-info-circle"></i> 实时板块强度排行；选日期可回看历史(开盘啦保留最近5交易日)</span>
        <input type="date" v-model="datePicker" class="admin-input" style="width:140px;padding:5px 8px;" @change="loadBoard">
        <button class="admin-search-btn" @click="clearDate('board')"><i class="fa fa-bolt"></i> 实时</button>
      </div>
      <div v-if="boardLoading" class="loading-placeholder"><div class="spinner"></div><div>加载板块强度...</div></div>
      <div v-else-if="!boardList.length" class="empty-state">暂无板块强度数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable" :class="{ active: boardSort.keyOf('name') }" @click="boardSort.onSort('name', 'string')">板块<span class="sort-ind">{{ boardSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('strength') }" @click="boardSort.onSort('strength')">强度<span class="sort-ind">{{ boardSort.ind('strength') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('change') }" @click="boardSort.onSort('change')">涨幅%<span class="sort-ind">{{ boardSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('speed') }" @click="boardSort.onSort('speed')">涨速%<span class="sort-ind">{{ boardSort.ind('speed') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('mainNet') }" @click="boardSort.onSort('mainNet')">主力净额(亿)<span class="sort-ind">{{ boardSort.ind('mainNet') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('volRatio') }" @click="boardSort.onSort('volRatio')">量比<span class="sort-ind">{{ boardSort.ind('volRatio') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('amount') }" @click="boardSort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ boardSort.ind('amount') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('totalMv') }" @click="boardSort.onSort('totalMv')">总市值(亿)<span class="sort-ind">{{ boardSort.ind('totalMv') }}</span></th>
            <th class="sortable" :class="{ active: boardSort.keyOf('peNow') }" @click="boardSort.onSort('peNow')">今PE<span class="sort-ind">{{ boardSort.ind('peNow') }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(b, idx) in boardSort.sorted(boardList)" :key="b.boardCode">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="name-col"><div class="name-main">{{ b.name }}</div><div class="board-code">{{ b.boardCode }}</div></td>
            <td class="strength">{{ Math.round(b.strength) }}</td>
            <td :class="b.change > 0 ? 'up' : 'down'">{{ signed(b.change) }}%</td>
            <td :class="b.speed > 0 ? 'up' : 'down'">{{ signed(b.speed) }}%</td>
            <td :class="b.mainNet > 0 ? 'up' : b.mainNet < 0 ? 'down' : 'dim'">{{ yi(b.mainNet) }}</td>
            <td>{{ b.volRatio.toFixed(2) }}</td>
            <td>{{ yi(b.amount) }}</td>
            <td>{{ yi(b.totalMv) }}</td>
            <td class="dim">{{ b.peNow ? b.peNow.toFixed(1) : '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 板块轮动历史 -->
    <div v-else-if="tab === 'history'" class="mrk-panel">
      <div class="rot-toolbar">
        <span class="rot-tip"><i class="fa fa-info-circle"></i> 工作日 15:30 后自动保存当日 Top10；近期数据积累后展示趋势</span>
        <div class="rot-source">
          <button v-for="s in sourceOptions" :key="s.key"
                  :class="{ active: rotSource === s.key }"
                  class="rot-source-btn"
                  @click="switchSource(s.key)">
            <i :class="s.icon"></i> {{ s.label }}
          </button>
        </div>
        <select v-model.number="rotDays" class="admin-input" style="width:90px;padding:5px 8px;" @change="loadHistory">
          <option :value="10">近 10 日</option>
          <option :value="20">近 20 日</option>
          <option :value="30">近 30 日</option>
          <option :value="50">近 50 日</option>
        </select>
        <button class="admin-search-btn" @click="loadHistory"><i class="fa fa-refresh"></i> 刷新</button>
      </div>
      <div v-if="rotLoading" class="loading-placeholder"><div class="spinner"></div></div>
      <div v-else-if="!rot.dates.length" class="empty-state">
        暂无历史数据(每日 15:30 后调度器抓取积累)
      </div>
      <template v-else>
        <!-- 顶部表格: 行=排名, 列=日期 -->
        <div class="rot-table-scroll">
          <table class="rot-table">
            <thead>
              <tr>
                <th class="rot-rownum">排名</th>
                <th v-for="d in rot.dates" :key="d" class="rot-date">{{ d.slice(5) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="rank in 10" :key="rank">
                <td class="rot-rownum">{{ rank }}</td>
                <td v-for="d in rot.dates" :key="d+rank" class="rot-cell">
                  <template v-for="b in boardAt(d, rank)" :key="b.name">
                    <div :class="['rot-board', { highlight: highlightSet.has(b.name) }]">{{ b.name }}</div>
                    <div class="rot-strength">{{ Math.round(b.strength) }}</div>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <!-- 强度趋势线 + 量能柱状 -->
        <div class="rot-charts">
          <div class="rot-chart-block">
            <div class="rot-chart-title">板块强度(每日第 1 名)</div>
            <div class="rot-svg-wrap" v-html="strengthLineSvg"></div>
          </div>
          <div class="rot-chart-block">
            <div class="rot-chart-title">板块量能(每日第 1 名成交额, 亿元)</div>
            <div class="rot-svg-wrap" v-html="amountBarSvg"></div>
          </div>
        </div>
        <!-- 多窗口排名 -->
        <div class="rot-windows">
          <div class="rot-chart-title">多窗口排名(基于不同时间窗口 Top10 排名加权)</div>
          <div class="rot-svg-wrap" v-html="windowLineSvg"></div>
          <div class="rot-window-legend">
            <span v-for="(w, idx) in rot.windows" :key="w.window" :style="{ color: windowColors[idx] }">
              <i class="fa fa-circle"></i> 近 {{ w.window }} 日
            </span>
          </div>
        </div>
      </template>
    </div>

    <!-- 人气热榜 -->
    <div v-else-if="tab === 'hot'" class="mrk-panel">
      <div class="rot-toolbar">
        <span class="rot-tip"><i class="fa fa-info-circle"></i> 各数据源人气热榜；选日期可回看历史</span>
        <div class="rot-source">
          <button v-for="s in sourceOptions" :key="s.key"
                  :class="{ active: hotSource === s.key }"
                  class="rot-source-btn"
                  @click="switchHotSource(s.key)">
            <i :class="s.icon"></i> {{ s.label }}
          </button>
        </div>
        <input type="date" v-model="datePicker" class="admin-input" style="width:140px;padding:5px 8px;" @change="loadHot">
        <button class="admin-search-btn" @click="clearDate('hot')"><i class="fa fa-bolt"></i> 实时</button>
      </div>
      <div v-if="hotLoading" class="loading-placeholder"><div class="spinner"></div><div>加载人气热榜...</div></div>
      <div v-else-if="!hotList.length" class="empty-state">暂无热榜数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: hotSort.keyOf('rank') }" @click="hotSort.onSort('rank')">人气排名<span class="sort-ind">{{ hotSort.ind('rank') }}</span></th>
            <th class="sortable" :class="{ active: hotSort.keyOf('code') }" @click="hotSort.onSort('code', 'string')">代码<span class="sort-ind">{{ hotSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: hotSort.keyOf('name') }" @click="hotSort.onSort('name', 'string')">名称<span class="sort-ind">{{ hotSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: hotSort.keyOf('change') }" @click="hotSort.onSort('change')">涨跌幅%<span class="sort-ind">{{ hotSort.ind('change') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in hotSort.sorted(hotList)" :key="h.code">
            <td class="rank-col">{{ h.rank }}</td>
            <td class="code-click" @click="linkToSoftware(h.code)">{{ h.code }}</td>
            <td>{{ h.name }}</td>
            <td :class="h.change > 0 ? 'up' : h.change < 0 ? 'down' : 'dim'">{{ signed(h.change) }}%</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(h.code) }" @click.stop="addToPool(h)">{{ inPool(h.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 龙虎榜 -->
    <div v-else class="mrk-panel">
      <div class="rot-toolbar">
        <span class="rot-tip"><i class="fa fa-info-circle"></i> 龙虎榜当日/历史；选日期可回看</span>
        <input type="date" v-model="datePicker" class="admin-input" style="width:140px;padding:5px 8px;" @change="loadLhb">
        <button class="admin-search-btn" @click="clearDate('lhb')"><i class="fa fa-bolt"></i> 实时</button>
      </div>
      <div v-if="lhbLoading" class="loading-placeholder"><div class="spinner"></div><div>加载龙虎榜...</div></div>
      <div v-else-if="!lhbList.length" class="empty-state">暂无龙虎榜数据</div>
      <table v-else class="stock-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: lhbSort.keyOf('code') }" @click="lhbSort.onSort('code', 'string')">代码<span class="sort-ind">{{ lhbSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('name') }" @click="lhbSort.onSort('name', 'string')">名称<span class="sort-ind">{{ lhbSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('change') }" @click="lhbSort.onSort('change')">涨跌幅%<span class="sort-ind">{{ lhbSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('limitBoards') }" @click="lhbSort.onSort('limitBoards')">连板<span class="sort-ind">{{ lhbSort.ind('limitBoards') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('buyIn') }" @click="lhbSort.onSort('buyIn')">买入(亿)<span class="sort-ind">{{ lhbSort.ind('buyIn') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('amount') }" @click="lhbSort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ lhbSort.ind('amount') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('turnover') }" @click="lhbSort.onSort('turnover')">换手%<span class="sort-ind">{{ lhbSort.ind('turnover') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('amplitude') }" @click="lhbSort.onSort('amplitude')">振幅%<span class="sort-ind">{{ lhbSort.ind('amplitude') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('floatMv') }" @click="lhbSort.onSort('floatMv')">流通市值(亿)<span class="sort-ind">{{ lhbSort.ind('floatMv') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in lhbSort.sorted(lhbList)" :key="l.code">
            <td class="code-click" @click="linkToSoftware(l.code)">{{ l.code }}</td>
            <td class="name-col"><div class="name-main">{{ l.name }}</div></td>
            <td :class="l.change > 0 ? 'up' : 'down'">{{ signed(l.change) }}%</td>
            <td><span v-if="l.limitBoards > 0" class="lb-badge">{{ l.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td :class="l.buyIn > 0 ? 'up' : l.buyIn < 0 ? 'down' : 'dim'">{{ yi(l.buyIn) }}</td>
            <td>{{ yi(l.amount) }}</td>
            <td>{{ l.turnover.toFixed(2) }}</td>
            <td>{{ l.amplitude.toFixed(2) }}</td>
            <td>{{ yi(l.floatMv) }}</td>
            <td>
              <button class="pool-add-btn" style="margin-right:4px;" @click="viewLhbDetail(l)">明细</button>
              <button class="pool-add-btn" :class="{ added: inPool(l.code) }" @click.stop="addToPool(l)">{{ inPool(l.code) ? '已入池' : '＋池' }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 龙虎榜营业部明细弹窗 -->
    <div v-if="lhbModal.show" class="modal-mask" @click.self="lhbModal.show = false">
      <div class="reason-modal">
        <div class="reason-head">
          <span><i class="fa fa-list-alt" style="color:#ffb400;"></i> {{ lhbModal.detail.name }} {{ lhbModal.code }} · 龙虎榜营业部</span>
          <button class="close-btn" @click="lhbModal.show = false"><i class="fa fa-close"></i></button>
        </div>
        <div v-if="lhbLoading" class="reason-loading">查询中...</div>
        <template v-else>
          <div v-if="lhbModal.detail.upReason" class="lhb-reason">涨停原因：{{ lhbModal.detail.upReason }}</div>
          <div class="lhb-total">
            <span>买入总计 <b class="up">{{ yi(lhbModal.detail.buyTotal) }}亿</b></span>
            <span>卖出总计 <b class="down">{{ yi(lhbModal.detail.sellTotal) }}亿</b></span>
            <span>换手 {{ (lhbModal.detail.turnover || 0).toFixed(2) }}%</span>
          </div>
          <div class="lhb-cols">
            <div class="lhb-col">
              <div class="lhb-col-title buy">买入营业部</div>
              <div v-for="(b, i) in lhbModal.detail.buyList" :key="i" class="lhb-row">
                <span class="lhb-idx">{{ i + 1 }}</span>
                <span class="lhb-name">{{ b.name }}</span>
                <span class="lhb-amt up">+{{ (b.buy / 1e8).toFixed(2) }}亿</span>
              </div>
              <div v-if="!lhbModal.detail.buyList.length" class="lhb-empty">无</div>
            </div>
            <div class="lhb-col">
              <div class="lhb-col-title sell">卖出营业部</div>
              <div v-for="(s, i) in lhbModal.detail.sellList" :key="i" class="lhb-row">
                <span class="lhb-idx">{{ i + 1 }}</span>
                <span class="lhb-name">{{ s.name }}</span>
                <span class="lhb-amt down">-{{ (s.sell / 1e8).toFixed(2) }}亿</span>
              </div>
              <div v-if="!lhbModal.detail.sellList.length" class="lhb-empty">无</div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, reactive } from 'vue'
import { kplBoardRank, kplHotRank, kplLhb, kplLhbDetail, sectorRotation } from '../api/kpl'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr } from '../utils/time'
import { usePoolStore } from '../stores/pool'
import { showToast } from '../utils/toast'
import { useSortable } from '../composables/useSortable'

const pool = usePoolStore()
const tab = ref('board')
const boardList = ref([])
const hotList = ref([])
const lhbList = ref([])
const boardLoading = ref(true)
const hotLoading = ref(true)
const lhbLoading = ref(true)
const hotSource = ref(localStorage.getItem('kuaixuan_hot_source') || 'kpl')
const datePicker = ref('')
const bjTime = ref('--:--:--')
let clockTimer = null
let refreshTimer = null

const lhbModal = reactive({ show: false, code: '', detail: { name: '', buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 } })

// 各表独立排序实例
const boardSort = useSortable()
const hotSort = useSortable()
const lhbSort = useSortable()

// 切 Tab 清排序
function switchTab(t) {
  tab.value = t
  boardSort.clear(); hotSort.clear(); lhbSort.clear()
}

function yi(v) { return (v / 1e8).toFixed(2) }
function signed(v) { return (v > 0 ? '+' : '') + Number(v).toFixed(2) }

async function viewLhbDetail(l) {
  lhbModal.show = true
  lhbModal.code = l.code
  lhbModal.detail = { name: l.name, buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 }
  lhbLoading.value = true
  try {
    const d = await kplLhbDetail(l.code)
    if (d && d.detail) lhbModal.detail = d.detail
  } catch (e) { /* 静默 */ } finally {
    lhbLoading.value = false
  }
}

function addToPool(h) {
  const n = pool.addStocks([{ code: h.code, name: h.name }])
  showToast(n ? `✅ ${h.code} ${h.name} 已加入股票池` : `${h.code} 已在池中`, n ? 'success' : 'info')
}

// 是否已在股票池(与主页面 StockTable 一致: 已入池按钮变绿禁用)
function inPool(code) {
  return pool.stockPool.some(x => x.code === code)
}

async function loadBoard() {
  try {
    const d = await kplBoardRank(datePicker.value)
    boardList.value = d.list || []
  } catch (e) { /* 静默 */ } finally {
    boardLoading.value = false
  }
}

function clearDate(which) {
  datePicker.value = ''
  if (which === 'board') { boardLoading.value = true; loadBoard() }
  else if (which === 'hot') { hotLoading.value = true; loadHot() }
  else { lhbLoading.value = true; loadLhb() }
}

async function loadHot() {
  try {
    const d = await kplHotRank(hotSource.value, datePicker.value)
    hotList.value = d.list || []
  } catch (e) { /* 静默 */ } finally {
    hotLoading.value = false
  }
}

function switchHotSource(src) {
  if (src === hotSource.value) return
  hotSource.value = src
  localStorage.setItem('kuaixuan_hot_source', src)
  hotLoading.value = true
  loadHot()
}

async function loadLhb() {
  try {
    const d = await kplLhb(datePicker.value)
    lhbList.value = d.list || []
  } catch (e) { /* 静默 */ } finally {
    lhbLoading.value = false
  }
}

// ===================== 板块轮动历史 =====================
const rotDays = ref(10)
const rotSource = ref(localStorage.getItem('kuaixuan_sector_source') || 'kpl')
const sourceOptions = [
  { key: 'kpl', label: '开盘啦',     icon: 'fa fa-bullseye' },
  { key: 'em',  label: '东方财富',   icon: 'fa fa-bar-chart' },
  { key: 'ths', label: '同花顺',     icon: 'fa fa-line-chart' },
]
const rotLoading = ref(false)
const rot = reactive({ dates: [], days: [], windows: [], common_names: [], source: 'kpl' })
const windowColors = ['#E24B4A', '#EF9F27', '#378ADD', '#888780']

const rotMap = computed(() => {
  const m = {}
  for (const day of rot.days) {
    m[day.date] = day.boards || []
  }
  return m
})
const highlightSet = computed(() => {
  const cnt = {}
  for (const day of rot.days) {
    for (const b of (day.boards || []).slice(0, 3)) {
      cnt[b.name] = (cnt[b.name] || 0) + 1
    }
  }
  const s = new Set()
  for (const [n, c] of Object.entries(cnt)) {
    if (c >= Math.max(2, Math.ceil(rot.dates.length / 4))) s.add(n)
  }
  return s
})

function boardAt(date, rank) {
  const day = rotMap.value[date] || []
  return day.filter(b => b.rank === rank)
}

async function loadHistory() {
  rotLoading.value = true
  try {
    const d = await sectorRotation(rotDays.value, rotSource.value)
    rot.source = (d && d.source) || rotSource.value
    rot.dates = (d && d.dates) || []
    rot.days = (d && d.rotation && d.rotation.days) || []
    rot.windows = (d && d.windows && d.windows.windows) || []
    rot.common_names = (d && d.windows && d.windows.common_names) || []
  } catch (e) { /* ignore */ }
  finally { rotLoading.value = false }
}

function switchSource(src) {
  if (src === rotSource.value) return
  rotSource.value = src
  localStorage.setItem('kuaixuan_sector_source', src)
  loadHistory()
}

const strengthLineSvg = computed(() => {
  const days = rot.dates
  const data = days.map(d => {
    const b = (rotMap.value[d] || []).find(x => x.rank === 1)
    return b ? Number(b.strength) || 0 : 0
  })
  if (!data.length || data.every(x => x === 0)) {
    return '<svg viewBox="0 0 600 120" preserveAspectRatio="none" class="rot-svg"><text x="300" y="60" text-anchor="middle" fill="#888">暂无强度数据</text></svg>'
  }
  const W = 600, H = 120, padL = 40, padR = 10, padT = 10, padB = 18
  const maxV = Math.max(...data, 1)
  const minV = Math.min(...data, 0)
  const range = maxV - minV || 1
  const xs = data.map((_, i) => padL + i * (W - padL - padR) / Math.max(1, data.length - 1))
  const ys = data.map(v => padT + (H - padT - padB) * (1 - (v - minV) / range))
  const points = xs.map((x, i) => `${x},${ys[i]}`).join(' ')
  const labels = data.map((v, i) => `<text x="${xs[i]}" y="${ys[i] - 4}" font-size="9" fill="#E24B4A" text-anchor="middle">${Math.round(v)}</text>`).join('')
  const axisY = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>` +
                `<line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>`
  const xLabels = days.map((d, i) => `<text x="${xs[i]}" y="${H - 4}" font-size="8" fill="#888" text-anchor="middle">${d.slice(5)}</text>`).join('')
  return `<svg viewBox="0 0 600 120" preserveAspectRatio="none" class="rot-svg">` +
         `<polyline points="${points}" fill="none" stroke="#E24B4A" stroke-width="1.5"/>` + labels + axisY + xLabels +
         `<circle cx="${xs[0]}" cy="${ys[0]}" r="2.5" fill="#E24B4A"/>` +
         `<circle cx="${xs[xs.length - 1]}" cy="${ys[ys.length - 1]}" r="2.5" fill="#E24B4A"/>` +
         `</svg>`
})

const amountBarSvg = computed(() => {
  const days = rot.dates
  const data = days.map(d => {
    const b = (rotMap.value[d] || []).find(x => x.rank === 1)
    return b ? Number(b.amount) / 1e8 : 0
  })
  if (!data.length || data.every(x => x === 0)) {
    return '<svg viewBox="0 0 600 120" preserveAspectRatio="none" class="rot-svg"><text x="300" y="60" text-anchor="middle" fill="#888">暂无量能数据</text></svg>'
  }
  const W = 600, H = 120, padL = 40, padR = 10, padT = 10, padB = 18
  const maxV = Math.max(...data, 1)
  const barW = Math.max(4, (W - padL - padR) / data.length - 2)
  let bars = ''
  for (let i = 0; i < data.length; i++) {
    const x = padL + i * (W - padL - padR) / data.length + 1
    const h = data[i] / maxV * (H - padT - padB)
    const y = H - padB - h
    bars += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" fill="#378ADD"/>`
    bars += `<text x="${x + barW / 2}" y="${y - 2}" font-size="8" fill="#378ADD" text-anchor="middle">${Math.round(data[i])}</text>`
  }
  const axisY = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>` +
                `<line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>`
  const xLabels = days.map((d, i) => {
    const x = padL + i * (W - padL - padR) / data.length + barW / 2 + 1
    return `<text x="${x}" y="${H - 4}" font-size="8" fill="#888" text-anchor="middle">${d.slice(5)}</text>`
  }).join('')
  return `<svg viewBox="0 0 600 120" preserveAspectRatio="none" class="rot-svg">` + bars + axisY + xLabels + `</svg>`
})

const windowLineSvg = computed(() => {
  const wins = rot.windows
  if (!wins.length) {
    return '<svg viewBox="0 0 600 220" preserveAspectRatio="none" class="rot-svg"><text x="300" y="100" text-anchor="middle" fill="#888">暂无多窗口数据</text></svg>'
  }
  const names = rot.common_names.slice(0, 6)
  if (!names.length) {
    return '<svg viewBox="0 0 600 220" preserveAspectRatio="none" class="rot-svg"><text x="300" y="100" text-anchor="middle" fill="#888">暂无多窗口数据</text></svg>'
  }
  const W = 600, H = 220, padL = 30, padR = 10, padT = 20, padB = 60
  let lines = ''
  for (let wi = 0; wi < wins.length; wi++) {
    const top = wins[wi].top || []
    const color = windowColors[wi % windowColors.length]
    const pts = names.map((nm, ni) => {
      const idx = top.findIndex(t => t.name === nm)
      const rank = idx >= 0 ? idx + 1 : 99
      const x = padL + ni * (W - padL - padR) / Math.max(1, names.length - 1)
      const y = padT + (rank > 50 ? (H - padT - padB) * 0.95 : (rank - 1) / 5 * (H - padT - padB) * 0.6 + padT)
      return `${x},${y}`
    }).join(' ')
    lines += `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.2" stroke-dasharray="${wi === 0 ? '' : (wi === 1 ? '4,3' : '1,3')}"/>`
    lines += pts.split(' ').map((p) => `<circle cx="${p.split(',')[0]}" cy="${p.split(',')[1]}" r="2.5" fill="${color}"/>`).join('')
  }
  const axisX = `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>`
              + `<line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="#888" stroke-width="0.5"/>`
  const xLabels = names.map((nm, ni) => {
    const x = padL + ni * (W - padL - padR) / Math.max(1, names.length - 1)
    return `<text x="${x}" y="${H - padB + 14}" font-size="9" fill="#888" text-anchor="middle">${nm}</text>`
  }).join('')
  const yLabels = ['1', '3', '5'].map((v, i) => `<text x="${padL - 6}" y="${padT + (i / 3) * (H - padT - padB) * 0.6 + 4}" font-size="9" fill="#888" text-anchor="end">${v}</text>`).join('')
  return `<svg viewBox="0 0 600 220" preserveAspectRatio="none" class="rot-svg">` + axisX + lines + xLabels + yLabels + `</svg>`
})

onMounted(() => {
  bjTime.value = bjTimeStr()
  clockTimer = setInterval(() => { bjTime.value = bjTimeStr() }, 1000)
  loadBoard()
  loadHistory()
  loadHot()
  loadLhb()
  refreshTimer = setInterval(() => { loadBoard(); loadHot(); loadLhb() }, 60000)
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped>
.page-shell { max-width: 1400px; margin: 0 auto; padding: 16px; }
.page-back { color: var(--text-muted); cursor: pointer; font-size: 13px; margin-bottom: 12px; display: inline-block; }
.page-back:hover { color: #ffb400; }
.mrk-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.mrk-title { font-size: 20px; font-weight: 700; color: #ffe0a0; }
.mrk-title .fa { color: #ffb400; }
.mrk-sub { color: var(--text-muted); font-size: 13px; }
.mrk-time { margin-left: auto; color: #aaa; font-size: 14px; font-family: monospace; }
.mrk-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.mrk-tab {
  padding: 8px 18px; border-radius: 8px; border: 1px solid var(--border-soft);
  background: var(--bg-hover); color: var(--text-secondary); font-size: 14px; cursor: pointer; transition: all 0.2s;
}
.mrk-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.mrk-tab.active { background: rgba(255,180,0,0.15); border-color: #ffb400; color: #ffd700; font-weight: 600; }
.mrk-panel { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px; }
.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: #ffb400; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); }
.board-code { font-size: 11px; color: var(--text-muted); }
.strength { color: #ffb400; font-weight: 700; }
.lb-badge { display: inline-block; color: #ff8a5c; border: 1px solid rgba(255,80,40,0.5); border-radius: 4px; padding: 0 5px; font-size: 11px; background: rgba(255,80,40,0.12); }
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.reason-modal { background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: 12px; width: 640px; max-width: 92vw; max-height: 76vh; overflow: auto; padding: 18px; }
.reason-head { display: flex; align-items: center; justify-content: space-between; color: #ffe0a0; font-size: 16px; margin-bottom: 14px; }
.close-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; }
.close-btn:hover { color: #ff6a6a; }
.reason-loading { color: var(--text-muted); padding: 20px; text-align: center; }
.lhb-reason { color: #ffb400; font-size: 13px; margin-bottom: 10px; line-height: 1.5; }
.lhb-total { display: flex; gap: 20px; color: #aaa; font-size: 13px; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--border-soft); }
.lhb-cols { display: flex; gap: 16px; }
.lhb-col { flex: 1; }
.lhb-col-title { font-size: 13px; margin-bottom: 8px; }
.lhb-col-title.buy { color: #ff8a8a; }
.lhb-col-title.sell { color: #8ae08a; }
.lhb-row { display: flex; align-items: center; gap: 6px; padding: 4px 0; font-size: 12px; border-bottom: 1px solid rgba(255,255,255,0.04); }
.lhb-idx { width: 16px; color: var(--text-muted); }
.lhb-name { flex: 1; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lhb-amt { font-family: monospace; }
.lhb-empty { color: #666; font-size: 12px; padding: 8px 0; }

/* 浅色主题覆盖 */
body[data-bg="light"] .page-back { color: #5a6b85; }
body[data-bg="light"] .page-back:hover { color: #c79100; }
body[data-bg="light"] .mrk-title { color: #8a5500; }
body[data-bg="light"] .mrk-title .fa { color: #c79100; }
body[data-bg="light"] .mrk-sub { color: #5a6b85; }
body[data-bg="light"] .mrk-time { color: #5a6b85; }
body[data-bg="light"] .mrk-tab { color: #5a6b85; border-color: var(--border-soft); background: rgba(255,255,255,0.6); }
body[data-bg="light"] .mrk-tab:hover { color: #5a4a3a; border-color: #c79100; }
body[data-bg="light"] .mrk-tab.active { color: #5a4a3a; background: rgba(255,180,0,0.15); border-color: #c79100; }
body[data-bg="light"] .mrk-panel { background: rgba(255,255,255,0.85); border-color: var(--border-soft); }
body[data-bg="light"] .strength { color: #8a5500; }
body[data-bg="light"] .lb-badge { color: #b83010; border-color: rgba(184,48,16,0.5); background: rgba(255,80,80,0.1); }
body[data-bg="light"] .reason-head { color: #5a4a3a; }
body[data-bg="light"] .close-btn:hover { color: #b83010; }
body[data-bg="light"] .lhb-reason { color: #8a5500; }
body[data-bg="light"] .lhb-col-title { color: #5a4a3a; }
body[data-bg="light"] .lhb-col-title.buy { color: #b83010; }
body[data-bg="light"] .lhb-name { color: #1a1d26; }
body[data-bg="light"] .lhb-row { border-bottom-color: rgba(0,0,0,0.08); }
body[data-bg="light"] .lhb-empty { color: #6a7a90; }
body[data-bg="light"] .board-code { color: #1a1d26; }
body[data-bg="light"] .reason-modal { background: rgba(255,255,255,0.98); border-color: var(--border-soft); }
/* ===================== 板块轮动历史视图 ===================== */
.rot-toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
.rot-tip { color: var(--text-muted); font-size: 12px; flex: 1; }
.rot-source { display: flex; gap: 0; border-radius: 6px; overflow: hidden; border: 1px solid var(--border-soft, #444); }
.rot-source-btn { background: var(--bg-input, #1a1a1a); color: var(--text-secondary, #aaa); border: none; padding: 5px 12px; font-size: 12px; cursor: pointer; transition: background 0.15s; }
.rot-source-btn:hover { background: var(--bg-card, #222); }
.rot-source-btn.active { background: var(--accent-warm, #ffb400); color: #1a1a1a; font-weight: 600; }
body[data-bg="light"] .rot-source { border-color: #d0d0d0; }
body[data-bg="light"] .rot-source-btn { background: #f5f5f5; color: #555; }
body[data-bg="light"] .rot-source-btn:hover { background: #eaeaea; }
body[data-bg="light"] .rot-source-btn.active { background: #d97b00; color: #fff; }
.rot-table-scroll { overflow-x: auto; border: 1px solid var(--border-soft); border-radius: 6px; }
.rot-table { border-collapse: collapse; min-width: 100%; font-size: 12px; }
.rot-table th, .rot-table td { padding: 5px 8px; text-align: center; border-bottom: 1px solid var(--border-soft); white-space: nowrap; }
.rot-table th { background: var(--bg-hover); color: var(--text-secondary); font-weight: 500; position: sticky; top: 0; }
.rot-rownum { color: var(--text-muted); font-size: 11px; min-width: 40px; }
.rot-date { color: var(--text-secondary); font-size: 11px; min-width: 70px; }
.rot-cell { min-width: 80px; padding: 3px 4px !important; vertical-align: middle; }
.rot-board { font-size: 12px; color: var(--text-main); }
.rot-board.highlight { color: #fff; background: #E24B4A; border-radius: 4px; padding: 1px 6px; display: inline-block; }
.rot-strength { font-size: 10px; color: var(--text-muted); margin-top: 1px; }
.rot-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
.rot-chart-block { background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: 8px; padding: 12px; }
.rot-chart-title { font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
.rot-svg { width: 100%; height: auto; }
.rot-windows { margin-top: 16px; background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: 8px; padding: 12px; }
.rot-window-legend { display: flex; gap: 16px; justify-content: center; margin-top: 6px; font-size: 12px; }
.rot-window-legend i { margin-right: 4px; }
@media (max-width: 768px) {
  .rot-charts { grid-template-columns: 1fr; }
}

</style>
