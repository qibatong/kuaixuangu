<template>
  <div class="srp">
    <div class="srp-head">
      <span class="srp-title"><i class="fa fa-history"></i> 板块轮动历史</span>
      <span class="srp-sub">近 {{ days }} 日 · 点击板块看成分股</span>
    </div>
    <div v-if="loading && !rot.dates.length" class="srp-loading"><div class="spinner"></div><div>加载板块轮动...</div></div>
    <div v-else-if="!rot.dates.length" class="srp-empty">暂无板块轮动数据</div>
    <template v-else>
      <RotCharts
:dates="historyDates" :rot-map="rotMap" :windows="rot.windows" :common-names="rot.common_names"
                 @pick="onPick"
/>
      <div v-if="picked" class="srp-stocks">
        <div class="srp-stocks-head">
          <i class="fa fa-th-large"></i> {{ picked.name }} 成分股 Top{{ stocks.length }}
          <button class="srp-stocks-close" @click="picked=null; stocks=[]"><i class="fa fa-times"></i></button>
        </div>
        <div v-if="stocksLoading" class="srp-loading"><div class="spinner"></div></div>
        <div v-else-if="!stocks.length" class="srp-empty">该板块暂无成分股</div>
        <table v-else class="srp-table">
          <thead><tr><th>代码</th><th>名称</th><th>涨幅%</th><th>换手%</th><th>成交额(亿)</th></tr></thead>
          <tbody>
            <tr v-for="s in stocks" :key="s.code" @click="linkToSoftware(s.code)">
              <td>{{ s.code }}</td><td>{{ s.name }}</td>
              <td :class="(s.change||0)>=0?'up':'down'">{{ s.change!=null? signed(s.change)+'%':'-' }}</td>
              <td>{{ s.turnover!=null? s.turnover.toFixed(1):'-' }}</td>
              <td>{{ s.amount!=null? yi(s.amount):'-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { sectorRotation, kplBoardStocks } from '../api/kpl'
import RotCharts from './RotCharts.vue'
import { signed, yi } from '../utils/format'
import { linkToSoftware } from '../utils/tdx'

const days = ref(10)
const loading = ref(true)
const rot = reactive({ dates: [], days: [], windows: [], common_names: [] })
const picked = ref(null)
const stocks = ref([])
const stocksLoading = ref(false)

const historyDates = computed(() => [...rot.dates].reverse())
const rotMap = computed(() => {
  const m = {}
  for (const day of rot.days) m[day.date] = day.boards || []
  return m
})

async function load() {
  loading.value = true
  try {
    const d = await sectorRotation(days.value, 'kpl')
    rot.dates = (d && d.dates) || []
    rot.days = (d && d.rotation && d.rotation.days) || []
    rot.windows = (d && d.windows && d.windows.windows) || []
    rot.common_names = (d && d.windows && d.windows.common_names) || []
  } catch (e) {} finally { loading.value = false }
}

async function onPick(board) {
  picked.value = board
  stocks.value = []
  stocksLoading.value = true
  try {
    const d = await kplBoardStocks(board.board_code || board.code)
    stocks.value = d.list || []
  } catch (e) { stocks.value = [] } finally { stocksLoading.value = false }
}

onMounted(load)
</script>

<style scoped>
.srp { background: var(--card, #111826); border: 1px solid var(--border-soft, #1f2937); border-radius: var(--r-lg); padding: var(--s4); margin-bottom: var(--s4); }
.srp-head { display: flex; align-items: center; margin-bottom: var(--s3); }
.srp-title { font-size: var(--fs-md); font-weight: 700; color: var(--text-main, #f3f4f6); }
.srp-title i { color: var(--accent, var(--up)); margin-right: var(--s2); }
.srp-sub { margin-left: auto; font-size: var(--fs-xs); color: var(--text-muted, var(--text-faint)); }
.srp-loading, .srp-empty { padding: var(--s6) 0; text-align: center; color: var(--text-muted, var(--text-faint)); font-size: var(--fs-xs); }
.srp-stocks { margin-top: var(--s3); border-top: 1px solid var(--border-soft, #1f2937); padding-top: var(--s3); }
.srp-stocks-head { display: flex; align-items: center; font-size: var(--fs-base); font-weight: 700; color: var(--text-main, #f3f4f6); margin-bottom: var(--s2); }
.srp-stocks-head i { color: var(--accent, var(--up)); margin-right: var(--s2); }
.srp-stocks-close { margin-left: auto; background: none; border: none; color: var(--text-muted, var(--text-faint)); cursor: pointer; font-size: var(--fs-base); }
.srp-table { width: 100%; border-collapse: collapse; }
.srp-table th, .srp-table td { padding: var(--s2) var(--s2); font-size: var(--fs-sm); text-align: left; border-top: 1px solid rgba(255,255,255,.04); }
.srp-table th { color: var(--text-muted, var(--text-faint)); font-weight: 500; }
.srp-table tr { cursor: pointer; }
.srp-table tr:hover { background: rgba(255,255,255,.04); }
.up { color: var(--accent); }
.down { color: var(--down); }
</style>
