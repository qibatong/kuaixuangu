<template>
  <div class="sentiment-panel">
    <div class="senti-title"><i class="fa fa-dashboard"></i> 市场情绪</div>

    <div v-if="loading" class="senti-loading">加载中...</div>

    <template v-else-if="s">
      <!-- 情绪值: 排序第一位(整体盘面冷热, 最关键) + 进度条 -->
      <div class="senti-item senti-strong">
        <span class="senti-label">情绪值</span>
        <div class="senti-bar">
          <div class="senti-bar-fill" :class="strongCls" :style="{ width: Math.min(100, s.strong) + '%' }"></div>
        </div>
        <span class="senti-val" :class="strongCls">{{ s.strong }}</span>
        <span class="senti-tag" :class="strongCls">{{ strongText }}</span>
      </div>
      <!-- 两市资金: 成交额(亿) + 较昨日同时刻缩量/放量 -->
      <div class="senti-item" v-if="brief.market" :title="'两市股票总数 ' + brief.market.stockCount + ' 只'">
        <span class="senti-label">两市资金</span>
        <span class="senti-val mkt-amt">{{ brief.market.amount.toFixed(0) }}亿</span>
        <!-- 2026-09-07: 差为 0 时原显示"放量 0 亿"(0 不小于 0) → 改判「持平」 -->
        <template v-if="diffAmt !== null">
          <span class="senti-tag-aux" :class="diffAmt < 0 ? 'mkt-shrink' : (diffAmt > 0 ? 'mkt-grow' : 'mkt-flat')">
            {{ diffAmt < 0 ? '缩量' : (diffAmt > 0 ? '放量' : '持平') }}{{ diffAmt !== 0 ? ' ' + Math.abs(diffAmt).toFixed(0) + '亿' : '' }}
          </span>
        </template>
      </div>
      <!-- 涨跌家数分布(今日 + 昨日同时刻对比) -->
      <div class="senti-item" v-if="brief.breadth" :title="briefBreadthTip">
        <span class="senti-label">涨跌家数</span>
        <span class="senti-val mkt-rise">{{ brief.breadth.rise }}</span>
        <span class="senti-label">/</span>
        <span class="senti-val mkt-fall">{{ brief.breadth.fall }}</span>
        <template v-if="brief.breadth.yesterday">
          <span class="senti-label aux">昨同时</span>
          <span class="senti-val dim aux-val">{{ brief.breadth.yesterday.rise }}/{{ brief.breadth.yesterday.fall }}</span>
        </template>
      </div>
      <!-- 涨停板 / 跌停板 -->
      <div class="senti-item">
        <span class="senti-label">涨停板</span>
        <span class="senti-val zt">{{ s.ztCount }}</span>
        <span class="senti-label tab">跌停板</span>
        <span class="senti-val dt">{{ s.dtCount ?? '-' }}</span>
      </div>
      <!-- 高度板(连板高度) -->
      <div class="senti-item">
        <span class="senti-label">高度板</span>
        <span class="senti-val lbg">{{ s.lbgd }}板</span>
      </div>
      <!-- 大幅回撤 -->
      <div class="senti-item">
        <span class="senti-label">大幅回撤</span>
        <span class="senti-val dim">{{ s.dfNum }}</span>
      </div>
    </template>

    <div v-else class="senti-loading dim">情绪数据暂不可用</div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { kplSentiment, kplMarketBrief } from '../api/kpl'

const s = ref(null)
const loading = ref(true)
const brief = ref({})    // {market:{stockCount,amount,date}, breadth:{rise,fall,...}, last:{...}}

// 两市成交额较昨日对比(亿): 优先用 last_same_time(昨日同一时点, 同时刻对比),
// 兜底 last(昨日全天, 15:30 收盘快照)
const diffAmt = computed(() => {
  const m = brief.value.market
  const y = brief.value.last_same_time || brief.value.last
  if (!m || !y || y.amount === undefined) return null
  return Math.round((m.amount - y.amount) * 100) / 100
})

const briefBreadthTip = computed(() => {
  const b = brief.value.breadth
  if (!b) return ''
  let tip = '今日涨跌家数: 涨 ' + b.rise + ' / 跌 ' + b.fall
  if (b.yesterday) tip += '\n昨日同时刻: ' + b.yesterday.rise + ' / ' + b.yesterday.fall
  return tip
})

const strongCls = computed(() => {
  if (!s.value) return ''
  const v = s.value.strong
  return v >= 75 ? 'hot' : v <= 25 ? 'cold' : 'normal'
})

const strongText = computed(() => {
  if (!s.value) return ''
  const v = s.value.strong
  return v >= 75 ? '偏高·防风险' : v <= 25 ? '冰点·看回暖' : '中性'
})

onMounted(async () => {
  try {
    const d = await kplSentiment()
    if (d && d.sentiment) s.value = d.sentiment
  } catch (e) {
    /* 情绪数据可选, 失败静默 */
  } finally {
    loading.value = false
  }
  // 市场概览: 两市成交额/股票数 + 涨跌家数(失败静默, 不阻塞主面板)
  try {
    const b = await kplMarketBrief()
    if (b) brief.value = b
  } catch (e) { /* 静默 */ }
})
</script>

