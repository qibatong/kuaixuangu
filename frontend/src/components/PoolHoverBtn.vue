<template>
  <button class="pool-add-btn pool-hover-btn" :class="{ added: inPool }" @click.stop="add">
    {{ inPool ? '已＋' : '＋自选' }}
  </button>
</template>

<script setup>
import { computed } from 'vue'
import { usePoolStore } from '../stores/pool'
import { showToast } from '../utils/toast'

// 名称格右侧悬停浮现的「＋自选/已＋」小按钮(2026-09-05 主人需求: 去掉独立操作列,
// 改为把鼠标放到股票名称上浮现加入自选; 已在池中也只在悬停时显示「已＋」)。
// 显隐由 main.css 的 .pool-hover-wrap:hover .pool-hover-btn 控制。
const props = defineProps({
  item: { type: Object, required: true }
})

const pool = usePoolStore()
// 2026-09-20 性能优化: 用 codeSet(Set 索引) 替代 stockPool.some() —— 数千行时 O(1) vs O(n)
const inPool = computed(() => pool.codeSet.has(props.item.code))

function add() {
  const n = pool.addStocks([props.item])
  showToast(n ? `✅ ${props.item.code} ${props.item.name} 已加入股票池` : `${props.item.code} 已在池中`, n ? 'success' : 'info')
}
</script>