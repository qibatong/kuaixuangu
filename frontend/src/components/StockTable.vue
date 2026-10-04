<template>
  <div class="stock-table-container">
    <div v-if="!stocks.length" class="empty-state">暂无符合条件股票</div>

    <!-- 选股表(竞价/盘中共用同一套列; 对齐生产机紧凑布局: 固定列宽, 代码+名称合并)
         ★ 2026-09-29 主人指令(**第三次**列调整: 精简展示列)——
           · 竞价选股: 去掉「竞价金额(竞额)」与「自由流通市值(自由流通)」
           · 实时动态选股: 去掉「竞额」「自由流通」「换手率」
           📌 三项仍是**评分因子/筛选门槛**, 后端照算照筛(见 stocks_spot.compute_score_spot /
              apply_spot_filters), 本次是**纯前端列下线**, 名单与排序结果一字不变。
         历史: 2026-09-28 v4.11.77 spot 加「竞涨/竞额」、去「实体/量比」
              (并去掉名称格内「3 日 20% 异动提示」徽章 —— 那是"盘前决策明天"的预告)。
         最终: spot = 现涨 | 竞涨 | [主力净额] | 评分 | 可信 | 概念
               竞价 = 现涨 | 竞涨 | 实体 | 评分 | 可信 | 概念
         🔴 换列必须**同步改 colgroup**, 否则 fixed 布局下末列会吞掉剩余宽度(见下方注释)。 -->
    <table v-else class="stock-table stock-table-compact">
      <colgroup>
        <col style="width:36px" />
        <!-- 2026-09-23: 名称列 88→106px —— 名称格内新增「连板高度」胶囊(最长「昨5板+」约 60px),
             与「异动」橙标签并排时最宽约 99px, 需 ≥103px 才不裁切。 -->
        <col style="width:106px" />
        <col style="width:46px" />
        <!-- 2026-09-28: 第 4 列 spot/竞价同为「竞涨」(46px) -->
        <col style="width:46px" />
        <!-- 🔴 2026-09-29: 「实体」**仅竞价**(50px) —— spot 侧该列不存在, 必须 v-if 与 th/td
             同步, 否则 colgroup 比表头多一列 ⇒ 整行错位、末列吞掉剩余宽度。 -->
        <col v-if="!isSpot" style="width:50px" />
        <!-- 2026-09-20: 主力净额列(盘中追踪)。🔴 colgroup 必须与表头列数一致 ——
             fixed 布局下缺 col 的末列会吞掉全部剩余宽度(概念列曾因此独占 ~470px)。
             整列为空时 v-if 隐藏 col(与 th/td 同步), 否则末列吞宽度 -->
        <col v-if="hasMainNet" style="width:56px" />
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
          <!-- 2026-09-28 v4.11.77: 「实体」**仅竞价**(开→收, 集合竞价语义); spot 去掉
               ★ 2026-09-29: 原「竞额」列已按主人要求下线(两条策略都不再展示) -->
          <th v-if="!isSpot" scope="col" class="sortable num col-entchg" :class="{ active: sortKey === 'entityChange' }" :aria-sort="ariaSortFor('entityChange')" title="实体涨幅 = (现价 ÷ 今开 − 1) × 100%，衡量开盘后买盘是否延续" tabindex="0" @click="onSort('entityChange', 'number')" @keydown.enter.prevent="onSort('entityChange', 'number')" @keydown.space.prevent="onSort('entityChange', 'number')">实体<span class="sort-ind" aria-hidden="true">{{ sortInd('entityChange') }}</span></th>
          <th v-if="hasMainNet" scope="col" class="sortable num col-mainnet" :class="{ active: sortKey === 'mainNet' }" :aria-sort="ariaSortFor('mainNet')" title="盘中主力净额(亿): 主力买入-卖出, 猫爪 fundflow_kp, 盘中约5分钟刷新; 盘后/非交易时段显示 -" tabindex="0" @click="onSort('mainNet', 'number')" @keydown.enter.prevent="onSort('mainNet', 'number')" @keydown.space.prevent="onSort('mainNet', 'number')">主力净额<span class="sort-ind" aria-hidden="true">{{ sortInd('mainNet') }}</span></th>
          <th scope="col" class="sortable num col-score" :class="{ active: sortKey === 'probability' }" :aria-sort="ariaSortFor('probability')" title="评分 = 竞价强度 / 活跃度 / 风险 / 市值 / 昨日表现 五因子加权 × 100（权重后台可调，0~100 分）" tabindex="0" @click="onSort('probability', 'number')" @keydown.enter.prevent="onSort('probability', 'number')" @keydown.space.prevent="onSort('probability', 'number')">评分<span class="sort-ind" aria-hidden="true">{{ sortInd('probability') }}</span></th>
          <th scope="col" class="sortable num col-conf" :class="{ active: sortKey === 'confidence' }" :aria-sort="ariaSortFor('confidence')" title="可信度 = 基准 65 + 竞价强度 / 竞价活跃度 / 竞价涨幅 三项加成，区间 55~90（越高说明多因子越一致）" tabindex="0" @click="onSort('confidence', 'number')" @keydown.enter.prevent="onSort('confidence', 'number')" @keydown.space.prevent="onSort('confidence', 'number')">可信<span class="sort-ind" aria-hidden="true">{{ sortInd('confidence') }}</span></th>
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
          <!-- 2026-09-30 v4.11.83 盯盘反馈: 数值用 :key 绑原值 ⇒ 轮询后值一变即重建元素, 触发一次性背景闪(.tick);
               同时补 ▲/▼ 方向符号 ⇒ 涨跌不再只靠颜色(色弱可用)。num-cell = 数字列禁折行(见样式段)。 -->
          <td class="num-cell" :class="realCls(item)" :title="item.realChange === null || item.realChange === undefined ? ('无实时行情数据（竞价锁定时刻 ' + pct(item._staleReal) + '）') : ''"><span :key="'r' + item.realChange" class="tick">{{ pct(item.realChange) }}<i class="arw" aria-hidden="true">{{ arrow(item.realChange) }}</i></span></td>
          <!-- 2026-09-28 v4.11.77: 「竞涨」两条策略共用(spot 后端本就下发 bidChange) -->
          <td class="num-cell" :class="chgCls(item.bidChange)" :title="'竞价涨幅: 集合竞价撮合价相对昨收的涨幅'"><span :key="'b' + item.bidChange" class="tick">{{ pct(item.bidChange) }}<i class="arw" aria-hidden="true">{{ arrow(item.bidChange) }}</i></span></td>
          <!-- 2026-09-28 v4.11.77: 「实体」仅竞价(开→收, 集合竞价语义); spot 去掉 -->
          <td v-if="!isSpot" class="num-cell" :class="item.entityChange === null || item.entityChange === undefined ? 'dim' : (item.entityChange > 0 ? 'up' : 'down')" :title="item.entityChange === null || item.entityChange === undefined ? ('无实时行情数据（竞价锁定时刻 ' + pct(item._staleEntity) + '）') : ''"><span :key="'e' + item.entityChange" class="tick">{{ pct(item.entityChange) }}<i class="arw" aria-hidden="true">{{ arrow(item.entityChange) }}</i></span></td>
          <!-- ★ 2026-09-29 主人要求下线: 「竞额」「换手」「自由流通」三列(仍是评分因子, 只是不展示) -->
          <td v-if="hasMainNet" :class="item.mainNet === null || item.mainNet === undefined ? 'dim' : (item.mainNet > 0 ? 'up' : 'down')" :title="item.mainNet === null || item.mainNet === undefined ? '盘中主力净额(亿): 非交易时段或数据未就绪' : ('盘中主力净额: ' + (item.mainNet > 0 ? '+' : '') + item.mainNet + '亿（约每5分钟刷新）')">{{ item.mainNet === null || item.mainNet === undefined ? '-' : (item.mainNet > 0 ? '+' : '') + item.mainNet.toFixed(3) }}</td>
          <td class="score-cell col-muted num-cell">{{ fmtNum(item.probability, 0, '分') }}</td>
          <td class="col-muted num-cell">{{ fmtNum(item.confidence, 0, '%') }}</td>
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

