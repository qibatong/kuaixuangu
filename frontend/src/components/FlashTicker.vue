<template>
  <!--
    ① 财经快讯滚动条（2026-09-27 v4.11.62 · 工单 批次二 层①）
    —— 黄色走马灯横滚，数据由父级 MarketView 统一拉取后传入（本组件纯展示，不自己请求）。
    —— 点单条 → 在条下方展开该条 summary + 原文链接（来源已在 nf-* 页有完整入口，这里只做速览）。
    —— 走马灯用**纯 CSS 动画**（内容渲染两份 + translateX(-50%) 无缝循环）：
       不用 rAF/JS 滚动 ⇒ SSR 安全、无定时器泄漏、无 onBeforeUnmount 依赖。
    —— hover 或已展开详情时暂停滚动（`:hover` + `.is-open`）。
    —— 尊重 prefers-reduced-motion：关闭时不做动画，退化为可手动横滑。
    —— ⚠️ 内容渲染两份会 DOM 翻倍：断言条数时用「显示条数」而非 DOM 计数（见 _verify 里的 uiItems）。
  -->
  <div class="ft" :class="{ 'is-open': !!openId }">
    <span class="ft-tag"><i class="fa fa-bolt"></i> 快讯</span>

    <div class="ft-track">
      <div v-if="!items.length" class="ft-empty">
        {{ loading ? '加载快讯中…' : (degraded.length ? '数据源暂缺：' + degraded.join('、') : '暂无快讯') }}
      </div>
      <div v-else class="ft-rail">
        <button
          v-for="(it, i) in loopItems"
          :key="it.id + '-' + i"
          type="button"
          class="ft-item"
          :class="{ active: it.id === openId }"
          @click="toggle(it)"
        >
          <span class="ft-time">{{ it.time_label || '--:--' }}</span>
          <span class="ft-text">{{ it.title }}</span>
        </button>
      </div>
    </div>

    <button v-if="openId" type="button" class="ft-close" aria-label="收起" @click="toggle(null)">
      <i class="fa fa-times"></i>
    </button>

    <!-- 展开详情：本条完整 summary + 原文外链 -->
    <div v-if="cur" class="ft-detail">
      <div class="ft-detail-head">
        <span class="ft-detail-time">{{ cur.date }} {{ cur.time_label }}</span>
        <span class="ft-detail-src">{{ cur.source }}</span>
      </div>
      <div class="ft-detail-body">{{ cur.summary && cur.summary !== cur.title ? cur.summary : cur.title }}</div>
      <a v-if="cur.url" class="ft-detail-link" :href="cur.url" target="_blank" rel="noopener noreferrer">
        查看原文 <i class="fa fa-external-link"></i>
      </a>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  degraded: { type: Array, default: () => [] },
})

const openId = ref('')
const cur = computed(() => props.items.find((x) => x.id === openId.value) || null)

// 走马灯要求内容渲染两份才能「无缝」：第一份滚完时正好接上第二份。
// 只保留前 12 条 —— 条数太多会让单圈时长过长、且 DOM 翻倍更重。
const loopItems = computed(() => {
  const base = props.items.slice(0, 12)
  return base.length ? [...base, ...base] : []
})

function toggle(it) {
  if (!it) { openId.value = ''; return }
  openId.value = openId.value === it.id ? '' : it.id
}
</script>

<style scoped>
.ft {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--s2);
  height: 32px;
  padding: 0 var(--s2);
  border-radius: var(--r-md);
  background: rgba(255, 180, 0, 0.12);
  border: 1px solid rgba(255, 180, 0, 0.35);
  overflow: hidden;
  transition: height 0.15s;
}
/* 展开时不再锁高(详情条要占位) */
.ft.is-open { height: auto; flex-wrap: wrap; padding: var(--s2) var(--s2); }
.ft-tag {
  flex: 0 0 auto;
  display: inline-flex; align-items: center; gap: var(--s1);
  color: var(--star); font-size: var(--fs-xs); font-weight: 700; white-space: nowrap;
}
.ft-tag .fa { color: var(--star); }
.ft-track { flex: 1 1 auto; min-width: 0; overflow: hidden; }
.ft-empty { color: var(--text-muted); font-size: var(--fs-xs); white-space: nowrap; }
/* 走马灯轨道：两份内容 + 每项自带右间距 ⇒ translateX(-50%) 精确无缝 */
.ft-rail {
  display: flex; align-items: center; flex-wrap: nowrap; width: max-content;
  animation: ft-scroll 60s linear infinite;
}
.ft:hover .ft-rail, .ft.is-open .ft-rail { animation-play-state: paused; }
@keyframes ft-scroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
@media (prefers-reduced-motion: reduce) {
  .ft-rail { animation: none; }
  .ft-track { overflow-x: auto; -webkit-overflow-scrolling: touch; }
}
.ft-item {
  flex: 0 0 auto;
  display: inline-flex; align-items: center; gap: var(--s2);
  margin-right: var(--s6);
  background: transparent; border: none; padding: 0;
  color: var(--text-main); font-size: var(--fs-xs); line-height: 1.6;
  cursor: pointer; white-space: nowrap;
}
.ft-item.active { color: var(--star); }
.ft-time { color: var(--star); font-variant-numeric: tabular-nums; font-weight: 600; }
.ft-text { max-width: none; }
.ft-close {
  flex: 0 0 auto; background: transparent; border: none; cursor: pointer;
  color: var(--text-muted); font-size: var(--fs-sm); padding: 0 2px;
}
.ft-close:hover { color: var(--text-main); }
.ft-detail {
  flex: 1 0 100%;
  margin-top: var(--s2); padding-top: var(--s2);
  border-top: 1px dashed rgba(255, 180, 0, 0.3);
}
.ft-detail-head { display: flex; align-items: center; gap: var(--s2); margin-bottom: var(--s1); }
.ft-detail-time { color: var(--star); font-size: var(--fs-xs); font-variant-numeric: tabular-nums; }
.ft-detail-src { color: var(--text-muted); font-size: var(--fs-xs); }
.ft-detail-body { color: var(--text-main); font-size: var(--fs-xs); line-height: 1.65; white-space: pre-wrap; }
.ft-detail-link { display: inline-block; margin-top: var(--s1); color: var(--accent); font-size: var(--fs-xs); text-decoration: none; }
.ft-detail-link:hover { text-decoration: underline; }

body[data-bg="light"] .ft { background: rgba(199, 145, 0, 0.12); border-color: rgba(199, 145, 0, 0.4); }
body[data-bg="light"] .ft-tag { color: #8a5500; }
body[data-bg="light"] .ft-tag .fa { color: #c79100; }
body[data-bg="light"] .ft-time { color: #a06a00; }
body[data-bg="light"] .ft-item.active { color: #8a5500; }
</style>
