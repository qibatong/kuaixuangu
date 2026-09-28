<template>
  <div class="lhb-panel">
    <div class="rot-toolbar">
      <span class="rot-tip"><i class="fa fa-info-circle"></i> 龙虎榜当日 / 历史；选日期可回看。当日数据 17:00 后陆续披露。</span>
      <!-- ★ 2026-09-28 新增：把接口本来就返回的 `date` 显示出来。此前整页只有时钟，
           用户看不出「这份榜是哪一天的」（数据 18:00 后才公布；选中非交易日时后端会自动
           对齐到最近交易日 —— 这里显式说明，避免把上一交易日的榜误读成今天的）。 -->
      <span class="lhb-date-badge">
        榜单日期 <b>{{ dataDate || '今日 · 实时' }}</b>
        <span v-if="datePicker && dataDate && datePicker !== dataDate" class="lhb-date-note">
          （{{ datePicker }} 非交易日，显示最近交易日）
        </span>
      </span>
      <input v-model="datePicker" type="date" class="rot-date" @change="load">
      <button class="rot-reset-btn" title="回到实时" aria-label="回到实时" @click="clearDate"><i class="fa fa-bolt"></i></button>
      <button class="rot-reset-btn" title="刷新" aria-label="刷新" @click="load"><i class="fa fa-refresh"></i></button>
    </div>

    <!-- 分类 tab -->
    <div class="lhb-tabs">
      <button v-for="t in tabs" :key="t.key" class="lhb-tab" :class="{active: curTab===t.key}" @click="pickTab(t.key)">
        {{ t.label }}
      </button>
    </div>

    <!-- 资金流向图 -->
    <LhbSankey v-if="curTab==='sankey'" :list="list" />

    <template v-else>
    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载龙虎榜...</div></div>
    <div v-else-if="!filteredList.length" class="empty-state">
      {{ tagsLoading ? '正在统计机构 / 游资席位…（首次需拉取席位明细，约数秒）' : '暂无数据' }}
    </div>
    <div v-else class="lhb-table-scroll">
      <table class="stock-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: sort.keyOf('code') }" @click="sort.onSort('code', 'string')">代码<span class="sort-ind">{{ sort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('name') }" @click="sort.onSort('name', 'string')">名称<span class="sort-ind">{{ sort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('change') }" @click="sort.onSort('change')">涨跌幅%<span class="sort-ind">{{ sort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('limitBoards') }" @click="sort.onSort('limitBoards')">连板<span class="sort-ind">{{ sort.ind('limitBoards') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('buyIn') }" @click="sort.onSort('buyIn')">净买入(亿)<span class="sort-ind">{{ sort.ind('buyIn') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('amount') }" @click="sort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ sort.ind('amount') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('turnover') }" @click="sort.onSort('turnover')">换手%<span class="sort-ind">{{ sort.ind('turnover') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('floatMv') }" @click="sort.onSort('floatMv')">流通市值<span class="sort-ind">{{ sort.ind('floatMv') }}</span></th>
            <!-- ★ 2026-09-28 补列：前两个字段接口本来就下发（fetch_lhb 的 amplitude / joinNum）、
                 此前无列；「净买占比」是**纯派生**（净买额 ÷ 成交额），零上游成本 —— 同样净买 1 亿，
                 成交 3 亿的票和成交 30 亿的票完全不是一回事。 -->
            <th class="sortable" :class="{ active: sort.keyOf('amplitude') }" @click="sort.onSort('amplitude')">振幅%<span class="sort-ind">{{ sort.ind('amplitude') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('joinNum') }" @click="sort.onSort('joinNum')">上榜家数<span class="sort-ind">{{ sort.ind('joinNum') }}</span></th>
            <th class="sortable" :class="{ active: sort.keyOf('netRatioNum') }" @click="sort.onSort('netRatioNum')">净买占比<span class="sort-ind">{{ sort.ind('netRatioNum') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in sort.sorted(filteredList)" :key="l.code">
            <td class="code-click" @click="linkToSoftware(l.code)">{{ l.code }}</td>
            <td class="name-col" @click="openDetail(l)" style="cursor:pointer">
              <span class="stock-name">{{ l.name }}</span>
              <span v-if="l.instFlag" class="tag tag-inst">机构</span>
              <span v-if="l.hotFlag" class="tag tag-hot">游资</span>
            </td>
            <td :class="l.change > 0 ? 'up' : l.change < 0 ? 'down' : 'dim'">{{ signed(l.change) }}%</td>
            <td><span v-if="l.limitBoards > 0" class="lb-badge">{{ l.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td class="buyin-cell" :class="buyinClass(l.buyIn)">{{ yi(l.buyIn) }}</td>
            <td>{{ yi(l.amount) }}</td>
            <td>{{ (l.turnover || 0).toFixed(2) }}</td>
            <td>{{ yi(l.floatMv) }}</td>
            <td>{{ num1(l.amplitude) }}</td>
            <td>{{ l.joinNum === null || l.joinNum === undefined ? '-' : l.joinNum }}</td>
            <td :class="ratioCls(l.netRatioNum)">{{ ratioText(l.netRatioNum) }}</td>
            <td><button class="pool-add-btn" @click="viewDetail(l)">明细</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 个股详情弹层 -->
    <StockDetailPanel v-if="detailStock.code" :code="detailStock.code" :name="detailStock.name" @close="detailStock={code:'',name:''}" />

    <!-- 营业部明细弹窗 -->
    <div v-if="modal.show" class="modal-mask" @click.self="modal.show = false">
      <div class="reason-modal">
        <div class="reason-head">
          <span><i class="fa fa-list-alt lhb-head-icon"></i> {{ modal.detail.name }} {{ modal.code }} · 龙虎榜营业部</span>
          <button class="close-btn" @click="modal.show = false"><i class="fa fa-close"></i></button>
        </div>
        <div v-if="detailLoading" class="reason-loading">查询中...</div>
        <template v-else>
          <div v-if="modal.detail.upReason" class="lhb-reason">涨停原因：{{ modal.detail.upReason }}</div>
          <div class="lhb-total">
            <span>买入总计 <b class="up">{{ yi(modal.detail.buyTotal) }}亿</b></span>
            <span>卖出总计 <b class="down">{{ yi(modal.detail.sellTotal) }}亿</b></span>
            <!-- ★ 2026-09-28 新增：净额（买−卖）。此前只并列两个总数，用户还得自己减 -->
            <span>净额 <b :class="modalNet >= 0 ? 'up' : 'down'">{{ modalNet >= 0 ? '+' : '' }}{{ yi(modalNet) }}亿</b></span>
            <span>换手 {{ (modal.detail.turnover || 0).toFixed(2) }}%</span>
          </div>
          <!-- ★ 2026-09-28：席位行本来就**同时**带 `buy` 与 `sell`（上游 doc101 如此），
               此前只渲染单侧、另一侧被丢掉 ⇒ 改成「买 / 卖 / 净」三列（单位：亿）。 -->
          <div class="lhb-cols">
            <div class="lhb-col">
              <div class="lhb-col-title buy">买入营业部（亿元）</div>
              <div v-for="(b, i) in modal.detail.buyList" :key="i" class="lhb-row">
                <span class="lhb-idx">{{ i + 1 }}</span>
                <span class="lhb-name">{{ b.name }}<span v-if="b.hot" class="tag tag-hot">游资</span><span v-if="b.inst" class="tag tag-inst">机构</span></span>
                <span class="lhb-amt up">买 {{ (b.buy / 1e8).toFixed(2) }}</span>
                <span class="lhb-amt down">卖 {{ (b.sell / 1e8).toFixed(2) }}</span>
                <span class="lhb-amt" :class="(b.buy - b.sell) >= 0 ? 'up' : 'down'">净 {{ ((b.buy - b.sell) / 1e8).toFixed(2) }}</span>
              </div>
              <div v-if="!modal.detail.buyList.length" class="lhb-empty">无</div>
            </div>
            <div class="lhb-col">
              <div class="lhb-col-title sell">卖出营业部（亿元）</div>
              <div v-for="(s, i) in modal.detail.sellList" :key="i" class="lhb-row">
                <span class="lhb-idx">{{ i + 1 }}</span>
                <span class="lhb-name">{{ s.name }}<span v-if="s.hot" class="tag tag-hot">游资</span><span v-if="s.inst" class="tag tag-inst">机构</span></span>
                <span class="lhb-amt up">买 {{ (s.buy / 1e8).toFixed(2) }}</span>
                <span class="lhb-amt down">卖 {{ (s.sell / 1e8).toFixed(2) }}</span>
                <span class="lhb-amt" :class="(s.buy - s.sell) >= 0 ? 'up' : 'down'">净 {{ ((s.buy - s.sell) / 1e8).toFixed(2) }}</span>
              </div>
              <div v-if="!modal.detail.sellList.length" class="lhb-empty">无</div>
            </div>
          </div>
        </template>
      </div>
    </div>
    </template>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, computed } from 'vue'
import { kplLhb, kplLhbDetail, kplLhbTags } from '../api/kpl'
import { linkToSoftware } from '../utils/tdx'
import { useSortable } from '../composables/useSortable'
import { yi, signed } from '../utils/format'
import StockDetailPanel from './StockDetailPanel.vue'
import LhbSankey from './LhbSankey.vue'

const list = ref([])
const loading = ref(true)
const detailLoading = ref(false)
const datePicker = ref('')
const dataDate = ref('')
const sort = useSortable()
const curTab = ref('all')

const tabs = [
  { key: 'all', label: '净买入排行' },
      { key: 'sankey', label: '资金流向图' },
  { key: 'inst', label: '机构席位' },
  { key: 'hot', label: '知名游资' },
]

// ★ 2026-09-28：机构/游资判定**已上移到后端**（backend/app/services/kpl.py 的 `_seat_flags`）——
//   它用的是上游明细行自带的 `YouZiIcon`（游资图标）与 `GroupID`（游资派系），比原先这份关键词表
//   可靠：关键词既会漏（名单外的游资抓不到），也会误伤（「宁波 / 温州 / 量化」这类地名泛称）。
//   明细接口现在每个席位都带 `inst` / `hot`，这里直接渲染，不再本地猜。

// 机构 / 游资两个 tab 的数据**按需**拉取：列表接口(doc100)没有这两个字段，后端得按代码去取
// 席位明细(doc101)汇总。为保证龙虎榜首屏不被拖慢（55 只逐个明细要数秒），只在切到这两个 tab
// 时才发这一次请求；后端有 10 分钟缓存、明细另有 300s 缓存。
const tagsLoading = ref(false)
const tagsLoaded = ref(false)

async function ensureTags() {
  if (tagsLoaded.value || tagsLoading.value || !list.value.length) return
  tagsLoading.value = true
  try {
    const codes = list.value.map(l => l.code).filter(Boolean)
    const d = await kplLhbTags(codes, dataDate.value || '')
    const t = (d && d.tags) || {}
    list.value = list.value.map(l => {
      const x = t[l.code] || {}
      return { ...l, hasInst: !!x.inst, hasHot: !!x.hot, instNet: x.instNet, hotNet: x.hotNet }
    })
    tagsLoaded.value = true
  } catch (e) {
    // 静默：标签拿不到时这两个 tab 为空，但不影响「净买入排行 / 资金流向图」
  } finally {
    tagsLoading.value = false
  }
}

function pickTab(k) {
  curTab.value = k
  if (k === 'inst' || k === 'hot') ensureTags()
}

const filteredList = computed(() => {
  // 给每条打上标签（★ 2026-09-28：顺带算出「净买占比」，它要参与排序 ⇒ 必须挂在行上）
  const tagged = list.value.map(l => ({
    ...l,
    instFlag: l.hasInst || false,
    hotFlag: l.hasHot || false,
    netRatioNum: netRatioVal(l),
  }))
  if (curTab.value === 'inst') return tagged.filter(l => l.instFlag)
  if (curTab.value === 'hot') return tagged.filter(l => l.hotFlag)
  return tagged
})

// 振幅（上游下发的是字符串，如 "9.57"）：缺失/非数字显示 '-'，**不**填 0
function num1(v) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(1) : '-'
}

// 净买额占成交额比（纯派生：净买额 ÷ 成交额）
// ⚠️ 必须两个数都有效且成交额 > 0 才给值，否则返回 null ⇒ 界面显示 '-'。
//    绝不能用 0 冒充「没有净买」—— 这是本项目反复强调的「不要把不知道画成 0」。
function netRatioVal(l) {
  const a = Number(l && l.amount)
  const b = Number(l && l.buyIn)
  if (!Number.isFinite(a) || !Number.isFinite(b) || a <= 0) return null
  return b / a * 100
}
function ratioText(v) {
  if (v === null || v === undefined) return '-'
  return (v > 0 ? '+' : '') + v.toFixed(2) + '%'
}
function ratioCls(v) {
  if (v === null || v === undefined) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}

function buyinClass(v) {
  if (!v) return ''
  if (v >= 5) return 'buyin-big'
  if (v >= 1) return 'buyin-mid'
  return ''
}

const modal = reactive({
  show: false,
  code: '',
  detail: { name: '', buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 },
})

const detailStock = reactive({ code: '', name: '' })
// 净额 = 买入总计 − 卖出总计（弹窗用；缺失按 0 处理以免出现 NaN）
const modalNet = computed(() => (Number(modal.detail.buyTotal) || 0) - (Number(modal.detail.sellTotal) || 0))

function openDetail(l) {
  detailStock.code = l.code
  detailStock.name = l.name
}

async function load() {
  loading.value = true
  tagsLoaded.value = false      // 榜单变了 ⇒ 机构/游资标签要重取（下次切到那两个 tab 时）
  try {
    const d = await kplLhb(datePicker.value)
    list.value = d.list || []
    dataDate.value = d.date || ''
  } catch (e) { } finally {
    loading.value = false
  }
}

function clearDate() { datePicker.value = ''; load() }

async function viewDetail(l) {
  modal.show = true
  modal.code = l.code
  modal.detail = { name: l.name, buyList: [], sellList: [], buyTotal: 0, sellTotal: 0, upReason: '', turnover: 0 }
  detailLoading.value = true
  try {
    const d = await kplLhbDetail(l.code)
    if (d && d.detail) modal.detail = d.detail
  } catch (e) { } finally { detailLoading.value = false }
}

onMounted(load)
</script>

<style scoped>
.lhb-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
/* 榜单日期(2026-09-28): 复盘要能一眼看出"这是哪天的榜" */
.lhb-date-badge { color: var(--text-secondary); font-size: 0.8125rem; margin-left: 10px; }
.lhb-date-badge b { color: #ffb400; }
.lhb-date-note { color: var(--text-muted); }
.lhb-tab {
  padding: 6px 14px; border-radius: 999px; border: 1px solid var(--border-soft);
  background: transparent; color: var(--text-secondary); cursor: pointer; font-size: 0.8125rem;
}
.lhb-tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.lhb-table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.name-col { white-space: nowrap; }
.stock-name { font-weight: 600; }
.tag {
  display: inline-block; font-size: 0.65rem; padding: 0 4px; border-radius: 3px;
  margin-left: 4px; vertical-align: middle;
}
.tag-inst { background: rgba(255,180,0,0.15); color: #ffb400; border: 1px solid rgba(255,180,0,0.3); }
.tag-hot { background: rgba(255,80,40,0.15); color: #ff6a3c; border: 1px solid rgba(255,80,40,0.3); }
.buyin-mid { background: rgba(255,80,40,0.12); }
.buyin-big { background: rgba(255,40,20,0.25); font-weight: 700; }
.lb-badge {
  display: inline-block; color: #ff8a5c;
  border: 1px solid rgba(255, 80, 40, 0.5); border-radius: 4px;
  padding: 0 5px; font-size: 0.75rem; background: rgba(255, 80, 40, 0.12);
}
.lhb-head-icon { color: #ffb400; }
.modal-mask {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center; z-index: 1000;
}
.reason-modal {
  background: var(--bg-panel-solid); border: 1px solid var(--border-soft);
  border-radius: 12px; width: 640px; max-width: 92vw; max-height: 76vh;
  overflow: auto; padding: 18px;
}
.reason-head { display: flex; align-items: center; justify-content: space-between; color: #ffe0a0; font-size: 1rem; margin-bottom: 14px; }
.close-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 1rem; }
.close-btn:hover { color: #ff6a6a; }
.reason-loading { color: var(--text-muted); padding: 20px; text-align: center; }
.lhb-reason { color: #ffb400; font-size: 0.8125rem; margin-bottom: 10px; line-height: 1.5; }
.lhb-total { display: flex; gap: 20px; flex-wrap: wrap; color: #aaa; font-size: 0.8125rem; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--border-soft); }
.lhb-cols { display: flex; gap: 16px; }
.lhb-col { flex: 1; min-width: 0; }
.lhb-col-title { font-size: 0.8125rem; margin-bottom: 8px; }
.lhb-col-title.buy { color: #ff8a8a; }
.lhb-col-title.sell { color: #8ae08a; }
.lhb-row { display: flex; align-items: center; gap: 6px; padding: 4px 0; font-size: 0.75rem; border-bottom: 1px solid rgba(255, 255, 255, 0.04); }
.lhb-idx { width: 16px; color: var(--text-muted); }
.lhb-name { flex: 1; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lhb-amt { font-family: inherit; }
.lhb-empty { color: #666; font-size: 0.75rem; padding: 8px 0; }
body[data-bg="light"] .lhb-row { border-bottom-color: rgba(0,0,0,0.08); }
@media (max-width: 768px) {
  .stock-table { min-width: 800px; }
  .lhb-cols { flex-direction: column; gap: 8px; }
  .reason-modal { width: 96vw; padding: 12px 10px; }
}
</style>