// 2026-09-29: `bidAmtText()` 随「竞额」列一起下线(唯一调用点已删) —— 后端 bidAmt 照旧下发,
//   若日后要恢复该列, 从 git 历史取回本函数即可(2026-09-03 的「恢复竞额列」版本)。
function realCls(item) {
  const r = item.realChange
  if (r === null || r === undefined || isNaN(r)) return 'dim'      // P0-3: 未知不配色
  const b = item.bidChange
  if (b !== null && b !== undefined && !isNaN(b) && r < b) return 'real-green'
  return r > 0 ? 'up' : 'down'
}

// 2026-09-30 v4.11.83 盯盘反馈: 涨跌方向符号 —— 与颜色**双编码**, 红绿色弱用户也能判方向。
// 0 / 缺失 / 非数字 → 不显示符号(避免"0% 也带箭头"的噪声)。
function arrow(v) {
  if (v === null || v === undefined || isNaN(v) || Number(v) === 0) return ''
  return Number(v) > 0 ? '▲' : '▼'
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
  padding: var(--s1) 2px !important;
  font-size: var(--fs-xs) !important;
}
.stock-table-compact th {
  padding: var(--s2) 2px !important;
  font-size: var(--fs-xs) !important;
}
.stock-table-compact th.sortable {
  white-space: nowrap;
}
.concept-cell {
  width: 72px;
  min-width: 0;
  white-space: normal;
  line-height: 1.3;
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  padding: var(--s1) 2px !important;
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
  padding: 1px var(--s1);
  font-size: var(--fs-xs);
  border-radius: var(--r-sm);
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
  padding: var(--s1) 2px !important;
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
  font-size: var(--fs-sm);
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
  font-size: var(--fs-2xl);
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
  gap: var(--s1);
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
  font-size: var(--fs-xs);
  line-height: 1;
  padding: 1px var(--s1);
  border-radius: var(--r-sm);
  /* 5-4: 监管告警标签从金色边框(奖牌语义)改为琥珀色实心胶囊(告警语义) */
  background: var(--warn);
  border: 1px solid var(--warn);
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
  font-size: var(--fs-xs);
  line-height: 1;
  padding: 1px var(--s1);
  border-radius: var(--r-sm);
  border: 1px solid transparent;
  font-weight: 600;
  white-space: nowrap;
  cursor: help;
}
.dev-badge-red { background: rgba(255, 77, 79, 0.16); border-color: rgba(255, 77, 79, 0.6); color: var(--accent-text); }
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
  font-size: var(--fs-xs);
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
  border: 1px solid var(--success);
  color: var(--success-text);
  border-radius: var(--r-sm);
  padding: 2px var(--s2);
  font-size: var(--fs-xs);
  cursor: pointer;
  white-space: nowrap;
}
.pool-add-btn:hover {
  background: rgba(120, 200, 80, 0.3);
}
.pool-add-btn.added {
  background: rgba(120, 200, 80, 0.35);
  border-color: var(--success);
  color: #e8ffd0;
  cursor: default;
}
.row-offline td { opacity: 0.55; }
.name-col { min-width: 90px; }
.name-main { line-height: 1.4; }
.offline-tag {
  display: inline-block;
  margin-top: var(--s1);
  font-size: var(--fs-xs);
  line-height: 1.3;
  color: var(--text-muted);
  border: 1px dashed #777;
  border-radius: var(--r-sm);
  padding: 1px var(--s1);
}

