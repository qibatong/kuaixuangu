<template>
  <div class="stock-table-container">
    <div v-if="!stocks.length" class="empty-state">暂无符合条件股票</div>

    <!-- 选股表(竞价/盘中共用同一套列; 对齐生产机紧凑布局: 固定列宽, 代码+名称合并)
         2026-09-28 v4.11.77: spot 列定义**第二次调整**(主人指令)——
           · 去掉「实体涨幅」(entity_change: 开→收, 竞价语义) 与「量比」(vol_ratio)
             及名称格内「3 日 20% 异动提示」徽章(useDevWarn, 盘后 15:45 扫出的**次日**
             风险预告 —— 竞价是"盘前决策明天", 该徽章才有意义; 盘中名单是"此刻的答案")。
           · 增加「竞价涨幅 / 竞价金额」—— 📌 这两项 spot 后端**本来就下发**
             (stocks_spot.py `_spot_payload` 的 bidChange / bidAmt), 故为**纯前端列改造**。
             竞价那一刻的量对盘中仍有参考价值(今日开盘有多强)。
         最终 spot = 现涨 | 竞涨 | 竞额 | 换手; 竞价 = 现涨 | 竞涨 | 实体 | 竞额(**逐字不变**)。
         🔴 换列必须**同步改 colgroup**, 否则 fixed 布局下末列会吞掉剩余宽度(见下方注释)。 -->
    <table v-else class="stock-table stock-table-compact">
      <colgroup>
        <col style="width:36px" />
        <!-- 2026-09-23: 名称列 88→106px —— 名称格内新增「连板高度」胶囊(最长「昨5板+」约 60px),
             与「异动」橙标签并排时最宽约 99px, 需 ≥103px 才不裁切。 -->
        <col style="width:106px" />
        <col style="width:46px" />
        <!-- 2026-09-28: 第 4 列 spot/竞价同为「竞涨」(46px); 第 5 列**按策略分叉** ——
             竞价是「实体」(涨跌幅, 3~4 字符 → 50px), spot 是「竞额」(如 12345万 → 需 64px)。 -->
        <col style="width:46px" />
        <!-- 2026-09-28: 第 5 列 spot=竞额(5 位数, 64px) / 竞价=实体(50px) —— 见上方注释 -->
        <col :style="isSpot ? 'width:64px' : 'width:50px'" />
        <!-- 2026-09-28: 第 6 列 spot=换手(如 12.34%, 44px) / 竞价=竞额(64px) -->
        <col :style="isSpot ? 'width:44px' : 'width:64px'" />
        <!-- 2026-09-20: 主力净额列(盘中追踪)。🔴 colgroup 必须与表头列数一致 ——
             fixed 布局下缺 col 的末列会吞掉全部剩余宽度(概念列曾因此独占 ~470px)。
             5-8: 整列为空时 v-if 隐藏 col(与 th/td 同步), 否则末列吞宽度 -->
        <col v-if="hasMainNet" style="width:56px" />
        <col style="width:52px" />
        <col style="width:42px" />
        <col style="width:52px" />
        <col style="width:72px" />
      </colgroup>
      <thead>
        <tr>
          <th scope="col" class="col-medal" aria-label="排名"></th>
          <th scope="col" class="sortable merged-col col-name" :class="{ active: sortKey === 'code' || sortKey === 'name' }" :aria-sort="ariaSortFor('code')" tabindex="0" @click="onSort('code', 'string')" @keydown.enter.prevent="onSort('code', 'string')" @keydown.space.prevent="onSort('code', 'string')">名称<span class="sort-ind" aria-hidden="true">{{ sortInd('code') }}</span></th>
          <th scope="col" class="sortable num col-realchg" :class="{ active: sortKey === 'realChange' }" :aria-sort="ariaSortFor('realChange')" title="实时涨幅：当前价相对昨收的涨幅" tabindex="0" @click="onSort('realChange', 'number')" @keydown.enter.prevent="onSort('realChange', 'number')" @keydown.space.prevent="onSort('realChange', 'number')">现涨<span class="sort-ind" aria-hidden="true">{{ sortInd('realChange') }}</span></th>
          <!-- 2026-09-28 v4.11.77: 「竞涨 / 竞额」**两条策略共用**(spot 后端本就下发); spot 无「量比/实体」 -->
          <th scope="col" class="sortable num col-bidchg" :class="{ active: sortKey === 'bidChange' }" :aria-sort="ariaSortFor('bidChange')" title="竞价涨幅" tabindex="0" @click="onSort('bidChange', 'number')" @keydown.enter.prevent="onSort('bidChange', 'number')" @keydown.space.prevent="onSort('bidChange', 'number')">竞涨<span class="sort-ind" aria-hidden="true">{{ sortInd('bidChange') }}</span></th>
          <!-- 2026-09-28 v4.11.77: 「实体」**仅竞价**(开→收, 集合竞价语义); spot 去掉 -->
          <th v-if="!isSpot" scope="col" class="sortable num col-entchg" :class="{ active: sortKey === 'entityChange' }" :aria-sort="ariaSortFor('entityChange')" tabindex="0" @click="onSort('entityChange', 'number')" @keydown.enter.prevent="onSort('entityChange', 'number')" @keydown.space.prevent="onSort('entityChange', 'number')">实体<span class="sort-ind" aria-hidden="true">{{ sortInd('entityChange') }}</span></th>
          <th scope="col" class="sortable num col-bidamt" :class="{ active: sortKey === 'bidAmt' }" :aria-sort="ariaSortFor('bidAmt')" title="集合竞价阶段撮合成交金额" tabindex="0" @click="onSort('bidAmt', 'number')" @keydown.enter.prevent="onSort('bidAmt', 'number')" @keydown.space.prevent="onSort('bidAmt', 'number')">竞额<span class="sort-ind" aria-hidden="true">{{ sortInd('bidAmt') }}</span></th>
          <!-- 2026-09-28 v4.11.77: 「换手」**仅 spot**(竞价无实时换手) -->
          <th v-if="isSpot" scope="col" class="sortable num col-turnover" :class="{ active: sortKey === 'turnover' }" :aria-sort="ariaSortFor('turnover')" title="换手率：成交量 / 自由流通股本（spot 评分因子之一）" tabindex="0" @click="onSort('turnover', 'number')" @keydown.enter.prevent="onSort('turnover', 'number')" @keydown.space.prevent="onSort('turnover', 'number')">换手<span class="sort-ind" aria-hidden="true">{{ sortInd('turnover') }}</span></th>
          <th v-if="hasMainNet" scope="col" class="sortable num col-mainnet" :class="{ active: sortKey === 'mainNet' }" :aria-sort="ariaSortFor('mainNet')" title="盘中主力净额(亿): 主力买入-卖出, 猫爪 fundflow_kp, 盘中约5分钟刷新; 盘后/非交易时段显示 -" tabindex="0" @click="onSort('mainNet', 'number')" @keydown.enter.prevent="onSort('mainNet', 'number')" @keydown.space.prevent="onSort('mainNet', 'number')">主力净额<span class="sort-ind" aria-hidden="true">{{ sortInd('mainNet') }}</span></th>
          <!-- 2026-09-20: 列文案改「自由流通」—— 后端 circulationMV 已统一为自由流通口径 -->
          <th scope="col" class="sortable num col-mv" :class="{ active: sortKey === 'circulationMV' }" :aria-sort="ariaSortFor('circulationMV')" title="自由流通市值(亿), 与选股门槛同口径" tabindex="0" @click="onSort('circulationMV', 'number')" @keydown.enter.prevent="onSort('circulationMV', 'number')" @keydown.space.prevent="onSort('circulationMV', 'number')">自由流通<span class="sort-ind" aria-hidden="true">{{ sortInd('circulationMV') }}</span></th>
          <th scope="col" class="sortable num col-score" :class="{ active: sortKey === 'probability' }" :aria-sort="ariaSortFor('probability')" tabindex="0" @click="onSort('probability', 'number')" @keydown.enter.prevent="onSort('probability', 'number')" @keydown.space.prevent="onSort('probability', 'number')">评分<span class="sort-ind" aria-hidden="true">{{ sortInd('probability') }}</span></th>
          <th scope="col" class="sortable num col-conf" :class="{ active: sortKey === 'confidence' }" :aria-sort="ariaSortFor('confidence')" tabindex="0" @click="onSort('confidence', 'number')" @keydown.enter.prevent="onSort('confidence', 'number')" @keydown.space.prevent="onSort('confidence', 'number')">可信<span class="sort-ind" aria-hidden="true">{{ sortInd('confidence') }}</span></th>
          <th scope="col" class="sortable col-concept" :class="{ active: sortKey === 'concept' }" :aria-sort="ariaSortFor('concept')" tabindex="0" @click="onSort('concept', 'string')" @keydown.enter.prevent="onSort('concept', 'string')" @keydown.space.prevent="onSort('concept', 'string')">概念<span class="sort-ind" aria-hidden="true">{{ sortInd('concept') }}</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(item, idx) in sortedStocks" :key="item.code">
          <td class="medal-cell"><span v-if="idx < 3" class="row-medal" :title="['金牌','银牌','铜牌'][idx]">{{ ['🥇','🥈','🥉'][idx] }}</span></td>
          <td class="stock-info-cell" :data-stock-code="item.code" :data-stock-name="item.name" role="button" tabindex="0" :aria-label="'查看 ' + item.name + ' 分时图'" @click="openStockChart(item.code, item.name)" @keydown.enter.prevent="onCellKeydown($event, item)" @keydown.space.prevent="onCellKeydown($event, item)">
            <div class="stock-name-row">
              <span class="pool-hover-wrap">
                <span class="stock-name">{{ item.name }}</span>
                <PoolHoverBtn :item="item" />
              </span>
            </div>
            <div class="stock-code-row"><span class="stock-code">{{ item.code }}</span></div>
            <!-- 连板高度标签(2026-09-23 P0) + 异动监管标签: 同一行**并排**, 不新增列
                 (colgroup 与表头列数必须一致, 见上方 colgroup 注释)。
                 本行**始终渲染**(无标签时留空占位) —— 行高恒定, 各列网格线才对齐。
                 连板数口径 = 「买入前一日」(后端 _fill_lb 下发), 未知时不渲染而不是显示「新启动」。
    🔴 2026-09-28 主人拍板：0 档(前一日未涨停)也**不渲染** —— 见 utils/lb.js 的 lbLabel()。
                 2026-09-23 15:5x 主人要求:
                   ① 连板标签的悬停提示(同档历史统计/样本区间/免责)**整条取消** —— 不再有 title。
                   ② 异动标签里「偏离较大」不再显示 —— 过滤在 useYidongMonitor.yidongTag() 里统一做。
                 2026-09-27 v4.11.64 追加第三个徽章「异动风险」(红/黄): 与橙色「严重异动」语义**不同** ——
                   · 橙色「严重异动」= 开盘啦/交易所**已经公布**的监管名单(useYidongMonitor)
                   · 红/黄「异动风险」= 我们**按上交所 5.4.2 口径自算**的「会不会越线」(useDevWarn)
                   两者互不覆盖, 故并排显示; 无标签时该 span 不渲染(行高由 .badge-row 恒定保证)。
                 2026-09-28 v4.11.77 主人要求: **spot 态不渲染红/黄「异动风险」徽章** ——
                   该徽章源自 /api/dev/tomorrow(盘后 15:45 扫全市场, 是**次日**越线预告),
                   是"盘前决策明天"的信息; 盘中实时名单是"此刻的答案", 且 spot 无 9:26 定格、
                   主人明确要精简该列。竞价态保留(逐字不变)。
                   ⚠️ 因此 v4.11.76 起 spot 名单下 useDevWarn 的数据拉取对本表无消费方 —— 但
                   useDevWarnAutoLoad 仍在 onMounted 拉一次(模块级单例, 异动/复盘页共用),
                   不动它以免影响其它页面的首屏。 -->
            <div class="badge-row">
              <span v-if="lbLabel(item.lb)" class="lb-tag" :class="lbClass(item.lb)">{{ lbLabel(item.lb) }}</span>
              <span v-if="yidongTag(item.code)" class="yd-badge" :title="yidongTagTitle(item.code)">{{ yidongTag(item.code) }}</span>
              <span
                v-if="!isSpot && devWarnLabel(item.code)"
                class="dev-badge"
                :class="'dev-badge-' + devWarn(item.code)"
                :title="devWarnTitle(item.code)"
              >{{ devWarnLabel(item.code) }}</span>
            </div>
          </td>
          <td :class="realCls(item)" :title="item.realChange === null || item.realChange === undefined ? ('无实时行情数据（竞价锁定时刻 ' + pct(item._staleReal) + '）') : ''">{{ pct(item.realChange) }}</td>
          <!-- 2026-09-28 v4.11.77: 「竞涨」两条策略共用(spot 后端本就下发 bidChange) -->
          <td :class="chgCls(item.bidChange)" :title="'竞价涨幅: 集合竞价撮合价相对昨收的涨幅'">{{ pct(item.bidChange) }}</td>
          <!-- 2026-09-28 v4.11.77: 「实体」仅竞价(开→收, 集合竞价语义); spot 去掉 -->
          <td v-if="!isSpot" :class="item.entityChange === null || item.entityChange === undefined ? 'dim' : (item.entityChange > 0 ? 'up' : 'down')" :title="item.entityChange === null || item.entityChange === undefined ? ('无实时行情数据（竞价锁定时刻 ' + pct(item._staleEntity) + '）') : ''">{{ pct(item.entityChange) }}</td>
          <!-- 2026-09-28 v4.11.77: 「竞额」两条策略共用(spot 后端本就下发 bidAmt, 单位万元) -->
          <td class="col-muted" :title="'集合竞价阶段撮合成交金额: ' + (item.bidAmt ? bidAmtText(item.bidAmt) : '-')">{{ item.bidAmt || item.bidAmt === 0 ? bidAmtText(item.bidAmt) : '-' }}</td>
          <!-- 2026-09-28 v4.11.77: 「换手」仅 spot(竞价无实时换手) -->
          <td v-if="isSpot" class="col-muted" :title="item.turnover === null || item.turnover === undefined ? '无实时行情数据' : ('换手率: ' + fmtNum(item.turnover, 2) + '%')">{{ item.turnover === null || item.turnover === undefined ? '-' : fmtNum(item.turnover, 2) + '%' }}</td>
          <td v-if="hasMainNet" :class="item.mainNet === null || item.mainNet === undefined ? 'dim' : (item.mainNet > 0 ? 'up' : 'down')" :title="item.mainNet === null || item.mainNet === undefined ? '盘中主力净额(亿): 非交易时段或数据未就绪' : ('盘中主力净额: ' + (item.mainNet > 0 ? '+' : '') + item.mainNet + '亿（约每5分钟刷新）')">{{ item.mainNet === null || item.mainNet === undefined ? '-' : (item.mainNet > 0 ? '+' : '') + item.mainNet.toFixed(3) }}</td>
          <td class="col-muted">{{ fmtNum(item.circulationMV, 1) }}</td>
          <td class="score-cell col-muted">{{ fmtNum(item.probability, 0, '分') }}</td>
          <td class="col-muted">{{ fmtNum(item.confidence, 0, '%') }}</td>
          <td class="concept-cell" :title="'概念: ' + (item.concept || '')">
            <template v-if="item.concept">
              <span v-for="(c, i) in conceptList(item.concept)" :key="i" class="concept-item">{{ c }}</span>
            </template>
            <span v-else>-</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { fmtNum, pct } from '../utils/format'
