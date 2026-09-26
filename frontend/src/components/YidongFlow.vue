<template>
  <!--
    ⑥ 实时异动流（2026-09-27 v4.11.62 · 工单 批次二 层⑥）
    —— 复用 /api/kpl/yidong-realtime（开盘啦 doc90 偏离值异动）。
    —— 🔴 如实说明：该接口**不返回时间戳**（给的是 type/trigger/change/days/deviation/target），
       所以这里做成「按累计偏离值排序的异动流」，而不是假装成带时间的封板/炸板流水。
       真要做带时间的封板/炸板时间线，需要另接分时明细源 —— 属二期，不在本版编造。
    —— 已触发（triggered）的行高亮；每行可点开个股。
  -->
  <div class="yf">
    <div class="yf-head">
      <span class="yf-title"><i class="fa fa-bolt"></i> 实时异动流</span>
      <span class="yf-sub">按累计偏离值排序</span>
      <span v-if="day" class="yf-day">{{ day }}<template v-if="time"> · {{ time }}</template></span>
    </div>

    <div v-if="loading && !list.length" class="yf-empty">加载异动列表…</div>
    <div v-else-if="failed" class="yf-empty warn">
      <i class="fa fa-exclamation-triangle"></i> 异动数据源暂缺（会员功能），稍后自动重试
    </div>
    <div v-else-if="!list.length" class="yf-empty">当前无触发异动的个股</div>

    <div v-else class="yf-list">
      <div
        v-for="(it, i) in list" :key="it.code || i"
        class="yf-item" :class="{ on: it.triggered }"
        @click="go(it.code)"
      >
        <span class="yf-type">{{ it.type || '异动' }}</span>
        <span class="yf-name">{{ it.name }}</span>
        <span class="yf-code">{{ it.code }}</span>
        <span class="yf-trigger">{{ it.trigger || '—' }}</span>
        <span class="yf-dev" :class="dirCls(it.deviation)">偏离 {{ signed(it.deviation) }}%</span>
        <span class="yf-chg" :class="dirCls(it.change)">{{ signed(it.change) }}%</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { linkToSoftware } from '../utils/tdx'
import { signed } from '../utils/format'

const props = defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
  day: { type: String, default: '' },
  time: { type: String, default: '' },
})

// 模板里统一叫 list（与其它盯盘台面板口径一致）
const list = computed(() => props.items || [])

function dirCls(v) {
  if (v === null || v === undefined) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}
function go(code) { linkToSoftware(code) }
</script>

<style scoped>
.yf {
  border-radius: 10px; padding: 10px 12px;
  background: var(--bg-hover); border: 1px solid var(--border-soft);
}
.yf-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.yf-title { color: var(--text-main); font-size: 0.8125rem; font-weight: 700; }
.yf-title .fa { color: #ffb400; }
.yf-sub { color: var(--text-muted); font-size: 0.6875rem; }
.yf-day { margin-left: auto; color: var(--text-muted); font-size: 0.6875rem; }
.yf-empty { color: var(--text-muted); font-size: 0.75rem; padding: 8px 2px; }
.yf-empty.warn { color: #ffb400; }
.yf-empty.warn i { margin-right: 5px; }
.yf-list { max-height: 260px; overflow-y: auto; }
.yf-item {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 2px; border-bottom: 1px dashed var(--border-soft);
  font-size: 0.75rem; cursor: pointer;
}
.yf-item:last-child { border-bottom: none; }
.yf-item:hover { background: rgba(255, 180, 0, 0.06); }
.yf-item.on { background: rgba(255, 82, 82, 0.08); }
.yf-type {
  flex: 0 0 auto; color: #ffd76a; font-size: 0.6875rem;
  border: 1px solid rgba(255, 180, 0, 0.4); border-radius: 4px; padding: 0 5px; white-space: nowrap;
}
.yf-name { flex: 0 0 auto; color: var(--text-main); font-weight: 600; white-space: nowrap; }
.yf-code { flex: 0 0 auto; color: var(--text-muted); font-size: 0.6875rem; }
.yf-trigger { flex: 1 1 auto; min-width: 0; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.yf-dev, .yf-chg { flex: 0 0 auto; font-variant-numeric: tabular-nums; white-space: nowrap; }
.yf-dev { color: var(--text-muted); }
.up { color: #ff5252; }
.down { color: #00c864; }
.dim { color: var(--text-muted); }

body[data-bg="light"] .yf { background: rgba(255, 255, 255, 0.85); }
body[data-bg="light"] .yf-type { color: #8a5500; }
body[data-bg="light"] .up { color: #c62828; }
body[data-bg="light"] .down { color: #1a7a2a; }
</style>
