<template>
  <!--
    数据更新时刻（2026-09-27 v4.11.63《快选移动端追加清单》§二·4）
    —— **纯展示组件**：只吃 props，不持时钟、不发请求（便于 SSR 冒烟测试用夹具渲染）。
    —— 三态都必须说人话：
       ① 还没成功过 → 「等待首次更新…」（不能显示 00:00:00 冒充"更新过"）
       ② 成功过     → 「更新于 HH:MM:SS」
       ③ 自动刷新   → 附「· 每 30s 自动刷新」只在 interval>0 时出现
          （收盘 / 历史回看模式没有轮询，父组件传 0 即可让这句消失 —— 不许写死。）
  -->
  <span class="ds" :class="{ 'ds-ok': ok }" :title="title">
    <i class="fa fa-clock-o" aria-hidden="true"></i>
    <span v-if="ok" class="ds-at">更新于 {{ at }}</span>
    <span v-else class="ds-at">等待首次更新…</span>
    <span v-if="interval > 0" class="ds-hint">· 每 {{ interval }}s 自动刷新</span>
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  at: { type: String, default: '' },
  ok: { type: Boolean, default: false },
  // 自动刷新间隔(秒)。0 = 当前不自动刷新(收盘/历史回看) ⇒ 不显示该提示。
  interval: { type: Number, default: 0 },
  // 悬浮说明：讲清"这戳是数据时刻，不是当前时间"
  hint: { type: String, default: '本页数据的取回时刻（与页头时钟不同：时钟走，它只在成功取到新数据时才走）' },
})

const title = computed(() => {
  const head = props.ok ? `数据更新于 ${props.at}` : '本页尚未成功取到数据'
  return props.hint ? `${head}\n${props.hint}` : head
})
</script>

<style scoped>
.ds {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.75rem;
  color: var(--text-muted);
  white-space: nowrap;
  /* 数字等宽 —— 时间跳秒时宽度不变，不会推着旁边的元素左右动（清单 §二·2 同一纪律） */
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum";
}
.ds i { opacity: 0.7; }
.ds.ds-ok { color: var(--text-secondary); }
.ds-hint { opacity: 0.75; }
</style>