// 2026-09-23 P0 连板高度标签: 纯展示映射(档位/配色), 逻辑与统计常量在 utils/lb.js
// 🔴 `lbTip` 已不再引入: 2026-09-23 15:5x 主人要求取消连板标签的悬停提示(函数仍留在 lb.js)。
import { lbClass, lbLabel } from '../utils/lb'
import { useYidongMonitor } from '../composables/useYidongMonitor'
// 2026-09-27 v4.11.64「异动风险」徽章（《快选异动停牌风险功能工单》§六：打通选股名单）
// ★ 用 AutoLoad 版：它在 **onMounted** 里惰性拉取。不能放 setup 顶层 ——
//   setup 在 SSR/单测里会执行、onMounted 不会，顶层拉取会在 Node 里发真实 fetch。
import { useDevWarnAutoLoad } from '../composables/useDevWarn'
import PoolHoverBtn from './PoolHoverBtn.vue'
import { openStockChart } from '../composables/uiBus'

const { yidongTag, yidongTagTitle } = useYidongMonitor()
const { devWarn, devWarnLabel, devWarnTitle } = useDevWarnAutoLoad()

const props = defineProps({
  stocks: { type: Array, default: () => [] },
  strategy: { type: String, default: 'auction' },
  bidSealMap: { type: Object, default: () => ({}) }  // code -> {limitBoards, bidSealAmt, bidNetAmt}
})

