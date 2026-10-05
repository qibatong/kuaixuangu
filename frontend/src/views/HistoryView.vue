<template>
  <div class="page-shell">
    <h1 class="visually-hidden">历史回看</h1>

    <!-- 板块轮动历史(从盘中页搬来, 点击板块看成分股) -->
    <SectorRotationPanel />

    <div class="history-panel">
      <div class="history-head">
        <span class="history-title"><i class="fa fa-history"></i> 历史选股记录</span>
      </div>

      <!-- 视图切换 Tab: 按批次 / 综合查询 / AI预测回看 -->
      <div class="view-tabs">
        <button class="view-tab" :class="{ active: viewMode === 'batch' }" @click="switchView('batch')">
          <i class="fa fa-folder-open-o"></i> 按批次 <span class="view-tab-desc">每次选股一组</span>
        </button>
        <button class="view-tab" :class="{ active: viewMode === 'query' }" @click="switchView('query')">
          <i class="fa fa-search"></i> 综合查询 <span class="view-tab-desc">跨批次条件筛选</span>
        </button>
        <button class="view-tab" :class="{ active: viewMode === 'aipick' }" @click="switchView('aipick')">
          <!-- 2026-09-05: fa-robot 为 FA5 图标, 项目用 FA4.7 不渲染(空白) → 换 fa-android -->
          <i class="fa fa-android"></i> AI预测·金睛 <span class="view-tab-desc">按日期回看预测报告</span>
        </button>
        <!-- 2026-09-25: 火眼(LightGBM) 平行链路回看 -->
        <button class="view-tab" :class="{ active: viewMode === 'aipick_lgb' }" @click="switchView('aipick_lgb')">
          <i class="fa fa-flask"></i> AI预测·火眼 <span class="view-tab-desc">火眼模型预测回看</span>
        </button>
      </div>

      <!-- ===== 按批次视图 ===== -->
      <div v-if="viewMode === 'batch'" class="batch-view">
        <div class="batch-tip">按选股批次分组展示：<b>每次选股操作（锁定/筛选）为一组</b>，点击批次可展开查看该批选出的股票明细</div>
        <div v-if="batchesLoading" class="loading-placeholder"><div class="spinner"></div><div>正在加载批次...</div></div>
        <div v-else-if="!batches.length" class="empty-state">暂无历史批次<br><span style="font-size:0.75rem">先在主页选股（锁定/筛选）后，这里就会按批次展示</span></div>
        <div v-else class="batch-list">
          <div v-for="b in batches" :key="b.id" class="batch-card" :class="{ expanded: expandedId === b.id }">
            <div class="batch-head" @click="toggleBatch(b.id)">
              <span class="batch-time">
                <i class="fa fa-clock-o"></i> {{ b.batch_date }} {{ b.batch_time }}
              </span>
              <span class="batch-type" :class="b.action === 'lock' ? 'type-lock' : 'type-filter'">
                {{ b.action === 'lock' ? '锁定选股' : '筛选重算' }}
              </span>
              <span v-if="b.auto_applied" class="batch-auto-tag" title="9:26 系统自动应用: 用户当天未主动点应用, 系统按用户偏好自动保存批次">⚙️ 自动</span>
              <span class="batch-meta">
                <span class="batch-market">{{ b.markets }}</span>
                <span class="batch-count">{{ b.stock_count }}只</span>
              </span>
              <span class="batch-toggle"><i class="fa" :class="expandedId === b.id ? 'fa-chevron-up' : 'fa-chevron-down'"></i></span>
            </div>
            <div v-if="expandedId === b.id" class="batch-body">
              <div v-if="batchDetailLoading" class="loading-placeholder" style="padding:10px;"><div class="spinner"></div><div>加载明细...</div></div>
              <div v-else-if="!batchStocks.length" class="empty-state" style="padding:12px;">该批次无股票</div>
              <div v-else style="overflow-x:auto;">
                <table class="stock-table" style="min-width:1100px">
                  <thead>
                    <tr>
                      <th>排名</th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('code') }" @click="batchSort.onSort('code', 'string')">代码<span class="sort-ind">{{ batchSort.ind('code') }}</span></th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('name') }" @click="batchSort.onSort('name', 'string')">名称<span class="sort-ind">{{ batchSort.ind('name') }}</span></th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('bid_change') }" @click="batchSort.onSort('bid_change')">竞价涨幅<span class="sort-ind">{{ batchSort.ind('bid_change') }}</span></th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('real_change') }" @click="batchSort.onSort('real_change')">实时涨幅<span class="sort-ind">{{ batchSort.ind('real_change') }}</span></th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('entity_change') }" @click="batchSort.onSort('entity_change')">实体涨幅<span class="sort-ind">{{ batchSort.ind('entity_change') }}</span></th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('bid_amt') }" @click="batchSort.onSort('bid_amt')">竞价金额(万)<span class="sort-ind">{{ batchSort.ind('bid_amt') }}</span></th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('bid_ratio') }" @click="batchSort.onSort('bid_ratio')">竞价/昨比<span class="sort-ind">{{ batchSort.ind('bid_ratio') }}</span></th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('circulation_mv') }" @click="batchSort.onSort('circulation_mv')">流通市值(亿)<span class="sort-ind">{{ batchSort.ind('circulation_mv') }}</span></th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('industry') }" @click="batchSort.onSort('industry', 'string')">行业<span class="sort-ind">{{ batchSort.ind('industry') }}</span></th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('probability') }" @click="batchSort.onSort('probability')">评分<span class="sort-ind">{{ batchSort.ind('probability') }}</span></th>
                      <th class="sortable" :class="{ active: batchSort.keyOf('confidence') }" @click="batchSort.onSort('confidence')">可信度<span class="sort-ind">{{ batchSort.ind('confidence') }}</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="s in batchSort.sorted(batchStocks)" :key="s.code">
                      <td>{{ s.rank }}</td>
                      <td class="code-click" @click="linkToSoftware(s.code)">{{ s.code }}</td><td>{{ s.name }}</td>
                      <td :class="chgCls(s.bid_change)">{{ chgPct(s.bid_change) }}</td>
                      <td :class="chgCls(s.real_change)">{{ chgPct(s.real_change) }}</td>
                      <td :class="chgCls(s.entity_change)">{{ chgPct(s.entity_change) }}</td>
                      <td>{{ bidAmtText(s.bid_amt) }}</td>
                      <td :class="ratioCls(s.bid_ratio)">{{ ratioText(s.bid_ratio) }}</td>
                      <td>{{ fmtNum(s.circulation_mv, 1) }}</td>
                      <td>{{ s.industry }}</td>
                      <td class="score-cell">{{ fmtNum(s.probability, 0, '分') }}</td>
                      <td>{{ fmtNum(s.confidence, 0, '%') }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ===== 综合查询视图 ===== -->
      <div v-else-if="viewMode === 'query'" class="history-query">
        <!-- 战绩统计(可折叠) -->
        <div v-if="stats" class="stats-panel">
          <div class="stats-title" style="cursor:pointer;" @click="statsCollapsed = !statsCollapsed">
            <i class="fa" :class="statsCollapsed ? 'fa-chevron-down' : 'fa-chevron-up'"></i>
            <i class="fa fa-line-chart"></i> 战绩统计
            <span class="stats-range">{{ stats.range.from }} ~ {{ stats.range.to }}</span>
            <span class="stats-toggle">{{ statsCollapsed ? '展开详情' : '收起详情' }}</span>
          </div>
          <div class="stats-cards">
            <div class="stat-card"><div class="stat-num">{{ stats.overview.total }}</div><div class="stat-label">总入选(次)</div></div>
            <div class="stat-card"><div class="stat-num" :class="rateCls(stats.overview.win_rate)">{{ pct(stats.overview.win_rate) }}</div><div class="stat-label">整体胜率</div></div>
            <div class="stat-card medal-gold"><div class="stat-num" :class="rateCls(stats.top3.win_rate)">{{ pct(stats.top3.win_rate) }}</div><div class="stat-label">🥇前三强胜率</div></div>
            <div class="stat-card"><div class="stat-num" :class="stats.overview.avg_real > 0 ? 'up' : stats.overview.avg_real < 0 ? 'down' : ''">{{ signed(stats.overview.avg_real) }}%</div><div class="stat-label">平均实时涨幅</div></div>
            <div class="stat-card"><div class="stat-num" :class="stats.top3.avg_real > 0 ? 'up' : stats.top3.avg_real < 0 ? 'down' : ''">{{ signed(stats.top3.avg_real) }}%</div><div class="stat-label">前三强平均涨幅</div></div>
          </div>
          <template v-if="!statsCollapsed">
            <div v-if="stats.by_score.length" class="stats-score">
              <div class="stats-sub">评分有效性（评分越高胜率越高说明评分有效）</div>
              <div class="score-bars">
                <div v-for="g in stats.by_score" :key="g.range" class="score-bar" :title="`${g.range}分：${g.count}次，胜率${pct(g.win_rate)}，平均${signed(g.avg_real)}%`">
                  <div class="score-bar-label">{{ g.range }}分</div>
                  <div class="score-bar-track"><div class="score-bar-fill" :style="{ width: Math.max(3, g.win_rate * 100) + '%' }" :class="rateCls(g.win_rate)"></div></div>
                  <div class="score-bar-val">{{ pct(g.win_rate) }} <span class="dim">({{ g.count }})</span></div>
                </div>
              </div>
            </div>
            <div v-if="stats.daily.length" class="stats-daily">
              <div class="stats-sub">每日趋势（最近 {{ stats.daily.length }} 个有记录的交易日）</div>
              <div class="daily-list">
                <div v-for="d in stats.daily" :key="d.date" class="daily-row">
                  <span class="daily-date">{{ d.date }}</span>
                  <span class="daily-cnt">{{ d.count }}次</span>
                  <span class="daily-rate" :class="rateCls(d.win_rate)">{{ pct(d.win_rate) }}</span>
                  <span class="daily-real" :class="d.avg_real > 0 ? 'up' : d.avg_real < 0 ? 'down' : 'dim'">{{ signed(d.avg_real) }}%</span>
                </div>
              </div>
            </div>
            <div v-else class="stats-empty">当前日期范围暂无历史数据，先选几次股再回来看战绩</div>
          </template>
        </div>

        <div class="query-form">
          <label>日期 <input v-model="f.date_from" type="date"> ~ <input v-model="f.date_to" type="date"></label>
          <label>竞价涨幅 <input v-model="f.bid_min" type="number" placeholder="不限"> ~ <input v-model="f.bid_max" type="number" placeholder="不限"> %</label>
          <label>流通市值 <input v-model="f.mv_min" type="number" placeholder="不限"> ~ <input v-model="f.mv_max" type="number" placeholder="不限"> 亿</label>
          <label>评分≥ <input v-model="f.prob_min" type="number" placeholder="不限"></label>
          <label>可信度≥ <input v-model="f.conf_min" type="number" placeholder="不限"></label>
          <select v-model="f.action"><option value="">全部类型</option><option value="lock">锁定选股</option><option value="filter">筛选重算</option></select>
          <button class="tdx-export-btn query-submit-btn" style="background:var(--accent-deep);" @click="runQuery"><i class="fa fa-search"></i> 查询</button>
        </div>
        <div class="query-tip">打开时已自动查询当月记录；<b>同一天同一只股票评分相同自动去重</b>（只保留一条）；竞价涨幅、流通市值、评分、可信度等条件可留空，留空表示不限制</div>
        <div class="query-result">
          <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>正在查询...</div></div>
          <div v-else-if="!rows.length" class="empty-state">没有符合条件的记录<br><span style="font-size:0.75rem">可放宽日期范围或属性条件</span></div>
          <template v-else>
            <div class="query-summary">共 {{ total }} 条记录（同一天同评分自动去重）</div>
            <div style="overflow-x:auto;">
              <table class="stock-table" style="min-width:1180px">
                <thead>
                  <tr>
                    <th class="sortable" :class="{ active: querySort.keyOf('batch_date') }" @click="querySort.onSort('batch_date', 'string')">日期<span class="sort-ind">{{ querySort.ind('batch_date') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('batch_time') }" @click="querySort.onSort('batch_time', 'string')">时间<span class="sort-ind">{{ querySort.ind('batch_time') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('action') }" @click="querySort.onSort('action', 'string')">类型<span class="sort-ind">{{ querySort.ind('action') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('code') }" @click="querySort.onSort('code', 'string')">代码<span class="sort-ind">{{ querySort.ind('code') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('name') }" @click="querySort.onSort('name', 'string')">名称<span class="sort-ind">{{ querySort.ind('name') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('bid_change') }" @click="querySort.onSort('bid_change')">竞价涨幅<span class="sort-ind">{{ querySort.ind('bid_change') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('real_change') }" @click="querySort.onSort('real_change')">实时涨幅<span class="sort-ind">{{ querySort.ind('real_change') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('entity_change') }" @click="querySort.onSort('entity_change')">实体涨幅<span class="sort-ind">{{ querySort.ind('entity_change') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('bid_amt') }" @click="querySort.onSort('bid_amt')">竞价金额(万)<span class="sort-ind">{{ querySort.ind('bid_amt') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('bid_ratio') }" @click="querySort.onSort('bid_ratio')">竞价/昨比<span class="sort-ind">{{ querySort.ind('bid_ratio') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('circulation_mv') }" @click="querySort.onSort('circulation_mv')">流通市值(亿)<span class="sort-ind">{{ querySort.ind('circulation_mv') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('industry') }" @click="querySort.onSort('industry', 'string')">行业<span class="sort-ind">{{ querySort.ind('industry') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('probability') }" @click="querySort.onSort('probability')">评分<span class="sort-ind">{{ querySort.ind('probability') }}</span></th>
                    <th class="sortable" :class="{ active: querySort.keyOf('confidence') }" @click="querySort.onSort('confidence')">可信度<span class="sort-ind">{{ querySort.ind('confidence') }}</span></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(s, i) in querySort.sorted(rows)" :key="i">
                    <td>{{ s.batch_date }}</td><td>{{ s.batch_time }}</td>
                    <td>{{ s.action === 'lock' ? '锁定' : '筛选' }}</td>
                    <td class="code-click" @click="linkToSoftware(s.code)">{{ s.code }}</td><td>{{ s.name }}</td>
                    <td :class="chgCls(s.bid_change)">{{ chgPct(s.bid_change) }}</td>
                    <td :class="realCls(s)">{{ chgPct(s.real_change) }}</td>
                    <td :class="chgCls(s.entity_change)">{{ chgPct(s.entity_change) }}</td>
                    <td>{{ bidAmtText(s.bid_amt) }}</td>
                    <td :class="ratioCls(s.bid_ratio)">{{ ratioText(s.bid_ratio) }}</td>
                    <td>{{ fmtNum(s.circulation_mv, 1) }}</td><td>{{ s.industry }}</td>
                    <td class="score-cell">{{ fmtNum(s.probability, 0, '分') }}</td><td>{{ fmtNum(s.confidence, 0, '%') }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="rows.length < total" style="text-align:center;margin:12px 0;">
              <button class="tdx-export-btn nav-btn nav-history" @click="loadMore"><i class="fa fa-plus-circle"></i> 加载更多（已显示 {{ rows.length }} / {{ total }} 条）</button>
            </div>
          </template>
        </div>
      </div>

      <!-- ===== AI预测回看视图(2026-09-01): 嵌入 AipickView, 全宽展示 + 日期选择器回看历史报告 ===== -->
      <div v-else-if="viewMode === 'aipick'" class="aipick-view">
        <AipickView />
      </div>

      <!-- ===== AI预测·LightGBM 回看(2026-09-25): 同一组件的另一模型视图, 日期回看口径一致 ===== -->
      <div v-else class="aipick-view">
        <AipickLgbView />
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import SectorRotationPanel from '../components/SectorRotationPanel.vue'
import { listBatches, queryHistory } from '../api/history'
import { trackUsage } from '../api/activity'
import { fetchPerformance } from '../api/stats'
import { showToast } from '../utils/toast'
import { linkToSoftware } from '../utils/tdx'
import { fmtDate } from '../utils/time'
import { useSortable } from '../composables/useSortable'
import { signed, fmtNum, pct as chgPct } from '../utils/format'
// 2026-09-01: AI预测回看 tab 直接嵌入组件(自带 VipGate 门禁 + 日期选择器 + 规则过滤)
import AipickView from './AipickView.vue'
// 2026-09-25: LightGBM 平行链路回看(共用 AipickReport, 只是 model='lgb')
import AipickLgbView from './AipickLgbView.vue'

const PAGE_SIZE = 100
// 表格排序实例
const batchSort = useSortable()
const querySort = useSortable()
// ---------- 视图切换 ----------
const viewMode = ref('batch')   // batch(按批次) / query(综合查询) / aipick(AI预测回看)
function switchView(m) {
  if (viewMode.value === m) return
  viewMode.value = m
  // 2026-09-22 v4.11.35: 用户主动切视图算一次使用(同一个 tab 重复点不算)
  trackUsage('history')
  if (m === 'batch') loadBatches()
}

// ---------- 按批次视图 ----------
const batches = ref([])
const batchesLoading = ref(false)
const expandedId = ref(null)
const batchStocks = ref([])
const batchDetailLoading = ref(false)

async function loadBatches() {
  batchesLoading.value = true
  try {
    const d = await listBatches()
    batches.value = d.batches || []
  } catch (e) {
    showToast('❌ ' + e.message, 'error')
  } finally {
    batchesLoading.value = false
  }
}

async function toggleBatch(id) {
  if (expandedId.value === id) {
    expandedId.value = null
    batchStocks.value = []
    return
  }
  expandedId.value = id
  batchStocks.value = []
  batchDetailLoading.value = true
  try {
    const d = await listBatches(id)
    batchStocks.value = d.stocks || []
  } catch (e) {
    showToast('❌ ' + e.message, 'error')
  } finally {
    batchDetailLoading.value = false
  }
}

// ---------- 综合查询视图 ----------
const f = reactive({
  date_from: '',
  date_to: '',
  bid_min: '',
  bid_max: '',
  mv_min: '',
  mv_max: '',
  prob_min: '',
  conf_min: '',
  action: ''
})
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const stats = ref(null)
const statsCollapsed = ref(true)    // 评分/每日趋势默认收起(面板更紧凑)
let page = 1

function initDefaults() {
  const now = new Date()
  if (!f.date_from) f.date_from = fmtDate(new Date(now.getFullYear(), now.getMonth(), 1))
  if (!f.date_to) f.date_to = fmtDate(now)
}

function cleanParams() {
  const p = {}
  Object.entries(f).forEach(([k, v]) => { if (String(v).trim() !== '') p[k] = v })
  return p
}

async function runQuery() {
  loading.value = true
  page = 1
  try {
    const data = await queryHistory(cleanParams(), page, PAGE_SIZE)
    rows.value = data.list || []
    total.value = data.total || 0
  } catch (e) {
    rows.value = []
    total.value = 0
    showToast('❌ ' + e.message, 'error')
  } finally {
    loading.value = false
  }
  loadStats()
}

async function loadStats() {
  try {
    stats.value = await fetchPerformance(cleanParams())
  } catch (e) {
    stats.value = null
  }
}

function pct(v) {
  if (v === null || v === undefined || isNaN(v)) return '—'
  return (Number(v) * 100).toFixed(1) + '%'
}
function rateCls(v) { return v >= 0.5 ? 'up' : v >= 0.3 ? '' : 'down' }

async function loadMore() {
  page++
  try {
    const data = await queryHistory(cleanParams(), page, PAGE_SIZE)
    rows.value = rows.value.concat(data.list || [])
  } catch (e) {
    showToast('❌ ' + e.message, 'error')
  }
}

function realCls(s) {
  const r = s.real_change
  if (r === null || r === undefined || isNaN(r)) return 'dim'      // P0-3: 未知不配色
  const b = s.bid_change
  if (b !== null && b !== undefined && !isNaN(b) && r < b) return 'real-green'
  return r > 0 ? 'up' : 'down'
}
// 涨跌配色(P0-3): 未知 → dim; 0 保持既有 down 口径
function chgCls(v) {
  if (v === null || v === undefined || isNaN(v)) return 'dim'
  return v > 0 ? 'up' : 'down'
}
function bidAmtText(amt) {
  if (amt === null || amt === undefined || isNaN(amt)) return '—'
  return amt >= 10000 ? (amt / 10000).toFixed(2) + '亿' : Number(amt).toFixed(0)
}
function ratioCls(br) {
  if (br === null || br === undefined || isNaN(br)) return 'dim'
  return br >= 2 ? 'ratio-hot' : br >= 1 ? 'ratio-warm' : ''
}
function ratioText(br) {
  if (br === null || br === undefined || isNaN(br)) return '—'
  return br.toFixed(2) + '%'
}

onMounted(() => {
  initDefaults()
  loadBatches()
  runQuery()
})
</script>

<style scoped>
.view-tabs {
  display: flex;
  gap: var(--s2);
  margin: var(--s2) 0 var(--s3);
}
.view-tab {
  display: inline-flex;
  align-items: baseline;
  gap: var(--s2);
  background: var(--bg-subtle);
  border: 1px solid var(--border-soft);
  color: var(--text-secondary);
  border-radius: var(--r-md);
  padding: var(--s2) var(--s4);
  font-size: var(--fs-base);
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s;
}
.view-tab:hover { border-color: var(--star); color: var(--warn-text); }
.view-tab.active {
  background: rgba(255,180,0,0.12);
  border-color: var(--star);
  color: var(--gold);
}
.view-tab-desc { font-size: var(--fs-xs); color: var(--text-muted); }
.view-tab.active .view-tab-desc { color: #c9a94a; }

.batch-view { margin-top: var(--s1); }
.batch-tip {
  background: rgba(255,180,0,0.06);
  border: 1px solid rgba(255,180,0,0.25);
  border-radius: var(--r-md);
  padding: var(--s2) var(--s3);
  color: #c9a94a;
  font-size: var(--fs-xs);
  margin-bottom: var(--s3);
}
.batch-tip b { color: var(--gold); }
.batch-list { display: flex; flex-direction: column; gap: var(--s2); }
.batch-card {
  background: var(--bg-subtle);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-lg);
  overflow: hidden;
}
.batch-card.expanded { border-color: rgba(255,180,0,0.4); }
.batch-head {
  display: flex;
  align-items: center;
  gap: var(--s3);
  padding: var(--s3) var(--s4);
  cursor: pointer;
  flex-wrap: wrap;
}
.batch-head:hover { background: var(--bg-hover); }
.batch-time { color: var(--warn-text); font-size: var(--fs-base); font-weight: 500; }
.batch-type {
  font-size: var(--fs-xs);
  border-radius: var(--r-sm);
  padding: 1px var(--s2);
}
.type-lock { color: var(--accent); border: 1px solid var(--accent-deep); background: rgba(var(--accent-rgb), 0.1); }
.type-filter { color: var(--accent-text); border: 1px solid var(--accent); background: rgba(var(--accent-rgb),0.1); }
.batch-auto-tag { font-size: var(--fs-xs); color: var(--text-muted); border: 1px dashed var(--text-muted); border-radius: var(--r-sm); padding: 1px var(--s2); margin-left: var(--s2); }
.batch-meta { display: flex; gap: var(--s2); margin-left: auto; align-items: center; }
.batch-market { color: var(--text-muted); font-size: var(--fs-xs); }
.batch-count { color: var(--success-text); font-size: var(--fs-xs); }
.batch-toggle { color: var(--text-muted); font-size: var(--fs-xs); }
.batch-body { border-top: 1px solid var(--border-soft); padding: var(--s2) var(--s2); }

/* 浅色主题覆盖 */
body[data-bg="light"] .view-tab:hover {  color: #5a4a3a; border-color: #b83010;  }
body[data-bg="light"] .view-tab {  color: #5a4a3a; border-color: #d0d0d0; background: rgba(255,255,255,0.6);  }
body[data-bg="light"] .view-tab.active .view-tab-desc {  color: #6a5a20;  }
body[data-bg="light"] .batch-tip b {  color: #8a5500;  }
body[data-bg="light"] .batch-time {  color: #5a4a3a;  }
body[data-bg="light"] .type-lock {  color: #b83010; border-color: #b83010; background: rgba(255,80,80,0.12);  }
body[data-bg="light"] .batch-type {  color: #1a1d26;  }
</style>
