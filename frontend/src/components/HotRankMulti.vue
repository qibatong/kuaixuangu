<template>
  <div class="hrm">
    <div class="hrm-title"><i class="fa fa-fire"></i> 热门个股<span class="hrm-sub">点表头按该源排序 · 点行挂行情</span></div>
    <div v-if="loading" class="hrm-empty">加载中…</div>
    <div v-else-if="!rows.length" class="hrm-empty">暂无数据</div>
    <table v-else class="hrm-table">
      <thead>
        <tr>
          <th class="c-rk">#</th>
          <th>名称</th>
          <th class="c-src" :class="{on: sortBy==='kpl'}" @click="sortBy='kpl'">开盘啦</th>
          <th class="c-src" :class="{on: sortBy==='em'}" @click="sortBy='em'">东财</th>
          <th class="c-src" :class="{on: sortBy==='ths'}" @click="sortBy='ths'">同花顺</th>
          <th class="c-chg">涨幅</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(r,i) in rows" :key="r.code" @click="linkToSoftware(r.code, r.name)">
          <td class="c-rk">{{ i+1 }}</td>
          <td class="c-name">{{ r.name }}</td>
          <td class="c-src">{{ r.kpl || '·' }}</td>
          <td class="c-src">{{ r.em || '·' }}</td>
          <td class="c-src">{{ r.ths || '·' }}</td>
          <td class="c-chg" :class="(r.change||0)>=0?'up':'down'">{{ signed(r.change) }}%</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { kplHotRank } from '../api/kpl'
import { signed } from '../utils/format'
import { linkToSoftware } from '../utils/tdx'

const loading = ref(true)
const all = ref([])
const sortBy = ref('score')

const rows = computed(() => {
  const arr = [...all.value]
  if (sortBy.value === 'kpl') arr.sort((a,b) => (a.kpl||99) - (b.kpl||99))
  else if (sortBy.value === 'em') arr.sort((a,b) => (a.em||99) - (b.em||99))
  else if (sortBy.value === 'ths') arr.sort((a,b) => (a.ths||99) - (b.ths||99))
  else arr.sort((a,b) => a.score - b.score)
  return arr.slice(0, 15)
})

async function load() {
  try {
    const [a, b, c] = await Promise.all([
      kplHotRank('kpl'), kplHotRank('em'), kplHotRank('ths'),
    ])
    const map = {}
    const put = (lst, key) => {
      (lst || []).forEach((h, i) => {
        const code = h.code
        if (!map[code]) map[code] = { code, name: h.name, change: h.change, kpl: 0, em: 0, ths: 0 }
        map[code][key] = i + 1
        if (h.change != null) map[code].change = h.change
      })
    }
    put(a.list, 'kpl'); put(b.list, 'em'); put(c.list, 'ths')
    all.value = Object.values(map).map(r => ({ ...r, score: (r.kpl||99)+(r.em||99)+(r.ths||99) }))
  } finally { loading.value = false }
}
onMounted(load)
</script>

<style scoped>
.hrm { background: var(--card, #111826); border: 1px solid var(--border-soft, #1f2937); border-radius: var(--r-lg); padding: var(--s3); }
.hrm-title { font-size: var(--fs-base); font-weight: 700; color: var(--text-main, #f3f4f6); margin-bottom: var(--s2); display: flex; align-items: center; }
.hrm-title i { color: var(--accent, var(--up)); margin-right: var(--s2); }
.hrm-sub { margin-left: auto; font-size: var(--fs-xs); color: var(--text-muted, var(--text-faint)); font-weight: 400; }
.hrm-empty { padding: var(--s5) 0; text-align: center; color: var(--text-muted, var(--text-faint)); font-size: var(--fs-xs); }
.hrm-table { width: 100%; border-collapse: collapse; }
.hrm-table th, .hrm-table td { padding: var(--s1) var(--s1); font-size: var(--fs-xs); text-align: left; }
.hrm-table th { color: var(--text-muted, var(--text-faint)); font-weight: 400; border-bottom: 1px solid var(--border-soft, #1f2937); }
.c-src { cursor: pointer; text-align: center; }
.c-src.on { color: var(--accent, var(--up)); font-weight: 700; }
.hrm-table tbody tr { cursor: pointer; }
.hrm-table tbody tr:hover { background: rgba(255,255,255,.04); }
.c-rk { color: var(--text-muted, var(--text-faint)); width: 16px; font-family: inherit; }
.c-name { color: var(--text-main, #f3f4f6); }
.c-src { color: var(--text-muted, var(--text-faint)); font-family: inherit; text-align: center; }
.c-chg { text-align: right; font-weight: 600; }
.up { color: var(--accent); }
.down { color: var(--down); }
</style>
