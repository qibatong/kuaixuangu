<template>
  <!--
    竞价精选(ZH 选股) —— 2026-09-29 新增, 嵌入首页左栏「竞价一进二」右侧。
    口径: 涨停基因(近120日≥1次, 半分位向下判据) × 高开≥3% × 竞价放量占昨量(量/量) 5~10%, 非ST。
    数据: /api/stats/zh-picks?date=(空=今天; 传日期=**历史查看**, 后端按该交易日算, 见 api_stats_zh_picks)。
    门禁: 与竞价一进二同强度(VipGate 预判省一次必然过不了门禁的请求)。
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
            <span v-if="date" class="zh-chip">{{ pickDate ? '数据日' : '今日' }} {{ date }}</span>
            <span v-if="list.length" class="zh-chip zh-chip-accent">{{ list.length }} 只</span>
          </div>
          <div class="zh-tools">
            <input v-model="pickDate" class="zh-date" type="date" :max="maxDate"
                   title="查看历史：选一个交易日" @change="load(true)">
            <button v-if="pickDate" class="zh-btn" title="回到今天" @click="backToday">今天</button>
            <button class="zh-btn" :disabled="loading" title="刷新" @click="load(true)">
              <i class="fa fa-refresh" :class="{ spin: loading }"></i>
            </button>
          </div>
        </div>

        <div v-if="err" class="zh-empty"><i class="fa fa-exclamation-circle"></i> {{ err }}</div>
        <div v-else-if="loading && !list.length" class="zh-empty">加载中...</div>
        <div v-else-if="!list.length" class="zh-empty">
          <i class="fa fa-filter"></i> {{ pickDate ? (date + ' 无入选标的') : '今日竞价精选暂无数据' }}
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
              <!-- 名称格 hover 给出策略口径(占昨量/涨停基因) —— 主人要的列只保留 5 个 -->
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
import { todayBj } from '../utils/time'
import { useUserStore } from '../stores/user'

const props = defineProps({ embedded: { type: Boolean, default: false } })
const user = useUserStore()
const list = ref([])
const date = ref('')          // 后端实际生效的数据日(可能因非交易日对齐)
const pickDate = ref('')      // 用户选的历史日(空 = 今天)
const maxDate = todayBj()
const loading = ref(false)
const err = ref('')

// 涨跌配色(缺失/非法 → dim; 与全站 chgCls 同口径)
function cls(v) {
  if (v === null || v === undefined || v === '' || isNaN(v)) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}

function backToday() {
  pickDate.value = ''
  load(true)
}

async function load(manual = false) {
  if (!user.isVipOrPaid) return        // 前端预判, 省一次必然过不了门禁的请求
  loading.value = true
  try {
    const r = await zhPicks(pickDate.value || '')
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
/* 头部与「竞价一进二」同一套尺度(两面板在首页左栏并排, 必须看起来是一家) */
.zh-panel { padding: 2px 0 8px; }
.zh-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  margin: 2px 0 4px;
}
.zh-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-main);
}
.zh-icon { color: var(--accent); }
.zh-chip {
  font-size: 11px;
  font-weight: 500;
  padding: 1px 8px;
  border-radius: 10px;
  border: 1px solid var(--border-soft, #3a3f4b);
  color: var(--text-secondary);
}
.zh-chip-accent { color: #7ce8a0; border-color: rgba(124, 232, 160, 0.4); }
.zh-tools { display: flex; align-items: center; gap: 6px; }
.zh-btn {
  background: none;
  border: 1px solid var(--border-soft, #3a3f4b);
  color: var(--text-secondary);
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 12px;
  cursor: pointer;
}
.zh-btn:disabled { opacity: .5; cursor: default; }
.zh-date {
  background: transparent;
  border: 1px solid var(--border-soft, #3a3f4b);
  color: var(--text-secondary);
  border-radius: 6px;
  padding: 1px 6px;
  font-size: 12px;
  color-scheme: dark;
}
.zh-empty { padding: 22px 10px; text-align: center; color: var(--text-muted); }
.zh-empty-hint { margin-top: 6px; font-size: 11.5px; opacity: .8; }

/* ---- 表格 ---- */
.zh-scroll { overflow-x: auto; }
.zh-table { font-size: 12px; }
.zh-table th { font-size: 11.5px; white-space: nowrap; }
.zh-table td { white-space: nowrap; }
/*
  🔴 关闭 sticky 表头（与 YijinerView.vue:311-321 同因，勿"顺手恢复"）：
  全局 main.css 给 `.home-col-left .stock-table thead th` 设了
  position:sticky + top:var(--sticky-thead-top)，而该变量由首页 JS 按 `.home-filter`
  高度写入 —— 本 tab **没有 .home-filter** ⇒ 变量沿用别的 tab 的旧值(实测 56px)
  ⇒ 表头被顶到首行之下、视觉上遮住第一条(2026-09-29 主人截图即此)。
  选择器多一层 .zh-panel 是必要的：与全局规则同为 (0,2,2)，靠源码顺序获胜太脆弱。
*/
.zh-panel .zh-table thead th { position: static; }

.fa-refresh.spin { animation: zh-spin .9s linear infinite; display: inline-block; }
@keyframes zh-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* 嵌入首页左栏(半宽)时收紧标题 */
.yj-embedded .zh-title { font-size: 14px; }

/* 浅色主题 */
body[data-bg="light"] .zh-chip { color: #5a4a3a; }
body[data-bg="light"] .zh-empty { color: #6b6257; }

/* 手机窄屏: 表格横向滚动(不藏列, 与全站范式一致) */
@media (max-width: 430px) {
  .zh-table { display: block; overflow-x: auto; }
  .zh-table thead th { position: static; }
}
</style>
