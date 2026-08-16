<template>
  <div class="sentiment-panel">
    <div class="senti-title"><i class="fa fa-dashboard"></i> 市场情绪</div>

    <div v-if="loading" class="senti-loading">加载中...</div>

    <template v-else-if="s">
      <!-- 两市概况: 成交额(亿) + 较上一交易日差异 + 股票总数 -->
      <div class="senti-item" v-if="brief.market" :title="'两市股票总数 ' + brief.market.stockCount + ' 只'">
        <span class="senti-label">两市</span>
        <span class="senti-val mkt-amt">{{ brief.market.amount.toFixed(0) }}亿</span>
        <template v-if="diffAmt !== null">
          <span class="senti-label" style="margin-left:4px;">较昨</span>
          <span class="senti-val" :class="diffAmt < 0 ? 'mkt-shrink' : 'mkt-grow'">
            {{ diffAmt < 0 ? '缩量' : '放量' }} {{ Math.abs(diffAmt).toFixed(0) }}亿
          </span>
        </template>
      </div>
      <!-- 涨跌家数分布(今日 + 昨日同时刻对比) -->
      <div class="senti-item" v-if="brief.breadth" :title="briefBreadthTip">
        <span class="senti-label">涨跌</span>
        <span class="senti-val mkt-rise">{{ brief.breadth.rise }}</span>
        <span class="senti-label">/</span>
        <span class="senti-val mkt-fall">{{ brief.breadth.fall }}</span>
        <template v-if="brief.breadth.yesterday">
          <span class="senti-label" style="margin-left:4px;">昨同时</span>
          <span class="senti-val dim" style="font-size:12px;">
            {{ brief.breadth.yesterday.rise }}/{{ brief.breadth.yesterday.fall }}
          </span>
        </template>
      </div>
      <div class="senti-item">
        <span class="senti-label">涨停家数</span>
        <span class="senti-val zt">{{ s.ztCount }}</span>
      </div>
      <div class="senti-item">
        <span class="senti-label">连板高度</span>
        <span class="senti-val lbg">{{ s.lbgd }}板</span>
      </div>
      <!-- 一字涨停: 与连板高度同维度(强势涨停), 放中间位置视觉连贯 -->
      <div class="senti-item yizi" :title="yiziTrend && yiziTrend.length ? ('近5日一字涨停趋势: ' + yiziTrend.map(d => d.date.slice(5) + ':' + d.yizi_count + '个').join('  ')) : ''">
        <span class="senti-label">一字涨停</span>
        <template v-if="yiziToday">
          <span class="senti-val yz">{{ yiziToday.yizi_count }}</span>
          <span class="senti-label">个</span>
          <span class="senti-label" style="margin-left:4px;">竞价</span>
          <span class="senti-val yz-amt">{{ yiziAmtText(yiziToday.bid_amt) }}</span>
        </template>
        <template v-else>
          <span class="senti-val dim">0</span>
        </template>
      </div>
      <div class="senti-item senti-strong">
        <span class="senti-label">情绪值</span>
        <div class="senti-bar">
          <div class="senti-bar-fill" :class="strongCls" :style="{ width: Math.min(100, s.strong) + '%' }"></div>
        </div>
        <span class="senti-val" :class="strongCls">{{ s.strong }}</span>
        <span class="senti-tag" :class="strongCls">{{ strongText }}</span>
      </div>
      <div class="senti-item">
        <span class="senti-label">大幅回撤</span>
        <span class="senti-val dim">{{ s.dfNum }}</span>
      </div>
      <div class="senti-item senti-day">{{ s.day }}</div>

      <!-- 昨日涨停今表现(策略验证: 验证"剔除昨日涨停"是否合理) -->
      <div class="yp-sep"></div>
      <div class="yp-block">
        <span class="senti-label">昨日涨停今表现</span>
        <span class="yp-item" :title="'昨日涨停股今日平均涨幅。正值=昨日涨停今天仍强(剔除可能错过), 负值=昨日涨停今天普遍回调(剔除合理)'">
          涨停 <b :class="ypCls(yp.zt?.change)">{{ signed(yp.zt?.change) }}%</b>
        </span>
        <span class="yp-item" :title="'昨日连板股今日平均涨幅'">
          连板 <b :class="ypCls(yp.lb?.change)">{{ signed(yp.lb?.change) }}%</b>
        </span>
        <span class="yp-item" :title="'昨日破板(炸板)股今日平均涨幅'">
          破板 <b :class="ypCls(yp.pb?.change)">{{ signed(yp.pb?.change) }}%</b>
        </span>
        <span v-if="yp.zt && yp.zt.date" class="yp-date">{{ yp.zt.date.slice(5) }}</span>
      </div>
    </template>

    <div v-else class="senti-loading dim">情绪数据暂不可用</div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { kplSentiment, kplYesterdayPerf, kplMarketBrief } from '../api/kpl'