/* 2026-09-20 性能优化: content-visibility 虚拟化渲染。
   数千行时浏览器自动跳过视口外行的布局/绘制, 滚动流畅度大幅提升。
   纯 CSS 方案(不引入虚拟滚动库), 不影响既有 sticky 表头、排序、点击图表等。
   contain-intrinsic-size 用首列固定高度 64px 撑稳定滚动条(行高恒定, 无跳动)。 */
.stock-table-compact tbody tr {
  content-visibility: auto;
  contain-intrinsic-size: auto 64px;
}
/* ===================== 2026-09-30 v4.11.83 实机体检修复 =====================
   实机实测(生产站 390px)暴露三处, 根因都在本组件 scoped 样式:
   ① 数字折行: main.css 的 ≤768 块把 .stock-table 改成 table-layout:auto,
      浏览器按内容重分配列宽 ⇒ 名称列吃到 176px, 而「评分/可信」被压到 28px,
      「94分」「83%」折成两行(桌面端 65/80px 正常)。
   ② 横滑丢身份: 容器 overflow-x:auto 横滑 80px 后, 名称列 left 由 35 变 −45
      (被切掉约 1/4), 屏幕上认不出是哪只票 —— 盯盘最痛的失误点。
   ③ min-width 死配置: main.css 里 .stock-table{min-width:880/820/900px}(三处媒体块)
      一直被本组件 scoped 的 `min-width: 0`(带 [data-v-*] 属性选择器, 特异度更高)
      覆盖 ⇒「手机宽表横滑」的既定设计从未生效。
   修法: 数字列禁折行 + 名称列横滑冻结 + 给主表一个最小宽度兜底。
   仅 ≤768 生效, 桌面端(≥769)逐像素不变。 */
