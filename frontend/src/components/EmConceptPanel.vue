<template>
  <!--
    东财概念榜面板（2026-09-27 v4.11.58）
    —— 由原 views/ConceptView.vue 的「左栏精选板块」改造而成（工单 三.4 方案 A）。
    —— 原页已并入 /market 作为第二个数据源，/concept 路由重定向到 /market?src=em，
       所以这里只保留列表本身，点行 emit('select', board) 交给 MarketView 的共用弹层。
    —— 数据源 = /api/kpl/em-concept-rank（后端猫爪失败时会自动降级东财概念榜，
       response.source 字段标明实际来源）；成分股接口 = emBoardMembers，同样在父级弹层里调。
  -->
  <div class="ecp">
    <div class="ecp-head">
      <span class="ecp-title"><i class="fa fa-fire"></i> 东财概念榜</span>
      <span class="ecp-sub">点击任意行展开成分股</span>
      <span class="ecp-count">{{ conceptList.length }} 个</span>
    </div>

    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载概念板块...</div></div>
    <div v-else-if="!conceptList.length" class="empty-state">暂无板块数据（非交易时段接口可能暂不可用）</div>
    <div v-else class="ecp-table-scroll">
      <table class="stock-table ecp-table">
        <thead>
          <tr>
            <th>#</th>
            <th class="sortable" :class="{ active: sortKey === 'name' }" @click="toggleSort('name')">板块<span class="sort-ind">{{ sortInd('name') }}</span></th>
            <th class="sortable" :class="{ active: sortKey === 'change' }" @click="toggleSort('change')">涨幅%<span class="sort-ind">{{ sortInd('change') }}</span></th>
            <th class="sortable" :class="{ active: sortKey === 'speed' }" @click="toggleSort('speed')">涨速%<span class="sort-ind">{{ sortInd('speed') }}</span></th>
            <th class="sortable" :class="{ active: sortKey === 'mainNet' }" @click="toggleSort('mainNet')">主力净额(亿)<span class="sort-ind">{{ sortInd('mainNet') }}</span></th>
            <th class="sortable" :class="{ active: sortKey === 'strength' }" @click="toggleSort('strength')">强度<span class="sort-ind">{{ sortInd('strength') }}</span></th>
            <th class="sortable" :class="{ active: sortKey === 'volRatio' }" @click="toggleSort('volRatio')">量比<span class="sort-ind">{{ sortInd('volRatio') }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(b, idx) in sortedConcepts" :key="b.boardCode" class="ecp-row" @click="$emit('select', b)">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="name-col">
              <div class="name-main">
                {{ b.name }}
                <span v-if="b.aucGroup" class="auc-badge" :class="aucBadgeClass(b.aucGroup)">{{ aucBadgeText(b.aucGroup) }}</span>
              </div>
              <div class="ecp-subinfo">
                <span>{{ b.boardCode }}</span>
                <span v-if="b.aucGroup" class="auc-net" :class="b.aucNet > 0 ? 'up' : b.aucNet < 0 ? 'down' : 'dim'">竞价 {{ yi(b.aucNet) }}</span>
                <span v-if="b.leaderName" class="leader" :class="b.leaderChange > 0 ? 'up' : b.leaderChange < 0 ? 'down' : 'dim'">领涨 {{ b.leaderName }} {{ signed(b.leaderChange) }}%</span>
              </div>
            </td>
            <td :class="b.change > 0 ? 'up' : b.change < 0 ? 'down' : 'dim'">{{ signed(b.change) }}%</td>
            <td :class="b.speed > 0 ? 'up' : b.speed < 0 ? 'down' : 'dim'">{{ signed(b.speed) }}%</td>
            <td :class="b.mainNet > 0 ? 'up' : b.mainNet < 0 ? 'down' : 'dim'">{{ b.mainNet ? yi(b.mainNet) : '—' }}</td>
            <td>{{ b.strength ? Math.round(b.strength) : '—' }}</td>
            <td>{{ b.volRatio ? b.volRatio.toFixed(2) : '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { usePolling } from '../composables/usePolling'
import { emConceptRank } from '../api/kpl'
import { yi, signed } from '../utils/format'

defineEmits(['select'])

const loading = ref(true)
const conceptList = ref([])

// 排序: 默认按涨速降序(异动榜语义), 点击列头切换
const sortKey = ref('speed')
const sortDir = ref(-1)  // -1 降序 / 1 升序

function toggleSort(k) {
  if (sortKey.value === k) {
    sortDir.value = -sortDir.value
  } else {
    sortKey.value = k
    sortDir.value = (k === 'name') ? 1 : -1   // 名称默认升序, 数值默认降序
  }
}
function sortInd(k) {
  if (sortKey.value !== k) return ''
  return sortDir.value === -1 ? '↓' : '↑'
}

// 竞价异动徽章: List1=今日新增(红) / List2=昨日延续(橙) / List3=其它(灰)
function aucBadgeText(g) {
  if (g === 'List1') return '竞价新增'
  if (g === 'List2') return '竞价延续'
  if (g === 'List3') return '竞价异动'
  return g
}
function aucBadgeClass(g) {
  if (g === 'List1') return 'auc-l1'
  if (g === 'List2') return 'auc-l2'
  return 'auc-l3'
}

const sortedConcepts = computed(() => {
  const arr = [...conceptList.value]
  const k = sortKey.value
  const d = sortDir.value
  arr.sort((a, b) => {
    if (k === 'name') return a.name.localeCompare(b.name) * d
    const av = a[k] ?? -Infinity
    const bv = b[k] ?? -Infinity
    return (av - bv) * d
  })
  return arr
})

async function loadConcepts() {
  try {
    const d = await emConceptRank()
    conceptList.value = d.list || []
  } catch (e) { /* 静默: 保留旧值 */ } finally {
    loading.value = false
  }
}

onMounted(() => {
  // 首次加载由 loadConcepts() 自己发起，轮询用 immediate:false 起步，
  // 避免「onMounted 拉一次 + usePolling 立刻又拉一次」的首屏双请求。
  loadConcepts()
  // 分钟级异动榜: 30s 刷新
  usePolling(loadConcepts, 30000, { immediate: false })
})
</script>

<style scoped>
.ecp { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px; }
.ecp-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.ecp-title { font-size: 0.9375rem; font-weight: 700; color: var(--text-main); }
.ecp-title .fa { color: var(--accent); }
.ecp-sub { font-size: 0.75rem; color: var(--text-muted); }
.ecp-count { margin-left: auto; font-size: 0.75rem; color: var(--text-muted); }

.ecp-table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.ecp-table { width: 100%; }
.ecp-table th, .ecp-table td { white-space: nowrap; }

.ecp-row { cursor: pointer; }
.ecp-row:hover td { background: rgba(255, 180, 0, 0.07); }
.ecp-subinfo { font-size: 0.7188rem; color: var(--text-muted); margin-top: 2px; }
.ecp-subinfo .leader { margin-left: 6px; font-weight: 600; }

/* 竞价异动徽章(2026-09-21 融合 theme_auc_kp 进精选板块榜) */
.auc-badge {
  display: inline-block; margin-left: 6px; padding: 0 6px;
  font-size: 0.6563rem; font-weight: 700; line-height: 1.5;
  border-radius: 4px; vertical-align: middle;
}
.auc-l1 { background: rgba(255, 76, 76, 0.14); color: var(--up, #ff5252); }
.auc-l2 { background: rgba(255, 160, 0, 0.14); color: #ff9f0a; }
.auc-l3 { background: rgba(140, 150, 170, 0.16); color: var(--text-muted); }
.auc-net { margin-left: 6px; font-weight: 600; }

body[data-bg="light"] .ecp { background: rgba(255, 255, 255, 0.85); border-color: var(--border-soft); }
body[data-bg="light"] .ecp-row:hover td { background: rgba(199, 145, 0, 0.08); }
body[data-bg="light"] .auc-l1 { color: #b83010; }
body[data-bg="light"] .auc-l2 { color: #8a5500; }

@media (max-width: 768px) {
  .ecp { padding: 10px 8px; }
  .ecp-table { min-width: 720px; }
  .ecp-table th { padding: 7px 4px; font-size: 0.75rem; }
  .ecp-table td { padding: 6px 4px; font-size: 0.75rem; }
}
</style>
