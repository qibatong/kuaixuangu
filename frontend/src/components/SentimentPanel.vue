<template>
  <div class="sentiment-panel">
    <!-- 指数带(2026-09-20 新增): A股核心8指数, 涨红跌绿 -->
    <div class="index-strip">
      <div v-for="it in indices" :key="it.code" class="index-card">
        <span class="idx-name">{{ it.name }}</span>
        <span class="idx-px" :class="idxCls(it.pctChg)">{{ fmtIdxPx(it.px) }}</span>
        <!-- 5-3: 百分比与绝对值用 · 分隔 + 绝对值降对比度, 避免 "+0.97%+38.04" 粘连误读 -->
        <span class="idx-chg" :class="idxCls(it.pctChg)">{{ fmtIdxChg(it.pctChg) }}<template v-if="it.chg !== null"><span class="idx-chg-sep"> · </span><span class="idx-chg-pts">{{ fmtIdxPts(it.chg) }}</span></template></span>
      </div>
    </div>
    <div class="senti-vdivider"></div>

    <!-- 情绪卡(2026-09-20 主人指令: 数据源换猫爪 emoindic_daily 情绪周期) -->
    <template v-if="emo && emo.u5 !== undefined">
      <div class="emo-card">
        <span class="idx-name">市场量能</span>
        <span class="emo-val mkt-amt">{{ fmtAmt(emo.am) }}</span>
        <span class="idx-chg" v-if="emo.am_diff !== null && emo.am_diff !== undefined" :class="emo.am_diff < 0 ? 'mkt-shrink' : 'mkt-grow'">{{ emo.am_diff < 0 ? '缩量' : '放量' }} {{ fmtAmt(Math.abs(emo.am_diff)) }}</span>
        <span class="idx-chg idx-flat" v-else>-</span>
      </div>
      <div class="emo-card">
        <span class="idx-name">涨跌家数</span>
        <span class="emo-val"><span class="mkt-rise">{{ emo.s2 ?? '-' }}</span>/<span class="mkt-fall">{{ emo.s6 ?? '-' }}</span></span>
        <span class="idx-chg idx-flat">涨/跌</span>
      </div>
      <div class="emo-card">
        <span class="idx-name">涨停/跌停</span>
        <span class="emo-val"><span class="zt">{{ emo.u5 ?? '-' }}</span>/<span class="dt">{{ emo.d3 ?? '-' }}</span></span>
        <span class="idx-chg idx-flat">炸板 {{ emo.u12 ?? '-' }}·{{ emo.fp108 != null ? emo.fp108.toFixed(1) + '%' : '-' }}</span>
      </div>
      <div class="emo-card">
        <span class="idx-name">高度板</span>
        <span class="emo-val lbg">{{ emo.l17 ?? '-' }}板</span>
        <span class="idx-chg idx-flat">连板高度</span>
      </div>
      <div class="emo-card">
        <span class="idx-name">亏钱效应</span>
        <span class="emo-val loss">{{ emo.deep_retrace_count ?? '-' }}</span>
        <span class="idx-chg idx-flat">大幅回撤</span>
      </div>
    </template>
    <div v-else-if="loading" class="senti-loading">加载中...</div>
    <div v-else class="senti-loading dim">情绪数据暂不可用</div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { kplIndexBrief } from '../api/kpl'
import { isIntradayNow } from '../utils/time'

const indices = ref([])
const emo = ref(null)
const loading = ref(true)

// 指数涨跌配色(红涨绿跌)
function idxCls(v) {
  if (v === null || v === undefined) return ''
  return v > 0 ? 'idx-up' : v < 0 ? 'idx-down' : 'idx-flat'
}
function fmtIdxPx(v) {
  return (v === null || v === undefined) ? '--' : v.toFixed(2)
}
function fmtIdxChg(v) {
  if (v === null || v === undefined) return '--'
  return (v > 0 ? '+' : '') + v.toFixed(2) + '%'
}
function fmtIdxPts(v) {
  return (v > 0 ? '+' : '') + v.toFixed(2)
}
function fmtAmt(v) {
  return (v === null || v === undefined) ? '--' : (v / 1e8).toFixed(0) + '亿'
}

// 指数带 + 情绪周期(同一接口一次取全): 盘中每 30s 刷新
let idxTimer = null
async function loadIndices() {
  try {
    const d = await kplIndexBrief()
    if (d && Array.isArray(d.list)) indices.value = d.list
    if (d && d.emo) emo.value = d.emo
  } catch (e) { /* 失败静默 */ } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadIndices()
  idxTimer = setInterval(() => { if (isIntradayNow()) loadIndices() }, 30000)
})
onBeforeUnmount(() => { if (idxTimer) clearInterval(idxTimer) })
</script>