// 2026-09-28 v4.11.77: strategy='spot'(盘中实时) 的列差异, 共 **4 处**(全部用本变量判定) ——
//   · 加「竞涨 / 竞额」: 后端 stocks_spot._spot_payload 本就下发 bidChange/bidAmt,
//     无需后端改动; 竞价那一刻的量对盘中仍有参考价值(今日开盘有多强)。
//   · 去「实体」: 开→收是**集合竞价语义**, 盘中不存在这个"开盘瞬间"。
//   · 去「量比」: 主人要求精简(它仍是 spot 评分因子, 只是不再占一列展示)。
//   · 去名称格内红/黄「异动风险」徽章: 见 badge-row 处注释(次日预告, 非"此刻")。
// ⚠️ 竞价态(含不传 prop / 任意非 'spot' 取值)**必须逐字不变** —— 本文件其余部分
//    仍被 AuctionView 之外的老调用点依赖, 判断一律用「严格 === 'spot'」, 不用 !== 'auction'。
const isSpot = computed(() => props.strategy === 'spot')



// 无障碍: 股票单元格键盘触发弹图(Enter/Space)。仅当焦点在单元格本身
// (而非其内部的加自选等按钮)时触发, 避免内部按钮操作被误当成弹图。
function onCellKeydown(e, item) {
  if (e.target !== e.currentTarget) return
  openStockChart(item.code, item.name)
}

