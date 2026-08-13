<template>
  <div class="sentiment-panel">
    <div class="senti-title"><i class="fa fa-dashboard"></i> 市场情绪</div>

    <div v-if="loading" class="senti-loading">加载中...</div>

    <template v-else-if="s">
      <div class="senti-item">
        <span class="senti-label">涨停家数</span>
        <span class="senti-val zt">{{ s.ztCount }}</span>
      </div>
      <div class="senti-item">
        <span class="senti-label">连板高度</span>
        <span class="senti-val lbg">{{ s.lbgd }}板</span>
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

    <div v-else class="senti-loading dim">情绪数据暂不可用（开盘啦源未就绪）</div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { kplSentiment, kplYesterdayPerf } from '../api/kpl'

const s = ref(null)
const loading = ref(true)
const yp = ref({})

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
  color: #999;
  font-size: 12px;
}
.senti-val {
  font-size: 15px;
  font-weight: 700;
}
.senti-val.zt { color: #ff6a6a; }
.senti-val.lbg { color: #ffb400; }
.senti-val.dim { color: #bbb; }
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
.senti-loading { color: #888; font-size: 13px; }
.yp-sep { width: 1px; height: 26px; background: rgba(255,255,255,0.15); }
.yp-block { display: flex; align-items: center; gap: 12px; }
.yp-item { color: #aaa; font-size: 12px; }
.yp-item b.up { color: #ff6a6a; }
.yp-item b.down { color: #6ad66a; }
.yp-item b.dim { color: #999; }
.yp-date { color: #666; font-size: 11px; }
</style>