// 一字涨停统计(由父组件 StockView 传入; 解耦后 SentimentPanel 不再自取)
const { yiziToday, yiziTrend } = defineProps({
  yiziToday: { type: Object, default: null },  // {yizi_count, bid_amt}
  yiziTrend: { type: Array, default: () => [] } // 近 5 日趋势
})

function yiziAmtText(amt) {
  if (amt === null || amt === undefined) return '-'
  return (amt / 10000).toFixed(1) + '亿'
}

const s = ref(null)
const loading = ref(true)
const yp = ref({})
const brief = ref({})    // {market:{stockCount,amount,date}, breadth:{rise,fall,...}, last:{...}}

// 两市成交额较上一交易日差异(亿): last.amount 为昨日收盘全天额
const diffAmt = computed(() => {
  const m = brief.value.market
  const last = brief.value.last
  if (!m || !last || last.amount === undefined) return null
  return Math.round((m.amount - last.amount) * 100) / 100
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

function ypCls(v) {
  if (v === undefined || v === null) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}

function signed(v) {
  if (v === undefined || v === null || isNaN(v)) return '-'
  return (v > 0 ? '+' : '') + Number(v).toFixed(2)
}

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
  // 昨日涨停今表现(策略验证)
  try {
    const p = await kplYesterdayPerf()
    if (p && p.perf) yp.value = p.perf
  } catch (e) { /* 静默 */ }
})
</script>

<style scoped>
.sentiment-panel {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  padding: 10px 16px;
  margin: 10px 0;
}
.senti-title {
  color: #ffe0a0;
  font-size: 14px;
  font-weight: 600;
}
.senti-title .fa { color: #ffb400; margin-right: 4px; }
.senti-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.senti-label {
  color: var(--text-muted);
  font-size: 12px;
}
.senti-val {
  font-size: 15px;
  font-weight: 700;
}
.senti-val.zt { color: #ff6a6a; }
.senti-val.lbg { color: #ffb400; }
.mkt-amt { color: #ffd76a; }      /* 两市成交额: 金色 */
.mkt-shrink { color: #6ad66a; }   /* 缩量: 绿(缩=情绪降温) */
.mkt-grow { color: #ff8a5a; }     /* 放量: 橙红 */
.mkt-rise { color: #ff6a6a; }     /* 涨家数: 红 */
.mkt-fall { color: #6ad66a; }     /* 跌家数: 绿 */
.senti-val.yz { color: #ff5028; }   /* 一字涨停数: 火焰红 */
.senti-val.yz-amt { color: #ffb400; } /* 一字竞价额: 橙金 */
.senti-val.dim { color: var(--text-secondary); }
.senti-val.hot { color: #ff6a6a; }
.senti-val.cold { color: #6ad66a; }
.senti-val.normal { color: #ffe0a0; }
.senti-bar {
  width: 90px;
  height: 8px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 4px;
  overflow: hidden;
}
.senti-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s;
}
.senti-bar-fill.hot { background: linear-gradient(90deg, #ffb400, #ff5028); }
.senti-bar-fill.cold { background: linear-gradient(90deg, #4aa84a, #6ad66a); }
.senti-bar-fill.normal { background: linear-gradient(90deg, #ffb400, #ffe0a0); }
.senti-tag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
}
.senti-tag.hot { color: #ff8a8a; border: 1px solid rgba(255, 80, 40, 0.5); }
.senti-tag.cold { color: #8ae08a; border: 1px solid rgba(106, 214, 106, 0.5); }
.senti-tag.normal { color: #ccc; border: 1px solid rgba(255, 255, 255, 0.2); }
.senti-day { color: #666; font-size: 11px; margin-left: auto; }
.senti-loading { color: var(--text-muted); font-size: 13px; }
.yp-sep { width: 1px; height: 26px; background: var(--border-soft); }
.yp-block { display: flex; align-items: center; gap: 12px; }
.yp-item { color: #aaa; font-size: 12px; }
.yp-item b.up { color: #ff6a6a; }
.yp-item b.down { color: #6ad66a; }
.yp-item b.dim { color: var(--text-muted); }
.yp-date { color: #666; font-size: 11px; }

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
body[data-bg="light"] .senti-val.yz {  color: #b83010;  }
body[data-bg="light"] .senti-val.yz-amt {  color: #8a5500;  }
body[data-bg="light"] .senti-val.hot {  color: #b83010;  }
body[data-bg="light"] .senti-val.normal {  color: #5a4a3a;  }
body[data-bg="light"] .senti-block-label {  color: #5a6b85;  }
body[data-bg="light"] .senti-block-title {  color: #5a4a3a;  }
body[data-bg="light"] .senti-bar-track {  background: rgba(0,0,0,0.06);  }
body[data-bg="light"] .senti-bar-fill.hot {  background: linear-gradient(90deg, #c79100, #b83010);  }
body[data-bg="light"] .senti-bar-fill.normal {  background: linear-gradient(90deg, #c79100, #8a5500);  }
body[data-bg="light"] .yp-block {  color: #5a4a3a;  }
body[data-bg="light"] .yp-date {  color: #5a6b85;  }
body[data-bg="light"] .yp-item b {  color: #1a1d26;  }
</style>