// 排序状态: { key: 'bidChange', dir: 'asc' | 'desc' } 或 null
const sortState = ref(null)

// 切换排序状态: 无 → 降序(默认, 数值越大越靠前) → 升序 → 无
// 字符串列默认升序(字典序), 数值列默认降序
function onSort(key, type) {
  if (!sortState.value || sortState.value.key !== key) {
    sortState.value = { key, dir: type === 'string' ? 'asc' : 'desc' }
  } else if (sortState.value.dir === 'desc') {
    sortState.value = { key, dir: 'asc' }
  } else {
    sortState.value = null
  }
}

function sortInd(key) {
  if (!sortState.value || sortState.value.key !== key) return ''
  return sortState.value.dir === 'desc' ? ' ↓' : ' ↑'
}

// 无障碍: th 的 aria-sort 取值(none/ascending/descending), 读屏可感知当前排序列与方向
function ariaSortFor(key) {
  if (!sortState.value || sortState.value.key !== key) return 'none'
  return sortState.value.dir === 'desc' ? 'descending' : 'ascending'
}

const sortKey = computed(() => sortState.value ? sortState.value.key : null)

// 5-8: 主力净额列整列为空(null/undefined)时自动隐藏(盘后/非交易时段), 数据到达自动恢复
const hasMainNet = computed(() => props.stocks.some(s => s.mainNet !== null && s.mainNet !== undefined))

