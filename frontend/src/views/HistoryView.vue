<template>
  <div class="page-shell">
    <div class="page-back">
      <router-link to="/" class="tdx-export-btn" style="background:rgba(255,180,0,0.18);border:1px solid #ffb400;color:#ffe0a0;"><i class="fa fa-arrow-left"></i> 返回选股</router-link>
    </div>
    <div class="history-panel" style="height:min(80vh, 720px);">
      <div class="history-head">
        <span class="history-title"><i class="fa fa-history"></i> 历史选股记录</span>
      </div>
      <div class="history-query">
        <div class="query-form">
          <label>日期 <input type="date" v-model="f.date_from"> ~ <input type="date" v-model="f.date_to"></label>
          <label>竞价涨幅 <input type="number" v-model="f.bid_min" placeholder="不限"> ~ <input type="number" v-model="f.bid_max" placeholder="不限"> %</label>
          <label>流通市值 <input type="number" v-model="f.mv_min" placeholder="不限"> ~ <input type="number" v-model="f.mv_max" placeholder="不限"> 亿</label>
          <label>评分≥ <input type="number" v-model="f.prob_min" placeholder="不限"></label>
          <label>可信度≥ <input type="number" v-model="f.conf_min" placeholder="不限"></label>
          <select v-model="f.action"><option value="">全部类型</option><option value="lock">锁定选股</option><option value="filter">筛选重算</option></select>
          <button class="tdx-export-btn" style="background:#ff5c5c;" @click="runQuery"><i class="fa fa-search"></i> 查询</button>
        </div>
        <div class="query-tip">打开时已自动查询当月记录；同一只票一天内多次入选会全部保留（按入选时间展示，方便追踪调整轨迹）；竞价涨幅、流通市值、评分、可信度等条件可留空，留空表示不限制</div>
        <div class="query-result">
          <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>正在查询...</div></div>
          <div v-else-if="!rows.length" class="empty-state">没有符合条件的记录<br><span style="font-size:11px">可放宽日期范围或属性条件</span></div>
          <template v-else>
            <div class="query-summary">共 {{ total }} 条记录（同一只票一天多次入选会都展示，可按日期+时间区分）</div>
            <div style="overflow-x:auto;">
              <table class="stock-table" style="min-width:1180px">
                <thead><tr><th>日期</th><th>时间</th><th>类型</th><th>代码</th><th>名称</th><th>竞价涨幅</th><th>实时涨幅</th><th>实体涨幅</th><th>异动</th><th>竞价金额(万)</th><th>竞价/昨比</th><th>流通市值(亿)</th><th>行业</th><th>评分</th><th>可信度</th></tr></thead>
                <tbody>
                  <tr v-for="(s, i) in rows" :key="i">
                    <td>{{ s.batch_date }}</td><td>{{ s.batch_time }}</td>
                    <td>{{ s.action === 'lock' ? '锁定' : '筛选' }}</td>
                    <td class="code-click" @click="linkToSoftware(s.code)">{{ s.code }}</td><td>{{ s.name }}</td>
                    <td :class="s.bid_change > 0 ? 'up' : 'down'">{{ signed(s.bid_change) }}%</td>
                    <td :class="realCls(s)">{{ signed(s.real_change) }}%</td>
                    <td :class="s.entity_change > 0 ? 'up' : 'down'">{{ signed(s.entity_change) }}%</td>
                    <td>{{ warnLabel(s.warn_type) }}</td>
                    <td>{{ bidAmtText(s.bid_amt) }}</td>
                    <td :class="ratioCls(s.bid_ratio)">{{ ratioText(s.bid_ratio) }}</td>
                    <td>{{ s.circulation_mv.toFixed(1) }}</td><td>{{ s.industry }}</td>
                    <td class="up">{{ s.probability }}分</td><td>{{ s.confidence }}%</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="rows.length < total" style="text-align:center;margin:12px 0;">
              <button class="tdx-export-btn" style="background:rgba(255,180,0,0.15);border:1px solid #ffb400;color:#ffe0a0;" @click="loadMore"><i class="fa fa-plus-circle"></i> 加载更多（已显示 {{ rows.length }} / {{ total }} 条）</button>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { queryHistory } from '../api/history'
import { showToast } from '../utils/toast'
import { linkToSoftware } from '../utils/tdx'
import { fmtDate } from '../utils/time'

const PAGE_SIZE = 100
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
}

async function loadMore() {
  page++
  try {
    const data = await queryHistory(cleanParams(), page, PAGE_SIZE)
    rows.value = rows.value.concat(data.list || [])
  } catch (e) {
    showToast('❌ ' + e.message, 'error')
  }
}

function signed(v) { return (v > 0 ? '+' : '') + v.toFixed(2) }
function realCls(s) {
  if (s.real_change < s.bid_change) return 'real-green'
  return s.real_change > 0 ? 'up' : 'down'
}
function warnLabel(w) { return w === 5 ? '强' : w === 4 ? '⚡中' : w === 3 ? '↑弱' : '-' }
function bidAmtText(amt) { return amt >= 10000 ? (amt / 10000).toFixed(2) + '亿' : amt.toFixed(0) }
function ratioCls(br) {
  if (br === null || br === undefined || isNaN(br)) return 'dim'
  return br >= 2 ? 'ratio-hot' : br >= 1 ? 'ratio-warm' : ''
}
function ratioText(br) {
  if (br === null || br === undefined || isNaN(br)) return '-'
  return br.toFixed(2) + '%'
}

onMounted(() => {
  initDefaults()
  runQuery()
})
</script>