<style scoped>
.sentiment-panel {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  padding: 8px 12px;
  margin: 10px 0;
}
.senti-title {
  color: #ffe0a0;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}
.senti-title .fa { color: #ffb400; margin-right: 4px; }
.senti-item {
  display: flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.senti-label {
  color: var(--text-muted);
  font-size: 11px;
}
.senti-label.aux { font-size: 10px; opacity: 0.7; }
.senti-val {
  font-size: 13px;
  font-weight: 700;
}
.senti-val.aux-val { font-size: 11px; font-weight: 500; }
.senti-val.zt { color: #ff6a6a; }
.senti-val.lbg { color: #ffb400; }
.mkt-amt { color: #ffd76a; }      /* 两市成交额: 金色 */
.mkt-shrink { color: #6ad66a; }   /* 缩量: 绿(缩=情绪降温) */
.mkt-flat   { color: #9a9a9a; }   /* 持平: 中性灰(不放量不缩量) */
.mkt-grow { color: #ff8a5a; }     /* 放量: 橙红 */
.mkt-rise { color: #ff6a6a; }     /* 涨家数: 红 */
.mkt-fall { color: #6ad66a; }     /* 跌家数: 绿 */
.senti-tag-aux {
  font-size: 11px;
  padding: 0 5px;
  border-radius: 3px;
  font-weight: 500;
}
.mkt-shrink { background: rgba(106, 214, 106, 0.12); }
.mkt-flat { background: rgba(154, 154, 154, 0.12); }
.mkt-grow { background: rgba(255, 138, 90, 0.12); }
.senti-val.dim { color: var(--text-secondary); }
.senti-val.hot { color: #ff6a6a; }
.senti-val.cold { color: #6ad66a; }
.senti-val.normal { color: #ffe0a0; }
.senti-bar {
  width: 60px;
  height: 6px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 3px;
  overflow: hidden;
}
.senti-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s;
}
.senti-bar-fill.hot { background: linear-gradient(90deg, #ffb400, #ff5028); }
.senti-bar-fill.cold { background: linear-gradient(90deg, #4aa84a, #6ad66a); }
.senti-bar-fill.normal { background: linear-gradient(90deg, #ffb400, #ffe0a0); }
.senti-tag {
  font-size: 11px;
  padding: 0 6px;
  border-radius: 4px;
  font-weight: 500;
}
/* 2026-08-17 主人反馈: 情绪标签(如"偏克?风限")有边框像按钮, 改为纯文字标签 */
.senti-tag.hot { color: #ff8a8a; }
.senti-tag.cold { color: #8ae08a; }
.senti-tag.normal { color: #ccc; }
.senti-loading { color: var(--text-muted); font-size: 13px; }
/* 跌停家数: 蓝色(主人约定避免绿色), 与涨跌家数行的 mkt-fall 区分 */
.senti-val.dt { color: var(--accent-text); font-weight: 700; }

.senti-label.tab { margin-left: 8px; }

/* 手机端紧凑(2026-08-18 主人反馈"空间比较大"): 缩 padding/字号/间距, 2行布局 */
@media (max-width: 600px) {
  .sentiment-panel { padding: 6px 8px; gap: 6px 10px; }
  .senti-title { font-size: 12px; }
  .senti-label { font-size: 10px; }
  .senti-val { font-size: 12px; }
  .senti-bar { width: 36px; }
  .senti-tag { font-size: 10px; padding: 0 4px; }
  .senti-tag-aux { font-size: 10px; padding: 0 4px; }
  .senti-label.tab { margin-left: 4px; }
}

/* 浅色主题覆盖 */
body[data-bg="light"] .senti-title {  color: #5a4a3a;  }
body[data-bg="light"] .senti-title .fa {  color: #c79100;  }
body[data-bg="light"] .senti-val.zt {  color: #b83010;  }
body[data-bg="light"] .senti-val.lbg {  color: #8a5500;  }
body[data-bg="light"] .mkt-amt {  color: #8a6a00;  }
body[data-bg="light"] .mkt-shrink {  color: #2a7a2a;  }
body[data-bg="light"] .mkt-grow {  color: #b83010;  }
body[data-bg="light"] .mkt-rise {  color: #b83010;  }
body[data-bg="light"] .mkt-fall {  color: #2a7a2a;  }
body[data-bg="light"] .senti-val.hot {  color: #b83010;  }
body[data-bg="light"] .senti-val.normal {  color: #5a4a3a;  }
body[data-bg="light"] .senti-bar-track {  background: rgba(0,0,0,0.06);  }
body[data-bg="light"] .senti-bar-fill.hot {  background: linear-gradient(90deg, #c79100, #b83010);  }
body[data-bg="light"] .senti-bar-fill.normal {  background: linear-gradient(90deg, #c79100, #8a5500);  }
body[data-bg="light"] .mkt-shrink { background: rgba(42, 122, 42, 0.1); }
body[data-bg="light"] .mkt-grow { background: rgba(184, 48, 16, 0.1); }
</style>
