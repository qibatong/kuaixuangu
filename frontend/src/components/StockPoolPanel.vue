<template>
  <div class="stock-pool-panel">
    <div class="pool-header">
      <div class="pool-title">
        <i class="fa fa-database"></i> 自选股票池
        <span class="auto-tag">{{ statusTag }}</span>
        <span v-if="expiryText" class="pool-expiry-info" v-html="expiryText"></span>
      </div>
      <div class="pool-buttons">
        <button class="pool-btn" @click="manualAdd"><i class="fa fa-plus-circle"></i> 加入当前前五</button>
        <button class="pool-btn" @click="manualAddAll"><i class="fa fa-plus"></i> 加入全部</button>
        <button class="pool-btn" @click="clearPool"><i class="fa fa-trash-o"></i> 清空股票池</button>
      </div>
    </div>
    <div class="pool-list">
      <div v-if="!pool.stockPool.length" class="empty-pool">暂无股票，9:30前系统自动将前五名选入池中</div>
      <div v-for="(item, idx) in pool.stockPool" :key="item.code" class="pool-item" :class="medalCls(idx)">
        <div class="pool-item-main">
          <span v-if="idx < 3" class="pool-medal">{{ ['🥇', '🥈', '🥉'][idx] }}</span>
          <span v-else class="pool-rank">{{ idx + 1 }}</span>
          <div class="pool-item-info">
            <div class="pool-stock-code code-click" :data-stock-code="item.code" :data-stock-name="item.name" @click="emit('open-chart', item.code, item.name)">{{ item.code }}</div>
            <div class="pool-stock-name">{{ item.name }}</div>
          </div>
        </div>
        <div class="pool-item-right">
          <div class="pool-snaps">
            <span v-if="hasSnapshot(item.bidChange)" class="pool-snap" :class="item.bidChange > 0 ? 'up' : 'down'">{{ item.bidChange > 0 ? '+' : '' }}{{ item.bidChange.toFixed(2) }}%</span>
            <span v-else class="pool-snap dim">-</span>
            <span v-if="hasSnapshot(item.probability)" class="pool-score">{{ item.probability }}分</span>
          </div>
          <div class="pool-add-time">{{ formatAddTime(item.addTime) }}</div>
          <button class="del-single" @click="pool.removeStock(item.code)">移除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { usePoolStore } from '../stores/pool'
import { useStocksStore } from '../stores/stocks'
import { showToast } from '../utils/toast'
import { linkToSoftware } from '../utils/tdx'
import { bjNow, pad2 } from '../utils/time'

const pool = usePoolStore()
const stocksStore = useStocksStore()

const emit = defineEmits(['open-chart'])

const LOCK_DURATION_MS = 10 * 60 * 60 * 1000

function hasSnapshot(v) { return v !== undefined && v !== null && !isNaN(v) }
function medalCls(idx) { return idx === 0 ? 'gold' : idx === 1 ? 'silver' : idx === 2 ? 'bronze' : '' }
// 兼容旧记录(HH:MM:SS)与新记录(HH:MM): 只保留 HH:MM, 给手机端紧凑
function formatAddTime(t) {
  if (!t) return ''
  const parts = String(t).split(':')
  return parts.length >= 2 ? parts[0] + ':' + parts[1] : t
}

const statusTag = computed(() => {
  if (pool.autoPoolLockTime && pool.stockPool.length) {
    return ` 已锁定于 ${new Date(pool.autoPoolLockTime).toLocaleTimeString('zh-CN', { hour12: false })} · 刷新不变`
  }
  const bj = bjNow()
  const h = bj.getHours(), m = bj.getMinutes()
  if (h < 9 || (h === 9 && m < 30)) {
    return `⏰ 9:30前自动收录中 (${pad2(h)}:${pad2(m)})`
  }
  return ` 已过9:30 停止自动选股`
})

const expiryText = computed(() => {
  if (!pool.autoPoolLockTime || !pool.stockPool.length) return ''
  const rem = pool.autoPoolLockTime + LOCK_DURATION_MS - Date.now()
  if (rem <= 0) return ''
  const h = Math.floor(rem / 3600000)
  const m = Math.floor((rem % 3600000) / 60000)
  return ` 锁定剩余 <strong>${h}小时${m}分</strong>`
})

// 当前模式的选股结果(竞价 cachedStocks / 盘中 spotStocks)
function currentList() {
  return stocksStore.mode === 'spot' ? stocksStore.spotStocks : stocksStore.cachedStocks
}

function manualAdd() {
  const list = currentList()
  if (!list.length) { showToast('当前模式无数据', 'error'); return }
  const n = pool.addStocks(list.slice(0, 5))
  showToast(n ? `✅ 已加入当前前五 (新增${n}只)` : '前五已在池中', n ? 'success' : 'info')
}

function manualAddAll() {
  const list = currentList()
  if (!list.length) { showToast('当前模式无数据', 'error'); return }
  const n = pool.addStocks(list)
  showToast(n ? `✅ 已加入全部 (新增${n}只)` : '全部已在池中', n ? 'success' : 'info')
}

function clearPool() {
  pool.clearAll()
  showToast('已清空股票池', 'info')
}
</script>