// 排序后的列表; null/undefined 始终排到末尾(无论升降)
const sortedStocks = computed(() => {
  if (!sortState.value) return props.stocks
  const { key, dir } = sortState.value
  const colDef = columnType(key)
  const mult = dir === 'asc' ? 1 : -1
  return [...props.stocks].sort((a, b) => {
    const av = a[key], bv = b[key]
    // null/undefined 排到末尾
    const aNull = av === null || av === undefined
    const bNull = bv === null || bv === undefined
    if (aNull && bNull) return 0
    if (aNull) return 1
    if (bNull) return -1
    if (colDef === 'string') return mult * String(av).localeCompare(String(bv), 'zh-Hans-CN')
    return mult * (av - bv)
  })
})

// 列类型映射(影响默认排序方向和比较方式)
function columnType(key) {
  const strKeys = new Set(['code', 'name', 'industry', 'concept'])
  return strKeys.has(key) ? 'string' : 'number'
}

// 涨跌配色(红涨绿跌): 未知不参与配色(dim), 0 保持既有 down 口径不变
function chgCls(v) {
  if (v === null || v === undefined || isNaN(v)) return 'dim'
  return v > 0 ? 'up' : 'down'
}

// 概念最多显示前 2 个, 每个概念独立一行(换行显示, 而非顿号/空格拼接; 完整概念放 title hover)
function conceptList(c) {
  if (!c) return []
  return String(c).split(/[、,，]/).map(s => s.trim()).filter(Boolean).slice(0, 2)
}

