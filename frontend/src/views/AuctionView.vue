<template>
  <div class="page-shell">
    <!-- 2026-10-05 (L3): 本页会作为「竞价异动」面板内嵌在首页右栏 ⇒ 原先出现**两个 h1**
         （首页的「选股」+ 这里的「竞价异动」）。独立路由时仍是 h1，内嵌时降为 h2。 -->
    <h1 v-if="isStandalone" class="visually-hidden">竞价异动</h1>
    <h2 v-else class="visually-hidden">竞价异动</h2>
    <!-- 配额门禁(2026-09-21 会员体系): 免费用户每天有限次数, 用尽后显示配额引导;
         quotaExceeded 由接口 429(code=quota_exceeded) 触发 -->
    <VipGate v-if="quotaExceeded" ref="gateRef" title="竞价异动" :required-level="1" />

    <template v-else>
    <div class="auc-head">
      <span class="auc-title"><i class="fa fa-bullhorn"></i> 竞价异动</span>
      <!-- 2026-09-05 P0: 静默刷新指示 —— 轮询/单 Tab 刷新时显示角落小 spinner,
           不遮挡表格内容(区别于首屏 loading 的大块占位) -->
      <span v-if="silentRefreshing" class="auc-silent-loading" title="数据刷新中">
        <i class="fa fa-circle-o-notch fa-spin"></i>
      </span>
      <!-- 连续失败: 提示已保留旧数据并自动退避重试(不弹窗打扰) -->
      <span
v-else-if="pollFailCount > 0" class="auc-poll-warn"
            title="刷新失败，已保留上次数据，稍后自动重试"
>
        <i class="fa fa-exclamation-triangle"></i> 稍后重试
      </span>
      <!-- 日期回看: 右侧对齐, 实时模式下日期框直接显示数据日期 -->
      <span class="auc-head-spacer"></span>
      <!-- 2026-09-27 v4.11.63《移动端清单》§二·4: 数据更新时刻。
           历史回看模式(选了日期)没有轮询 ⇒ interval 传 0，"每 30s 自动刷新"那句自动消失。 -->
      <DataStamp :at="dataAt" :ok="dataOk" :interval="datePicker ? 0 : 30" :stale="dataStale" />
      <!-- 🔴 2026-09-30: 显示值 = **实际请求的日期**(displayDate), 不再显示"库里最新有数据的一天"
           —— 原实现 `datePicker || dataDate` 会在盘前出现"框里 09-29、实际查 09-30"的误导。 -->
      <input :value="displayDate" type="date" class="rot-date" title="选择历史交易日（留空=实时）" @change="onDateChange">
      <button class="rot-reset-btn" title="回到实时" @click="clearDate"><i class="fa fa-bolt"></i></button>
      <!-- 2026-09-05 P0: 手动刷新入口(用户主动触发, 不增加常态轮询负载) -->
      <button
class="rot-reset-btn" title="刷新当前 Tab 数据" :disabled="silentRefreshing"
              @click="refreshCurrentTab"
>
        <i class="fa fa-refresh" :class="{ 'fa-spin': silentRefreshing }"></i>
      </button>
      <button
class="rot-reset-btn" title="刷新全部数据（重新加载所有 Tab）"
              :disabled="loading || silentRefreshing" @click="refreshAll"
>
        <i class="fa fa-repeat"></i>
      </button>
    </div>

    <!-- Tab 切换: 一排横排 -->
    <div class="auc-tabs">
      <button class="auc-tab" :class="{ active: tab === 's3' }" title="全市场竞价封单榜: 9:25涨停→9:20涨停回落→9:15涨停回落 三层排序" @click="switchTab('s3')">竞价封单</button>
      <button class="auc-tab" :class="{ active: tab === 'boom' }" @click="switchTab('boom')">竞价爆量</button>
      <button class="auc-tab" :class="{ active: tab === 'qc' }" title="9:15-9:30 竞价抢筹(异动板块大单)" @click="switchTab('qc')">竞价抢筹</button>
      <button class="auc-tab" :class="{ active: tab === 'seal' }" @click="switchTab('seal')">竞价委买</button>
      <button class="auc-tab" :class="{ active: tab === 'net' }" @click="switchTab('net')">竞价净额</button>
      <!-- 5-6: 按时间逻辑分组 —— 竞价口径(5) | 今日(1) | 昨日表现(4) -->
      <span class="auc-tab-group" title="今日">|</span>
      <button class="auc-tab" :class="{ active: tab === 'brokenToday' }" @click="switchTab('brokenToday')">今炸板</button>
      <span class="auc-tab-group" title="昨日表现">|</span>
      <button class="auc-tab" :class="{ active: tab === 'yestZt' }" @click="switchTab('yestZt')">昨涨停</button>
      <button class="auc-tab" :class="{ active: tab === 'yestBroken' }" @click="switchTab('yestBroken')">昨断板</button>
      <button class="auc-tab" :class="{ active: tab === 'brokenYest' }" @click="switchTab('brokenYest')">昨炸板</button>
      <!-- ★ 2026-09-29 主人要求: 去掉「昨上榜」板块(该板块另有独立页 /lhb, 见 views/LhbView.vue) -->
    </div>

    <div class="auc-panel">
      <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载中...</div></div>

      <!-- 竞价委买/爆量/净额 共用表 -->
      <table v-else-if="tab === 'seal' || tab === 'boom' || tab === 'net'" class="stock-table">
        <thead>
          <tr>
