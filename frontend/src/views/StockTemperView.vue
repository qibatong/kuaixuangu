<template>
  <div class="page-shell">
    <div class="temper-head">
      <span class="temper-title"><i class="fa fa-fire"></i> 股性排行</span>
      <span class="temper-sub">历史封板率 · 次日溢价 · 炸板反包 · 波动画像</span>
      <span class="temper-time">{{ bjTime }}</span>
    </div>

    <div class="temper-toolbar">
      <span class="temper-tip"><i class="fa fa-info-circle"></i> 按综合股性分降序；点一行看完整画像 · 统计范围：近一年有涨停/炸板记录的 {{ scope }} 只</span>
      <label class="temper-filter">
        最少涨停次数
        <select v-model.number="minZt" class="temper-select" @change="reload()">
          <option :value="0">全部</option>
          <option :value="3">≥3</option>
          <option :value="8">≥8</option>
          <option :value="15">≥15</option>
          <option :value="30">≥30</option>
        </select>
      </label>
      <input class="temper-search" v-model="keyword" placeholder="搜索代码/名称" @keyup.enter="reload()" />
      <div class="temper-page">
        <button class="pg-btn" :disabled="page <= 1" @click="page--; load(false)"><i class="fa fa-chevron-left"></i></button>
        <span class="pg-info">{{ list.length ? ((page - 1) * size + 1) + '-' + ((page - 1) * size + list.length) : 0 }} / {{ total }}</span>
        <button class="pg-btn" :disabled="(page - 1) * size + list.length >= total" @click="page++; load(false)"><i class="fa fa-chevron-right"></i></button>
      </div>
    </div>

    <div class="temper-panel">
      <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>计算股性画像中...</div></div>

      <div v-else-if="!list.length" class="empty-state">
        {{ emptyText }}<br><span class="empty-sub">盘后 15:30 自动落库涨停/炸板；回补历史后样本逐日增多</span>
      </div>

      <table v-else class="stock-table">
        <thead>
          <tr>
            <th>#</th>
            <th class="sortable" :class="{ active: sortKey === 'code' }" @click="onSort('code')">代码</th>
            <th class="sortable" :class="{ active: sortKey === 'name' }" @click="onSort('name')">名称</th>
            <th class="sortable" :class="{ active: sortKey === 'score' }" @click="onSort('score')">股性分</th>
            <th class="sortable" :class="{ active: sortKey === 'seal_rate' }" @click="onSort('seal_rate')">封板率%</th>
            <th class="sortable" :class="{ active: sortKey === 'broken_rate' }" @click="onSort('broken_rate')">炸板率%</th>
            <th class="sortable" :class="{ active: sortKey === 'max_zt' }" @click="onSort('max_zt')">最大连板</th>
            <th class="sortable" :class="{ active: sortKey === 'avg_next_open_prem' }" @click="onSort('avg_next_open_prem')">次日溢价%</th>
            <th class="sortable" :class="{ active: sortKey === 'gap_up_rate' }" @click="onSort('gap_up_rate')">高开率%</th>
            <th class="sortable" :class="{ active: sortKey === 'rebuy_rate' }" @click="onSort('rebuy_rate')">反包率%</th>
            <th class="sortable" :class="{ active: sortKey === 'win_rate' }" @click="onSort('win_rate')">打板胜率%</th>
            <th class="sortable" :class="{ active: sortKey === 'big_red_count' }" @click="onSort('big_red_count')">大阴线</th>
            <th>标签</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(it, i) in shown" :key="it.code" class="temper-row" @click="openDetail(it)">
            <td class="rank-col">{{ (page - 1) * size + i + 1 }}</td>
            <td class="code-click">{{ it.code }}</td>
            <td class="name-col"><div class="name-main">{{ it.name || it.code }}</div></td>
            <td><span class="score-badge" :class="scoreCls(it.score)">{{ it.score }}</span></td>
            <td class="up">{{ it.seal_rate }}</td>
            <td class="down">{{ it.broken_rate }}</td>
            <td class="zt-cell">{{ it.max_zt }}</td>
            <td :class="colCls(it.avg_next_open_prem)">{{ it.avg_next_open_prem }}</td>
            <td :class="colCls(it.gap_up_rate)">{{ it.gap_up_rate }}</td>
            <td :class="colCls(it.rebuy_rate)">{{ it.rebuy_rate }}</td>
            <td :class="colCls(it.win_rate)">{{ it.win_rate }}</td>
            <td class="dim">{{ it.big_red_count }}</td>
            <td class="tag-cell">
              <span v-for="t in (it.tags || [])" :key="t" class="tag-chip" :class="tagCls(t)">{{ t }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 个股画像下钻弹窗 -->
    <div v-if="detail.show" class="modal-mask" @click.self="detail.show = false">
      <div class="detail-modal">
        <div class="detail-head">
          <span><i class="fa fa-fire" style="color:#ff6a6a;"></i> {{ detail.name || detail.code }} {{ detail.code }} · 股性画像</span>
          <button class="close-btn" @click="detail.show = false"><i class="fa fa-close"></i></button>
        </div>
        <div v-if="detail.loading" class="loading-placeholder"><div class="spinner"></div><div>加载画像...</div></div>
        <template v-else>
          <div class="score-line">
            <span class="big-score" :class="scoreCls(detail.score)">{{ detail.score }}</span>
            <span class="score-label">综合股性分</span>
            <span v-for="t in (detail.tags || [])" :key="t" class="tag-chip" :class="tagCls(t)">{{ t }}</span>
          </div>

          <div class="metric-grid">
            <div class="metric" v-for="m in metrics" :key="m.key">
              <div class="metric-label">{{ m.label }}</div>
              <div class="metric-val" :class="m.cls ? m.cls(detail[m.key]) : ''">{{ fmtV(m, detail[m.key]) }}</div>
              <div class="metric-sub">{{ m.sub }}</div>
            </div>
          </div>

          <!-- 溢价衰减曲线 -->
          <div v-if="detail.premium_decay && Object.keys(detail.premium_decay).length" class="decay-block">
            <div class="decay-title"><i class="fa fa-line-chart"></i> 次日溢价衰减（按板数）</div>
            <div class="decay-bars">
              <div v-for="(val, tier) in detail.premium_decay" :key="tier" class="decay-t">
                <div class="decay-label">{{ tierLabel(tier) }} <span class="decay-n">n={{ val.n }}</span></div>
                <div class="decay-bar">
                  <div class="decay-fill" :style="{ width: barPct(val.avg_open) + '%' }" title="高开幅度"></div>
                </div>
                <div class="decay-val">高开 {{ val.avg_open }}% <span class="dim">/ 最高 {{ val.avg_high }}%</span></div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { stockTemperRank, stockTemperProfile } from '../api/stockTemper'
import { usePolling } from '../composables/usePolling'
import { bjDateTimeStr } from '../utils/time'

const list = ref([])
const loading = ref(true)
const total = ref(0)
const scope = ref(0)
const page = ref(1)
const size = ref(50)
const minZt = ref(3)
const keyword = ref('')
const sortKey = ref('score')
const sortDesc = ref(true)
const bjTime = ref('--:--:--')
const emptyText = ref('暂无样本（需至少落库一笔涨停/炸板记录）')

const detail = reactive({ show: false, code: '', name: '', loading: false, score: 0, tags: [], premium_decay: {} })

function scoreCls(s) {
  if (s >= 80) return 'score-hi'
  if (s >= 60) return 'score-mid'
  if (s >= 40) return 'score-lo'
  return 'score-low'
}
// 负反馈标签: 警示样式
const WARN_TAGS = ['冲高回落频繁', '波动偏大', '炸板率高', '隔日兑现']
function tagCls(t) { return WARN_TAGS.includes(t) ? 'tag-chip-warn' : '' }
function colCls(v) {
  if (v > 0) return 'up'
  if (v < 0) return 'down'
  return ''
}
const shown = computed(() => {
  const arr = list.value.slice()
  arr.sort((a, b) => {
    const k = sortKey.value
    let av = a[k], bv = b[k]
    if (typeof av === 'string') { av = (av || '').localeCompare(bv || ''); return sortDesc.value ? -av : av }
    av = Number(av || 0); bv = Number(bv || 0)
    return sortDesc.value ? bv - av : av - bv
  })
  return arr
})
function onSort(k) {
  if (sortKey.value === k) sortDesc.value = !sortDesc.value
  else { sortKey.value = k; sortDesc.value = true }
}

const metrics = [
  { key: 'zt_count', label: '涨停次数', sub: '含炸板样本', v: v => v },
  { key: 'seal_rate', label: '封板率', sub: '%', cls: v => (v >= 80 ? 'up' : '') },
  { key: 'max_zt', label: '最大连板', sub: '板', v: v => v },
  { key: 'avg_next_open_prem', label: '次日平均溢价', sub: '高开幅度%', cls: v => (v >= 0 ? 'up' : 'down') },
  { key: 'avg_next_high_prem', label: '次日最高溢价', sub: '盘中最高%', cls: v => (v >= 0 ? 'up' : 'down') },
  { key: 'gap_up_rate', label: '封住次日高开率', sub: '%', cls: v => (v >= 50 ? 'up' : '') },
  { key: 'rebuy_rate', label: '炸板反包率', sub: '炸板后N日重新封住', cls: v => (v >= 50 ? 'up' : '') },
  { key: 'win_rate', label: '打板胜率', sub: '次日收盘卖出', cls: v => (v >= 50 ? 'up' : '') },
  { key: 'pl_ratio', label: '盈亏比', sub: '平均盈利/亏损', cls: v => (v >= 1 ? 'up' : '') },
  { key: 'big_red_count', label: '大阴线', sub: '冲高回落:盘中最高-收盘价差≥8%' },
  { key: 'deep_dip_count', label: '日内大回撤', sub: '高点回撤≥5%次数' },
  { key: 'repair_rate', label: '回调修复率', sub: '大阴线后N日后再次封住', cls: v => (v >= 60 ? 'up' : '') }
]
function fmtV(m, v) {
  if (m.v) return m.v(v)
  return Number(v) !== v ? v : (typeof v === 'number' ? v : '--')
}
function tierLabel(t) { return t === '1' ? '首板' : t === '2' ? '二板' : t === '3' ? '三板' : t === '4' ? '四板' : t + '板' }
function barPct(v) { return Math.max(4, Math.min(100, (Number(v) + 5) * 6)) }

async function load(resetPage = true) {
  if (resetPage) { page.value = 1; loading.value = true }
  try {
    const d = await stockTemperRank(page.value, size.value, minZt.value, keyword.value.trim())
    list.value = (d && d.list) || []
    total.value = (d && d.total) || 0
    scope.value = (d && d.scope) || 0
    emptyText.value = keyword.value.trim() ? '无匹配的股票（试试代码或名称）' : '暂无样本（当前最少涨停数条件下无结果）'
  } catch (e) { /* 静默 */ } finally { loading.value = false }
}
function reload() { load(true) }

async function openDetail(it) {
  detail.show = true
  detail.code = it.code
  detail.name = it.name
  detail.loading = true
  try {
    const d = await stockTemperProfile(it.code)
    Object.assign(detail, d)
  } catch (e) { /* 静默 */ } finally { detail.loading = false }
}

onMounted(() => {
  bjTime.value = bjDateTimeStr()
  usePolling(() => { bjTime.value = bjDateTimeStr() }, 1000, { immediate: false })
  load(true)
})
</script>

<style scoped>
.temper-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.temper-title { font-size: 20px; font-weight: 700; color: #ffe0a0; }
.temper-title .fa { color: #ff6a6a; }
.temper-sub { color: var(--text-muted); font-size: 13px; }
.temper-time { margin-left: auto; color: #aaa; font-size: 14px; font-family: "LXGW WenKai Mono", monospace; }
.temper-toolbar { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; flex-wrap: wrap; }
.temper-tip { color: var(--text-muted); font-size: 12px; flex: 1; min-width: 0; }
.temper-filter { color: var(--text-secondary); font-size: 13px; display: inline-flex; align-items: center; gap: 6px; }
.temper-select { padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border-soft); background: var(--bg-main); color: var(--text-primary); font-size: 13px; }
.temper-search { padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border-soft); background: var(--bg-main); color: var(--text-primary); font-size: 13px; width: 150px; outline: none; }
.temper-search:focus { border-color: #ffb400; }
.temper-page { display: inline-flex; align-items: center; gap: 6px; }
.pg-btn { padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border-soft); background: var(--bg-hover); color: var(--text-secondary); cursor: pointer; }
.pg-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.pg-info { color: var(--text-muted); font-size: 12px; white-space: nowrap; }
.temper-panel { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px; }
.temper-row { cursor: pointer; }
.temper-row:hover td { background: rgba(255, 180, 0, 0.06); }
.score-badge { display: inline-block; min-width: 44px; text-align: center; padding: 3px 8px; border-radius: 8px; font-weight: 700; font-size: 13px; }
.score-hi { background: rgba(255, 90, 90, 0.18); color: #ff5a5a; border: 1px solid rgba(255, 90, 90, 0.5); }
.score-mid { background: rgba(255, 160, 40, 0.15); color: #ffa028; border: 1px solid rgba(255, 160, 40, 0.5); }
.score-lo { background: rgba(180, 108, 255, 0.14); color: #b56cff; border: 1px solid rgba(180, 108, 255, 0.4); }
.score-low { background: var(--bg-input); color: var(--text-muted); border: 1px solid var(--border-soft); }
.up { color: #ff5252; } .down { color: #5ac17a; }
.zt-cell { font-weight: 700; color: #ffb400; }
.rank-col { color: var(--text-muted); }
.name-col { max-width: 120px; } .name-main { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tag-cell { max-width: 200px; white-space: normal; }
.tag-chip { display: inline-block; margin: 1px 3px 1px 0; padding: 1px 7px; border-radius: 8px; background: rgba(90, 160, 255, 0.15); color: #5aa0ff; border: 1px solid rgba(90, 160, 255, 0.4); font-size: 11px; white-space: nowrap; }
.tag-chip-warn { background: rgba(255, 90, 90, 0.14); color: #ff6a6a; border-color: rgba(255, 90, 90, 0.45); }
body[data-bg="light"] .tag-chip-warn { background: rgba(220, 60, 60, 0.12); color: #c62828; border-color: rgba(220, 60, 60, 0.5); }
.code-click { color: #ffb400; cursor: pointer; }

/* 详情弹窗 */
.modal-mask { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.detail-modal { background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: 12px; width: 700px; max-width: 94vw; max-height: 84vh; overflow: auto; padding: 18px; }
.detail-head { display: flex; align-items: center; justify-content: space-between; color: #ffe0a0; font-size: 16px; margin-bottom: 14px; }
.close-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; }
.close-btn:hover { color: #ff6a6a; }
.score-line { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.big-score { font-size: 34px; font-weight: 800; }
.score-label { color: var(--text-muted); font-size: 13px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.metric { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 8px; padding: 10px; }
.metric-label { color: var(--text-muted); font-size: 12px; margin-bottom: 4px; }
.metric-val { font-size: 20px; font-weight: 700; }
.metric-sub { color: var(--text-muted); font-size: 11px; margin-top: 2px; }
.decay-block { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--border-soft); }
.decay-title { color: #ffe0a0; font-size: 14px; font-weight: 600; margin-bottom: 10px; }
.decay-t { margin-bottom: 8px; }
.decay-label { font-size: 12px; color: var(--text-secondary); margin-bottom: 3px; }
.decay-n { color: var(--text-muted); font-size: 11px; margin-left: 6px; }
.decay-bar { height: 8px; border-radius: 4px; background: var(--bg-input); overflow: hidden; margin-bottom: 3px; }
.decay-fill { height: 100%; background: linear-gradient(90deg, #ff8a3c, #ff2d55); }
.decay-val { font-size: 12px; color: var(--text-secondary); }

.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255, 90, 90, 0.3); border-top-color: #ff5a5a; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); }
.empty-sub { font-size: 12px; }

body[data-bg="light"] .temper-title { color: #8a1a12; }
body[data-bg="light"] .temper-title .fa { color: #c62828; }
body[data-bg="light"] .detail-head { color: #5a3a20; }
body[data-bg="light"] .decay-title { color: #5a3a20; }
body[data-bg="light"] .code-click { color: #b05e00; }

@media (max-width: 768px) {
  .temper-panel { overflow-x: auto; -webkit-overflow-scrolling: touch; padding: 10px 8px; }
  .temper-panel .stock-table { min-width: 1000px; }
  .metric-grid { grid-template-columns: repeat(2, 1fr); }
  .temper-time { margin-left: 0; width: 100%; }
}
</style>