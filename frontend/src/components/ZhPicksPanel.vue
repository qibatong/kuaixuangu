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
            <!-- 2026-09-29 主人: 回看日期不单独占一行 —— **点这里**就能选日期(今日/历史同一入口) -->
            <!-- 🔴 2026-09-29 主人实测: 日期控件放在 .zh-tools(右端) 时, .zh-head 是
                 `display:flex + justify-content:space-between` ⇒ 绝对定位子元素按 flex 对齐规则
                 被推到**末端**, 原生日历弹在最右侧。改为把隐藏控件**贴着 chip** 定位。 -->
            <span class="zh-picker">
              <button class="zh-chip zh-chip-date" :class="{ 'zh-chip-history': !!pickDate }"
                      :title="pickDate ? '正在回看历史：点击可换日期' : '点击选择回看日期'"
                      @click="openPicker">
                {{ pickDate ? '回看' : '今日' }} {{ date || maxDate }}
                <i class="fa" :class="pickDate ? 'fa-history' : 'fa-caret-down'"></i>
              </button>
              <input ref="dateEl" v-model="pickDate" class="zh-date-hidden" type="date" :max="maxDate"
                     @change="load(true)">
            </span>
            <span v-if="pickDate" class="zh-chip zh-chip-back" title="回到今天" @click="backToday">回今天</span>
            <span v-if="list.length" class="zh-chip zh-chip-accent">{{ list.length }} 只</span>
          </div>
          <div class="zh-tools">
            <button class="zh-btn" :disabled="loading" title="刷新" @click="load(true)">
              <i class="fa fa-refresh" :class="{ spin: loading }"></i>
            </button>
          </div>
        </div>

        <div v-if="err" class="zh-empty"><i class="fa fa-exclamation-circle"></i> {{ err }}</div>
        <div v-else-if="loading && !list.length" class="zh-empty">加载中...</div>
        <!-- 🔴 2026-09-30 主人「清理提示」: 原此处还有一行入选标准说明 ——
             "9:25 定格后生成；入选 = 涨停基因(近120日≥1次) + 高开≥3% + 竞价放量占昨量 5~10%，非ST"
             ⇒ 已删除(连同 .zh-empty-hint 样式)。
             ⚠️ 保留"今日竞价精选暂无数据"这一句: 它是**空态标识**、不是提示, 且删掉整块会在
                页面上留一片纯空白(违反主人最初要求的"不因删除提示而出现空白或错位")。
                要连这句一并删, 说一声即可。 -->
        <div v-else-if="!list.length" class="zh-empty">
          <i class="fa fa-filter"></i> {{ pickDate ? (date + ' 无入选标的') : '今日竞价精选暂无数据' }}
        </div>

        <template v-else>
          <!-- ★ 2026-09-29 主人反馈「手机端没有自适应」: ≤768px 横滑(带可滑提示),
               ≤430px 卡片化(零横滑) —— 与「竞价一进二」同一套做法。 -->
          <div class="zh-swipe-hint"><i class="fa fa-arrows-h"></i> 左右滑动查看全部 6 列</div>
          <div class="zh-scroll">
        <table class="stock-table zh-table">
          <thead>
            <!-- 2026-09-29 主人: 表头可排序 —— 复用全站 useSortable(点击 无→降序→升序→无) -->
            <tr>
              <th class="sortable" :class="{ active: sort.keyOf('code') }"
                  @click="sort.onSort('code', 'string')">名称<span class="sort-ind">{{ sort.ind('code') }}</span></th>
              <th class="sortable" :class="{ active: sort.keyOf('bidChange') }"
                  @click="sort.onSort('bidChange')">竞价涨幅<span class="sort-ind">{{ sort.ind('bidChange') }}</span></th>
              <th class="sortable" :class="{ active: sort.keyOf('realChange') }"
                  @click="sort.onSort('realChange')">实时涨幅<span class="sort-ind">{{ sort.ind('realChange') }}</span></th>
              <th class="sortable" :class="{ active: sort.keyOf('entityChange') }"
                  @click="sort.onSort('entityChange')">实体涨幅<span class="sort-ind">{{ sort.ind('entityChange') }}</span></th>
              <th class="sortable" :class="{ active: sort.keyOf('bidAmt') }"
                  @click="sort.onSort('bidAmt')">竞价金额<span class="sort-ind">{{ sort.ind('bidAmt') }}</span></th>
              <th class="sortable" :class="{ active: sort.keyOf('board') }"
                  @click="sort.onSort('board', 'string')">概念<span class="sort-ind">{{ sort.ind('board') }}</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="it in shown" :key="it.code">
              <!-- 名称格 hover 给出策略口径(占昨量/涨停基因) —— 主人要的列只保留 5 个 -->
              <td class="stock-info-cell" @click="linkToSoftware(it.code)"
                  :title="'占昨量 ' + it.volPct + '%（入选区间 5~10%） · 涨停基因 ' + it.ztGene + ' 次（近120日） · ' + (it.market || '')">
                <div class="stock-code-row"><span class="stock-code">{{ it.code }}</span></div>
                <div class="stock-name-row"><span class="stock-name">{{ it.name }}</span></div>
              </td>
              <td :class="cls(it.bidChange)" data-label="竞价涨幅">{{ signed(it.bidChange) }}%</td>
              <td :class="cls(it.realChange)" data-label="实时涨幅">{{ signed(it.realChange) }}%</td>
              <td :class="cls(it.entityChange)" data-label="实体涨幅">{{ signed(it.entityChange) }}%</td>
              <!-- 🔴 后端 bidAmt 单位是**万元**; amtText 吃**元** ⇒ ×1e4 -->
              <td :class="(it.bidAmt || 0) > 0 ? 'up' : 'dim'" data-label="竞价金额">{{ amtText((it.bidAmt || 0) * 1e4) }}</td>
              <td class="concept-cell dim" :title="it.board" data-label="概念"><span v-if="it.board" class="concept-clamp">{{ it.board }}</span><span v-else>-</span></td>
            </tr>
          </tbody>
        </table>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useSortable } from '../composables/useSortable'
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
const dateEl = ref(null)      // 隐藏的原生日期控件(由标题 chip 唤出)
const sort = useSortable()    // 2026-09-29 主人: 表头排序(全站同一交互)

