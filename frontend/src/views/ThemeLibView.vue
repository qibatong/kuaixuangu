<template>
  <!--
    题材库（2026-10-01 主人新增：首页宫格「题材库」格 → /theme）
    数据 = **开盘啦板块/题材榜**（`/api/kpl/board-rank`，开盘啦 RealRankingInfo，后端 60s 缓存），
    点任一题材**下钻成分股**（`/api/kpl/board-stocks`，含主力净额/涨停标识）。
    —— 两个接口都是全站既有能力（桌面「盘中」页也在用），**不新增任何上游调用/配额**。
    🔴 轮询：60s 且**仅盘中**（与后端 KPL_BOARD_TTL=60 同频；开盘啦是 8 万次/日付费配额）。
  -->
  <div class="page-shell tl-root">
    <h1 class="visually-hidden">题材库</h1>

    <header class="tl-head">
      <span class="tl-logo" aria-hidden="true">题材</span>
      <div class="tl-h1">题材库</div>
      <span class="tl-sub">开盘啦 · 点题材看成分股</span>
    </header>

    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载题材库...</div></div>
    <div v-else-if="failed" class="empty-state">题材库数据暂不可用（上游限流或非交易时段），稍后重试</div>
    <div v-else-if="!list.length" class="empty-state">暂无题材数据</div>

    <div v-else class="tl-list">
      <button
        v-for="(b, i) in list" :key="b.boardCode || i" class="tl-row"
        :data-tl="b.name" @click="openBoard(b)"
      >
        <span class="tl-rank" :class="'tl-r' + Math.min(i + 1, 5)">{{ i + 1 }}</span>
        <span class="tl-main">
          <span class="tl-line1">
            <span class="tl-name">{{ b.name }}</span>
            <span class="tl-chg">{{ signed(b.change) }}%</span>
          </span>
          <span class="tl-line2">
            <span class="tl-bar"><i :style="{ width: strengthW(b.strength) }"></i></span>
            <span class="tl-meta">主力<b :class="b.mainNet >= 0 ? 'n-red' : 'n-blue'">{{ yi(b.mainNet) }}</b>亿 · 成交 {{ yi(b.amount) }}亿<template v-if="b.volRatio"> · 量比 {{ Number(b.volRatio).toFixed(2) }}</template></span>
          </span>
        </span>
        <span class="tl-ar">›</span>
      </button>
    </div>

    <!-- 成分股弹层 -->
    <div v-if="cur" class="tl-mask" @click.self="closeBoard">
      <div class="tl-modal">
        <div class="tl-modal-h">
          <span class="tl-name">{{ cur.name }}</span>
          <span class="tl-chg">{{ signed(cur.change) }}%</span>
          <span class="tl-cnt">成分股 {{ stocks.length }}</span>
          <button class="tl-x" @click="closeBoard"><i class="fa fa-times"></i></button>
        </div>
        <div v-if="stocksLoading" class="loading-placeholder"><div class="spinner"></div><div>加载成分股...</div></div>
        <div v-else-if="!stocks.length" class="empty-state">该题材暂无成分股数据</div>
        <table v-else class="stock-table tl-table">
          <thead>
            <tr><th>名称</th><th>最新</th><th>涨幅%</th><th>主力净额(亿)</th><th>换手%</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in stocks" :key="s.code">
              <td class="tl-td-name">{{ s.name }}<span class="tl-code">{{ s.code }}</span></td>
              <td>{{ s.price ? Number(s.price).toFixed(2) : '-' }}</td>
              <td :class="s.change > 0 ? 'n-red' : (s.change < 0 ? 'n-blue' : '')">{{ signed(s.change) }}</td>
              <td :class="s.mainNet >= 0 ? 'n-red' : 'n-blue'">{{ yi(s.mainNet) }}</td>
              <td>{{ s.turnover ? Number(s.turnover).toFixed(2) : '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { kplBoardRank, kplBoardStocks } from '../api/kpl'
import { usePolling } from '../composables/usePolling'
import { isIntradayNow } from '../utils/time'

const loading = ref(true)
const failed = ref(false)
const list = ref([])
const cur = ref(null)
const stocks = ref([])
const stocksLoading = ref(false)

function yi(v) { return (v === null || v === undefined) ? '-' : (Number(v) / 1e8).toFixed(2) }
function signed(v) {
  const n = Number(v || 0)
  return (n >= 0 ? '+' : '') + n.toFixed(2)
}
/** 强度条：以榜首为满格（strength 口径由上游给，量纲未知 ⇒ 只用相对值） */
const maxStrength = computed(() => Math.max(1, ...list.value.map((b) => Math.abs(Number(b.strength) || 0))))
function strengthW(v) { return Math.max(6, Math.round((Math.abs(Number(v) || 0) / maxStrength.value) * 100)) + '%' }

async function load() {
  try {
    const d = await kplBoardRank()
    list.value = (d && d.list) || []
    failed.value = false
  } catch (e) { failed.value = true } finally { loading.value = false }
}

async function openBoard(b) {
  cur.value = b
  stocksLoading.value = true
  stocks.value = []
  try {
    const d = await kplBoardStocks(b.boardCode)
    stocks.value = (d && d.list) || []
  } catch (e) { stocks.value = [] } finally { stocksLoading.value = false }
}
function closeBoard() { cur.value = null; stocks.value = [] }

onMounted(load)
// 60s、仅盘中（与后端 KPL_BOARD_TTL=60 同频；收盘/周末不轮询）
usePolling(() => { if (isIntradayNow()) load() }, 60000, { immediate: false })
</script>

<style scoped>
.tl-root { max-width: 980px; margin: 0 auto; padding-bottom: calc(70px + env(safe-area-inset-bottom)); }

.tl-head { display: flex; align-items: center; gap: var(--s2); margin-bottom: var(--s2); }
/* 图标 = 「题材」文字（与首页宫格同款视觉） */
.tl-logo {
  width: 26px; height: 26px; border-radius: var(--r-md); flex: 0 0 auto;
  background: linear-gradient(145deg, var(--qg-cyan-a), var(--qg-cyan-b));
  display: flex; align-items: center; justify-content: center;
  color: var(--qg-on); font-size: var(--fs-xs); font-weight: 700;
}
.tl-h1 { font-size: var(--fs-lg); font-weight: 700; color: var(--text-main); }
.tl-sub { margin-left: auto; font-size: var(--fs-xs); color: var(--text-dim); }

.tl-list { display: flex; flex-direction: column; gap: var(--s2); }
.tl-row {
  display: flex; align-items: center; gap: var(--s2); width: 100%; text-align: left;
  background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: var(--r-lg);
  padding: var(--s2) var(--s2); cursor: pointer;
}
.tl-row:active { background: var(--bg-hover); }
.tl-rank {
  flex: 0 0 auto; width: 18px; height: 18px; border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 700;
  display: flex; align-items: center; justify-content: center; color: var(--qg-on); background: var(--text-dim);
}
.tl-r1 { background: var(--qg-red-a); }
.tl-r2 { background: var(--qg-orange-a); }
.tl-r3 { background: var(--qg-gold-b); }
.tl-r4, .tl-r5 { background: var(--qg-purple-a); }
.tl-main { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: var(--s1); }
.tl-line1 { display: flex; align-items: baseline; gap: var(--s2); }
.tl-name { font-size: var(--fs-sm); font-weight: 600; color: var(--text-main); }
.tl-chg { font-size: var(--fs-xs); color: var(--qg-red-a); font-weight: 700; }
.tl-line2 { display: flex; align-items: center; gap: var(--s2); }
.tl-bar { flex: 0 0 62px; height: 6px; border-radius: var(--r-sm); background: var(--bg-input); overflow: hidden; }
.tl-bar i { display: block; height: 100%; border-radius: var(--r-sm); background: linear-gradient(90deg, var(--qg-orange-a), var(--qg-red-a)); }
.tl-meta { font-size: var(--fs-xs); color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-meta b { font-weight: 700; }
.n-red { color: var(--qg-red-a); }
.n-blue { color: var(--qg-blue-a); }
.tl-ar { flex: 0 0 auto; color: var(--text-dim); }

.tl-mask { position: fixed; inset: 0; z-index: 1200; background: rgba(0, 0, 0, 0.6); display: flex; align-items: center; justify-content: center; padding: var(--s4); }
.tl-modal {
  width: 100%; max-width: 620px; max-height: 78vh; overflow: auto;
  background: var(--bg-panel-solid); border: 1px solid var(--border-soft);
  border-top: 3px solid var(--qg-cyan-a); border-radius: var(--r-lg); padding: var(--s3);
}
.tl-modal-h { display: flex; align-items: baseline; gap: var(--s2); margin-bottom: var(--s2); }
.tl-cnt { font-size: var(--fs-xs); color: var(--text-muted); }
.tl-x { margin-left: auto; background: transparent; border: none; color: var(--text-muted); cursor: pointer; }
.tl-table { width: 100%; }
.tl-td-name { display: flex; align-items: baseline; gap: var(--s1); }
.tl-code { font-size: var(--fs-xs); color: var(--text-dim); }
</style>