<th class="sortable" :class="{ active: sealSort.keyOf('code') }" @click="sealSort.onSort('code', 'string')">名称<span class="sort-ind">{{ sealSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('realChange') }" @click="sealSort.onSort('realChange')">现涨<span class="sort-ind">{{ sealSort.ind('realChange') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('bidChange') }" @click="sealSort.onSort('bidChange')">竞涨<span class="sort-ind">{{ sealSort.ind('bidChange') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf(tab === 'boom' || tab === 'net' ? 'bidAmt' : 'bidSealAmt') }" @click="sealSort.onSort(tab === 'boom' || tab === 'net' ? 'bidAmt' : 'bidSealAmt')">{{ tab === 'boom' || tab === 'net' ? '竞额' : '涨停委买额(亿)' }}<span class="sort-ind">{{ sealSort.ind(tab === 'boom' || tab === 'net' ? 'bidAmt' : 'bidSealAmt') }}</span></th>
            <th v-if="tab === 'boom'" class="sortable" :class="{ active: sealSort.keyOf('bidRatioYest') }" @click="sealSort.onSort('bidRatioYest')">竞价量比<span class="sort-ind">{{ sealSort.ind('bidRatioYest') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('bidTurnover') }" @click="sealSort.onSort('bidTurnover')">竞换<span class="sort-ind">{{ sealSort.ind('bidTurnover') }}</span></th>
            <th v-if="tab === 'net'" class="sortable" :class="{ active: sealSort.keyOf('bidNetAmt') }" @click="sealSort.onSort('bidNetAmt')">净额(亿)<span class="sort-ind">{{ sealSort.ind('bidNetAmt') }}</span></th>
            <th v-if="tab !== 'boom'" class="sortable" :class="{ active: sealSort.keyOf('limitBoards') }" @click="sealSort.onSort('limitBoards')">连板<span class="sort-ind">{{ sealSort.ind('limitBoards') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('floatMv') }" @click="sealSort.onSort('floatMv')">流通(亿)<span class="sort-ind">{{ sealSort.ind('floatMv') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('board') }" @click="sealSort.onSort('board', 'string')">概念<span class="sort-ind">{{ sealSort.ind('board') }}</span></th>
</tr>
        </thead>
        <tbody>
          <tr v-for="it in sealSort.sorted(sealList)" :key="it.code">
            <td class="stock-info-cell" @click="linkToSoftware(it.code, it.name)">
            <div class="stock-code-row"><span class="stock-code">{{ it.code }}</span></div>
            <div class="stock-name-row"><span class="pool-hover-wrap"><span class="stock-name">{{ it.name }}</span><PoolHoverBtn :item="it" /></span></div>
            <div v-if="yidongTag(it.code)" class="yd-badge-row"><span class="yd-badge" :title="yidongTagTitle(it.code)">{{ yidongTag(it.code) }}</span></div>
          </td>
            <td :class="it.realChange > 0 ? 'up' : 'down'">{{ signed(it.realChange) }}%</td>
            <td :class="it.bidChange > 0 ? 'up' : 'down'">{{ signed(it.bidChange) }}%</td>
            <td v-if="tab === 'boom' || tab === 'net'" :class="it.bidAmt > 0 ? 'up' : 'dim'">{{ amtText(it.bidAmt) }}<span v-if="tab === 'boom' && it.yestBidAmt" class="yest-bid-amt" :title="'昨日竞价额 ' + amtText(it.yestBidAmt)">昨{{ amtText(it.yestBidAmt) }}</span></td>
            <td v-else :class="it.bidSealAmt > 0 ? 'up' : 'dim'">{{ yi(it.bidSealAmt) }}</td>
            <td v-if="tab === 'boom'" :class="it.bidRatioYest ? (it.bidRatioYest >= 2 ? 'ratio-hot' : it.bidRatioYest >= 1.5 ? 'ratio-warm' : '') : 'dim'">{{ it.bidRatioYest ? it.bidRatioYest.toFixed(2) + 'x' : '-' }}</td>
            <td class="dim">{{ it.bidTurnover !== null && it.bidTurnover !== undefined ? it.bidTurnover.toFixed(2) + '%' : '-' }}</td>
            <td v-if="tab === 'net'" :class="it.bidNetAmt > 0 ? 'up' : it.bidNetAmt < 0 ? 'down' : 'dim'">{{ yi(it.bidNetAmt) }}</td>
            <td v-if="tab !== 'boom'"><span v-if="it.limitBoards > 0" class="lb-badge">{{ it.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td class="dim">{{ fmtMv(it.floatMv) }}</td>
            <td class="concept-cell dim" :title="it.board"><span v-if="it.board" class="concept-clamp">{{ conceptText(it.board) }}</span><span v-else class="dim">-</span></td>
</tr>
          <!-- 2026-09-29: 空表必须说清"为什么空"，不许用别的榜单顶上 -->
          <tr v-if="!sealList.length">
            <td colspan="9" class="snap-empty">
              {{ tab === 'boom' ? '今日竞价爆量榜暂无数据（9:15-9:30 竞价时段可用；开盘啦 09:25:30 后出数）'
                 : tab === 'net' ? '今日竞价净额榜暂无数据（开盘啦约 09:25:30 产出；不为空时按"实际流通"口径计算抢筹强度）'
                 : '今日竞价委买榜暂无数据（9:15-9:30 竞价时段可用）' }}
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 竞价封单: 连续5日(默认, 多列并排) + 单日三层榜 -->
      <template v-else-if="tab === 's3'">
      <!-- 2026-09-30 主人: 顶部口径提示一并去掉(不再占用版面)。
           口径仍记录在代码里: backend/app/services/bid_seal_daily.py 模块 docstring
           (展示集合 = 9:15/9:20/9:25 任一时点涨停; 各列即该时点, 第二行是该时点封单额)。 -->


      <!-- 连续多日(多列并排): 每列一个交易日, 列内 9:25 / 9:20 / 9:15 三列(当日多一列涨幅)。
           列数不写死 —— 由 --msd-cols 取 dailyDays.length 传入(2026-09-30 由 5 日改 4 日) -->
      <div v-if="s3Mode === 'multi'" class="msd-wrap">
        <div v-if="dailyLoading && !dailyDays.length" class="loading-placeholder">
          <div class="spinner"></div><div>加载中...</div>
        </div>
        <div v-else-if="!dailyDays.length" class="msd-empty-all">暂无连续封单数据（非交易日或上游不可用）</div>
        <!-- --msd-cols 供 CSS 算列数与最小宽度 ⇒ 天数变了布局自动跟随, 不会与 DAILY_DAYS 脱钩 -->
        <div v-else class="msd-grid" :style="{ '--msd-cols': dailyDays.length || DAILY_DAYS }">
          <div v-for="day in dailyDays" :key="day.date" class="msd-col">
            <div class="msd-head">
              <div class="msd-date">{{ day.date }}</div>
              <div class="msd-sum">
                <span>一字:<b class="msd-num">{{ day.yizi }}</b>个</span>
                <span class="msd-sep">|</span>
                <span>封单:<b class="msd-num">{{ sealDailyText(day.sealTotal) }}</b></span>
              </div>
              <!-- 2026-09-30 主人明确: 去掉环比那行 —— 表头只保留 日期 / 一字+封单 两行(与模板一致) -->
            </div>
            <div class="msd-sub" :class="{ 'msd-nochg': !chgShown(day) }">
              <!-- 每条记录只有 3 列(当日多一列涨幅), 列 = 时点本身(2026-09-30 主人明确映射):
                    9:25 列 → 股票名称 + 该时点封单额
                    9:20 列 → 概念     + 该时点封单额
                    9:15 列 → 几板     + 该时点封单额 -->
              <span class="msd-cell">9:25</span>
              <span class="msd-cell">9:20</span>
              <span class="msd-cell">9:15</span>
              <!-- 涨幅列只在当日出现(历史列整列去掉, 省宽度) -->
              <span v-if="chgShown(day)" class="msd-cell">涨幅</span>
            </div>
            <div class="msd-body">
              <div
                v-for="it in msdRows(day.rows)"
                :key="it.code"
                class="msd-row"
                :class="{ 'msd-nochg': !chgShown(day) }"
                :title="layerTip(it.layer)"
              >
                <!-- 第 1 行: 名称(9:25列) / 概念(9:20列) / 板(9:15列) —— 各占时点列, 不额外增列 -->
                <span class="msd-cell msd-name" @click="linkToSoftware(it.code, it.name)">{{ it.name || it.code }}</span>
                <span class="msd-cell msd-concept" :title="it.board">{{ firstConcept(it.board) }}</span>
                <span class="msd-cell msd-lb">{{ boardLabel(it.limitTimes) }}</span>
                <span v-if="chgShown(day)" class="msd-cell msd-blank"></span>
                <!-- 第 2 行: 三个时点的封单金额(正落在上方同名列下) + 涨幅 -->
                <span class="msd-cell msd-p25">{{ sealDailyText(it.v9_25) }}</span>
                <span class="msd-cell msd-p20">{{ sealDailyText(it.v9_20) }}</span>
                <span class="msd-cell msd-p15">{{ sealDailyText(it.v9_15) }}</span>
                <span v-if="chgShown(day)" class="msd-cell msd-chg" :class="chgCls(it)">{{ chgText(it) }}</span>
              </div>
              <div v-if="!day.rows.length" class="msd-empty">该交易日无涨停封单</div>
              <!-- 2026-09-30 v4.11.83 实机体检修复: 截断提示(与 HistoryView「已显示 N / total」同一惯例) -->
              <div v-else-if="day.rows.length > MAX_ROWS_MSD" class="msd-more">
                仅显示前 {{ MAX_ROWS_MSD }} / {{ day.rows.length }} 只
              </div>
            </div>
          </div>
        </div>
      </div>

      <template v-else>
      <div v-if="pending25" class="s3-hint s3-hint-soft">
        <i class="fa fa-info-circle"></i> 9:25 定格<b>尚未落库</b>（{{ pendingTip }}）；
        此刻 9:15 / 9:20 两列已是<b>今日</b>真实数据，到点会自动刷新出 9:25。
      </div>
      <div v-if="sealMissing" class="s3-hint">
        <i class="fa fa-info-circle"></i> 该日期<b>封单额与竞价额均未采集</b>（历史委托数据不提供，无法回填），
        下个交易日 9:15 / 9:20 / 9:25 自动采集后生效。涨幅/概念/流通市值不受影响。
      </div>
      <div v-else-if="s3Degraded" class="s3-hint s3-hint-soft">
        <i class="fa fa-info-circle"></i> 今日<b>弱市（涨停少或无）</b>，自动降级展示 <b>9:25 涨幅≥{{ s3DegradedThreshold }}% 的异动票</b>（数据基于竞价额，前缀「竞」）；
        状态列显示「9:25强势异动」即来源此层。
      </div>
      <div v-else-if="sealDegraded" class="s3-hint s3-hint-soft">
        <i class="fa fa-info-circle"></i> 历史日期无封单采集，当前显示<b>竞价额</b>（前缀「竞」）作为强弱参考；<b>下个交易日 9:15/9:20/9:25 采集后显示真实封单额</b>。
      </div>
      <table class="stock-table s3-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: s3Sort.keyOf('code') }" @click="s3Sort.onSort('code', 'string')">名称<span class="sort-ind">{{ s3Sort.ind('name') }}</span></th>
            <th class="tp-th tp-th-25 sortable" :class="{ active: s3Sort.keyOf('seal25'), 'th-pending': pending25 }" :title="pending25 ? pendingTip : ''" @click="s3Sort.onSort('seal25')">9:25<span v-if="pending25" class="tp-pending">待定格</span><span class="sort-ind">{{ s3Sort.ind('seal25') }}</span></th>
            <th class="tp-th tp-th-20 sortable" :class="{ active: s3Sort.keyOf('seal20') }" @click="s3Sort.onSort('seal20')">9:20<span class="sort-ind">{{ s3Sort.ind('seal20') }}</span></th>
            <th class="tp-th tp-th-15 sortable" :class="{ active: s3Sort.keyOf('seal15') }" @click="s3Sort.onSort('seal15')">9:15<span class="sort-ind">{{ s3Sort.ind('seal15') }}</span></th>
            <th class="tp-th tp-th-25 sortable" :class="{ active: s3Sort.keyOf('bidChg25') }" @click="s3Sort.onSort('bidChg25')">竞涨<span class="sort-ind">{{ s3Sort.ind('bidChg25') }}</span></th>
            <th class="sortable" :class="{ active: s3Sort.keyOf('bidTurnover') }" @click="s3Sort.onSort('bidTurnover')">竞换<span class="sort-ind">{{ s3Sort.ind('bidTurnover') }}</span></th>
            <th class="sortable" :class="{ active: s3Sort.keyOf('real_change') }" @click="s3Sort.onSort('real_change')">现涨<span class="sort-ind">{{ s3Sort.ind('real_change') }}</span></th>
            <th class="sortable" :class="{ active: s3Sort.keyOf('float_mv') }" @click="s3Sort.onSort('float_mv')">流通(亿)<span class="sort-ind">{{ s3Sort.ind('float_mv') }}</span></th>
            <th class="board-col sortable" :class="{ active: s3Sort.keyOf('board') }" @click="s3Sort.onSort('board', 'string')">概念<span class="sort-ind">{{ s3Sort.ind('board') }}</span></th>
</tr>
        </thead>
        <tbody>
          <tr v-for="it in s3Sort.sorted(s3List, s3Val)" :key="it.code">
            <td class="stock-info-cell" @click="linkToSoftware(it.code, it.name)">
            <div class="stock-code-row"><span class="stock-code">{{ it.code }}</span></div>
            <div class="stock-name-row"><span class="pool-hover-wrap"><span class="stock-name">{{ it.name || it.code }}</span><PoolHoverBtn :item="it" /></span></div>
            <div v-if="yidongTag(it.code)" class="yd-badge-row"><span class="yd-badge" :title="yidongTagTitle(it.code)">{{ yidongTag(it.code) }}</span></div>
          </td>
            <td class="seal-col seal-col-25" :class="{ 'cell-pending': pending25 }">{{ tpSeal25(it) }}</td>
            <td class="seal-col seal-col-20">{{ tpSeal(it, '9_20') }}</td>
            <td class="seal-col seal-col-15">{{ tpSeal(it, '9_15') }}</td>
            <td class="tp-th-25 chg-col" :class="[tpChgCls(it, '9_25'), { 'cell-pending': pending25 }]">{{ tpChg25(it) }}</td>
            <td class="dim">{{ it.bidTurnover !== null && it.bidTurnover !== undefined ? it.bidTurnover.toFixed(2) + '%' : '-' }}</td>
            <td class="real-chg-col" :class="realChgCls(it)">{{ realChg(it) }}</td>
            <td class="dim">{{ mvText(it) }}</td>
            <td class="concept-cell" :title="it.board"><span v-if="it.board" class="concept-clamp">{{ conceptText(it.board) }}</span><span v-else class="dim">-</span></td>
</tr>
          <tr v-if="!s3List.length">
            <td colspan="9" class="snap-empty">该日期暂无封单榜单（可能：今日无涨停/数据采集中/非交易日）；下个交易日 9:15/9:20/9:25 采集后生效</td>
          </tr>
        </tbody>
      </table>
      </template>
      </template>

      <!-- 竞价抢筹(上下双表: 上 9:20-9:25 / 下 最后1秒 9:24-9:25, 对标短线侠) -->
      <div v-else-if="tab === 'qc'" class="qc-dual">
        <div class="qc-panel">
          <div class="qc-panel-title">
            <i class="fa fa-clock-o"></i> 9:20 - 9:25 竞价涨幅
            <span class="qc-mode-switch">
              <button :class="{ active: qc20Mode === 'chg' }" @click="qc20Mode = 'chg'">涨幅抢筹</button>
              <button :class="{ active: qc20Mode === 'amt' }" @click="qc20Mode = 'amt'">竞额抢筹</button>
            </span>
          </div>
          <div ref="qcScroll1" class="qc-table-scroll">
          <table class="stock-table">
            <thead>
              <tr>
<th class="sortable" :class="{ active: qcSort.keyOf('code') }" @click="qcSort.onSort('code', 'string')">名称<span class="sort-ind">{{ qcSort.ind('name') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('realChange') }" @click="qcSort.onSort('realChange')">现涨<span class="sort-ind">{{ qcSort.ind('realChange') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('bidAmt') }" @click="qcSort.onSort('bidAmt')">竞额<span class="sort-ind">{{ qcSort.ind('bidAmt') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf(qc20Mode === 'amt' ? 'qcDelta' : 'qcDeltaChg') }" @click="qcSort.onSort(qc20Mode === 'amt' ? 'qcDelta' : 'qcDeltaChg')">抢筹幅度<span class="sort-ind">{{ qcSort.ind(qc20Mode === 'amt' ? 'qcDelta' : 'qcDeltaChg') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('bidRatio') }" @click="qcSort.onSort('bidRatio')">竞额/昨比<span class="sort-ind">{{ qcSort.ind('bidRatio') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('bidChange') }" @click="qcSort.onSort('bidChange')">竞涨<span class="sort-ind">{{ qcSort.ind('bidChange') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('bidTurnover') }" @click="qcSort.onSort('bidTurnover')">竞换<span class="sort-ind">{{ qcSort.ind('bidTurnover') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('floatMv') }" @click="qcSort.onSort('floatMv')">流通(亿)<span class="sort-ind">{{ qcSort.ind('floatMv') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('board') }" @click="qcSort.onSort('board', 'string')">概念<span class="sort-ind">{{ qcSort.ind('board') }}</span></th>
</tr>
            </thead>
            <tbody>
              <tr v-for="q in qcSort.sorted(qc20Mode === 'amt' ? qcList : qcChgList)" :key="'a' + q.code + qc20Mode">
                <td class="stock-info-cell" @click="linkToSoftware(q.code)">
            <div class="stock-code-row"><span class="stock-code">{{ q.code }}</span></div>
            <div class="stock-name-row"><span class="pool-hover-wrap"><span class="stock-name">{{ q.name }}</span><PoolHoverBtn :item="q" /></span></div>
            <div v-if="yidongTag(q.code)" class="yd-badge-row"><span class="yd-badge" :title="yidongTagTitle(q.code)">{{ yidongTag(q.code) }}</span></div>
          </td>
                <td :class="q.realChange > 0 ? 'up' : q.realChange < 0 ? 'down' : 'dim'">{{ q.realChange !== null && q.realChange !== undefined ? signed(q.realChange) + '%' : '-' }}</td>
                <td :class="q.bidAmt > 0 ? 'up' : 'dim'">{{ amtText(q.bidAmt) }}</td>
                <td :class="(qc20Mode === 'amt' ? q.qcDelta : q.qcDeltaChg) > 0 ? 'up' : (qc20Mode === 'amt' ? q.qcDelta : q.qcDeltaChg) < 0 ? 'down' : 'dim'"><b>{{ signed(qc20Mode === 'amt' ? q.qcDelta : q.qcDeltaChg) }}%</b></td>
                <td :class="q.bidRatio !== null && q.bidRatio !== undefined ? (q.bidRatio >= 20 ? 'ratio-hot' : q.bidRatio >= 10 ? 'ratio-warm' : '') : 'dim'" :title="q.bidRatio !== null && q.bidRatio !== undefined ? ('今日竞价额 ÷ 昨日全天成交额 = ' + q.bidRatio.toFixed(2) + '%') : ''">{{ q.bidRatio !== null && q.bidRatio !== undefined ? q.bidRatio.toFixed(2) + '%' : '-' }}</td>
                <td :class="q.bidChange > 0 ? 'up' : q.bidChange < 0 ? 'down' : 'dim'">{{ signed(q.bidChange) }}%</td>
                <td class="dim">{{ q.bidTurnover !== null && q.bidTurnover !== undefined ? q.bidTurnover.toFixed(2) + '%' : '-' }}</td>
                <td>{{ q.floatMv ? (q.floatMv / 1e8).toFixed(1) + '亿' : '-' }}</td>
                <td class="concept-cell dim qc-board" :title="q.board"><span v-if="q.board" class="concept-clamp">{{ conceptText(q.board) }}</span><span v-else class="dim">-</span></td>
</tr>
              <tr v-if="(qc20Mode === 'amt' ? qcList : qcChgList).length === 0">
                <td colspan="9" class="snap-empty">{{ qc20Mode === 'amt' ? '9:20-9:25 竞额抢筹数据 9:15-9:30 竞价时段可用' : '9:20-9:25 涨幅抢筹数据 9:20/9:25 快照采集后可用' }}</td>
              </tr>
            </tbody>
          </table>
          </div><!-- /.qc-table-scroll -->
        </div>
        <div class="qc-panel">
          <div class="qc-panel-title"><i class="fa fa-bolt"></i> 最后一秒竞价涨幅</div>
          <div ref="qcScroll2" class="qc-table-scroll">
          <table class="stock-table">
            <thead>
              <tr>
<th class="sortable" :class="{ active: qcLastSort.keyOf('code') }" @click="qcLastSort.onSort('code', 'string')">名称<span class="sort-ind">{{ qcLastSort.ind('name') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('realChange') }" @click="qcLastSort.onSort('realChange')">现涨<span class="sort-ind">{{ qcLastSort.ind('realChange') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('bidAmt') }" @click="qcLastSort.onSort('bidAmt')">竞额<span class="sort-ind">{{ qcLastSort.ind('bidAmt') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('qcDeltaLast') }" @click="qcLastSort.onSort('qcDeltaLast')">抢筹幅度<span class="sort-ind">{{ qcLastSort.ind('qcDeltaLast') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('bidRatio') }" @click="qcLastSort.onSort('bidRatio')">竞额/昨比<span class="sort-ind">{{ qcLastSort.ind('bidRatio') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('bidChange') }" @click="qcLastSort.onSort('bidChange')">竞涨<span class="sort-ind">{{ qcLastSort.ind('bidChange') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('bidTurnover') }" @click="qcLastSort.onSort('bidTurnover')">竞换<span class="sort-ind">{{ qcLastSort.ind('bidTurnover') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('floatMv') }" @click="qcLastSort.onSort('floatMv')">流通(亿)<span class="sort-ind">{{ qcLastSort.ind('floatMv') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('board') }" @click="qcLastSort.onSort('board', 'string')">概念<span class="sort-ind">{{ qcLastSort.ind('board') }}</span></th>
</tr>
            </thead>
            <tbody>
              <tr v-for="q in qcLastSort.sorted(qcLastList)" :key="'b' + q.code">
                <td class="stock-info-cell" @click="linkToSoftware(q.code)">
            <div class="stock-code-row"><span class="stock-code">{{ q.code }}</span></div>
            <div class="stock-name-row"><span class="pool-hover-wrap"><span class="stock-name">{{ q.name }}</span><PoolHoverBtn :item="q" /></span></div>
            <div v-if="yidongTag(q.code)" class="yd-badge-row"><span class="yd-badge" :title="yidongTagTitle(q.code)">{{ yidongTag(q.code) }}</span></div>
          </td>
                <td :class="q.realChange > 0 ? 'up' : q.realChange < 0 ? 'down' : 'dim'">{{ q.realChange !== null && q.realChange !== undefined ? signed(q.realChange) + '%' : '-' }}</td>
                <td :class="q.bidAmt > 0 ? 'up' : 'dim'">{{ amtText(q.bidAmt) }}</td>
                <td :class="q.qcDeltaLast > 0 ? 'up' : q.qcDeltaLast < 0 ? 'down' : 'dim'"><b>{{ signed(q.qcDeltaLast) }}%</b></td>
                <td :class="q.bidRatio !== null && q.bidRatio !== undefined ? (q.bidRatio >= 20 ? 'ratio-hot' : q.bidRatio >= 10 ? 'ratio-warm' : '') : 'dim'" :title="q.bidRatio !== null && q.bidRatio !== undefined ? ('今日竞价额 ÷ 昨日全天成交额 = ' + q.bidRatio.toFixed(2) + '%') : ''">{{ q.bidRatio !== null && q.bidRatio !== undefined ? q.bidRatio.toFixed(2) + '%' : '-' }}</td>
                <td :class="q.bidChange > 0 ? 'up' : q.bidChange < 0 ? 'down' : 'dim'">{{ signed(q.bidChange) }}%</td>
                <td class="dim">{{ q.bidTurnover !== null && q.bidTurnover !== undefined ? q.bidTurnover.toFixed(2) + '%' : '-' }}</td>
                <td>{{ q.floatMv ? (q.floatMv / 1e8).toFixed(1) + '亿' : '-' }}</td>
                <td class="concept-cell dim qc-board" :title="q.board"><span v-if="q.board" class="concept-clamp">{{ conceptText(q.board) }}</span><span v-else class="dim">-</span></td>
</tr>
              <tr v-if="!qcLastList.length">
                <td colspan="9" class="snap-empty">最后一秒数据 9:25 后可用（9:24 时点采集后）</td>
              </tr>
            </tbody>
          </table>
          </div><!-- /.qc-table-scroll -->
        </div>
      </div>

      <!-- 昨日涨停(今日竞价表现) -->
      <table v-else-if="tab === 'yestZt'" class="stock-table">
        <thead>
          <tr>
<th class="sortable" :class="{ active: yestZtSort.keyOf('code') }" @click="yestZtSort.onSort('code', 'string')">名称<span class="sort-ind">{{ yestZtSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('limitUpDays') }" @click="yestZtSort.onSort('limitUpDays')">连板<span class="sort-ind">{{ yestZtSort.ind('limitUpDays') }}</span></th>
            <th v-if="!isSmall" class="sortable" :class="{ active: yestZtSort.keyOf('floatMv') }" @click="yestZtSort.onSort('floatMv')">流通(亿)<span class="sort-ind">{{ yestZtSort.ind('floatMv') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('change') }" @click="yestZtSort.onSort('change')">现涨<span class="sort-ind">{{ yestZtSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('bidChange') }" @click="yestZtSort.onSort('bidChange')">竞涨<span class="sort-ind">{{ yestZtSort.ind('bidChange') }}</span></th>
            <th v-if="!isSmall" class="sortable" :class="{ active: yestZtSort.keyOf('bidTurnover') }" @click="yestZtSort.onSort('bidTurnover')">竞换<span class="sort-ind">{{ yestZtSort.ind('bidTurnover') }}</span></th>
            <th v-if="!isSmall" class="sortable" :class="{ active: yestZtSort.keyOf('bidAmt') }" @click="yestZtSort.onSort('bidAmt')">竞额(亿)<span class="sort-ind">{{ yestZtSort.ind('bidAmt') }}</span></th>
            <th class="sortable reason-th" :class="{ active: yestZtSort.keyOf('reason') }" @click="yestZtSort.onSort('reason', 'string')">涨停原因<span class="sort-ind">{{ yestZtSort.ind('reason') }}</span></th>
            <th v-if="!isSmall" class="sortable" :class="{ active: yestZtSort.keyOf('board') }" @click="yestZtSort.onSort('board', 'string')">概念<span class="sort-ind">{{ yestZtSort.ind('board') }}</span></th>
</tr>
        </thead>
        <tbody>
          <tr v-for="z in yestZtSort.sorted(yestZtList)" :key="z.code">
            <td class="stock-info-cell" @click="linkToSoftware(z.code)">
            <div class="stock-code-row"><span class="stock-code">{{ z.code }}</span></div>
            <div class="stock-name-row">
<span class="pool-hover-wrap"><span class="stock-name">{{ z.name }}</span><PoolHoverBtn :item="z" /></span>
            <span v-if="z.stillLimit" class="lb-badge">连板</span>
</div>
            <div v-if="yidongTag(z.code)" class="yd-badge-row"><span class="yd-badge" :title="yidongTagTitle(z.code)">{{ yidongTag(z.code) }}</span></div>
          </td>
            <td><span v-if="z.limitUpDays > 0" class="lb-badge">{{ z.limitUpDays }}板</span><span v-else class="dim">-</span></td>
            <td v-if="!isSmall" class="dim">{{ fmtMv(z.floatMv) }}</td>
            <td :class="z.change > 0 ? 'up' : z.change < 0 ? 'down' : 'dim'">{{ z.change !== null && z.change !== undefined ? signed(z.change) + '%' : '-' }}</td>
            <td :class="z.bidChange > 0 ? 'up' : z.bidChange < 0 ? 'down' : 'dim'">{{ z.bidChange !== null && z.bidChange !== undefined ? signed(z.bidChange) + '%' : '-' }}</td>
            <td v-if="!isSmall">{{ z.bidTurnover !== null && z.bidTurnover !== undefined ? z.bidTurnover.toFixed(2) + '%' : '-' }}</td>
            <td v-if="!isSmall">{{ z.bidAmt ? amtText(z.bidAmt) : '-' }}</td>
            <td class="reason-cell" :title="reasonTitle(z)"><span class="reason-clamp">{{ reasonOf(z) || '-' }}</span></td>
            <td v-if="!isSmall" class="concept-cell dim" :title="z.board"><span v-if="z.board" class="concept-clamp">{{ conceptText(z.board) }}</span><span v-else class="dim">-</span></td>
</tr>
          <!-- 2026-09-30: 空表必须说清"为什么空"(原来纯空白, 用户看不出是没数据还是坏了) -->
          <tr v-if="!yestZtList.length">
            <td :colspan="ztColSpan" class="snap-empty">{{ emptyHint() }}</td>
          </tr>
        </tbody>
      </table>

      <!-- 昨断板(昨涨停今断) -->
      <table v-else-if="tab === 'yestBroken'" class="stock-table">
        <thead>
          <tr>
<th class="sortable" :class="{ active: yestBrokenSort.keyOf('code') }" @click="yestBrokenSort.onSort('code', 'string')">名称<span class="sort-ind">{{ yestBrokenSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('yestChange') }" @click="yestBrokenSort.onSort('yestChange')">昨竞价<span class="sort-ind">{{ yestBrokenSort.ind('yestChange') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('change') }" @click="yestBrokenSort.onSort('change')">现涨<span class="sort-ind">{{ yestBrokenSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('bidChange') }" @click="yestBrokenSort.onSort('bidChange')">竞涨<span class="sort-ind">{{ yestBrokenSort.ind('bidChange') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('bidAmt') }" @click="yestBrokenSort.onSort('bidAmt')">竞额(亿)<span class="sort-ind">{{ yestBrokenSort.ind('bidAmt') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('bidTurnover') }" @click="yestBrokenSort.onSort('bidTurnover')">竞换<span class="sort-ind">{{ yestBrokenSort.ind('bidTurnover') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('floatMv') }" @click="yestBrokenSort.onSort('floatMv')">流通(亿)<span class="sort-ind">{{ yestBrokenSort.ind('floatMv') }}</span></th>
            <th class="sortable reason-th" :class="{ active: yestBrokenSort.keyOf('reason') }" @click="yestBrokenSort.onSort('reason', 'string')">涨停原因<span class="sort-ind">{{ yestBrokenSort.ind('reason') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('board') }" @click="yestBrokenSort.onSort('board', 'string')">概念<span class="sort-ind">{{ yestBrokenSort.ind('board') }}</span></th>
</tr>
        </thead>
        <tbody>
          <tr v-for="b2 in yestBrokenSort.sorted(yestBrokenList)" :key="b2.code">
            <td class="stock-info-cell" @click="linkToSoftware(b2.code)">
            <div class="stock-code-row"><span class="stock-code">{{ b2.code }}</span></div>
            <div class="stock-name-row"><span class="pool-hover-wrap"><span class="stock-name">{{ b2.name }}</span><PoolHoverBtn :item="b2" /></span></div>
            <div v-if="yidongTag(b2.code)" class="yd-badge-row"><span class="yd-badge" :title="yidongTagTitle(b2.code)">{{ yidongTag(b2.code) }}</span></div>
          </td>
            <td :class="b2.yestChange > 0 ? 'up' : b2.yestChange < 0 ? 'down' : 'dim'">{{ b2.yestChange !== null && b2.yestChange !== undefined ? signed(b2.yestChange) + '%' : '-' }}</td>
            <td :class="b2.change > 0 ? 'up' : b2.change < 0 ? 'down' : 'dim'">{{ b2.change !== null && b2.change !== undefined ? signed(b2.change) + '%' : '-' }}</td>
            <td :class="b2.bidChange > 0 ? 'up' : b2.bidChange < 0 ? 'down' : 'dim'">{{ b2.bidChange !== null && b2.bidChange !== undefined ? signed(b2.bidChange) + '%' : '-' }}</td>
            <td>{{ b2.bidAmt ? amtText(b2.bidAmt) : '-' }}</td>
            <td>{{ b2.bidTurnover !== null && b2.bidTurnover !== undefined ? b2.bidTurnover.toFixed(2) + '%' : '-' }}</td>
            <td class="dim">{{ fmtMv(b2.floatMv) }}</td>
            <td class="reason-cell" :title="reasonTitle(b2)"><span class="reason-clamp">{{ reasonOf(b2) || '-' }}</span></td>
            <td class="concept-cell dim" :title="b2.board"><span v-if="b2.board" class="concept-clamp">{{ conceptText(b2.board) }}</span><span v-else class="dim">-</span></td>
</tr>
          <tr v-if="!yestBrokenList.length">
            <td colspan="9" class="snap-empty">{{ emptyHint() }}</td>
          </tr>
        </tbody>
      </table>

      <!-- ★ 2026-09-29 主人要求: 原「昨上榜(龙虎榜)」表已下线 —— 该数据另有独立页 /lhb
           (views/LhbView.vue, 2026-09-27 拆出; 竞价时段本就为空, 收盘后才有数据)。 -->

      <!-- 炸板(昨/今) - 昨炸板:连板, 今炸板:炸板时间+涨停时间 -->
      <table v-else-if="tab === 'brokenYest' || tab === 'brokenToday'" class="stock-table broken-table" :class="tab === 'brokenToday' ? 'broken-today' : 'broken-yest'">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: brokenSort.keyOf('code') }" @click="brokenSort.onSort('code', 'string')">名称<span class="sort-ind">{{ brokenSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('change') }" @click="brokenSort.onSort('change')">现涨<span class="sort-ind">{{ brokenSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('bidChange') }" @click="brokenSort.onSort('bidChange')">竞涨<span class="sort-ind">{{ brokenSort.ind('bidChange') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('bidTurnover') }" @click="brokenSort.onSort('bidTurnover')">竞换<span class="sort-ind">{{ brokenSort.ind('bidTurnover') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('floatMv') }" @click="brokenSort.onSort('floatMv')">流通(亿)<span class="sort-ind">{{ brokenSort.ind('floatMv') }}</span></th>
            <th v-if="tab === 'brokenYest'" class="sortable" :class="{ active: brokenSort.keyOf('limitUpDays') }" @click="brokenSort.onSort('limitUpDays')">连板<span class="sort-ind">{{ brokenSort.ind('limitUpDays') }}</span></th>
            <th v-else-if="tab === 'brokenToday'" class="sortable" :class="{ active: brokenSort.keyOf('firstBreak') }" @click="brokenSort.onSort('firstBreak')">炸板时间<span class="sort-ind">{{ brokenSort.ind('firstBreak') }}</span></th>
            <th v-if="tab === 'brokenToday'" class="sortable" :class="{ active: brokenSort.keyOf('firstLimitUp') }" @click="brokenSort.onSort('firstLimitUp')">涨停时间<span class="sort-ind">{{ brokenSort.ind('firstLimitUp') }}</span></th>
            <th class="sortable reason-th" :class="{ active: brokenSort.keyOf('reason') }" @click="brokenSort.onSort('reason', 'string')">涨停原因<span class="sort-ind">{{ brokenSort.ind('reason') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('board') }" @click="brokenSort.onSort('board', 'string')">概念<span class="sort-ind">{{ brokenSort.ind('board') }}</span></th>
</tr>
        </thead>
        <tbody>
          <tr v-for="b in brokenSort.sorted(brokenList)" :key="b.code">
            <td class="stock-info-cell" @click="linkToSoftware(b.code)">
            <div class="stock-code-row"><span class="stock-code">{{ b.code }}</span></div>
            <div class="stock-name-row"><span class="pool-hover-wrap"><span class="stock-name">{{ b.name }}</span><PoolHoverBtn :item="b" /></span></div>
            <div v-if="yidongTag(b.code)" class="yd-badge-row"><span class="yd-badge" :title="yidongTagTitle(b.code)">{{ yidongTag(b.code) }}</span></div>
          </td>
            <td :class="b.change > 0 ? 'up' : 'down'">{{ signed(b.change) }}%</td>
            <td :class="b.bidChange > 0 ? 'up' : b.bidChange < 0 ? 'down' : 'dim'">{{ b.bidChange !== null && b.bidChange !== undefined ? signed(b.bidChange) + '%' : '-' }}</td>
            <td>{{ b.bidTurnover !== null && b.bidTurnover !== undefined ? b.bidTurnover.toFixed(2) + '%' : '-' }}</td>
            <td class="dim">{{ fmtMv(b.floatMv) }}</td>
            <td v-if="tab === 'brokenYest'"><span v-if="b.limitUpDays > 0" class="lb-badge">{{ b.limitUpDays }}板</span><span v-else class="dim">-</span></td>
            <td v-else-if="tab === 'brokenToday'" class="dim">{{ fmtT(b.firstBreak) }}</td>
            <td v-if="tab === 'brokenToday'" class="dim">{{ fmtT(b.firstLimitUp) }}</td>
            <td class="reason-cell" :title="reasonTitle(b)"><span class="reason-clamp">{{ reasonOf(b) || '-' }}</span></td>
            <td class="concept-cell dim" :title="b.board"><span v-if="b.board" class="concept-clamp">{{ conceptText(b.board) }}</span><span v-else class="dim">-</span></td>
</tr>
          <!-- 今炸板多一列(涨停时间) ⇒ colspan 10 / 9 -->
          <tr v-if="!brokenList.length">
            <td :colspan="tab === 'brokenToday' ? 10 : 9" class="snap-empty">{{ emptyHint() }}</td>
          </tr>
        </tbody>
      </table>

    </div>

    <!-- ★ 2026-09-29 主人要求: 涨停原因改为**列内直接展示**(不再弹窗) ⇒ 原 .reason-modal 弹窗已删除。
         完整原因与「补充说明」改放单元格 title 悬停(见 reasonTitle())。 -->
</template>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePolling } from '../composables/usePolling'
// 2026-10-04 手机端批次2: ≤480 隐藏次要列(见 composables/useMediaQuery.js)。
//   🔴 必须在 setup 顶层调用(同 usePolling 的纪律), 否则卸载时不解绑 ⇒ 切页仍触发。
import { useIsSmall } from '../composables/useMediaQuery'
// 2026-09-29: 去掉 kplLhb —— 「昨上榜」板块已下线(龙虎榜另有独立页 /lhb)
import { kplBidSeal, kplBidNet, kplBidBoom, kplBidQiangcang, kplBroken, kplYestBroken, kplYestZt } from '../api/kpl'
import { auctionOverview, bidSnapshot3points, bidSealDaily } from '../api/stats'
import { trackUsage } from '../api/activity'
import { linkToSoftware } from '../utils/tdx'
import { todayBj } from '../utils/time'
import { showToast } from '../utils/toast'
import { useSortable } from '../composables/useSortable'
import { useYidongMonitor } from '../composables/useYidongMonitor'
import { amtText, fmtT, sealDailyText, signed, yi } from '../utils/format'
import VipGate from '../components/VipGate.vue'
import PoolHoverBtn from '../components/PoolHoverBtn.vue'
// 2026-09-27 v4.11.63《移动端清单》§二·4: 数据更新时刻（与页头时钟区分开）
import DataStamp from '../components/DataStamp.vue'
import { useDataStamp } from '../composables/useDataStamp'

// 配额用尽(2026-09-21): 接口返回 429 code=quota_exceeded 时置 true → 显示配额引导页
const quotaExceeded = ref(false)
const gateRef = ref(null)
const { yidongTag, yidongTagTitle, refreshYidongCodes } = useYidongMonitor()
const tab = ref('s3')   // 默认选中三时点封单
const isSmall = useIsSmall()   // ≤480: 昨涨停表隐藏 流通/竞换/竞额/概念 四列, 保住原因
/** 空表提示的 colspan 必须跟着实际列数走, 否则空表那一格宽度不对(fixed/auto 布局都会歪) */
const ztColSpan = computed(() => (isSmall.value ? 5 : 9))
const sealRaw = ref([])
const bidNetList = ref([])   // 2026-08-18: 竞价净额专用(开盘啦 Type2 竞价>1000万), 空时回退封单列表
const boomList = ref([])
// 2026-09-29: lhbList 已随「昨上榜」板块一起下线
const brokenYestList = ref([])
const brokenTodayList = ref([])
const s3List = ref([])        // 三时点封单榜(后端已按三层排序)
// 2026-09-29 主人拍板: 竞价封单页新增「连续多日」视图(N 列并排, 每列=一个交易日)。
//   数据源与单日榜**不同**: 走猫爪 daily_auc_fd 分时封单(/api/stats/bid-seal-daily),
//   历史交易日与今日同等可用; 单日榜读自采库(历史日 9:15/9:20 两列恒空)。
// 🔴 2026-09-30 主人: 5 日 → **4 日**(页面紧凑化)。模式名随之由 'multi5' 改为 'multi' ——
//   原名带天数, 天数一改就成假名; 列数现在**由 .msd-grid 的 --msd-cols 按实际天数自动取**,
//   前后端都不再需要"天数"这个常量以外的第二份记录。
const s3Mode = ref('multi')    // 'multi' 连续多日 | 'day' 单日三层榜
const dailyDays = ref([])      // [{date, yizi, sealTotal, diff, diffPct, prevDate, rows[]}]
const dailyLoading = ref(false)
const DAILY_DAYS = 4           // 连续交易日数(上限受后端 MAX_DAYS=10 夹取)
// 2026-09-30 v4.11.83 实机体检修复: 多日对比表此前**无行数上限**(实机实测单次 308 行 / 2053 个单元格
//   常驻, 每票 4 日 × 8 格 = 32 个 span) —— 与 AipickReport.MAX_ROWS=150、HistoryView「加载更多」
//   同一惯例, 这里限 300 行/日并列(只截**渲染**, 不改后端取数与排序)。
const MAX_ROWS_MSD = 300
function msdRows(rows) { return (rows || []).slice(0, MAX_ROWS_MSD) }
// 🔴 2026-09-29 (B2): 「9:25 定格是否已落库」由**后端**给(`/api/stats/bid-snapshot-3points`
//   的 frozen/freezeAt, 判据 = auction_snapshot.has_today_snapshot(), 即选股闸门那把"快照维")。
//   前端**禁止**用 09:26:30 这类固定时刻自己猜 —— 2026-09-16「两个用户拿到昨天名单」就是
//   前端按固定时刻猜定格造成的。默认 true(不显示占位) 以免首屏闪烁。
const frozen = ref(true)
const freezeAt = ref('')
const pending25 = computed(() => !frozen.value)
const pendingTip = computed(() =>
  '9:25 定格尚未落库' + (freezeAt.value ? '（预计 ' + freezeAt.value + '，等猫爪竞价字段出满）' : ''))

/** 吸收后端给的"定格状态"; 缺字段按已定格处理(与旧版后端兼容, 不显示占位) */
function setFrozen(r) {
  frozen.value = !(r && r.frozen === false)
  freezeAt.value = (r && r.freezeAt) || ''
}

// 封单数据缺失提示: 榜有数据但所有 bid_buy_amt/bid_amt 都为 0(完全无数据)
const sealMissing = computed(() => {
  if (!s3List.value.length) return false
  return s3List.value.every(it => {
    const pts = it.points || {}
    return ['9_15', '9_20', '9_25'].every(tp => {
      const p = pts[tp]
      return !(p && (p.bid_buy_amt || p.bid_amt))
    })
  })
})
// 降级提示: 有数据但全部来自竞价额兜底(历史无封单采集)
const sealDegraded = computed(() => {
  if (!s3List.value.length) return false
  const anySeal = s3List.value.some(it => {
    const pts = it.points || {}
    return ['9_15', '9_20', '9_25'].some(tp => pts[tp] && pts[tp].bid_buy_amt)
  })
  return !anySeal
})
// 弱市降级提示: 三层涨停榜为空, 自动降级到 layer=4(≥5%) 或 layer=5(≥3%) 的异动票
const s3Degraded = computed(() => {
  if (!s3List.value.length) return false
  return s3List.value.every(it => it.degraded)
})
const s3DegradedThreshold = computed(() => {
  if (!s3Degraded.value) return 0
  return s3List.value.some(it => it.layer === 5) ? 3 : 5
})
const qcList = ref([])
const qcChgList = ref([])     // 涨幅抢筹(9:25涨幅−9:20涨幅, 全市场快照)
const qcLastList = ref([])
const yestZtList = ref([])
const yestBrokenList = ref([])
const loading = ref(true)
const qc20Mode = ref('chg')   // 左表口径: amt=竞额抢筹(开盘啦净额) / chg=涨幅抢筹(快照涨幅差) 默认涨幅抢筹
const datePicker = ref('')    // 用户选的日期(空=实时)

// 2026-09-27 v4.11.63《移动端清单》§二·4: 当前 Tab 的数据取回时刻。
// ⚠️ 分解赋值：模板只自动解包顶层 ref，写 `ds.at` 会渲染出 ref 对象。
const { at: dataAt, ok: dataOk, stale: dataStale, mark: markData } = useDataStamp()
const dataDate = ref('')      // 后端实际返回的数据日期(可能被对齐)
// 🔴 2026-09-30 主人「数据日期规矩」: **日期决定权收归后端**(唯一真相源)。
//   规矩: 交易日 **09:00 前** → 上一交易日; **09:00 起** → 当天(当天没数据就**空**, 绝不回退);
//         非交易日 → 最近一个有数据的交易日。后端 `auction-overview` 下发 serveDate/serveMode。
//   ★ 为什么必须收归后端: 原先前端拿 `ov.todayTradeDay` **自己拼回退** —— 于是"日期框显示
//     dataDate(库里最新有数据的一天)、实际请求 servedDate(今天)"变成两套口径, 盘前必然打架。
//     2026-09-30 凌晨实测(主人截图): 框里写 2026/09/29、实际查 2026/09/30、页面全空 —— 就是这个。
//   ★ 另: 回退**绝不写 datePicker**(否则把"实时模式"永久降级成"历史回看模式", 轮询一起被关,
//     2026-09-29 早盘现场实锤过), 只写下面这个由后端下发的 serveDate。
const serveDate = ref('')     // 后端下发的"该请求哪一天"
const serveMode = ref('')     // explicit | prev | today | offday(供文案/诊断)
const serveResolved = ref(false)   // 是否已从后端拿到过 serveDate(旧后端兼容用)

/** 本次请求应使用的数据日期: 用户显式选择 > 后端下发 > 今天 */
function servedDate() {
  return datePicker.value || serveDate.value || todayBj()
}

/** 日期框显示值 —— **必须等于实际请求的日期**, 否则又会出现"框里 A、实际查 B"的误导。 */
const displayDate = computed(() => datePicker.value || serveDate.value || dataDate.value)

/**
 * 吸收后端下发的服务日期。返回 true = 服务日期**变了**(调用方需重新加载)。
 * 旧后端(无 serveDate 字段)⇒ 保持原状、不猜(不倒退成前端自算日期的老路)。
 */
function applyServeDate(ov) {
  const next = String((ov && ov.serveDate) || '')
  if (!next) return false
  const mode = String((ov && ov.serveMode) || '')
  const first = !serveResolved.value
  const changed = next !== serveDate.value
  serveResolved.value = true
  serveMode.value = mode
  serveDate.value = next
  if (!changed) return false
  if (!first) {
    // 只在**切换那一刻**提示; 首次进页面不弹(否则每次打开都弹一次)
    if (mode === 'prev') {
      showToast(`开盘前（09:00 前）显示上一交易日 ${next} 的数据`, 'info')
    } else if (mode === 'offday') {
      showToast(`今日无交易（${todayBj()}），显示最近交易日 ${next} 的数据`, 'info')
    }
  }
  return true
}

/**
 * 轮询时重取后端服务日期 —— 09:00 / 09:15 这些**分界点**上页面要自动切过去。
 * 返回 true = 服务日期变了(调用方需重新加载)。
 */
async function refreshServeDate() {
  if (datePicker.value) return false          // 用户显式选日(历史回看): 不干预
  try {
    const ov = await withTimeout(auctionOverview(''))
    return applyServeDate(ov)
  } catch (e) {
    return false                              // 失败静默: 保留原状态
  }
}

/**
 * 空表提示 —— 说清"为什么空"。
 * 🔴 2026-09-30 主人「数据日期规矩」的展示侧: 交易日 **09:00 起**拿不到当天数据时,
 *   按铁律**不回退**上一交易日 ⇒ 页面必然是空的(09:00~09:15 更是天天如此)。
 *   不写清原因, 用户只会看到"一张空表 + 一个写着昨天日期的框"(正是主人截图那张)。
 */
function emptyHint() {
  const md = serveMode.value
  if (md === 'today') {
    return `今日（${todayBj()}）暂无数据：当天数据尚未产出；按规矩不使用上一交易日的数据，稍后自动刷新`
  }
  if (md === 'prev') return `上一交易日（${servedDate()}）暂无该数据`
  if (md === 'offday') return `最近交易日（${servedDate()}）暂无该数据`
  return `该日期（${servedDate() || todayBj()}）暂无数据`
}

// 各表独立排序实例
const sealSort = useSortable()
const s3Sort = useSortable()
const qcSort = useSortable()
const qcLastSort = useSortable()
const yestZtSort = useSortable()
const yestBrokenSort = useSortable()
// 2026-09-29: lhbSort 已随「昨上榜」板块一起下线
const brokenSort = useSortable()

// ---- 三时点封单榜单元格(拆分涨幅/封单两列) ----
// 实时涨幅: 后端从开盘啦 fetch_bid_seal 叠加 realChange(元)
function realChg(it) {
  const v = it.real_change
  if (v === null || v === undefined || isNaN(v)) return '-'
  return signed(v) + '%'
}
function realChgCls(it) {
  const v = it.real_change
  if (v === null || v === undefined || isNaN(v)) return 'dim'
  return v > 0 ? 'up' : v < 0 ? 'down' : 'dim'
}
// 涨幅: 按时点色系分涨(亮)/跌(暗)/0(灰), 避开 A 股红绿
function tpChg(it, tp) {
  const p = it.points && it.points[tp]
  if (!p || p.bid_change === null || p.bid_change === undefined) return '-'
  return signed(p.bid_change) + '%'
}
function tpChgCls(it, tp) {
  const p = it.points && it.points[tp]
  if (!p || p.bid_change === null || p.bid_change === undefined) return 'dim'
  // 按时点配色: tp-th-15 chg-up / tp-th-15 chg-down / dim
  const v = tp.slice(2) // "15" / "20" / "25"
  return p.bid_change > 0 ? 'chg-up-' + v : p.bid_change < 0 ? 'chg-dn-' + v : 'dim-' + v
}
// 该时点的强度金额(元): 真实封单额 bid_buy_amt 优先; 历史缺封单时用竞价额
// bid_amt(万→元) 兜底, 并标记 isBidAmt 区分。返回 {v, isBidAmt} 或 null
function sealVal(it, tp) {
  const p = it.points && it.points[tp]
  if (!p || p.bid_change === null || p.bid_change === undefined) return null
  if (p.bid_buy_amt) return { v: p.bid_buy_amt, isBidAmt: false }
  if (p.bid_amt) return { v: p.bid_amt * 1e4, isBidAmt: true }
  return null
}
// 封单额: > 1亿 显示「X.X 亿」, 否则「X.X 万」; 封单缺失用竞价额兜底(前缀「竞」)
function tpSeal(it, tp) {
  const s = sealVal(it, tp)
  if (!s) return '-'
  const v = s.v
  const txt = v >= 1e8 ? (v / 1e8).toFixed(2) + ' 亿' : (v / 1e4).toFixed(0) + ' 万'
  return s.isBidAmt ? '竞 ' + txt : txt
}
// 🔴 2026-09-29 (B2): 9:25 定格尚未落库时, 整列显示「待定格」(与"该票没值"的「-」区分开)。
//   判据是后端的 frozen —— 不猜时刻; 已定格/FROZEN 时行为与改动前逐字相同。
function tpSeal25(it) {
  return pending25.value ? '待定格' : tpSeal(it, '9_25')
}
function tpChg25(it) {
  return pending25.value ? '' : tpChg(it, '9_25')
}

// 竞价封单表排序取值: 按列 key 返回原始数值(null/undefined 自动排末尾)
function s3Val(it, key) {
  if (key === 'seal25' || key === 'seal20' || key === 'seal15') {
    const s = sealVal(it, '9_' + key.slice(4))
    return s ? s.v : null
  }
  if (key === 'bidChg25') {
    const p = it.points && it.points['9_25']
    return p && p.bid_change !== undefined && p.bid_change !== null ? p.bid_change : null
  }
  if (key === 'real_change') return it.real_change
  if (key === 'float_mv') {
    const p = it.points && (it.points['9_25'] || it.points['9_20'] || it.points['9_15'])
    return p && p.float_mv ? p.float_mv : null
  }
  if (key === 'trend') {
    const m = sealMode(it)
    if (!m) return null
    const order = { '持续加单': 9, '尾盘回补': 8, '尾盘加单': 7, '加单后走平': 6, '封单走平': 5, '撤单后走平': 4, '尾盘撤单': 3, '冲高回落': 2, '持续撤单': 1 }
    return order[m.label] ?? 0
  }
  return it[key]
}

// 加单趋势(整行): 9:15→9:20→9:25 强度额走势, 判断加单/撤单模式
function sealMode(it) {
  const s15 = sealVal(it, '9_15')
  const s20 = sealVal(it, '9_20')
  const s25 = sealVal(it, '9_25')
  const seg = (a, b) => { if (!a || !b) return null; const r = b.v / a.v; return r >= 1.1 ? 1 : r <= 0.9 ? -1 : 0 }
  const s1 = seg(s15, s20)
  const s2 = seg(s20, s25)
  if (s1 === null || s2 === null) return null
  // 降级说明: 历史日期无封单采集, 用竞价额(bid_amt)兜底计算
  const alt = (s15 && s15.isBidAmt) || (s20 && s20.isBidAmt) || (s25 && s25.isBidAmt) ? ' (按竞价额)' : ''
  if (s1 === 1 && s2 === 1) return { label: '持续加单', cls: 'strong', tip: '9:15→9:20→9:25持续放大, 主力不断加单' + alt }
  if (s1 === -1 && s2 === 1) return { label: '尾盘回补', cls: 'mid', tip: '9:20 撤单后 9:25 重新加单, 关注是否回封' + alt }
  if (s1 === 1 && s2 === -1) return { label: '冲高回落', cls: 'weak', tip: '9:20 加单后 9:25 大幅撤单, 警惕炸板' + alt }
  if (s1 === -1 && s2 === -1) return { label: '持续撤单', cls: 'danger', tip: '9:15→9:20→9:25持续减少, 开板风险高' + alt }
  if (s1 === 1 && s2 === 0) return { label: '加单后走平', cls: 'mid', tip: '9:20 加单, 9:25 持平' + alt }
  if (s1 === 0 && s2 === 1) return { label: '尾盘加单', cls: 'mid', tip: '9:25 相对 9:20 加单, 尾盘资金抢筹' + alt }
  if (s1 === 0 && s2 === -1) return { label: '尾盘撤单', cls: 'weak', tip: '9:25 相对 9:20 撤单' + alt }
  if (s1 === -1 && s2 === 0) return { label: '撤单后走平', cls: 'weak', tip: '9:20 撤单, 9:25 持平' + alt }
  return { label: '封单走平', cls: 'flat', tip: '三个时点封单额基本持平' + alt }
}
function mvText(it) {
  const p = it.points && (it.points['9_25'] || it.points['9_20'] || it.points['9_15'])
  return p && p.float_mv ? (p.float_mv / 1e8).toFixed(1) : '-'
}
// 纯文本概念: 只显示前 2 个, 用换行符分隔(配合 white-space:pre-line 渲染)
function conceptText(b) {
  if (!b) return ''
  const parts = String(b).split(/[、,，]/).map(s => s.trim()).filter(Boolean).slice(0, 2)
  return parts.join('\n')
}

// ---- 连续多日封单视图(2026-09-29) ----
// 封单额格式化见 utils/format.js 的 sealDailyText(纯函数, 已单测):
//   ≥1亿 → "80.8亿"(整数亿去尾随 .0, 如 7.7e9 → "77亿"); 否则 → "9116万"; 0/空 → "-"

/**
 * 概念: **只取核心的第一个**, 展示在股票名称右侧(2026-09-30 主人明确)。
 * 源串形如「并购重组,文化传媒」→ 取「并购重组」。
 */
function firstConcept(b) {
  if (!b) return ''
  return String(b).split(/[、,，]/).map(s => s.trim()).filter(Boolean)[0] || ''
}

/**
 * 「涨幅」子列**只出现在当日那一列**; 历史列整列去掉(2026-09-30 主人明确: 省排版面积)。
 *
 * 判据 = 该列日期 == 北京今天 **且**该列已有数据:
 *   · 竞价时段 / 盘后 → 今日列有数据 ⇒ 只有它带「涨幅」子列, 其余 4 列少一列、更省宽度 ✓
 *   · 盘前(今日尚未采集) 或 用户选了历史末日 → 没有任何列带涨幅 ✓
 *     (不做"占位显示 -", 否则会留一列全是 - 的空列, 正是主人要省掉的)
 */
function chgShown(day) {
  if (!day) return false
  return day.date === todayBj() && (day.rows || []).length > 0
}

function chgText(it) {
  return (it.chg > 0 ? '+' : '') + Number(it.chg).toFixed(2) + '%'
}

function chgCls(it) {
  if (it.chg > 0) return 'up'
  return it.chg < 0 ? 'down' : 'dim'
}

/** 连板数 → 标签文案: 1 → 「首板」, N → 「N板」, 0/未知 → 不显示 */
function boardLabel(n) {
  const v = Number(n) || 0
  if (!v) return ''
  return v === 1 ? '首板' : v + '板'
}

/**
 * 上榜原因(展示口径 = 9:15/9:20/9:25 任一时点涨停 —— 2026-09-30 主人明确规则)。
 * 层1 9:25 涨停 / 层2 9:20 涨停后回落 / 层3 仅 9:15 涨停。
 * 挂在行 title 上: 层2/层3 里有涨幅为负的炸板票, 不解释会被当成脏数据。
 */
function layerTip(layer) {
  if (layer === 1) return '9:25 竞价涨停（计入「一字」与封单总额）'
  if (layer === 2) return '9:20 竞价涨停、9:25 已回落（展示但不计入「一字」）'
  if (layer === 3) return '仅 9:15 竞价涨停（展示但不计入「一字」）'
  return ''
}

/**
 * 拉取连续 N 日封单。失败**不清空**已有数据(与其余 tab 同一纪律: 宁可显示上一次成功数据)。
 * 后端已按 历史日 1800s / 含当日 60s 缓存, 故此处在轮询里反复调也不会打爆上游配额。
 */
async function loadDailySeal(dt) {
  dailyLoading.value = true
  try {
    const r = await withTimeout(bidSealDaily(dt || todayBj(), DAILY_DAYS))
    dailyDays.value = (r && r.days) || []
    return true
  } catch (e) {
    return false
  } finally {
    dailyLoading.value = false
  }
}

// 2026-09-30 主人: 不再展示单日榜 ⇒ 切换入口与 setS3Mode 一并移除,
//   s3Mode 固定为 'multi'(下方单日榜分支保留但不可达, 需彻底删除时另行处理)。

// 流通市值(元) → "xx.x亿" (2026-08-17 竞价异动各 tab 统一流通列)
function fmtMv(v) {
  if (!v) return '-'
  return (v / 1e8).toFixed(1) + '亿'
}

// 切 Tab 时清掉排序(避免跨表残留的 key 干扰)
// ★ 2026-10-01: 支持深链到指定子版块（首页宫格「竞价异动」格的子项 → /auction?tab=xxx），
//   照 StockView 的 `?t=` 既有范式。落地即命中该版块，用户不用再找。
const route = useRoute()
// 2026-10-05 (L3): 独立路由 /auction 时本轮是 h1；被首页内嵌时降 h2（避免同页两个 h1）
const isStandalone = computed(() => route.name === 'auction')
const TAB_KEYS = ['s3', 'boom', 'qc', 'seal', 'net',
                  'brokenToday', 'yestZt', 'yestBroken', 'brokenYest']
function applyTabFromQuery() {
  const t = String(route.query.tab || '')
  if (TAB_KEYS.indexOf(t) >= 0 && tab.value !== t) switchTab(t)
}
watch(() => route.query.tab, () => applyTabFromQuery())

function switchTab(t) {
  tab.value = t
  // 2026-09-22 v4.11.35: 用户主动点竞价异动的 Tab 算一次使用(轮询走 ensureTabData, 不经此处)
  trackUsage('auction')
  sealSort.clear(); s3Sort.clear(); qcSort.clear(); qcLastSort.clear(); yestZtSort.clear()
  yestBrokenSort.clear(); brokenSort.clear()
  // 2026-08-18 性能优化: 切 tab 按需加载该 tab 数据(首次进入才拉)
  ensureTabData(t)
}

// 竞价委买/爆量/净额共用表格: 数据源按 Tab 切换
const sealList = computed(() => {
  if (tab.value === 'boom') return boomList.value
  if (tab.value === 'net') {
    // 🔴 2026-09-29 铁律「零值不得回退昨日」的展示侧: 净额榜为空就**显示空**(表格给出"暂无/
    //   待产出"说明)，不再回退渲染封单列表 —— 那会把"封单额"冒充成"净额"显示在同一列名下，
    //   是最容易被误读的一类"凑数据"。
    return [...(bidNetList.value || [])].sort((a, b) => (b.bidNetAmt || 0) - (a.bidNetAmt || 0))
  }
  return sealRaw.value
})

// 炸板: 昨/今 按 Tab 切换
const brokenList = computed(() => (tab.value === 'brokenYest' ? brokenYestList.value : brokenTodayList.value))

// ---- 涨停原因(列内直接展示) ----
// ★ 2026-09-29 主人要求: 不再点「查看」弹窗, 直接在「涨停原因」列展示, 并**精简**为最多 2 行
//   (CSS 截断, 见 .reason-clamp); 完整原因 + 补充说明放 title 悬停。
//   原弹窗实现(reasonModal / showReason)已随之删除 —— 需要时从 git 历史取回。
function reasonOf(it) {
  return (it && (it.reason || it.reason_text || it.reasonText)) || ''
}
function reasonTitle(it) {
  const t = reasonOf(it)
  const ex = (it && (it.reason_extra || it.extra)) || ''
  return (t || '暂无涨停原因数据') + (ex ? '\n补充：' + ex : '')
}

// 单个接口最多等 15s, 超时返回空对象(不让某个慢接口拖垮整页加载)
// 2026-09-28: 12000 → 15000。冷取数最坏实测 11.5s(结果层 + 上游层同时失效),
// 12s 会在临界点把结果截断成空列表(= 用户看到"点了没数据"), 留足余量。
// 2026-10-03：15s → 6s。原来一个挂住的请求会让首屏等满 15s 才渲染（主人反馈"要很久才有数据"）
function withTimeout(p, ms = 6000) {
  return Promise.race([
    p,
    new Promise(res => setTimeout(() => res({ list: [], list20: [], list20Chg: [], listLast: [], days: [] }), ms))
  ])
}

async function loadAll(fromUser = false) {
  const dt = datePicker.value
  loading.value = true
  try {
    // 2026-09-27 优化: 先拉 overview 判断是否需要回退, 避免周末重复拉两次 bidSnapshot
    const ov = await withTimeout(auctionOverview(''))
    let useDate = dt
    if (!dt) {
      // 实时模式: 用**后端下发**的服务日期(唯一真相源) —— 前端不再自算回退。
      // (原实现是"前端拿 ov.todayTradeDay 自己判", 与日期框显示两套口径, 见 serveDate 定义处)
      applyServeDate(ov)
      useDate = serveDate.value
    }
    const d = (ov.days && ov.days.length ? ov.days[0].date : '') || useDate || ''
    dataDate.value = d || useDate || ''
    if (useDate && fromUser) {
      if (!dataDate.value || dataDate.value !== useDate) {
        showToast(`数据日期 ${dataDate.value}${dataDate.value !== useDate ? '（非交易日自动对齐）' : ''}`, 'info')
      }
    }
    // 🔴 2026-10-03 首屏加速（主人批准）：原来「快照3点 → 当前tab」是**串行** ⇒ 总时长相加，
    //    最慢一环决定用户何时能看到数据（实测抢筹均 1.04s、最坏 4.51s）。
    //    改为并发 + 到达即渲染：先放开 loading 占位，两张表各自拿到数据就各自显示。
    loading.value = false
    await Promise.allSettled([
      withTimeout(bidSnapshot3points(useDate || todayBj())).then((s3) => {
        s3List.value = s3.list || []
        setFrozen(s3)
        loadedTabs.add('s3')
      }),
      ensureTabData(tab.value, { silent: true }),
    ])
  } catch (e) { } finally {
    loading.value = false
  }
}

// ===== 2026-08-18 按需加载: 切 tab 才拉该 tab 接口(避免 10 接口全量并行, 冷缓存首屏 20s+) =====
const loadedTabs = new Set()   // 已加载数据的 tab(跨日期失效: datePicker 变化时清)
const tabLoading = new Set()

// 返回值语义(2026-09-05 配合 usePolling 退避): true=成功/无需请求, false=请求失败
// 失败时**不清空**已有 list → 页面保留上一次成功数据, 不出现空白
async function ensureTabData(t) {
  // 🔴 2026-09-29: 统一走 servedDate()(用户选定 > 自动回退日 > 今天) —— **所有子 tab**
  //   (封单/爆量/净额/抢筹/昨涨停/昨断板/龙虎榜/今炸板/昨炸板/三时点榜)共用这一行,
  //   所以这一处修好即全体修好; 原实现取 datePicker.value ⇒ 盘前回退被写进去后,
  //   全部 tab 整天都在请求昨天。
  const dt = servedDate()
  if (t === 's3') {
    // 2026-09-29: 「连续多日」走另一接口(猫爪分时), 与单日榜**各自独立**加载 ——
    //   不能共用 loadedTabs 标记(否则切模式后不刷新); 后端有缓存, 每次轮询拉都便宜。
    if (s3Mode.value === 'multi') {
      if (tabLoading.has('s3')) return true
      tabLoading.add('s3')
      try {
        const ok = await loadDailySeal(dt)
        if (ok) { loadedTabs.add('s3'); markData() }
        return ok
      } finally {
        tabLoading.delete('s3')
      }
    }
    // 三时点榜随 loadAll 加载; 轮询时若已清标记则重新拉(实时刷新)
    if (!loadedTabs.has('s3')) {
      tabLoading.add('s3')
      try {
        const r = await withTimeout(bidSnapshot3points(dt || todayBj()))
        s3List.value = (r && r.list) || []
        setFrozen(r)
        loadedTabs.add('s3')
        markData()          // 只在成功路径推进（失败/降级/配额拦截一律不推进）
        return true
      } catch (e) { /* 静默; 保留上次 s3List */ return false } finally {
        tabLoading.delete('s3')
      }
    }
    return true
  }
  if (loadedTabs.has(t)) return true
  if (tabLoading.has(t)) return true   // 正在加载中: 跳过而非失败, 不触发退避
  tabLoading.add(t)
  try {
    let r = {}
    switch (t) {
      case 'seal': r = await withTimeout(kplBidSeal(dt)); sealRaw.value = (r && r.list) || []; break
      case 'boom': r = await withTimeout(kplBidBoom(dt)); boomList.value = (r && r.list) || []; break
      case 'net': r = await withTimeout(kplBidNet(dt)); bidNetList.value = (r && r.list) || []; break
      case 'qc': r = await withTimeout(kplBidQiangcang(dt));
        qcList.value = (r && r.list20) || []; qcChgList.value = (r && r.list20Chg) || []; qcLastList.value = (r && r.listLast) || []; break
      case 'yestZt': r = await withTimeout(kplYestZt(dt)); yestZtList.value = (r && r.list) || []; break
      case 'yestBroken': r = await withTimeout(kplYestBroken(dt)); yestBrokenList.value = (r && r.list) || []; break
      // 2026-09-29: 原 case 'lhb' 已随「昨上榜」板块下线(龙虎榜走独立页 /lhb)
      // 2026-09-27 v4.11.67: 原来 `dt ? '' : 'yesterday'` —— 只要带上 date(非交易日自动回退 /
      // 手动回看) 就丢掉 day=yesterday, 后端只能按 date 取"当日炸板" ⇒ 非交易日的
      // 「昨炸板」与「今炸板」显示同一批股票(主人反馈的"没定格")。改为始终带 day=yesterday:
      // 后端语义 = "date 这一天的昨炸板"(股票池=date 的前一交易日, 字段=date)。
      case 'brokenYest': r = await withTimeout(kplBroken('yesterday', dt)); brokenYestList.value = (r && r.list) || []; break
      case 'brokenToday': r = await withTimeout(kplBroken('', dt)); brokenTodayList.value = (r && r.list) || []; break
      default: return true
    }
    loadedTabs.add(t)
    markData()          // 只在成功路径推进（失败保留旧数据时，时间戳原地不动 = 告警）
    return true
  } catch (e) {
    // 单 tab 失败不影响其他; 各 list 保留上次成功值(不清空 → 页面不空白)
    handleQuota(e)
    return false
  } finally {
    tabLoading.delete(t)
  }
}

/** 配额超限(429)识别: 置位后整页显示配额引导(2026-09-21 会员体系) */
function handleQuota(e) {
  if (e && (e.code === 'quota_exceeded' || e.status === 429)) {
    quotaExceeded.value = true
    // 2026-09-22 v4.11.35: 被配额拦下 → 记一次「拦截」(运营的转化线索)
    trackUsage('auction', true)
    nextTick(() => {
      if (gateRef.value && gateRef.value.openQuota) {
        gateRef.value.openQuota({
          feature: e.feature || 'auction',
          feature_label: e.feature_label || '竞价异动',
          limit: e.limit || 0,
          used: e.used || 0,
        })
      }
    })
    return true
  }
  return false
}

// ===== 2026-09-05 P0: 静默刷新 / 手动刷新 / 失败提示 =====// 静默刷新态: 轮询或单 Tab 刷新时在标题栏角落显示小 spinner, 不遮挡内容
// (区别于首屏 loading 的大块占位 —— 那个只在首次/切日时全量加载出现)
const silentRefreshing = ref(false)
// 连续失败次数(usePolling 维护), >0 时在角落提示"稍后重试", 数据仍保留旧值
const pollFailCount = ref(0)
// usePolling 句柄( onMounted 中赋值; 手动刷新成功后 resetBackoff 恢复正常频率)
let polling = null

/** 手动刷新当前 Tab: 清该 tab 标记重拉(不影响其他 tab) */
async function refreshCurrentTab() {
  if (silentRefreshing.value) return
  silentRefreshing.value = true
  try {
    loadedTabs.delete(tab.value)
    const ok = await ensureTabData(tab.value)
    if (!ok) showToast('刷新失败，已保留上次数据', 'warning')
    else polling.resetBackoff()      // 手动成功 → 立即恢复正常轮询频率
  } finally {
    silentRefreshing.value = false
  }
}

/** 手动全局刷新: 清全部标记 + 重跑 loadAll(等价于重新进入页面) */
async function refreshAll() {
  if (loading.value || silentRefreshing.value) return
  silentRefreshing.value = true
  try {
    loadedTabs.clear()
    await loadAll(true)
    polling.resetBackoff()
    showToast('已刷新全部数据', 'success')
  } catch (e) {
    showToast('刷新失败，已保留上次数据', 'warning')
  } finally {
    silentRefreshing.value = false
  }
}

function clearDate() {
  datePicker.value = ''
  dataDate.value = ''
  // 2026-09-30: 回实时模式 ⇒ 清掉后端下发的服务日期, 让下一轮 applyServeDate 重新取;
  //   同时重置"首次"标记, 避免切回实时时又弹一次提示。
  serveDate.value = ''
  serveResolved.value = false
  loadedTabs.clear()   // 2026-08-18: 日期变化需重新加载各 tab
  dailyDays.value = []  // 2026-09-29: 连续封单是"以某日为末的5日窗口", 换日期必须重取
  loadAll(true)
}

// 2026-08-18 性能优化: 日期选择变化 → 清已加载标记, 重新按需加载
function onDateChange(e) {
  datePicker.value = e.target.value
  loadedTabs.clear()
  dailyDays.value = []  // 同上: 换末日后旧的 5 日窗口整体失效
  loadAll(true)
}

onMounted(() => {
  loadAll()
  refreshYidongCodes()
  // 深链放在 loadAll 之后: 先走既有首屏加载, 再切到指定子版块(switchTab 内部按需取数)
  applyTabFromQuery()
})

// ⚠️ 2026-09-27 v4.11.59 修: 轮询注册从 onMounted 回调整体搬到 setup 顶层。
//    原因见 EmConceptPanel.vue 处的长注释 —— Vue 调用 mounted 回调时 currentInstance
//    为 null, usePolling 的 onBeforeUnmount 会**静默注册失败**, 定时器与
//    visibilitychange 监听永不清理(离开页面后仍按 30s 打 10 个竞价接口)。
//    首拉已由上面 onMounted 的 loadAll() 完成, 且下方 fn 首跳会立刻重拉一次,
//    故这里保留原行为不变(不额外加 immediate:false, 以免改变竞价时段的取数时机)。
// 历史回看模式暂停实时刷新(每分钟拉历史无意义)
// 2026-08-18 性能优化: 轮询只刷新当前 tab(清标记重拉), 不再 10 接口全量
// 2026-08-24 主人要求: 盘中实时刷新间隔 60s → 30s(配合后端快照 TTL 降到 60s)
// 2026-09-05 P0: 轮询开启失败退避 — fn 返回成败, 连续失败时下一次间隔按 2^n 递增
// (30s → 60s → 120s … 上限 5min), 成功一次即重置。避免服务端抖动/限流时被前端
// 以固定 30s 持续打; 失败期间 ensureTabData 不清空 list, 页面保留上次成功数据。
// 2026-10-09: 间隔改为**动态** —— 09:26:00~09:30:00 是竞价爆量「等 9_25 定格」窗口
//   (定格实测 09:26:39 落库), 期间提速到 10s, 与后端服务层/接口层 TTL 同步提速,
//   把定格数据可见时刻从 ≈09:27:20 提前到 ≈09:26:50; 窗口外维持 30s ⇒ 全天请求量
//   基本不变(只在 4 分钟内变密)。判据与后端 kpl.bid_boom_hot_window() 同口径。
function bidBoomHotWindow () {
  const d = new Date()
  const sec = d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds()
  return sec >= 9 * 3600 + 26 * 60 && sec <= 9 * 3600 + 30 * 60
}
polling = usePolling(async () => {
  // 历史回看模式(**用户显式选了日期**)不轮询; 实时模式必须继续轮询,
  // 否则今日快照落库后页面不会自愈(2026-09-29 修: 原实现把回退写进 datePicker, 连轮询一起关了)。
  if (datePicker.value) return true
  // 2026-09-30: 每轮重取**后端下发**的服务日期 —— 09:00 / 09:15 这类分界点要自动切换,
  //   同时天然覆盖"今日快照落库后自愈"(serveDate 由后端算, 前端不再自己判条件)。
  //   overview 侧有 60s 缓存, 每 30s 打一次很廉价。
  await refreshServeDate()
  silentRefreshing.value = true
  try {
    loadedTabs.clear()
    const ok = await ensureTabData(tab.value, { silent: true })
    pollFailCount.value = ok ? 0 : (pollFailCount.value + 1)
    return ok
  } finally {
    silentRefreshing.value = false
  }
}, () => (bidBoomHotWindow() ? 10000 : 30000), { backoff: true })
</script>

<style scoped>
/* 2026-08-18: 竞价成交额列内嵌"昨日竞价额"小字 */
.yest-bid-amt {
  display: block; font-size: var(--fs-xs); color: var(--text-muted, var(--text-dim));
  font-weight: 400; line-height: 1.2;
}
.page-back { color: var(--text-muted); cursor: pointer; font-size: var(--fs-sm); margin-bottom: var(--s3); display: inline-block; }
.page-back:hover { color: var(--star); }
.auc-head { display: flex; align-items: center; gap: var(--s3); flex-wrap: wrap; margin-bottom: var(--s3); }
.auc-head-spacer { flex: 1; }
.auc-head .rot-date { flex-shrink: 0; }
.auc-head .rot-reset-btn { flex-shrink: 0; }
.auc-title { font-size: var(--fs-2xl); font-weight: 700; color: var(--warn-text); }
.auc-title .fa { color: var(--star); }
/* 2026-09-05 P0: 静默刷新指示(角落, 不遮挡内容) —— 金色与页面主色一致 */
.auc-silent-loading {
  color: var(--star); font-size: var(--fs-sm); line-height: 1;
  display: inline-flex; align-items: center;
  opacity: .9;
}
/* 2026-09-29 (B2): 9:25 定格未落库时的占位样式(灰色斜体, 与真值的红黄区分开) */
.tp-pending {
  display: inline-block; margin-left: var(--s1); padding: 0 var(--s1);
  border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 400;
  color: var(--text-muted, var(--text-dim)); background: rgba(136, 153, 170, .15);
  vertical-align: middle;
}
.th-pending { color: var(--text-muted, var(--text-dim)); }
.s3-table td.cell-pending { color: var(--text-muted, var(--text-dim)); font-style: italic; }
/* 连续失败提示: 用橙黄警示(避开绿色 —— A 股语境绿=跌) */
.auc-poll-warn {
  color: #ff9d3c; font-size: var(--fs-xs); line-height: 1;
  display: inline-flex; align-items: center; gap: var(--s1);
  background: rgba(255, 157, 60, .12);
  border: 1px solid rgba(255, 157, 60, .3);
  border-radius: var(--r-sm); padding: 2px var(--s2);
}
/* 刷新按钮禁用态: 降低透明度, 明确不可点 */
.auc-head .rot-reset-btn:disabled { opacity: .45; cursor: not-allowed; }
.auc-sub { color: var(--text-muted); font-size: var(--fs-sm); }
.auc-tabs {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s2);
  margin-bottom: var(--s4);
  padding: var(--s2);
  background: var(--bg-panel-solid);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-lg);
  position: sticky;
  top: 0;
  z-index: 10;
  /* 手机端 10 个 tab 横向可滚动, 不挤压截断 */
  overflow-x: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
  -webkit-overflow-scrolling: touch;
}
.auc-tabs::-webkit-scrollbar { display: none; width: 0; height: 0; }
/* 5-6: Tab 分组分隔线(竞价口径 | 今日 | 昨日表现) */
.auc-tab-group {
  display: inline-block;
  color: var(--text-muted);
  opacity: 0.5;
  font-size: var(--fs-xs);
  line-height: 1;
  flex: 0 0 auto;
  user-select: none;
  margin: 0 1px;
}
.auc-tab {
  padding: var(--s2) var(--s2);
  border-radius: var(--r-sm);
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: background-color 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
  font-weight: 400;
  white-space: nowrap;
  position: relative;
  flex: 1;
  text-align: center;
  min-width: 0;
}
.auc-tab:hover {
  background: rgba(255, 180, 0, 0.08);
  color: var(--accent);
}
.auc-tab.active {
  background: linear-gradient(135deg, rgba(255, 180, 0, 0.22), rgba(255, 140, 50, 0.18));
  color: var(--gold);
  font-weight: 600;
  box-shadow: var(--sh-1);
}
.auc-panel { background: var(--bg-subtle); border: 1px solid var(--border-soft); border-radius: var(--r-lg); padding: var(--s4); }
/* 竞价抢筹左右双表 */
.qc-dual { display: flex; flex-direction: column; gap: var(--s2); }
.qc-panel { width: 100%; background: var(--bg-subtle); border: 1px solid var(--border-soft); border-radius: var(--r-lg); padding: var(--s2); overflow: visible; display: flex; flex-direction: column; gap: var(--s2); }
.qc-table-scroll { overflow-x: auto; overflow-y: auto; max-height: 480px; scrollbar-width: none; -ms-overflow-style: none; border: 1px solid var(--border-soft); border-radius: var(--r-md); background: var(--bg-subtle); }
.qc-table-scroll::-webkit-scrollbar { display: none; width: 0; height: 0; }
/* 2026-09-20 视觉减噪: qc 抢筹双表 sticky 表头统一红底白字(与全局一致) */
.qc-table-scroll .stock-table thead th { position: sticky; top: 0; z-index: 8; background: var(--accent-deep2); border-bottom: none; }
.qc-panel-title { font-size: var(--fs-base); font-weight: 700; color: var(--warn-text); display: flex; align-items: center; gap: var(--s2); }
.qc-mode-switch { display: inline-flex; gap: var(--s1); margin-left: auto; }
.qc-mode-switch button {
  font-size: var(--fs-xs); padding: 2px var(--s2); border-radius: var(--r-sm); cursor: pointer;
  background: var(--border-soft); border: 1px solid rgba(255,255,255,0.18);
  color: var(--text-muted); transition: background-color 0.2s, border-color 0.2s, color 0.2s;
}
.qc-mode-switch button.active { background: rgba(255,180,0,0.18); border-color: var(--star); color: var(--gold); font-weight: 600; }
.qc-mode-switch button:hover { border-color: var(--star); color: var(--warn-text); }
/* 2026-08-20 所有竞价异动表格紧凑型: 缩小padding/font, 统一宽度策略 */
.auc-panel table.stock-table,
.qc-panel table.stock-table,
#auction-list .stock-table {
  table-layout: fixed;
  width: 100%;
  min-width: 0;
}
.auc-panel .stock-table th, .auc-panel .stock-table td,
.qc-panel .stock-table th, .qc-panel .stock-table td {
  padding: var(--s1) 2px !important;
  font-size: var(--fs-xs) !important;
  vertical-align: middle;
}
.auc-panel .stock-table th,
.qc-panel .stock-table th {
  padding: var(--s2) 2px !important;
  font-size: var(--fs-xs) !important;
  white-space: nowrap;
}
/* S3 封单榜列宽 (已移除"状态"列, 优化各列宽度) */
.s3-table th.board-col, .s3-table td.board-col { width: 64px; }
.s3-table th.tp-th, .s3-table td.tp-th-15, .s3-table td.tp-th-20, .s3-table td.tp-th-25 { width: 54px; font-size: var(--fs-xs); }
.s3-table th:nth-of-type(1) { width: 84px; } /* 名称 */
.s3-table th.tp-th-25 + th.tp-th-25 { width: 44px; } /* 竞涨 (紧跟最后一个 tp-th) */
.s3-table tr th:nth-of-type(7) { width: 38px; } /* 竞换 */
.s3-table tr th:nth-of-type(8) { width: 44px; } /* 现涨 */
.s3-table tr th:nth-of-type(9) { width: 44px; } /* 流通(亿) */
.s3-table tr th:last-child { width: 46px; } /* 操作 */
/* 通用竞价异动表格列宽 - 给不同tab中固定列分配宽度 */
.auc-panel .stock-table th:nth-of-type(1),
.qc-panel .stock-table th:nth-of-type(1) { width: 88px; } /* 名称 */
.auc-panel .stock-table th:nth-of-type(2),
.qc-panel .stock-table th:nth-of-type(2) { width: 46px; } /* 现涨 */
.auc-panel .stock-table th:nth-of-type(3) { width: 46px; } /* 竞涨 */
.auc-panel .stock-table th:nth-of-type(4) { width: 64px; } /* 竞额/委买额 */
.auc-panel .stock-table th:nth-of-type(5) { width: 50px; } /* 竞换/量比 */
.auc-panel .stock-table th:nth-of-type(6) { width: 42px; } /* 连板/竞换 */
.auc-panel .stock-table th:nth-of-type(7) { width: 52px; } /* 流通/净额 */
.auc-panel .stock-table th:nth-of-type(8) { width: 72px; } /* 概念 */
.auc-panel .stock-table th:nth-of-type(9) { width: 50px; } /* 操作 */
.auc-panel .stock-table th:last-child { width: 50px; }
/* qc 抢筹表: 10 列精确宽度 */
.qc-panel .stock-table th:nth-of-type(1) { width: 88px; } /* 名称 */
.qc-panel .stock-table th:nth-of-type(2) { width: 46px; } /* 现涨 */
.qc-panel .stock-table th:nth-of-type(3) { width: 56px; } /* 竞额 */
.qc-panel .stock-table th:nth-of-type(4) { width: 54px; } /* 抢筹幅度 */
.qc-panel .stock-table th:nth-of-type(5) { width: 50px; } /* 竞额/昨比 */
.qc-panel .stock-table th:nth-of-type(6) { width: 46px; } /* 竞涨 */
.qc-panel .stock-table th:nth-of-type(7) { width: 42px; } /* 竞换 */
.qc-panel .stock-table th:nth-of-type(8) { width: 52px; } /* 流通 */
.qc-panel .stock-table th:nth-of-type(9) { width: 72px; } /* 概念 */
.qc-panel .stock-table th:nth-of-type(10) { width: 46px; } /* 操作 */
/* 其他 tab 列宽微调 */
.auc-panel .stock-table td:nth-of-type(1),
.qc-panel .stock-table td:nth-of-type(1) { min-width: 0; }
/* ★ 2026-09-29: 「昨上榜(龙虎榜)」表已下线 ⇒ 其 10 列宽度规则一并删除 */
/* 炸板表 - 昨/今 双版本(昨9列, 今10列) */
.auc-panel .broken-table th:nth-of-type(1) { width: 88px; }  /* 名称 */
.auc-panel .broken-table th:nth-of-type(2) { width: 46px; }  /* 现涨 */
.auc-panel .broken-table th:nth-of-type(3) { width: 46px; }  /* 竞涨 */
.auc-panel .broken-table th:nth-of-type(4) { width: 46px; }  /* 竞换 */
.auc-panel .broken-table th:nth-of-type(5) { width: 50px; }  /* 流通(亿) */
/* 昨炸板: 第6列=连板(窄), 第7-9列=概念/原因/操作 */
.auc-panel .broken-yest th:nth-of-type(6) { width: 38px; }
.auc-panel .broken-yest th:nth-of-type(7) { width: 72px; }  /* 概念 */
.auc-panel .broken-yest th:nth-of-type(8) { width: 168px; }  /* 涨停原因(改为列内展示 ⇒ 加宽) */
.auc-panel .broken-yest th:nth-of-type(9) { width: 46px; }  /* 操作 */
/* 今炸板: 第6列=炸板时间, 第7列=涨停时间, 第8-10列=概念/原因/操作 */
.auc-panel .broken-today th:nth-of-type(6) { width: 58px; }  /* 炸板时间 */
.auc-panel .broken-today th:nth-of-type(7) { width: 58px; }  /* 涨停时间 */
.auc-panel .broken-today th:nth-of-type(8) { width: 72px; }  /* 概念 */
.auc-panel .broken-today th:nth-of-type(9) { width: 168px; }  /* 涨停原因(改为列内展示 ⇒ 加宽) */
.auc-panel .broken-today th:nth-of-type(10) { width: 46px; } /* 操作 */
/* 昨涨停/昨断板表的「涨停原因」列: 这两张表原本没有定宽规则 ⇒ 补一条(不然长原因会把整行撑开)
   ★ 2026-09-29 主人反馈"没有对齐": 表头默认居中、而单元格是左对齐文本 ⇒ 表头悬在文字中间上方。
   文本列应与内容同一对齐(且共用同一左内边距), 故此处把表头也左对齐。 */
.auc-panel .stock-table th.reason-th { width: 168px; text-align: left; padding-left: var(--s2); }
/* 概念列已缩短 */
.concept-cell { padding: var(--s1) 2px !important; font-size: var(--fs-xs) !important; }
/* 抢筹徽章缩小 */
.qc-panel .qc-badge { padding: 1px var(--s1); font-size: var(--fs-xs); }
/* 操作列按钮缩小 */
.auc-panel .pool-add-btn, .qc-panel .pool-add-btn { padding: 1px var(--s1); font-size: var(--fs-xs); }
.qc-panel .stock-table { width: 100%; border-collapse: collapse; }
/* 手机端 / 窄屏: qc-table-scroll 内仍保留横滚 (scrollbar 隐藏) */
/* 2026-08-19 回归通用 stock-table 样式(跟其他 tab 一致):
   之前 table-layout:fixed + nth-child 固定 26-240px 死列宽 → 桌面端列挤(12列 × 56-72px 都很窄)
   现让列宽自适应(名称/概念可换行), 仅概念列加 max-width 防止特长撑破布局 */
/* 2026-08-20: 以上样式已被紧凑型覆盖 (优先级相同但后写的 !important 胜出) */
.auc-panel .stock-table-container,
.qc-panel .stock-table-container {
  padding: var(--s2) !important;
}
/* 2026-09-20 视觉减噪: qc-panel 表头统一红底白字 */
.qc-panel .stock-table th { color: #fff; font-weight: 600; background: var(--accent-deep2); border-bottom: none; }
.qc-panel .stock-table td { border-bottom: 1px solid rgba(255,255,255,0.04); }
.qc-panel .stock-table th:nth-child(10), .qc-panel .stock-table td:nth-child(10) { text-align: center; white-space: nowrap; }  /* 操作 */
.qc-panel .stock-table th:nth-child(9), .qc-panel .stock-table td:nth-child(9) { max-width: 240px; white-space: pre-line; }  /* 概念: 按概念分隔换行, 不拆字 */
.qc-panel .name-main { font-size: var(--fs-sm); line-height: 1.3; }
/* 窄屏(<1280px) 纵向堆叠; <1100 已原有 fallback */
@media (max-width: 1280px) { .qc-dual { gap: var(--s2); } }
/* 2026-08-20 手机端修复(qc 双表列挤压): 改为整表横向滚动, 保留全部10列
   - 不再隐藏次要列, 手机端横向滑动查看完整数据(与竞价委买等其它 tab 一致)
   - 模式切换按钮加大点击区 */
@media (max-width: 700px) {
  .qc-panel { padding: var(--s2); }
  .qc-panel-title { font-size: var(--fs-sm); flex-wrap: wrap; }
  .qc-mode-switch button { padding: var(--s1) var(--s3); font-size: var(--fs-xs); }
  .qc-table-scroll .stock-table { min-width: 860px; white-space: nowrap; }
  .qc-table-scroll { max-height: 420px; }
  .qc-panel .stock-table th, .qc-panel .stock-table td { padding: var(--s1) var(--s1); font-size: var(--fs-xs); }
  /* 概念列: 加宽到 150px, 每概念独占一行(pre-line)且不拆字 */
  .qc-panel .stock-table th:nth-child(9),
  .qc-panel .stock-table td:nth-child(9) { min-width: 90px; max-width: 90px; white-space: pre-line; }
}
.loading-placeholder { text-align: center; padding: var(--s8); color: var(--text-muted); }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: var(--star); border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.lb-badge { display: inline-block; color: var(--up); border: 1px solid rgba(255,80,40,0.5); border-radius: var(--r-sm); padding: 0 var(--s1); font-size: var(--fs-xs); background: rgba(255,80,40,0.12); }
.bk-hot { color: var(--accent-deep); font-weight: 700; }

/* 模态框通用 */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 1000; }

/* 三时点封单榜状态标签: 层1(9:25封死)=红金 / 层2(9:20回落)=橙 / 层3(9:15回落)=黄 */
/* s3-tag 已随"状态"列一同移除 (2026-08-20) */

/* === 连续多日竞价封单(2026-09-29): N 列并排, 每列一个交易日 ===
   注: .msd-modebar / .msd-mode-switch / .msd-mode-tip 已随「单日榜入口 + 顶部口径提示」一并移除 */
/* 2026-10-01 主人: 「竞价封单支持左右滑动」⇒ 恢复 N 列并排 + 横向滚动（与电脑端一致）。
   touch 惯性滚动 + 隐藏滚动条，手机上手感更接近"表格横滑"。 */
.msd-wrap { margin-bottom: var(--s2); overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
.msd-wrap::-webkit-scrollbar { display: none; }
/* 🔴 2026-09-30 主人: 「整个页面是个整体」⇒ N 个交易日并列成**一张连续表格**:
   去掉原先每列独立卡片的边框/圆角/底色与列间隙, 只在日与日之间留一条分隔线。
   同时「N 天必须排在一排」的约束不变 —— 窗口不够宽时**整块横向滚动**, 不降列、不换行。
   🔴 2026-09-30 二次紧凑化(主人: 5 日 → 4 日 + 优化间距):
     列数与最小宽度改为**由 --msd-cols 变量推导**(模板按 dailyDays.length 传入):
       · 5 列时 min-width = 5 × 240px = 1200px(与旧值一致, 口径不变)
       · 4 列时 = 4 × 240px = 960px
     ⇒ 每列最小宽度恒为 240px(文字可用宽度不因减列而变窄 ⇒ **不会因减列而触发截断**),
        但整块最窄宽度随列数等比缩小 ⇒ 4 列时少占 240px, 更容易整排放下、不出横向滚动条。
     .msd-cell 左右内边距同时 6px → 4px(每格多让出 4px), 故 240px 比原来更宽松。
     实测(真实编译 CSS + 等价 DOM, 视口 900/1100/1440 三档):
       · 改动前: 最窄 1200px ⇒ 1100 视口就出横向滚动条; 且 4 个格被截断
         (涨幅 `+10.06%` 溢出 4px、概念 `汽车零部件` 溢出 6px);
       · 改动后: 最窄 960px ⇒ 1100 视口整排放下; 三档视口**截断格数均为 0**。 */
.msd-grid {
  --msd-cols: 4;              /* 缺省 4; 模板行内注入实际天数覆盖 */
  --msd-col-min: 240px;       /* 每列最小宽度(= 旧 1200px / 5 列) */
  display: grid;
  grid-template-columns: repeat(var(--msd-cols), minmax(0, 1fr));
  gap: 0;
  min-width: calc(var(--msd-cols) * var(--msd-col-min));
  border: 1px solid var(--border-soft);
  border-radius: var(--r-sm);
  overflow: hidden;
}

.msd-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.msd-col + .msd-col { border-left: 1px solid var(--border-soft); }

/* 2026-09-30 v4.11.84 (P2-8): ≤430 小屏改「**每日一卡**」纵向排列。
   此前 `.msd-grid` 的 `min-width = 4 × 240px = 960px` ⇒ 390 屏上必然横向滚动，
   一屏只看得到半个交易日，还得左右滑着比对 —— 与"多日对比"的本意相反。
   改单列后：**一张卡 = 一个交易日**（卡内 9:25/9:20/9:15 三列仍横排，卡宽 = 屏宽，无需横滑），
   上下滑动看历史；列与列之间由「左边框」换成「上边框」分隔。 */
/* 🔴 2026-10-01 主人**反转** 2026-09-30 的决定: 「竞价封单支持左右滑动」⇒ 删除 ≤430px 单列塌陷。
   原决定（单列每日一卡、卡内三时点横排、不横滑）实测整页 11354px（≈13.5 屏），
   且与电脑端呈现不一致；主人要求"与电脑端相同的数据 + 可左右滑动" ⇒ 保持 4 列并排，
   由 .msd-wrap 接手横向滚动（一屏看一天，左右滑比对）。 */
.msd-head {
  padding: var(--s1) var(--s1) var(--s1);          /* 原 4px 4px 5px: 表头下压 2px */
  /* 2026-10-05 (L12): 顶部色 #b3271f 上压白字实测 4.47:1（AA 临界偏下）⇒ 降深到 #a01f18（7.8:1）。
     渐变下半段本身更深，不受影响。 */
  background: linear-gradient(180deg, #a01f18, var(--brand-deep));
  color: #fff;
  text-align: center;
}
/* 2026-09-30 紧凑化: 日期 0.8→0.76rem, 摘要行 0.68→0.65rem 且去掉 1px 上边距
   (表头整体从 3 行视高收到约 2 行半, 但仍在 0.65rem 下限之上 ⇒ 保持清晰可读) */
.msd-date { font-size: var(--fs-sm); font-weight: 700; letter-spacing: 0.3px; }
.msd-sum { margin-top: 0; font-size: var(--fs-xs); opacity: 0.95; white-space: nowrap; }
.msd-num { font-weight: 700; }
.msd-sep { opacity: 0.6; margin: 0 1px; }
/* .msd-trend* 已随环比那行一并移除(2026-09-30 主人明确) */

/* === 列 = 时点本身(2026-09-30 主人明确映射, 最紧凑的一种):
       9:25 列 → 股票名称 + 9:25 封单额
       9:20 列 → 概念     + 9:20 封单额
       9:15 列 → 几板     + 9:15 封单额
    每条记录只占 **3 列**(当日多一列涨幅) × 2 行 ⇒ 比"名称/概念/板 各自单独占列"窄得多。
    表头与数据行共用同一套列模板 ⇒ 竖线上下同位置贯通。 === */
.msd-sub,
.msd-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: center;
}
/* 历史列没有涨幅 ⇒ 只 3 列 */
.msd-sub.msd-nochg,
.msd-row.msd-nochg { grid-template-columns: repeat(3, minmax(0, 1fr)); }

.msd-cell {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  text-align: center;
  /* 2026-09-30 对齐规范: 表头格与数据格**统一左右内边距**。
     此前只有名称格有 padding-left:6px、其余为 0 ⇒ 同列内文字起点不一致。
     box-sizing 已由全局样式设为 border-box ⇒ 不会撑破网格列。
     2026-09-30 紧凑化: 6px → 4px(列间呼吸位收窄, 同时把每列文字可用宽度让回 4px)。 */
  padding: 0 var(--s1);
}
/* 竖分割线: 每列左缘一条, **首列除外**(否则会在卡片左边缘画线)。
   表头与数据行两行都用同一组规则 ⇒ 线上下贯通。 */
.msd-sub .msd-cell:not(:first-child),
.msd-row .msd-cell:not(.msd-name):not(.msd-p25) {
  border-left: 1px solid rgba(255, 255, 255, 0.16);
}
body[data-bg="light"] .msd-sub .msd-cell:not(:first-child),
body[data-bg="light"] .msd-row .msd-cell:not(.msd-name):not(.msd-p25) { border-left-color: #dfe3ea; }
.msd-sub .msd-cell:not(:first-child) { border-left-color: rgba(255, 255, 255, 0.28); }

.msd-sub {
  background: linear-gradient(90deg, var(--brand-deep) 0%, #c2441f 55%, var(--warn-amber) 100%);
  color: #fff;
  font-size: var(--fs-xs);           /* 原 0.66rem */
  font-weight: 600;
  padding: 1px 0;               /* 原 2px 0 */
}
/* 2026-09-30 主人明确: **全部居中对齐**(名称/概念/板/金额/涨幅/表头 一律居中)。
   此前"名称左对齐 + 金额右对齐"的做法被否掉 ⇒ 这里不再有任何对齐覆盖,
   统一走 .msd-cell 的 text-align: center。 */

/* 🔴 2026-09-30 主人: 「每日独立的板块不需要单独滑动」⇒ 去掉每列自己的纵向滚动,
   改成整页一起滚(列高由内容自然撑开, N 列因 grid stretch 保持等高, 短列下方留白)。 */
.msd-body { overflow: visible; }
.msd-row {
  grid-template-rows: auto auto;
  /* 2026-09-30 紧凑化(主人要求「优化布局间距」): 行内上下内边距 3px/4px → 2px/2px,
     并显式压 line-height 至 1.3 —— 每行是**两行文字**(名称行 + 金额行),
     行高从全局 1.5 降到 1.3 是"变矮"的主要来源; 字号仅 0.68 → 0.66rem
     (仍高于 10px 可读下限), 不靠缩小文字换紧凑。
     实测(真实编译 CSS + 等价 DOM, 见交付说明): 行高 40.6px → **32.4px(−20%)**。 */
  padding: 2px 0;
  line-height: 1.3;
  border-bottom: 1px solid var(--border-soft);
  font-variant-numeric: tabular-nums;
  font-size: var(--fs-xs);
}
.msd-row:last-child { border-bottom: none; }
.msd-row:hover { background: rgba(255, 255, 255, 0.04); }
body[data-bg="light"] .msd-row:hover { background: rgba(0, 0, 0, 0.03); }
/* 2026-09-30 v4.11.83 性能修复(实机实测): 每行 content-visibility:auto —— 视口外的行由浏览器跳过
   布局/绘制(此前 .msd-body 是 overflow:visible 整页滚, 数百行全部参与布局)。
   contain-intrinsic-size 用实测行高 32px 稳定滚动条, 避免滚动时跳动。 */
.msd-row {
  content-visibility: auto;
  contain-intrinsic-size: auto 32px;
}
/* 截断提示(仅当该日行数超过 MAX_ROWS_MSD 时出现) */
.msd-more {
  padding: var(--s1) 0 2px;
  font-size: var(--fs-xs);
  color: var(--text-muted);
  text-align: center;
}

/* 第 1 行: 名称 / 概念 / 板 —— 分别落在 9:25 / 9:20 / 9:15 三列 */
.msd-row .msd-name {
  grid-area: 1 / 1;
  cursor: pointer;                    /* 对齐/内边距统一由 .msd-cell 提供(居中 + 0 4px) */
  color: var(--text-secondary); font-weight: 600;
}
/* 概念/板: 随主字号下移一档(0.66→0.64 / 0.64→0.62), 保持"次要信息更轻"的层级不变 */
.msd-row .msd-concept { grid-area: 1 / 2; color: var(--text-muted); font-size: var(--fs-xs); }
.msd-row .msd-lb { grid-area: 1 / 3; color: var(--star); font-size: var(--fs-xs); }
.msd-row .msd-blank { grid-area: 1 / 4; }
/* 第 2 行: 各列的封单金额 + 涨幅 —— 同样居中; 数字仍靠 .msd-row 的 tabular-nums 对齐位数 */
.msd-row .msd-p25 { grid-area: 2 / 1; color: var(--brand-soft); font-weight: 600; }
.msd-row .msd-p20 { grid-area: 2 / 2; color: var(--text-secondary); }
/* 9:15 金额: 与**表头背景色呼应**(2026-09-30 主人要求)。
   表头是 `linear-gradient(90deg, #8e1f1f, #c2441f 55%, #d9822b)`, 9:15 列正落在渐变右端
   ⇒ **直接用该渐变右端的原色 #d9822b**, 呼应是"同色"而非"近似色"。
   🔴 2026-09-30 调色: 初版取的是提亮版 #ffab3d, 主人反馈**太亮** —— 实测相对亮度 0.507,
      在深色卡片上对比度 11.1:1, 几乎是纯色发光块、盖过了 9:25 主指标。
      换回原色后亮度降到 0.309(对比 7.2:1), 仍远超正文 4.5:1 下限, 但不再刺眼。
   浅色主题: #d9822b 在白底上仅 2.9:1, 故换等色系的加深版保可读性。 */
.msd-row .msd-p15 { grid-area: 2 / 3; color: var(--warn-amber); }   /* v4.11.84 P1-4 */
body[data-bg="light"] .msd-row .msd-p15 { color: #b3641a; }
.msd-row .msd-chg { grid-area: 2 / 4; font-weight: 600; }
.msd-row .msd-chg.dim { color: var(--text-muted); font-weight: 400; }
.msd-empty, .msd-empty-all {
  padding: var(--s2) var(--s1);               /* 原 10px 6px */
  text-align: center;
  color: var(--text-muted);
  font-size: var(--fs-xs);              /* 原 0.72rem */
}

/* === 三时点封单榜表格样式: 三色分组 + 概念列 + 拆列 === */
.s3-hint {
  margin: var(--s2) 0 var(--s3);
  padding: var(--s2) var(--s3);
  border: 1px dashed var(--accent-warm, var(--star));
  border-radius: var(--r-md);
  background: rgba(255, 180, 0, 0.06);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: 1.6;
}
.s3-hint b { color: var(--accent-warm, var(--star)); }
.s3-hint-soft { border-color: var(--border-soft); background: rgba(106, 214, 106, 0.05); color: var(--text-secondary); }
.s3-hint-soft b { color: var(--down); }
body[data-bg="light"] .s3-hint-soft b { color: #1a7a60; }
.s3-table { table-layout: auto; }
.board-text { color: var(--text-secondary); font-size: var(--fs-xs); line-height: 1.3; }
/* 2026-08-20 合并代码+名称列: 上方名称, 下方代码, 代码字体更小 */
.stock-info-cell { cursor: pointer; min-width: 120px; min-height: 0; height: 56px; text-align: center; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.stock-info-cell .stock-name-row { order: 1; display: flex; align-items: center; justify-content: center; gap: var(--s1); line-height: 1.3; }
.stock-info-cell .stock-name { font-weight: 600; color: var(--text-main); font-size: var(--fs-sm); }
.yd-badge-row { order: 3; height: 17px; display: flex; align-items: center; justify-content: center; margin-top: 2px; }
.yd-badge { display: inline-block; font-size: var(--fs-xs); line-height: 1; padding: 1px var(--s1); border-radius: var(--r-sm); background: var(--warn); border: 1px solid var(--warn); color: #3a1f00; font-weight: 600; white-space: nowrap; }
.stock-info-cell .stock-code-row { order: 2; line-height: 1.2; text-align: center; margin-top: 2px; }
.stock-info-cell .stock-code {
  font-family: inherit; font-size: var(--fs-xs); color: var(--text-muted);
  letter-spacing: 0.5px;
}
.stock-info-cell:hover .stock-name { color: var(--accent); }
.stock-info-cell:hover .stock-code { color: var(--accent); }
/* 2026-08-20 所有表格单元格居中对齐 */
.s3-table th, .s3-table td,
.auction-table th, .auction-table td { text-align: center; vertical-align: middle; }
/* 2026-08-20 概念列: 纯文本无任何样式, 列宽极小
   2026-08-21: 改为 pre-line 按概念换行, 不限制最大宽度让概念正常显示 */
.concept-cell { width: 90px; min-width: 90px; white-space: pre-line; line-height: 1.3; font-size: var(--fs-xs); color: var(--text-secondary); padding: var(--s1) 2px; }
/* 2026-08-21 概念列限高2行: 过长概念不再把整行撑高(如"新华百货"多行导致与相邻行不上下对齐),
   超出部分省略(完整概念仍在 td 的 title 悬浮中可看) */
.concept-clamp { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; white-space: pre-line; line-height: 1.3; word-break: break-word; }
.s3-table th.tp-th { text-align: center; font-weight: 600; }
/* 2026-09-20 表头统一红底白字后: 时点色改为「白字 + 底部色条」表达(红底上原时点彩字不可读),
   数据列 td 的三时点配色(9:25红/9:20金/9:15白)保持不变 */
.s3-table th.tp-th-15 { color: #fff; border-bottom: 2px solid rgba(232,236,242,0.55); }
.s3-table th.tp-th-20 { color: #fff; border-bottom: 2px solid rgba(255, 180, 0, 0.75); }
.s3-table th.tp-th-25 { color: #fff; border-bottom: 2px solid rgba(255, 255, 255, 0.85); }
.s3-table td.tp-th-15, .s3-table td.tp-th-20, .s3-table td.tp-th-25 { text-align: center; white-space: nowrap; }
.s3-table td.chg-col { font-weight: 600; }
/* 实时涨幅列(开盘啦 realChange): 涨=红, 跌=蓝(A股忌讳绿, 避开绿色系) */
.real-chg-col { text-align: center; white-space: nowrap; font-weight: 600; font-variant-numeric: tabular-nums; }
.real-chg-col.up { color: var(--up-strong); }   /* v4.11.84 P1-4: token 化 */
.real-chg-col.down { color: var(--down); }
.real-chg-col.dim { color: var(--text-muted); }
/* 9:15 涨幅: 青蓝系(亮=涨, 暗=跌) */
.chg-up-15 { color: #e8ecf2; text-shadow: 0 0 6px rgba(255,255,255,0.25); }
.chg-dn-15 { color: #4fc07a; }
.dim-15    { color: var(--text-dim); }
/* 9:20 涨幅: 橙系 */
.chg-up-20 { color: #ffd566; text-shadow: 0 0 6px rgba(255, 180, 0, 0.3); }
.chg-dn-20 { color: #35b866; }
.dim-20    { color: #8a7a5a; }
/* 9:25 涨幅: 红系(亮=涨停封死, 暗=回落, 灰=平) - 9:25 最终竞价结果用 A 股主色红 */
.chg-up-25 { color: var(--brand-soft); text-shadow: 0 0 6px rgba(255, 90, 90, 0.35); font-weight: 700; }
.chg-dn-25 { color: #30b060; }
.dim-25    { color: var(--dim-soft); }  /* v4.11.84 P1-4: #b06b6b 叠水印后仅 3.80:1(跌破 AA) → #c98a8a(4.28:1 叠水印) */
/* 封单额: 按时点主色, 弱色 */
.seal-col { font-variant-numeric: tabular-nums; }
.seal-col-15 { color: #d8dce4; }
.seal-col-20 { color: var(--star); }
.seal-col-25 { color: var(--brand-soft); }
body[data-bg="light"] .seal-col-15 { color: #5a5a5a; }
body[data-bg="light"] .seal-col-20 { color: #b07800; }
body[data-bg="light"] .seal-col-25 { color: #c82020; }
body[data-bg="light"] .chg-up-15 { color: #5a5a5a; }
body[data-bg="light"] .chg-up-20 { color: #8a5a00; }
body[data-bg="light"] .chg-up-25 { color: #c82020; }
/* 浅色主题: 竞价涨幅<0 绿色(中国股市惯例跌=绿) */
body[data-bg="light"] .chg-dn-15 { color: #1a8a4a; }
body[data-bg="light"] .chg-dn-20 { color: #1a8a4a; }
body[data-bg="light"] .chg-dn-25 { color: #1a8a4a; }

/* === 加单趋势标签 === */
.seal-mode { display: inline-block; padding: 1px var(--s2); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; white-space: nowrap; }
.seal-mode-strong { color: var(--star); border: 1px solid var(--star); background: rgba(255, 180, 0, 0.12); }
.seal-mode-mid { color: var(--down); border: 1px solid #4a9e28; background: rgba(106, 214, 106, 0.12); }
.seal-mode-weak { color: var(--text-muted); border: 1px solid var(--text-dim); background: rgba(154, 154, 154, 0.12); }
.seal-mode-danger { color: var(--brand-soft); border: 1px solid var(--brand-soft); background: rgba(255, 106, 106, 0.12); }
.seal-mode-flat { color: var(--text-muted); border: 1px solid var(--border-soft); background: transparent; }
body[data-bg="light"] .seal-mode-strong { color: #8a5a00; border-color: var(--gold-deep); background: rgba(255, 180, 0, 0.12); }
body[data-bg="light"] .seal-mode-mid { color: #2d7020; border-color: #4a9e28; }
body[data-bg="light"] .seal-mode-weak { color: var(--text-faint); border-color: #8a8a8a; }
body[data-bg="light"] .seal-mode-danger { color: #c82020; border-color: #c82020; }

.snap-empty { text-align: center; color: var(--text-dim); padding: var(--s8) 0; font-size: var(--fs-sm); }

/* 浅色主题: 加深原 scoped 内的浅色文字 */
body[data-bg="light"] .auc-title { color: #8a5500; }
body[data-bg="light"] .auc-title .fa { color: var(--gold-deep); }
body[data-bg="light"] .auc-tab:hover { color: #8a5500; background: rgba(199,145,0,0.08); }
body[data-bg="light"] .auc-tab.active { color: #8a5500; background: linear-gradient(135deg, rgba(255,180,0,0.2), rgba(255,140,50,0.12)); box-shadow: 0 2px 6px rgba(199,145,0,0.18); }
body[data-bg="light"] .page-back { color: var(--text-faint); }
body[data-bg="light"] .page-back:hover { color: var(--gold-deep); }
body[data-bg="light"] .auc-panel { background: rgba(255,255,255,0.85); border-color: var(--border-soft); }
body[data-bg="light"] .qc-panel { background: rgba(255,255,255,0.85); border-color: var(--border-soft); }
body[data-bg="light"] .qc-panel-title { color: var(--watermark); }
body[data-bg="light"] .qc-mode-switch button { color: var(--text-faint); border-color: var(--border-soft); background: rgba(255,255,255,0.6); }
body[data-bg="light"] .qc-mode-switch button.active { color: var(--watermark); background: rgba(255,180,0,0.15); border-color: var(--gold-deep); }
body[data-bg="light"] .qc-mode-switch button:hover { color: var(--watermark); border-color: var(--gold-deep); }
body[data-bg="light"] .qc-panel .stock-table th { color: #fff; border-bottom-color: transparent; }
body[data-bg="light"] .qc-panel .stock-table td { border-bottom-color: rgba(0,0,0,0.08); }
body[data-bg="light"] .qc-panel .stock-table tbody tr:hover { background: rgba(184,48,16,0.04); }
body[data-bg="light"] .lb-badge { color: var(--brand-deep); border-color: rgba(184,48,16,0.5); background: rgba(255,80,80,0.1); }
body[data-bg="light"] .bk-hot { color: var(--brand-deep); }
body[data-bg="light"] .snap-empty { color: #8a8a8a; }
body[data-bg="light"] .modal-mask { background: rgba(0,0,0,0.45); }

/* ---- 涨停原因列(★ 2026-09-29: 列内直接展示, 不再点「查看」弹窗; 2 行截断以保持"精简") ----
   🔴 截断必须放在**内层 span** 上: `-webkit-line-clamp` 需要 `display:-webkit-box`,
      直接加在 `<td>` 上会破坏表格布局(box 不是 table-cell)。 */
.reason-cell { text-align: left; vertical-align: top; padding: var(--s2) var(--s2) !important; }
.reason-clamp {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; white-space: normal; word-break: break-word;
  /* ★ 2026-09-29: 不写死宽度 —— 原 160px 在 ~320px 的列里只占一半, 文字像一条窄条(主人反馈"没有对齐");
     改为自动撑满单元格(display:-webkit-box 本身按块级排布), 列宽由 th.reason-th 决定。 */
  line-height: 1.35; font-size: var(--fs-xs); color: var(--text-secondary); cursor: help;
}
body[data-bg="light"] .reason-clamp { color: #4a4a4a; }

/* ---- 涨停原因弹窗 ---- */
/* ★ 2026-09-29: 涨停原因弹窗(.reason-modal/.reason-head/.reason-title/.reason-body/
   .reason-block/.reason-label/.reason-text/.reason-board/.reason-primary)整套样式已删除 ——
   原因改为表格列内直接展示(见上方 .reason-cell / .reason-clamp)。 */

/* ===================== 移动端适配 (<=768px 手机/小平板) ===================== */
/* 2026-08-20 右栏嵌入首页后: 右栏约占 50% 宽, 在 1100-1300px 区间右栏约 470-570px,
   10 个 tab 换行堆 2-3 行严重挤压 → 把"单行横滚"方案提前到 1300px 断点 */
@media (max-width: 1280px) {
  .auc-tab { padding: var(--s1) var(--s2); font-size: var(--fs-xs); }
}

@media (max-width: 768px) {
  /* 宽表格横向滚动 */
  .auc-panel { overflow-x: auto; -webkit-overflow-scrolling: touch; padding: var(--s2) var(--s2); }
  .auc-panel .stock-table { min-width: 880px; }
  /* ===== 2026-08-20 用户明确: PC端 tab 用两端对齐(美观), 手机端必须"自动换行, 不然会重叠"
     覆盖方案: 只在 <=768px 生效, 绝不碰桌面端 baseline space-between 样式.
     1) 去掉 flex:1 均分 (否则 10 个 tab 挤 430px 每个只 43px, 文字截断/重叠)
     2) flex-wrap:wrap 自动 2-3 行 (按真实宽度换行)
     3) justify-content:flex-start 左对齐(换行时行首整齐, space-between 会把单独的行尾 tab
        拉到最右, 视觉跳动, 用户不想要; flex-start + gap 每行均匀, 也不重叠)
     4) overflow-x:visible 既然换行就不需要横滚条, 也不会被裁切导致"右边被遮住" */
  .auc-tab { flex: 0 0 auto !important; padding: var(--s2) var(--s3); font-size: var(--fs-xs); min-width: 0; }
  .auc-tabs {
    flex-wrap: wrap !important;
    overflow-x: visible !important;
    justify-content: flex-start !important;
    align-content: flex-start;
    gap: var(--s2) var(--s2) !important; /* 行间距 6px, 列间距 8px, 保证文字不互叠 */
    padding: var(--s2);
  }
  /* 滚动/吸附没用了, 关掉(因为已换成 wrap) */
  .auc-tabs { scroll-snap-type: none; }
  .auc-tabs .auc-tab { scroll-snap-align: none; }
  /* 头部紧凑: 标题+日期+按钮同行, 不换行 */
  /* 2026-10-05 主人：「日期显示不全，把前面的时间和每 30s 更新去掉是不是就能显示全了」。
     实测 390px 顶栏（可用 378）：标题 73 + 更新戳 **205**（其中「· 每 30s 自动刷新」95）
     + 日期框 120（被下面的 max-width 死卡，**自然宽 131**）+ 3 个按钮 96 + 间隔 40 = **542**
     ⇒ 溢出 164px：日期只画到「2026年9月…」，末尾按钮被推到顶栏外（x=437）。
     处置（主人的判断是对的，但要补一刀）：
       ① **时间戳整块让位**（−205px）。新鲜度反馈并未失去：刷新中有 spinner、
          刷新失败有「稍后重试」；且此页日期框本身已表明数据所属交易日。
       ② 🔴 光去文字**不够** —— 日期框被 `max-width: 120px` 卡住，必须同时放开到 136
          （自然宽 131 + 余量），「2026/09/30」才能完整显示。
       ③ `nowrap` → `wrap` 作兜底：再窄的屏（≤360）宁可折两行，也不裁切控件。
     合计 73+131+96+40 = 340 ≤ 378 ✓（余 38px）。 */
  .auc-head { gap: var(--s2); flex-wrap: wrap; }
  .auc-head .ds { display: none; }
  .auc-title { font-size: var(--fs-md); flex-shrink: 0; }
  .auc-head-spacer { flex: 1 1 auto; min-width: 0; }
  /* 日期选择器缩窄 */
  /* 120 → 136：120 会把「2026/09/30」裁成「2026年9月…」（实测自然宽 131，见上） */
  .auc-head input[type="date"].rot-date { max-width: 136px; font-size: var(--fs-xs); min-height: 28px; padding: var(--s1) var(--s2); }
  .auc-head button.rot-reset-btn { padding: var(--s1) var(--s2); font-size: var(--fs-xs); }
  /* 表格字号/行高压缩 */
  .stock-table th { padding: var(--s2) var(--s1); font-size: var(--fs-xs); }
  .stock-table td { padding: var(--s2) var(--s1); font-size: var(--fs-xs); }
  /* S3 封单榜超窄屏: 名字列更窄, 三时点列压到 44px */
  .s3-table th:nth-of-type(1) { width: 72px !important; }
  .s3-table th.tp-th, .s3-table td.tp-th-15, .s3-table td.tp-th-20, .s3-table td.tp-th-25 { width: 48px !important; font-size: var(--fs-xs); }
  .s3-table th.board-col { width: 90px !important; white-space: pre-line; }
  .s3-table td.concept-cell { width: 90px; max-width: 90px; white-space: pre-line; }
  /* 操作按钮触控加大(可点区域 ≥40px) */
  .pool-add-btn { padding: var(--s1) var(--s2); font-size: var(--fs-xs); min-height: 28px; }
  /* 三时点提示条 */
  .s3-hint { font-size: var(--fs-xs); padding: var(--s2) var(--s2); }
  /* 页面留白压缩 */
  .page-back { font-size: var(--fs-xs); margin-bottom: var(--s2); }
  /* 涨停原因列: 窄屏缩留白(宽度仍由列宽自动决定, 不再写死) */
  .reason-cell { padding: var(--s1) var(--s2) !important; }
  .reason-clamp { font-size: var(--fs-xs); }
  /* 合并代码+名称列: 手机缩小 name 字号 */
  .stock-info-cell { min-width: 68px; }
  .stock-info-cell .stock-name { font-size: var(--fs-xs) !important; }
  .stock-info-cell .stock-code { font-size: var(--fs-xs) !important; letter-spacing: 0; }
  /* 竞价封单/爆量/净额等其它 tab 表格: 概念列按概念换行显示, 不拆字, 宽度 90px 与 qc 表一致
     各概念用 \n 分隔, 配合 white-space:pre-line 每概念独占一行, 单个概念内不拆字 */
  .auc-panel .concept-cell,
  .qc-panel .concept-cell {
    width: 90px !important;
    max-width: 90px !important;
    white-space: pre-line;
  }
  .auc-panel .stock-table th:nth-of-type(8),
  .auc-panel .stock-table td:nth-of-type(8),
  .auc-panel .broken-yest th:nth-of-type(7),
  .auc-panel .broken-yest td:nth-of-type(7),
  .auc-panel .broken-today th:nth-of-type(8),
  .auc-panel .broken-today td:nth-of-type(8),
  .auc-panel .broken-today th:nth-of-type(9),
  .auc-panel .broken-today td:nth-of-type(9) {
    width: 90px !important;
    max-width: 90px;
    white-space: pre-line;
  }
}
/* 超小屏(< 480px, 如 iPhone SE 1/2/3 375px): 再压一级字号/留白 */
@media (max-width: 480px) {
  .auc-tab { padding: var(--s1) var(--s2) !important; font-size: var(--fs-xs) !important; }
  .auc-title { font-size: var(--fs-base); }
  .stock-table th, .stock-table td { padding: var(--s1) 2px; font-size: var(--fs-xs) !important; }
  .stock-info-cell .stock-name { font-size: var(--fs-xs) !important; }
  .stock-info-cell .stock-code { font-size: var(--fs-xs) !important; }
  .auc-panel .stock-table { min-width: 800px; }
}
</style>
