<template>
  <!--
    竞价精选(ZH 选股) —— 2026-09-29 新增, 应主人要求嵌入首页左栏「竞价一进二」右侧。
    口径: 涨停基因(近120日≥1次, 半分位向下判据) × 高开≥3% × 竞价放量占昨量(量/量) 5~10%, 非ST。
    数据: /api/stats/zh-picks (后端 9:25 定格后生成; 铁律: 交易日当日无快照返回空, 不回退昨日)。
    门禁: 与竞价一进二同强度(前端 VipGate 预判省一次请求)。
  -->
  <div class="page-shell" :class="{ 'yj-embedded': embedded }">
    <h1 class="visually-hidden">竞价精选</h1>

    <VipGate v-if="!user.isVipOrPaid" title="竞价精选" :required-level="1" />

    <template v-else>
      <div class="zh-panel">
        <div class="zh-head">
          <div class="zh-title">
            <i class="fa fa-star zh-icon"></i>
            竞价精选
            <span v-if="date" class="zh-chip">数据日 {{ date }}</span>
            <span v-if="list.length" class="zh-chip zh-chip-accent">{{ list.length }} 只</span>
          </div>
          <button class="zh-refresh" :disabled="loading" title="刷新" @click="load(true)">
            <i class="fa fa-refresh" :class="{ spin: loading }"></i>
          </button>
        </div>

        <div v-if="err" class="zh-empty"><i class="fa fa-exclamation-circle"></i> {{ err }}</div>
        <div v-else-if="loading && !list.length" class="zh-empty">加载中...</div>
        <div v-else-if="!list.length" class="zh-empty">
          <i class="fa fa-filter"></i> 今日竞价精选暂无数据
          <div class="zh-empty-hint">9:25 定格后生成；入选 = 涨停基因(近120日≥1次) + 高开≥3% + 竞价放量占昨量 5~10%，非ST</div>
        </div>

        <table v-else class="stock-table zh-table">
          <thead>
            <tr>
              <th>名称</th><th>竞价涨幅</th><th>实时涨幅</th><th>实体涨幅</th><th>竞价金额</th><th>概念</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="it in list" :key="it.code">
              <!-- 名称单元格 hover 给出策略口径(占昨量/涨停基因) —— 主人要的列只保留 5 个 -->
              <td class="stock-info-cell" @click="linkToSoftware(it.code)"
                  :title="'占昨量 ' + it.volPct + '%（入选区间 5~10%） · 涨停基因 ' + it.ztGene + ' 次（近120日） · ' + (it.market || '')">
                <div class="stock-code-row"><span class="stock-code">{{ it.code }}</span></div>
                <div class="stock-name-row"><span class="stock-name">{{ it.name }}</span></div>
              </td>
              <td :class="cls(it.bidChange)">{{ signed(it.bidChange) }}%</td>
              <td :class="cls(it.realChange)">{{ signed(it.realChange) }}%</td>
              <td :class="cls(it.entityChange)">{{ signed(it.entityChange) }}%</td>
              <!-- 🔴 后端 bidAmt 单位是**万元**; amtText 吃**元** ⇒ ×1e4 -->
              <td :class="(it.bidAmt || 0) > 0 ? 'up' : 'dim'">{{ amtText((it.bidAmt || 0) * 1e4) }}</td>
              <td class="concept-cell dim" :title="it.board"><span v-if="it.board" class="concept-clamp">{{ it.board }}</span><span v-else>-</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import VipGate from './VipGate.vue'
import { zhPicks } from '../api/stats'
import { signed, amtText } from '../utils/format'
import { linkToSoftware } from '../utils/tdx'
import { useUserStore } from '../stores/user'

// 涨跌配色(缺失/非法 → dim; 与全站 chgCls 同口径)
function cls(v) {
  if (v === null || v === undefined || v === '' || isNaN(v)) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}

const props = defineProps({ embedded: { type: Boolean, default: false } })
const user = useUserStore()
const list = ref([])
const date = ref('')
const loading = ref(false)
const err = ref('')

async function load(manual = false) {
  if (!user.isVipOrPaid) return        // 前端预判, 省一次必然过不了门禁的请求
  loading.value = true
  try {
    const r = await zhPicks()
    list.value = (r && r.list) || []
    date.value = (r && r.date) || ''
    err.value = ''
  } catch (e) {
    err.value = '加载失败，请稍后重试'
    if (manual) list.value = []
  } finally {
    loading.value = false
  }
}
onMounted(() => { if (user.isVipOrPaid) load() })
</script>

<style scoped>
.zh-panel { padding: 10px 12px; }
.zh-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.zh-title { display: flex; align-items: center; gap: 8px; font-size: 1.05rem; font-weight: 600; }
.zh-icon { color: var(--accent-text, #e8c268); }
.zh-chip { font-size: 0.75rem; color: var(--text-muted, #9aa); border: 1px solid var(--border-soft, #333); border-radius: 10px; padding: 1px 8px; }
.zh-chip-accent { color: #7ce8a0; border-color: rgba(124, 232, 160, 0.4); }
.zh-refresh { background: none; border: 1px solid var(--border-soft, #333); color: var(--text-muted, #9aa); border-radius: 6px; padding: 3px 8px; cursor: pointer; margin-left: auto; }
.zh-refresh:disabled { opacity: 0.5; cursor: default; }
.zh-empty { padding: 22px 10px; text-align: center; color: var(--text-muted, #9aa); }
.zh-empty-hint { margin-top: 6px; font-size: 0.78rem; opacity: 0.8; }
.zh-table :deep(th), .zh-table :deep(td) { text-align: center; }
.fa-refresh.spin { animation: zh-spin 0.9s linear infinite; display: inline-block; }
@keyframes zh-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* 手机窄屏: 与 YijinerView 同范式 —— 表格横向滚动, 不藏列 */
@media (max-width: 430px) {
  .zh-table { display: block; overflow-x: auto; white-space: nowrap; }
}
</style>