<style scoped>
.sentiment-panel {
  display: flex;
  align-items: stretch;
  gap: 10px;
  flex-wrap: wrap;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  padding: 8px 12px;
  margin: 10px 0;
}
.senti-title {
  color: #ffe0a0;
  font-size: 0.8125rem;
  font-weight: 600;
  white-space: nowrap;
  align-self: center;
}
.senti-title .fa { color: #ffb400; margin-right: 4px; }
/* 指数带: 紧凑靠左不拉伸(2026-09-20 主人反馈"情绪卡太靠右"), 空间不够时内部横滑 */
.index-strip {
  flex: 0 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.index-strip::-webkit-scrollbar { display: none; width: 0; height: 0; }
.index-card {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  min-width: 78px;
}
.idx-name { font-size: 0.75rem; font-weight: 600; color: var(--text-muted); white-space: nowrap; }
.idx-px { font-size: 0.9375rem; font-weight: 700; font-family: inherit; }
.idx-chg { font-size: 0.75rem; font-weight: 600; font-family: inherit; white-space: nowrap; font-variant-numeric: tabular-nums; }
.idx-chg .idx-chg-sep { color: var(--text-muted); font-weight: 400; }
.idx-chg .idx-chg-pts { color: var(--text-secondary); font-weight: 500; }
.idx-px { font-variant-numeric: tabular-nums; }
.idx-up { color: #ff5252; }
.idx-down { color: #00c864; }
.idx-flat { color: var(--text-muted); }
/* 情绪卡: 与指数卡同款竖排卡片 */
.emo-card {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  min-width: 78px;
}
.emo-val { font-size: 0.9375rem; font-weight: 700; white-space: nowrap; font-variant-numeric: tabular-nums; }
/* 亏钱效应: 绿色(负面指标, 主人指定) */
.emo-val.loss { color: #00c864; }
.senti-vdivider { width: 1px; align-self: stretch; background: rgba(255, 255, 255, 0.1); flex-shrink: 0; }
.senti-loading { color: var(--text-muted); font-size: 0.8125rem; align-self: center; }
.mkt-amt { color: #ffd76a; }      /* 成交额: 金色 */
.mkt-shrink { color: #6ad66a; }   /* 缩量: 绿 */
.mkt-grow { color: #ff8a5a; }     /* 放量: 橙红 */
.mkt-rise { color: #ff6a6a; }     /* 涨家数: 红 */
.mkt-fall { color: #6ad66a; }     /* 跌家数: 绿 */
.senti-val.zt, .emo-val .zt { color: #ff6a6a; }
.emo-val.lbg { color: #ffb400; }
.senti-val.dt, .emo-val .dt { color: var(--accent-text); }

/* 手机端紧凑: 指数带独占一行横滑, 情绪卡换行堆叠 */
@media (max-width: 576px) {
  .sentiment-panel { padding: 6px 8px; gap: 6px 10px; }
  .index-strip { flex-basis: 100%; order: 1; }
  .senti-vdivider { display: none; }
  .index-card { min-width: 74px; }
  .emo-card { min-width: 74px; }
  .idx-name { font-size: 0.75rem; }
  .idx-px { font-size: 0.8125rem; }
  .idx-chg { font-size: 0.75rem; }
  .emo-val { font-size: 0.8125rem; }
}

/* 浅色主题覆盖 */
body[data-bg="light"] .index-card { background: rgba(0, 0, 0, 0.04); }
body[data-bg="light"] .emo-card { background: rgba(0, 0, 0, 0.04); }
body[data-bg="light"] .senti-vdivider { background: rgba(0, 0, 0, 0.1); }
body[data-bg="light"] .idx-up { color: #c62828; }
body[data-bg="light"] .idx-down { color: #1a7a2a; }
body[data-bg="light"] .emo-val.loss { color: #1a7a2a; }
body[data-bg="light"] .mkt-amt { color: #8a6a00; }
body[data-bg="light"] .mkt-shrink { color: #2a7a2a; }
body[data-bg="light"] .mkt-grow { color: #b83010; }
body[data-bg="light"] .mkt-rise { color: #b83010; }
body[data-bg="light"] .mkt-fall { color: #2a7a2a; }
body[data-bg="light"] .senti-val.zt, body[data-bg="light"] .emo-val .zt { color: #b83010; }
body[data-bg="light"] .emo-val.lbg { color: #8a5500; }
</style>
