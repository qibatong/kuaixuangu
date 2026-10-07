<template>
  <!--
    ② 昨日涨停今日表现（2026-09-27 v4.11.62 · 工单 批次二 层②，决策核心）
    —— 蓝色卡片，一行四指标：平均高开 / 现溢价 / 连板高度 / 炸板率。
    —— 右上角给情绪判断（修复期 · 可出手 / 退潮期 · 管住手 / 分歧期 · 看量能）。
    —— 数据由父级统一取：平均高开/现溢价 = 昨日涨停股今日竞价涨幅与实时涨幅的均值
       （后端 /api/kpl/yest-zt 一次给全，前端只做聚合）；连板高度/炸板率取情绪接口。
    —— ⚠️ 情绪标签的「绿=好 / 红=坏」是**情绪语义色**，与 A 股涨红跌绿是两套色系：
       SentimentPanel 的「亏钱效应」本来就用了绿色（主人指定），此处保持一致。
    —— 🔴 数据取不到时**逐项显示 --**，绝不把 null 当 0 渲染（否则「平均高开 0.00%」会被误读成真实平开）。
  -->
  <div class="yz">
    <div class="yz-head">
      <span class="yz-title"><i class="fa fa-bolt"></i> 昨日涨停今日表现</span>
      <span v-if="date" class="yz-date">{{ date }}</span>
      <span class="yz-mood" :class="'mood-' + mood.tone">{{ mood.label }}</span>
    </div>

    <div class="yz-grid">
      <div class="yz-cell">
        <span class="yz-k">平均高开</span>
        <span class="yz-v" :class="cls(avgOpen)">{{ pctOrDash(avgOpen) }}</span>
      </div>
      <div class="yz-cell">
        <span class="yz-k">现溢价</span>
        <span class="yz-v" :class="cls(avgNow)">{{ pctOrDash(avgNow) }}</span>
      </div>
      <div class="yz-cell">
        <span class="yz-k">连板高度</span>
        <span class="yz-v lb">{{ maxLadder === null || maxLadder === undefined ? '--' : maxLadder + ' 板' }}</span>
      </div>
      <div class="yz-cell">
        <span class="yz-k">炸板率</span>
        <span class="yz-v bk">{{ brokenRate === null || brokenRate === undefined ? '--' : brokenRate.toFixed(1) + '%' }}</span>
      </div>
    </div>

    <div class="yz-foot">
      <span v-if="count" class="yz-count">样本 {{ count }} 只（上一交易日涨停）</span>
      <span v-if="!count && !loading" class="yz-count dim">暂无可统计样本</span>
      <span v-if="loading && !count" class="yz-count dim">统计中…</span>
      <router-link v-if="count" class="yz-more" to="/ladder">连板天梯 <i class="fa fa-angle-right"></i></router-link>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  count: { type: Number, default: 0 },
  avgOpen: { type: Number, default: null },   // 平均高开(%)
  avgNow: { type: Number, default: null },    // 现溢价(%)
  maxLadder: { type: Number, default: null }, // 连板高度(板)
  brokenRate: { type: Number, default: null },// 炸板率(%)
  date: { type: String, default: '' },
  loading: { type: Boolean, default: false },
})

/**
 * 情绪判断（纯展示口径，规则写在注释里便于对齐）：
 *   现溢价 ≥ 2% 且 不低于平均高开  → 修复期 · 可出手（高开后还能走强）
 *   现溢价 < 0                     → 退潮期 · 管住手（高开被砸穿，承接差）
 *   其余                            → 分歧期 · 看量能
 * 样本不足（无 avgNow）时不硬判，返回「待统计」。
 */
const mood = computed(() => {
  const { avgOpen: o, avgNow: n } = props
  if (n === null || n === undefined) return { label: '待统计', tone: 'flat' }
  if (n < 0) return { label: '退潮期 · 管住手', tone: 'bad' }
  if (n >= 2 && (o === null || o === undefined || n >= o)) return { label: '修复期 · 可出手', tone: 'good' }
  return { label: '分歧期 · 看量能', tone: 'warn' }
})

function cls(v) {
  if (v === null || v === undefined) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'flat'
}

function pctOrDash(v) {
  if (v === null || v === undefined) return '--'
  return (v > 0 ? '+' : '') + v.toFixed(2) + '%'
}
</script>

<style scoped>
.yz {
  border-radius: var(--r-lg);
  padding: var(--s2) var(--s3);
  background: rgba(64, 140, 255, 0.12);
  border: 1px solid rgba(64, 140, 255, 0.38);
}
.yz-head { display: flex; align-items: center; gap: var(--s2); margin-bottom: var(--s2); flex-wrap: wrap; }
.yz-title { color: #8fc0ff; font-size: var(--fs-sm); font-weight: 700; }
.yz-title .fa { color: #4d94ff; }
.yz-date { color: var(--text-muted); font-size: var(--fs-xs); font-variant-numeric: tabular-nums; }
/* 情绪标签：固定在右上角 */
.yz-mood {
  margin-left: auto;
  font-size: var(--fs-xs); font-weight: 700;
  padding: 2px var(--s2); border-radius: var(--r-lg); white-space: nowrap;
}
.mood-good { color: var(--down); background: rgba(0, 200, 100, 0.14); border: 1px solid rgba(0, 200, 100, 0.4); }
.mood-bad { color: var(--accent); background: rgba(255, 82, 82, 0.14); border: 1px solid rgba(255, 82, 82, 0.4); }
.mood-warn { color: var(--star); background: rgba(255, 180, 0, 0.14); border: 1px solid rgba(255, 180, 0, 0.4); }
.mood-flat { color: var(--text-muted); background: rgba(255, 255, 255, 0.06); border: 1px solid var(--border-soft); }

/* 四指标一行（手机端两行两列，宽度不够时不挤压） */
.yz-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--s2); }
.yz-cell { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.yz-k { color: var(--text-muted); font-size: var(--fs-xs); white-space: nowrap; }
.yz-v { font-size: var(--fs-lg); font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; }
.yz-v.up { color: var(--accent); }
.yz-v.down { color: var(--down); }
.yz-v.flat, .yz-v.dim { color: var(--text-muted); }
.yz-v.lb { color: var(--star); }
.yz-v.bk { color: var(--up); }

.yz-foot { display: flex; align-items: center; gap: var(--s2); margin-top: var(--s2); }
.yz-count { color: var(--text-secondary); font-size: var(--fs-xs); }
.yz-count.dim { color: var(--text-muted); }
.yz-more { margin-left: auto; color: #8fc0ff; font-size: var(--fs-xs); text-decoration: none; white-space: nowrap; }
.yz-more:hover { text-decoration: underline; }

@media (max-width: 480px) {
  .yz-grid { grid-template-columns: repeat(2, 1fr); row-gap: var(--s2); }
}

body[data-bg="light"] .yz { background: rgba(64, 140, 255, 0.1); border-color: rgba(40, 100, 200, 0.35); }
body[data-bg="light"] .yz-title { color: #1a5fb4; }
body[data-bg="light"] .yz-title .fa { color: #2a6fd4; }
body[data-bg="light"] .mood-good { color: var(--down); }
body[data-bg="light"] .mood-bad { color: #c62828; }
body[data-bg="light"] .mood-warn { color: #8a5500; }
body[data-bg="light"] .yz-v.up { color: #c62828; }
body[data-bg="light"] .yz-v.down { color: var(--down); }
body[data-bg="light"] .yz-v.lb { color: #8a5500; }
body[data-bg="light"] .yz-more { color: #1a5fb4; }
</style>
