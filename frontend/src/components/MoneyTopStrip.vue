<template>
  <!--
    ③ 最强资金 TOP（2026-09-27 v4.11.62 · 工单 批次二 层③）
    —— 横滑卡片条，按主力净额降序取 TOP N：题材名 | 涨幅 | 净流入(亿)，红正绿负。
    —— 数据复用板块榜接口（/api/kpl/board-rank 本来就返回 mainNet），**不额外发请求**。
    —— 点某张卡 emit('select', board) ⇒ 父级把下方「题材榜」滚到该行并高亮
       （对应工单「二期体验 5」，这里顺手做掉，成本极低）。
    —— 🔴 mainNet 为 null 的板块**不参与排序**（不能用 0 顶替，否则「净额 0 亿」会混进 TOP）。
  -->
  <div class="mt">
    <div class="mt-head">
      <span class="mt-title"><i class="fa fa-money"></i> 最强资金 TOP{{ list.length ? ' · ' + list.length : '' }}</span>
      <span class="mt-sub">按主力净额排序</span>
    </div>

    <div v-if="loading && !list.length" class="mt-loading">加载中…</div>
    <div v-else-if="!list.length" class="mt-loading dim">暂无板块资金数据{{ failed ? '（数据源暂缺）' : '' }}</div>

    <div v-else class="mt-rail">
      <button
        v-for="b in list"
        :key="b.boardCode || b.name"
        type="button"
        class="mt-card"
        :class="{ active: b.boardCode === activeCode || (!b.boardCode && b.name === activeName) }"
        @click="$emit('select', b)"
      >
        <span class="mt-name" :title="b.name">{{ b.name }}</span>
        <span class="mt-net" :class="cls(b.mainNet)">{{ money(b.mainNet) }}</span>
        <span class="mt-chg" :class="cls(b.change)">{{ signedPct(b.change) }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  // 原始板块列表（父级传全量，本组件负责排序/截断）
  boards: { type: Array, default: () => [] },
  topN: { type: Number, default: 10 },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
  activeCode: { type: String, default: '' },
  activeName: { type: String, default: '' },
})

defineEmits(['select'])

const list = computed(() => {
  const arr = (props.boards || []).filter((b) => b && b.mainNet !== null && b.mainNet !== undefined)
  arr.sort((a, b) => (Number(b.mainNet) || 0) - (Number(a.mainNet) || 0))
  return arr.slice(0, props.topN)
})

function cls(v) {
  if (v === null || v === undefined) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'flat'
}

function money(v) {
  if (v === null || v === undefined) return '--'
  const yi = v / 1e8
  return (yi > 0 ? '+' : '') + yi.toFixed(2) + '亿'
}

function signedPct(v) {
  if (v === null || v === undefined) return '--'
  return (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%'
}
</script>

<style scoped>
.mt { margin-top: 0; }
.mt-head { display: flex; align-items: baseline; gap: var(--s2); margin-bottom: var(--s2); }
.mt-title { color: var(--star); font-size: var(--fs-sm); font-weight: 700; }
.mt-title .fa { color: var(--star); }
.mt-sub { color: var(--text-muted); font-size: var(--fs-xs); }
.mt-loading { color: var(--text-muted); font-size: var(--fs-xs); padding: var(--s2) 2px; }
.mt-loading.dim { color: var(--text-dim); }

/* 横滑条：每卡 96px，溢价卡不挤压 */
.mt-rail {
  display: flex; gap: var(--s2);
  overflow-x: auto; -webkit-overflow-scrolling: touch;
  scrollbar-width: none; padding-bottom: 2px;
}
.mt-rail::-webkit-scrollbar { display: none; height: 0; }
.mt-card {
  flex: 0 0 auto; width: 96px;
  display: flex; flex-direction: column; align-items: center; gap: var(--s1);
  padding: var(--s2) var(--s2);
  border-radius: var(--r-lg);
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.mt-card:hover { border-color: var(--accent); }
.mt-card.active { border-color: var(--star); background: rgba(255, 180, 0, 0.12); }
.mt-name {
  max-width: 100%; font-size: var(--fs-xs); color: var(--text-main);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mt-net { font-size: var(--fs-sm); font-weight: 700; font-variant-numeric: tabular-nums; }
.mt-chg { font-size: var(--fs-xs); font-variant-numeric: tabular-nums; }
.mt-net.up, .mt-chg.up { color: var(--accent); }
.mt-net.down, .mt-chg.down { color: var(--down); }
.mt-net.flat, .mt-chg.flat, .mt-net.dim, .mt-chg.dim { color: var(--text-muted); }

body[data-bg="light"] .mt-title { color: #8a5500; }
body[data-bg="light"] .mt-card { background: rgba(255, 255, 255, 0.85); }
body[data-bg="light"] .mt-net.up, body[data-bg="light"] .mt-chg.up { color: #c62828; }
body[data-bg="light"] .mt-net.down, body[data-bg="light"] .mt-chg.down { color: var(--down); }
</style>