@media (max-width: 768px) {
  /* 数字列禁折行: auto 布局下就不会再把「94分」压成两行 */
  .stock-table-compact td.num-cell,
  .stock-table-compact th.num {
    white-space: nowrap;
  }
  /* 最小宽度兜底 = colgroup 常驻列之和(36+106+46+46+42+52+40);
     「实体/主力净额」是按策略出现的列, 其自身宽度会把表继续撑开, 故此处不并入。 */
  .stock-table-compact {
    min-width: 368px;
  }
  /* 名称列横滑冻结(第 2 列 = 名称; 第 1 列是奖牌/序号, 允许滑走)。
     🔴 必须不透明底色, 否则横向滚动时后面的列会"穿"过来。
     🔴🔴 表头格与体格**底色必须分开** —— 2026-09-30 实机踩坑: 表头格若用面板底色
        (`--bg-panel-solid`), 深色主题下 = rgba(18,22,35,.98) 变成"红表头中间一块黑格",
        浅色主题下 = #ffffff 配表头白字 ⇒ 文字直接看不见。
        表头格应改用**表头自己的底色**(main.css `.stock-table thead` 用的 var(--accent-deep2) = #d80000)。 */
  .stock-table-compact thead th:nth-child(2),
  .stock-table-compact tbody td:nth-child(2) {
    position: sticky;
    left: 0;
    z-index: 6;
    box-shadow: var(--sh-1);
  }
  .stock-table-compact thead th:nth-child(2) {
    z-index: 9;                                  /* 表头行整体 sticky top(z-index:8) ⇒ 名称表头要更高一层 */
    background: var(--accent-deep2, var(--accent-deep2));
  }
  .stock-table-compact tbody td:nth-child(2) {
    background: var(--bg-panel-solid, #121623);  /* 体格用面板底色: 要盖住从它下面滑过的列 */
  }
}

/* ---- 数字变化反馈: 值一变就重建的 .tick 闪一次背景(不做位移/不做过渡, 盯盘要克制) ---- */
@keyframes kx-tick-up {
  from { background: rgba(255, 138, 111, 0.26); }
  to   { background: transparent; }
}
@keyframes kx-tick-down {
  from { background: rgba(0, 200, 100, 0.24); }
  to   { background: transparent; }
}
.tick {
  display: inline-block;
  padding: 0 2px;
  border-radius: var(--r-sm);
}
.up .tick   { animation: kx-tick-up 0.30s ease-out; }
.down .tick { animation: kx-tick-down 0.30s ease-out; }
/* 方向符号: 小一号 + 半透明, 不抢红绿主次 */
.arw {
  font-style: normal;
  font-size: 0.72em;
  opacity: 0.8;
  margin-left: 1px;
}
/* 系统「减少动态效果」已在 main.css 全局降级(animation-duration:.01ms), 此处无需重复 */

/* 浅色主题覆盖: 表头红底白字(与全局统一) */
body[data-bg="light"] th.sortable {  color: #fff;  }
body[data-bg="light"] th.sortable:hover {  color: #fff;  }
body[data-bg="light"] th.sortable.active {  color: #fff;  }
body[data-bg="light"] .sort-ind {  color: #fff;  }
</style>