// 2026-09-03 主人要求恢复: 竞价选股表重新展示「竞额」列(bidAmt, 万元); amt>=10000万(1亿)折算显示亿
function bidAmtText(amt) {
  if (amt === null || amt === undefined || isNaN(amt)) return '—'
  return amt >= 10000 ? (amt / 10000).toFixed(2) + '亿' : Math.round(amt).toFixed(0)
}
function realCls(item) {
  const r = item.realChange
  if (r === null || r === undefined || isNaN(r)) return 'dim'      // P0-3: 未知不配色
  const b = item.bidChange
  if (b !== null && b !== undefined && !isNaN(b) && r < b) return 'real-green'
  return r > 0 ? 'up' : 'down'
}
</script>

<style scoped>
/* 紧凑表(对齐生产机): 固定列宽 + 紧凑字号 */
.stock-table-compact {
  table-layout: fixed;
  min-width: 0;
  width: 100%;
}
.stock-table-compact th,
.stock-table-compact td {
  text-align: center;
  vertical-align: middle;
  padding: 5px 2px !important;
  font-size: 0.75rem !important;
}
.stock-table-compact th {
  padding: 7px 2px !important;
  font-size: 0.75rem !important;
}
.stock-table-compact th.sortable {
  white-space: nowrap;
}
.concept-cell {
  width: 72px;
  min-width: 0;
  white-space: normal;
  line-height: 1.3;
  font-size: 0.75rem;
  color: var(--text-secondary);
  padding: 4px 2px !important;
}
.concept-cell .concept-item {
  display: block;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.stock-table-compact .pool-add-btn {
  padding: 1px 5px;
  font-size: 0.75rem;
  border-radius: 3px;
}
/* 2026-09-20 视觉减噪: 中性列(竞额/市值/评分/可信)灰字, 页面只保留涨跌红绿一个彩色语义 */
.col-muted { color: var(--text-muted); }
/* 首列"名称": 代码+名称 上下排布, 可点击打开图表 */
.stock-info-cell {
  cursor: pointer;
  min-width: 0;
  min-height: 0;
  /* 2026-09-21 字号12px下限: badge 行高 13->17px, 单元格 52->56px 同步
     2026-09-23 P0 连板高度标签: badge 行改为**始终渲染**, 内容高 = padding8 + 名称16.9
       + 代码16.4 + badge行19 ≈ 60.3px → 56px 会裁掉胶囊上下边(原「异动」行只在有标签时
       出现, 同样溢出只是不易察觉)。故单元格 56->64px, 并同步 contain-intrinsic-size。 */
  height: 64px;
  text-align: center;
  padding: 4px 2px !important;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.stock-info-cell .stock-name-row {
  order: 1;
  display: block;
  align-items: center;
  justify-content: center;
  gap: 0;
  line-height: 1.3;
}
.stock-info-cell .stock-name {
  font-weight: 600;
  color: var(--text-main);
  font-size: 0.8125rem;
}
/* 5-1: 奖牌独立一列(2026-09-21 主人拍板: 绝对定位+padding 预留方案导致名称列前后行不对齐)
   独立 36px 窄列, 前三名显示奖牌居中, 其余行空 —— 名称列恢复整齐对齐 */
.medal-cell {
  text-align: center;
  vertical-align: middle;
  padding: 0 !important;
}
.stock-info-cell .row-medal,
.medal-cell .row-medal {
  font-size: 1.25rem;
  line-height: 1;
}
/* 名称格内的标签行: 「连板高度」胶囊 + 「异动监管」标签 **并排**。
   2026-09-23: 由原 `.yd-badge-row`(仅异动) 扩成 `.badge-row` —— 整行**始终渲染**,
   无标签时留空占位, 保证行高恒定、各列网格线对齐(即原注释声明的设计意图)。 */
.badge-row {
  order: 3;
  /* 2026-09-21 字号12px下限: badge 10->12px, 行高 13->17px 容纳(字12+padding2+border2) */
  height: 17px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  margin-top: 2px;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
}
/* 连板高度档位标签(.lb-tag / .lb-tag-lv1..5 及浅色主题)的配色**已提到全局**
   `src/styles/main.css`（2026-09-28）—— 异动名单 `DevWarnList.vue` 也要渲染同一套标签，
   留在本组件 scoped 里就得复制一份梯度，日后只改一处必然漂移。
   类名唯一性(用 lb-tag 而非 lb-badge)的依据见全局那一节的注释。 */
.yd-badge {
  display: inline-block;
  font-size: 0.75rem;
  line-height: 1;
  padding: 1px 5px;
  border-radius: 3px;
  /* 5-4: 监管告警标签从金色边框(奖牌语义)改为琥珀色实心胶囊(告警语义) */
  background: #ff9632;
  border: 1px solid #ff9632;
  color: #3a1f00;
  font-weight: 600;
  white-space: nowrap;
}
/* 2026-09-27 v4.11.64「异动风险」徽章：**描边式**（不是实心）——
   与上面橙色实心的「严重异动」一眼可分；红色=今日已越线，黄色=明日涨停即越线/已临近。
   三者并排时宽度预算：连板(2~3字) + 严重异动(4字) + 异动风险(4字) ≈ 110px，
   超过窄屏名称列宽时由 .badge-row 的 overflow:hidden 裁掉尾部（不换行、不撑高）。 */
.dev-badge {
  display: inline-block;
  font-size: 0.75rem;
  line-height: 1;
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid transparent;
  font-weight: 600;
  white-space: nowrap;
  cursor: help;
}
.dev-badge-red { background: rgba(255, 77, 79, 0.16); border-color: rgba(255, 77, 79, 0.6); color: #ff9a9a; }
.dev-badge-yellow { background: rgba(255, 197, 61, 0.13); border-color: rgba(255, 197, 61, 0.55); color: #ffd666; }
body[data-bg="light"] .dev-badge-red { background: #fde3e3; border-color: #d4380d; color: #8c1c00; }
body[data-bg="light"] .dev-badge-yellow { background: #fff8e0; border-color: #c79100; color: #7a4d00; }
.stock-info-cell .stock-code-row {
  order: 2;
  line-height: 1.2;
  text-align: center;
  margin-top: 2px;
}
.stock-info-cell .stock-code {
  font-family: inherit;
  font-size: 0.75rem;
  color: var(--text-muted);
  letter-spacing: 0.5px;
}
.stock-info-cell:hover .stock-name,
.stock-info-cell:hover .stock-code {
  color: var(--accent);
}
th.sortable {
  cursor: pointer;
  user-select: none;
}
th.sortable:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.12);
}
th.sortable.active {
  color: #fff;
}
.sort-ind {
  display: inline-block;
  width: 10px;
  color: #fff;
  font-weight: 700;
}
th.sortable.active .sort-ind {
  opacity: 1;
}
/* 2026-09-20 减噪: 排序提示箭头 ↕ 默认隐藏, 悬停该列头才显示(红底白字下更干净) */
th.sortable .sort-ind:empty::before {
  content: '↕';
  opacity: 0;
  font-weight: 400;
}
th.sortable:hover .sort-ind:empty::before {
  opacity: 0.75;
}
/* 浅色主题: 表头同为红底白字, 排序箭头白字 */
body[data-bg="light"] .sort-ind { color: #fff; }
body[data-bg="light"] th.sortable:hover { color: #fff; }
body[data-bg="light"] th.sortable.active { color: #fff; }
.accel-hot {
  color: var(--accent-deep);
  font-weight: 700;
}
.op-col {
  min-width: 56px;
}
.pool-add-btn {
  background: rgba(120, 200, 80, 0.15);
  border: 1px solid #78c850;
  color: #c0e8a0;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 0.75rem;
  cursor: pointer;
  white-space: nowrap;
}
.pool-add-btn:hover {
  background: rgba(120, 200, 80, 0.3);
}
.pool-add-btn.added {
  background: rgba(120, 200, 80, 0.35);
  border-color: #78c850;
  color: #e8ffd0;
  cursor: default;
}
.row-offline td { opacity: 0.55; }
.name-col { min-width: 90px; }
.name-main { line-height: 1.4; }
.offline-tag {
  display: inline-block;
  margin-top: 3px;
  font-size: 0.75rem;
  line-height: 1.3;
  color: var(--text-muted);
  border: 1px dashed #777;
  border-radius: 4px;
  padding: 1px 5px;
}

/* 2026-09-20 性能优化: content-visibility 虚拟化渲染。
   数千行时浏览器自动跳过视口外行的布局/绘制, 滚动流畅度大幅提升。
   纯 CSS 方案(不引入虚拟滚动库), 不影响既有 sticky 表头、排序、点击图表等。
   contain-intrinsic-size 用首列固定高度 64px 撑稳定滚动条(行高恒定, 无跳动)。 */
.stock-table-compact tbody tr {
  content-visibility: auto;
  contain-intrinsic-size: auto 64px;
}
/* 浅色主题覆盖: 表头红底白字(与全局统一) */
body[data-bg="light"] th.sortable {  color: #fff;  }
body[data-bg="light"] th.sortable:hover {  color: #fff;  }
body[data-bg="light"] th.sortable.active {  color: #fff;  }
body[data-bg="light"] .sort-ind {  color: #fff;  }
</style>