// 名称列按**代码**排序 —— 与全站「名称」合并列同范式(YidongView/MarketView 亦如此)
const shown = computed(() => sort.sorted(list.value, (it, k) => (k === 'code' ? (it.code || '') : it[k])))
const loading = ref(false)
const err = ref('')

function openPicker() {
  const el = dateEl.value
  if (!el) return
  // Chrome 支持 showPicker(); 其余浏览器降级为 focus+click(原生控件仍会弹出)
  if (typeof el.showPicker === 'function') {
    try { el.showPicker(); return } catch (e) { /* 降级 */ }
  }
  el.focus(); el.click()
}

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
.zh-panel { padding: 2px 0 var(--s2); }
.zh-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s2);
  flex-wrap: wrap;
  margin: 2px 0 var(--s1);
}
.zh-title {
  display: flex;
  align-items: center;
  gap: var(--s2);
  font-size: var(--fs-md);
  font-weight: 700;
  color: var(--text-main);
}
.zh-icon { color: var(--accent); }
.zh-chip {
  font-size: var(--fs-xs);
  font-weight: 400;
  padding: 1px var(--s2);
  border-radius: var(--r-lg);
  border: 1px solid var(--border-soft, #3a3f4b);
  color: var(--text-secondary);
}
.zh-chip-accent { color: var(--success-text); border-color: rgba(124, 232, 160, 0.4); }
.zh-tools { display: flex; align-items: center; gap: var(--s2); }
.zh-btn {
  background: none;
  border: 1px solid var(--border-soft, #3a3f4b);
  color: var(--text-secondary);
  border-radius: var(--r-md);
  padding: 2px var(--s2);
  font-size: var(--fs-xs);
  cursor: pointer;
}
.zh-btn:disabled { opacity: .5; cursor: default; }
/* 2026-09-29 主人: 回看日期并入标题 chip —— 可点击、无独立输入行 */
.zh-chip-date {
  cursor: pointer;
  background: none;
  font-family: inherit;
  line-height: 1.6;
}
.zh-chip-date:hover { border-color: var(--accent); color: var(--text-main); }
.zh-chip-history { color: var(--accent); border-color: var(--accent); }
.zh-chip-back { cursor: pointer; }
.zh-chip-back:hover { color: var(--accent); border-color: var(--accent); }
/* 隐藏的原生日期控件: 必须由 .zh-picker(贴着 chip)做定位上下文 ——
   若留在 .zh-tools(右端), flex 容器里的绝对定位子元素会被推到末端 ⇒ 日历弹在最右侧 */
.zh-picker { position: relative; display: inline-flex; }
.zh-date-hidden {
  position: absolute; left: 0; top: 100%;
  width: 1px; height: 1px; padding: 0; border: 0; opacity: 0;
  pointer-events: none;
}
.zh-empty { padding: var(--s6) var(--s2); text-align: center; color: var(--text-muted); }
/* 2026-09-30 主人清理提示: .zh-empty-hint 已随其说明文案一并删除 */

/* ---- 表格 ---- */
.zh-scroll { overflow-x: auto; }
.zh-table { font-size: var(--fs-xs); }
.zh-table th { font-size: var(--fs-xs); white-space: nowrap; }
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
.yj-embedded .zh-title { font-size: var(--fs-base); }

/* 浅色主题 */
body[data-bg="light"] .zh-chip { color: var(--watermark); }
body[data-bg="light"] .zh-empty { color: #6b6257; }

/* ==================== 手机端自适应(★ 2026-09-29 主人反馈「手机端没有自适应」) ====================
   🔴 原实现只在 ≤430px 给 **`.zh-table` 本身**加 `display:block; overflow-x:auto` ——
      `display` 一改就不是表格了: 行列塌成块、列宽对不齐、数字列全串行(手机端看着就是"坏的")。
      而且 CSS 里的横滑容器 `.zh-scroll` **根本没被模板用上**(表格是裸的)。
   改为与「竞价一进二」同一套(见 YijinerView.vue 的 .yj-scroll/.yj-swipe-hint/卡片段):
      ① ≤768px: 表格套横滑容器 + 一行"左右滑动"提示(手机不显示滚动条, 不提示用户不知道右边有列)
      ② ≤430px: **卡片化** —— tr 变卡片、td 变卡片内字段, 字段名由 `<td data-label>` 用
         `::before` 生成(纯 CSS, 不动接口/不动数据结构), 零横滑。
   回滚: 删掉下面两段 @media 即回到"裸表格"原状。 */
.zh-swipe-hint { display: none; }

@media (max-width: 768px) {
  .zh-swipe-hint {
    display: flex; align-items: center; gap: var(--s1);
    padding: var(--s1) var(--s2); margin-bottom: var(--s1);
    font-size: var(--fs-xs); color: var(--text-secondary);
    background: rgba(255, 255, 255, 0.04);
    border: 1px dashed var(--border-soft, #3a3f4b); border-radius: var(--r-md);
  }
  .zh-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .zh-table { min-width: 420px; font-size: var(--fs-xs); }
  .zh-table th, .zh-table td { padding: var(--s2) var(--s1); }
  /* 概念用 overflow-wrap 而非 word-break:break-all —— 后者会把 MiniLED/CPO/PCB 从中间断开 */
  .zh-table td.concept-cell { max-width: 110px; white-space: normal; line-height: 1.3; overflow-wrap: anywhere; }
}

/* ============================================================
   🔴 2026-10-08 主人指示「竞价精选手机端看起来不方便，改成和电脑端一样的表格形式」
   ⇒ **撤销** 2026-09-29 的卡片化（与 YijinerView 同批试的卡片，同日一并撤）。

   做法：删掉 tr→卡片 / td→字段 的全套规则（含 `.zh-swipe-hint{display:none}`、
        `.zh-scroll{overflow-x:visible}`、`.zh-table{display:block;min-width:0}`、
        `thead{display:none}` 与 data-label 伪元素）⇒ 恢复「表头 + 列 + 横滑」，
        手机与电脑**同一套表格结构**。
   注：≤768px 给的 min-width 是 420px（6 列）——390px 屏只多出一点点，横滑很轻微。
   回滚：从 git 历史取回本段原卡片规则即可。
   ============================================================ */
@media (max-width: 430px) {
  /* 表格态表头可见 ⇒ 本条必须保留，否则首行会被 sticky 表头压住。 */
  .zh-table thead th { position: static; }
}
</style>
