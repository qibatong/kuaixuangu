<template>
  <div class="page-shell">
    <h1 class="visually-hidden">竞价异动</h1>
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
      <input :value="datePicker || dataDate" type="date" class="rot-date" title="选择历史交易日" @change="onDateChange">
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
      <button class="auc-tab" :class="{ active: tab === 'lhb' }" @click="switchTab('lhb')">昨上榜</button>
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
            <td class="stock-info-cell" @click="linkToSoftware(it.code)">
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
        </tbody>
      </table>

      <!-- 竞价封单榜(短线侠式三层排序: 9:25涨停 > 9:20涨停回落 > 9:15涨停回落) -->
      <template v-else-if="tab === 's3'">
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
            <th class="tp-th tp-th-25 sortable" :class="{ active: s3Sort.keyOf('seal25') }" @click="s3Sort.onSort('seal25')">9:25<span class="sort-ind">{{ s3Sort.ind('seal25') }}</span></th>
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
            <td class="stock-info-cell" @click="linkToSoftware(it.code)">
            <div class="stock-code-row"><span class="stock-code">{{ it.code }}</span></div>
            <div class="stock-name-row"><span class="pool-hover-wrap"><span class="stock-name">{{ it.name || it.code }}</span><PoolHoverBtn :item="it" /></span></div>
            <div v-if="yidongTag(it.code)" class="yd-badge-row"><span class="yd-badge" :title="yidongTagTitle(it.code)">{{ yidongTag(it.code) }}</span></div>
          </td>
            <td class="seal-col seal-col-25">{{ tpSeal(it, '9_25') }}</td>
            <td class="seal-col seal-col-20">{{ tpSeal(it, '9_20') }}</td>
            <td class="seal-col seal-col-15">{{ tpSeal(it, '9_15') }}</td>
            <td class="tp-th-25 chg-col" :class="tpChgCls(it, '9_25')">{{ tpChg(it, '9_25') }}</td>
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
            <th class="sortable" :class="{ active: yestZtSort.keyOf('floatMv') }" @click="yestZtSort.onSort('floatMv')">流通(亿)<span class="sort-ind">{{ yestZtSort.ind('floatMv') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('change') }" @click="yestZtSort.onSort('change')">现涨<span class="sort-ind">{{ yestZtSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('bidChange') }" @click="yestZtSort.onSort('bidChange')">竞涨<span class="sort-ind">{{ yestZtSort.ind('bidChange') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('bidTurnover') }" @click="yestZtSort.onSort('bidTurnover')">竞换<span class="sort-ind">{{ yestZtSort.ind('bidTurnover') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('bidAmt') }" @click="yestZtSort.onSort('bidAmt')">竞额(亿)<span class="sort-ind">{{ yestZtSort.ind('bidAmt') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('reason') }" @click="yestZtSort.onSort('reason', 'string')">涨停原因<span class="sort-ind">{{ yestZtSort.ind('reason') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('board') }" @click="yestZtSort.onSort('board', 'string')">概念<span class="sort-ind">{{ yestZtSort.ind('board') }}</span></th>
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
            <td class="dim">{{ fmtMv(z.floatMv) }}</td>
            <td :class="z.change > 0 ? 'up' : z.change < 0 ? 'down' : 'dim'">{{ z.change !== null && z.change !== undefined ? signed(z.change) + '%' : '-' }}</td>
            <td :class="z.bidChange > 0 ? 'up' : z.bidChange < 0 ? 'down' : 'dim'">{{ z.bidChange !== null && z.bidChange !== undefined ? signed(z.bidChange) + '%' : '-' }}</td>
            <td>{{ z.bidTurnover !== null && z.bidTurnover !== undefined ? z.bidTurnover.toFixed(2) + '%' : '-' }}</td>
            <td>{{ z.bidAmt ? amtText(z.bidAmt) : '-' }}</td>
            <td class="reason-cell" @click="showReason(z)"><span class="reason-link"><i class="fa fa-fire"></i> 查看</span></td>
            <td class="concept-cell dim" :title="z.board"><span v-if="z.board" class="concept-clamp">{{ conceptText(z.board) }}</span><span v-else class="dim">-</span></td>
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
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('reason') }" @click="yestBrokenSort.onSort('reason', 'string')">涨停原因<span class="sort-ind">{{ yestBrokenSort.ind('reason') }}</span></th>
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
            <td class="reason-cell" @click="showReason(b2)"><span class="reason-link"><i class="fa fa-fire"></i> 查看</span></td>
            <td class="concept-cell dim" :title="b2.board"><span v-if="b2.board" class="concept-clamp">{{ conceptText(b2.board) }}</span><span v-else class="dim">-</span></td>
</tr>
        </tbody>
      </table>

      <!-- 昨上榜(龙虎榜) -->
      <table v-else-if="tab === 'lhb'" class="stock-table lhb-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: lhbSort.keyOf('code') }" @click="lhbSort.onSort('code', 'string')">名称<span class="sort-ind">{{ lhbSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('change') }" @click="lhbSort.onSort('change')">现涨<span class="sort-ind">{{ lhbSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('bidChange') }" @click="lhbSort.onSort('bidChange')">竞涨<span class="sort-ind">{{ lhbSort.ind('bidChange') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('bidTurnover') }" @click="lhbSort.onSort('bidTurnover')">竞换<span class="sort-ind">{{ lhbSort.ind('bidTurnover') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('buyIn') }" @click="lhbSort.onSort('buyIn')">买入(亿)<span class="sort-ind">{{ lhbSort.ind('buyIn') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('floatMv') }" @click="lhbSort.onSort('floatMv')">流通(亿)<span class="sort-ind">{{ lhbSort.ind('floatMv') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('turnover') }" @click="lhbSort.onSort('turnover')">换手%<span class="sort-ind">{{ lhbSort.ind('turnover') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('reason') }" @click="lhbSort.onSort('reason', 'string')">涨停原因<span class="sort-ind">{{ lhbSort.ind('reason') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('board') }" @click="lhbSort.onSort('board', 'string')">概念<span class="sort-ind">{{ lhbSort.ind('board') }}</span></th>
</tr>
        </thead>
        <tbody>
          <tr v-for="l in lhbSort.sorted(lhbList)" :key="l.code">
            <td class="stock-info-cell" @click="linkToSoftware(l.code)">
            <div class="stock-code-row"><span class="stock-code">{{ l.code }}</span></div>
            <div class="stock-name-row"><span class="pool-hover-wrap"><span class="stock-name">{{ l.name }}</span><PoolHoverBtn :item="l" /></span></div>
            <div v-if="yidongTag(l.code)" class="yd-badge-row"><span class="yd-badge" :title="yidongTagTitle(l.code)">{{ yidongTag(l.code) }}</span></div>
          </td>
            <td :class="l.change > 0 ? 'up' : 'down'">{{ signed(l.change) }}%</td>
            <td :class="l.bidChange > 0 ? 'up' : l.bidChange < 0 ? 'down' : 'dim'">{{ l.bidChange !== null && l.bidChange !== undefined ? signed(l.bidChange) + '%' : '-' }}</td>
            <td class="dim">{{ l.bidTurnover !== null && l.bidTurnover !== undefined ? l.bidTurnover.toFixed(2) + '%' : '-' }}</td>
            <td :class="l.buyIn > 0 ? 'up' : 'dim'">{{ yi(l.buyIn) }}</td>
            <td class="dim">{{ fmtMv(l.floatMv) }}</td>
            <td>{{ l.turnover.toFixed(2) }}</td>
            <td class="reason-cell" @click="showReason(l)"><span class="reason-link"><i class="fa fa-fire"></i> 查看</span></td>
            <td class="concept-cell dim" :title="l.board"><span v-if="l.board" class="concept-clamp">{{ conceptText(l.board) }}</span><span v-else class="dim">-</span></td>
</tr>
        </tbody>
      </table>

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
            <th class="sortable" :class="{ active: brokenSort.keyOf('reason') }" @click="brokenSort.onSort('reason', 'string')">涨停原因<span class="sort-ind">{{ brokenSort.ind('reason') }}</span></th>
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
            <td class="reason-cell" @click="showReason(b)"><span class="reason-link"><i class="fa fa-fire"></i> 查看</span></td>
            <td class="concept-cell dim" :title="b.board"><span v-if="b.board" class="concept-clamp">{{ conceptText(b.board) }}</span><span v-else class="dim">-</span></td>
</tr>
        </tbody>
      </table>
    </div>

    <!-- 涨停原因弹窗(点击表格"查看"链接) -->
    <div v-if="reasonModal.show" class="modal-mask" @click.self="reasonModal.show = false">
      <div class="reason-modal">
        <div class="reason-head">
          <span class="reason-title"><i class="fa fa-fire"></i> 涨停原因 <span v-if="reasonModal.name" class="dim small">· {{ reasonModal.name }} <span v-if="reasonModal.code" class="dim small">({{ reasonModal.code }})</span></span></span>
          <span class="snap-close" @click="reasonModal.show = false">✕</span>
        </div>
        <div class="reason-body">
          <div v-if="reasonModal.board" class="reason-board">
            <span class="reason-label">概念题材</span>
            <span class="reason-board-txt">{{ reasonModal.board }}</span>
          </div>
          <div class="reason-block reason-primary">
            <div class="reason-label">核心原因</div>
            <div class="reason-text">{{ reasonModal.text || '暂无涨停原因数据' }}</div>
          </div>
          <div v-if="reasonModal.extra" class="reason-block">
            <div class="reason-label">补充说明</div>
            <div class="reason-text">{{ reasonModal.extra }}</div>
          </div>
        </div>
      </div>
    </div>
</template>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { usePolling } from '../composables/usePolling'
import { kplBidSeal, kplBidNet, kplBidBoom, kplBidQiangcang, kplBroken, kplLhb, kplYestBroken, kplYestZt } from '../api/kpl'
import { auctionOverview, bidSnapshot3points } from '../api/stats'
import { trackUsage } from '../api/activity'
import { linkToSoftware } from '../utils/tdx'
import { todayBj } from '../utils/time'
import { showToast } from '../utils/toast'
import { useSortable } from '../composables/useSortable'
import { useYidongMonitor } from '../composables/useYidongMonitor'
import { yi, signed, amtText, fmtT } from '../utils/format'
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
const sealRaw = ref([])
const bidNetList = ref([])   // 2026-08-18: 竞价净额专用(开盘啦 Type2 竞价>1000万), 空时回退封单列表
const boomList = ref([])
const lhbList = ref([])
const brokenYestList = ref([])
const brokenTodayList = ref([])
const s3List = ref([])        // 三时点封单榜(后端已按三层排序)

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
let autoFallback = false      // 已自动回退(避免清空后无限循环)

// 各表独立排序实例
const sealSort = useSortable()
const s3Sort = useSortable()
const qcSort = useSortable()
const qcLastSort = useSortable()
const yestZtSort = useSortable()
const yestBrokenSort = useSortable()
const lhbSort = useSortable()
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

// 流通市值(元) → "xx.x亿" (2026-08-17 竞价异动各 tab 统一流通列)
function fmtMv(v) {
  if (!v) return '-'
  return (v / 1e8).toFixed(1) + '亿'
}

// 切 Tab 时清掉排序(避免跨表残留的 key 干扰)
function switchTab(t) {
  tab.value = t
  // 2026-09-22 v4.11.35: 用户主动点竞价异动的 Tab 算一次使用(轮询走 ensureTabData, 不经此处)
  trackUsage('auction')
  sealSort.clear(); s3Sort.clear(); qcSort.clear(); qcLastSort.clear(); yestZtSort.clear()
  yestBrokenSort.clear(); lhbSort.clear(); brokenSort.clear()
  // 2026-08-18 性能优化: 切 tab 按需加载该 tab 数据(首次进入才拉)
  ensureTabData(t)
}

// 竞价委买/爆量/净额共用表格: 数据源按 Tab 切换
const sealList = computed(() => {
  if (tab.value === 'boom') return boomList.value
  if (tab.value === 'net') {
    // 2026-08-18 主人要求: 竞价净额用开盘啦"竞价>1000万"接口(全市场), 空时回退封单列表
    if (bidNetList.value && bidNetList.value.length) {
      return [...bidNetList.value].sort((a, b) => (b.bidNetAmt || 0) - (a.bidNetAmt || 0))
    }
    return [...sealRaw.value].sort((a, b) => (b.bidNetAmt || 0) - (a.bidNetAmt || 0))
  }
  return sealRaw.value
})

// 炸板: 昨/今 按 Tab 切换
const brokenList = computed(() => (tab.value === 'brokenYest' ? brokenYestList.value : brokenTodayList.value))

// ---- 涨停原因弹窗 ----
const reasonModal = ref({ show: false, code: '', name: '', board: '', text: '', extra: '' })
function showReason(it) {
  if (!it) return
  reasonModal.value = {
    show: true,
    code: it.code || '',
    name: it.name || '',
    board: it.board || '',
    text: it.reason || it.reason_text || it.reasonText || '',
    extra: it.reason_extra || it.extra || ''
  }
}

// 单个接口最多等 15s, 超时返回空对象(不让某个慢接口拖垮整页加载)
// 2026-09-28: 12000 → 15000。冷取数最坏实测 11.5s(结果层 + 上游层同时失效),
// 12s 会在临界点把结果截断成空列表(= 用户看到"点了没数据"), 留足余量。
function withTimeout(p, ms = 15000) {
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
    if (!dt && ov.days && ov.days.length) {
      const lastTrading = ov.days[0].date
      if (lastTrading && lastTrading !== todayBj()) {
        autoFallback = true
        datePicker.value = lastTrading
        useDate = lastTrading
        showToast(`当前非交易时段，自动显示最近交易日 ${lastTrading} 的数据`, 'info')
      }
    }
    autoFallback = false
    const s3 = await withTimeout(bidSnapshot3points(useDate || todayBj()))
    s3List.value = s3.list || []
    loadedTabs.add('s3')
    const d = (ov.days && ov.days.length ? ov.days[0].date : '') || useDate || ''
    dataDate.value = d || useDate || ''
    if (useDate && fromUser) {
      if (!dataDate.value || dataDate.value !== useDate) {
        showToast(`数据日期 ${dataDate.value}${dataDate.value !== useDate ? '（非交易日自动对齐）' : ''}`, 'info')
      }
    }
    await ensureTabData(tab.value, { silent: true })
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
  const dt = datePicker.value
  if (t === 's3') {
    // 三时点榜随 loadAll 加载; 轮询时若已清标记则重新拉(实时刷新)
    if (!loadedTabs.has('s3')) {
      tabLoading.add('s3')
      try {
        const r = await withTimeout(bidSnapshot3points(dt || todayBj()))
        s3List.value = (r && r.list) || []
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
      case 'lhb': r = await withTimeout(kplLhb(dt)); lhbList.value = (r && r.list) || []; break
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
  autoFallback = false
  loadedTabs.clear()   // 2026-08-18: 日期变化需重新加载各 tab
  loadAll(true)
}

// 2026-08-18 性能优化: 日期选择变化 → 清已加载标记, 重新按需加载
function onDateChange(e) {
  datePicker.value = e.target.value
  loadedTabs.clear()
  loadAll(true)
}

onMounted(() => {
  loadAll()
  refreshYidongCodes()
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
polling = usePolling(async () => {
  if (datePicker.value) return true      // 历史回看模式: 不轮询(每分钟拉历史无意义)
  silentRefreshing.value = true
  try {
    loadedTabs.clear()
    const ok = await ensureTabData(tab.value, { silent: true })
    pollFailCount.value = ok ? 0 : (pollFailCount.value + 1)
    return ok
  } finally {
    silentRefreshing.value = false
  }
}, 30000, { backoff: true })
</script>

<style scoped>
/* 2026-08-18: 竞价成交额列内嵌"昨日竞价额"小字 */
.yest-bid-amt {
  display: block; font-size: 0.75rem; color: var(--text-muted, #889);
  font-weight: 400; line-height: 1.2;
}
.page-back { color: var(--text-muted); cursor: pointer; font-size: 0.8125rem; margin-bottom: 12px; display: inline-block; }
.page-back:hover { color: #ffb400; }
.auc-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.auc-head-spacer { flex: 1; }
.auc-head .rot-date { flex-shrink: 0; }
.auc-head .rot-reset-btn { flex-shrink: 0; }
.auc-title { font-size: 1.25rem; font-weight: 700; color: #ffe0a0; }
.auc-title .fa { color: #ffb400; }
/* 2026-09-05 P0: 静默刷新指示(角落, 不遮挡内容) —— 金色与页面主色一致 */
.auc-silent-loading {
  color: #ffb400; font-size: 0.8125rem; line-height: 1;
  display: inline-flex; align-items: center;
  opacity: .9;
}
/* 连续失败提示: 用橙黄警示(避开绿色 —— A 股语境绿=跌) */
.auc-poll-warn {
  color: #ff9d3c; font-size: 0.75rem; line-height: 1;
  display: inline-flex; align-items: center; gap: 3px;
  background: rgba(255, 157, 60, .12);
  border: 1px solid rgba(255, 157, 60, .3);
  border-radius: 4px; padding: 2px 6px;
}
/* 刷新按钮禁用态: 降低透明度, 明确不可点 */
.auc-head .rot-reset-btn:disabled { opacity: .45; cursor: not-allowed; }
.auc-sub { color: var(--text-muted); font-size: 0.8125rem; }
.auc-tabs {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 14px;
  padding: 6px;
  background: var(--bg-panel-solid);
  border: 1px solid var(--border-soft);
  border-radius: 10px;
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
  font-size: 0.75rem;
  line-height: 1;
  flex: 0 0 auto;
  user-select: none;
  margin: 0 1px;
}
.auc-tab {
  padding: 6px 10px;
  border-radius: 5px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.7812rem;
  cursor: pointer;
  transition: background-color 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
  font-weight: 500;
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
  color: #ffd700;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(255, 180, 0, 0.2);
}
.auc-panel { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px; }
/* 竞价抢筹左右双表 */
.qc-dual { display: flex; flex-direction: column; gap: 10px; }
.qc-panel { width: 100%; background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 10px; overflow: visible; display: flex; flex-direction: column; gap: 8px; }
.qc-table-scroll { overflow-x: auto; overflow-y: auto; max-height: 480px; scrollbar-width: none; -ms-overflow-style: none; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--bg-hover); }
.qc-table-scroll::-webkit-scrollbar { display: none; width: 0; height: 0; }
/* 2026-09-20 视觉减噪: qc 抢筹双表 sticky 表头统一红底白字(与全局一致) */
.qc-table-scroll .stock-table thead th { position: sticky; top: 0; z-index: 8; background: var(--accent-deep2); border-bottom: none; }
.qc-panel-title { font-size: 0.875rem; font-weight: 700; color: #ffe0a0; display: flex; align-items: center; gap: 10px; }
.qc-mode-switch { display: inline-flex; gap: 4px; margin-left: auto; }
.qc-mode-switch button {
  font-size: 0.75rem; padding: 2px 10px; border-radius: 4px; cursor: pointer;
  background: var(--border-soft); border: 1px solid rgba(255,255,255,0.18);
  color: #aaa; transition: background-color 0.2s, border-color 0.2s, color 0.2s;
}
.qc-mode-switch button.active { background: rgba(255,180,0,0.18); border-color: #ffb400; color: #ffd700; font-weight: 600; }
.qc-mode-switch button:hover { border-color: #ffb400; color: #ffe0a0; }
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
  padding: 5px 2px !important;
  font-size: 0.75rem !important;
  vertical-align: middle;
}
.auc-panel .stock-table th,
.qc-panel .stock-table th {
  padding: 7px 2px !important;
  font-size: 0.75rem !important;
  white-space: nowrap;
}
/* S3 封单榜列宽 (已移除"状态"列, 优化各列宽度) */
.s3-table th.board-col, .s3-table td.board-col { width: 64px; }
.s3-table th.tp-th, .s3-table td.tp-th-15, .s3-table td.tp-th-20, .s3-table td.tp-th-25 { width: 54px; font-size: 0.75rem; }
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
/* 昨上榜(龙虎榜)表 - 10 列精确宽度 */
.auc-panel .lhb-table th:nth-of-type(1) { width: 88px; }  /* 名称 */
.auc-panel .lhb-table th:nth-of-type(2) { width: 46px; }  /* 现涨 */
.auc-panel .lhb-table th:nth-of-type(3) { width: 46px; }  /* 竞涨 */
.auc-panel .lhb-table th:nth-of-type(4) { width: 46px; }  /* 竞换 */
.auc-panel .lhb-table th:nth-of-type(5) { width: 60px; }  /* 买入(亿) */
.auc-panel .lhb-table th:nth-of-type(6) { width: 52px; }  /* 流通(亿) */
.auc-panel .lhb-table th:nth-of-type(7) { width: 44px; }  /* 换手% */
.auc-panel .lhb-table th:nth-of-type(8) { width: 72px; }  /* 概念 */
.auc-panel .lhb-table th:nth-of-type(9) { width: 66px; }  /* 涨停原因 */
.auc-panel .lhb-table th:nth-of-type(10) { width: 46px; } /* 操作 */
/* 炸板表 - 昨/今 双版本(昨9列, 今10列) */
.auc-panel .broken-table th:nth-of-type(1) { width: 88px; }  /* 名称 */
.auc-panel .broken-table th:nth-of-type(2) { width: 46px; }  /* 现涨 */
.auc-panel .broken-table th:nth-of-type(3) { width: 46px; }  /* 竞涨 */
.auc-panel .broken-table th:nth-of-type(4) { width: 46px; }  /* 竞换 */
.auc-panel .broken-table th:nth-of-type(5) { width: 50px; }  /* 流通(亿) */
/* 昨炸板: 第6列=连板(窄), 第7-9列=概念/原因/操作 */
.auc-panel .broken-yest th:nth-of-type(6) { width: 38px; }
.auc-panel .broken-yest th:nth-of-type(7) { width: 72px; }  /* 概念 */
.auc-panel .broken-yest th:nth-of-type(8) { width: 66px; }  /* 涨停原因 */
.auc-panel .broken-yest th:nth-of-type(9) { width: 46px; }  /* 操作 */
/* 今炸板: 第6列=炸板时间, 第7列=涨停时间, 第8-10列=概念/原因/操作 */
.auc-panel .broken-today th:nth-of-type(6) { width: 58px; }  /* 炸板时间 */
.auc-panel .broken-today th:nth-of-type(7) { width: 58px; }  /* 涨停时间 */
.auc-panel .broken-today th:nth-of-type(8) { width: 72px; }  /* 概念 */
.auc-panel .broken-today th:nth-of-type(9) { width: 66px; }  /* 涨停原因 */
.auc-panel .broken-today th:nth-of-type(10) { width: 46px; } /* 操作 */
/* 概念列已缩短 */
.concept-cell { padding: 4px 2px !important; font-size: 0.75rem !important; }
/* 抢筹徽章缩小 */
.qc-panel .qc-badge { padding: 1px 4px; font-size: 0.75rem; }
/* 操作列按钮缩小 */
.auc-panel .pool-add-btn, .qc-panel .pool-add-btn { padding: 1px 5px; font-size: 0.75rem; }
.qc-panel .stock-table { width: 100%; border-collapse: collapse; }
/* 手机端 / 窄屏: qc-table-scroll 内仍保留横滚 (scrollbar 隐藏) */
/* 2026-08-19 回归通用 stock-table 样式(跟其他 tab 一致):
   之前 table-layout:fixed + nth-child 固定 26-240px 死列宽 → 桌面端列挤(12列 × 56-72px 都很窄)
   现让列宽自适应(名称/概念可换行), 仅概念列加 max-width 防止特长撑破布局 */
/* 2026-08-20: 以上样式已被紧凑型覆盖 (优先级相同但后写的 !important 胜出) */
.auc-panel .stock-table-container,
.qc-panel .stock-table-container {
  padding: 6px !important;
}
/* 2026-09-20 视觉减噪: qc-panel 表头统一红底白字 */
.qc-panel .stock-table th { color: #fff; font-weight: 600; background: var(--accent-deep2); border-bottom: none; }
.qc-panel .stock-table td { border-bottom: 1px solid rgba(255,255,255,0.04); }
.qc-panel .stock-table th:nth-child(10), .qc-panel .stock-table td:nth-child(10) { text-align: center; white-space: nowrap; }  /* 操作 */
.qc-panel .stock-table th:nth-child(9), .qc-panel .stock-table td:nth-child(9) { max-width: 240px; white-space: pre-line; }  /* 概念: 按概念分隔换行, 不拆字 */
.qc-panel .name-main { font-size: 0.8125rem; line-height: 1.3; }
/* 窄屏(<1280px) 纵向堆叠; <1100 已原有 fallback */
@media (max-width: 1280px) { .qc-dual { gap: 8px; } }
/* 2026-08-20 手机端修复(qc 双表列挤压): 改为整表横向滚动, 保留全部10列
   - 不再隐藏次要列, 手机端横向滑动查看完整数据(与竞价委买等其它 tab 一致)
   - 模式切换按钮加大点击区 */
@media (max-width: 700px) {
  .qc-panel { padding: 8px; }
  .qc-panel-title { font-size: 0.8125rem; flex-wrap: wrap; }
  .qc-mode-switch button { padding: 4px 12px; font-size: 0.75rem; }
  .qc-table-scroll .stock-table { min-width: 860px; white-space: nowrap; }
  .qc-table-scroll { max-height: 420px; }
  .qc-panel .stock-table th, .qc-panel .stock-table td { padding: 3px 3px; font-size: 0.75rem; }
  /* 概念列: 加宽到 150px, 每概念独占一行(pre-line)且不拆字 */
  .qc-panel .stock-table th:nth-child(9),
  .qc-panel .stock-table td:nth-child(9) { min-width: 90px; max-width: 90px; white-space: pre-line; }
}
.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: #ffb400; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.lb-badge { display: inline-block; color: #ff8a5c; border: 1px solid rgba(255,80,40,0.5); border-radius: 4px; padding: 0 5px; font-size: 0.75rem; background: rgba(255,80,40,0.12); }
.bk-hot { color: var(--accent-deep); font-weight: 700; }

/* 模态框通用 */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 1000; }

/* 三时点封单榜状态标签: 层1(9:25封死)=红金 / 层2(9:20回落)=橙 / 层3(9:15回落)=黄 */
/* s3-tag 已随"状态"列一同移除 (2026-08-20) */

/* === 三时点封单榜表格样式: 三色分组 + 概念列 + 拆列 === */
.s3-hint {
  margin: 8px 0 12px;
  padding: 9px 12px;
  border: 1px dashed var(--accent-warm, #ffb400);
  border-radius: 8px;
  background: rgba(255, 180, 0, 0.06);
  color: var(--text-secondary);
  font-size: 0.7812rem;
  line-height: 1.6;
}
.s3-hint b { color: var(--accent-warm, #ffb400); }
.s3-hint-soft { border-color: var(--border-soft); background: rgba(106, 214, 106, 0.05); color: var(--text-secondary); }
.s3-hint-soft b { color: #6ad66a; }
body[data-bg="light"] .s3-hint-soft b { color: #1a7a60; }
.s3-table { table-layout: auto; }
.board-text { color: var(--text-secondary); font-size: 0.75rem; line-height: 1.3; }
/* 2026-08-20 合并代码+名称列: 上方名称, 下方代码, 代码字体更小 */
.stock-info-cell { cursor: pointer; min-width: 120px; min-height: 0; height: 56px; text-align: center; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.stock-info-cell .stock-name-row { order: 1; display: flex; align-items: center; justify-content: center; gap: 4px; line-height: 1.3; }
.stock-info-cell .stock-name { font-weight: 600; color: var(--text-main); font-size: 0.8125rem; }
.yd-badge-row { order: 3; height: 17px; display: flex; align-items: center; justify-content: center; margin-top: 2px; }
.yd-badge { display: inline-block; font-size: 0.75rem; line-height: 1; padding: 1px 5px; border-radius: 3px; background: #ff9632; border: 1px solid #ff9632; color: #3a1f00; font-weight: 600; white-space: nowrap; }
.stock-info-cell .stock-code-row { order: 2; line-height: 1.2; text-align: center; margin-top: 2px; }
.stock-info-cell .stock-code {
  font-family: inherit; font-size: 0.75rem; color: var(--text-muted);
  letter-spacing: 0.5px;
}
.stock-info-cell:hover .stock-name { color: var(--accent); }
.stock-info-cell:hover .stock-code { color: var(--accent); }
/* 2026-08-20 所有表格单元格居中对齐 */
.s3-table th, .s3-table td,
.auction-table th, .auction-table td { text-align: center; vertical-align: middle; }
/* 2026-08-20 概念列: 纯文本无任何样式, 列宽极小
   2026-08-21: 改为 pre-line 按概念换行, 不限制最大宽度让概念正常显示 */
.concept-cell { width: 90px; min-width: 90px; white-space: pre-line; line-height: 1.3; font-size: 0.75rem; color: var(--text-secondary); padding: 4px 2px; }
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
.real-chg-col.up { color: #ff5a5a; }
.real-chg-col.down { color: #00c864; }
.real-chg-col.dim { color: var(--text-muted); }
/* 9:15 涨幅: 青蓝系(亮=涨, 暗=跌) */
.chg-up-15 { color: #e8ecf2; text-shadow: 0 0 6px rgba(255,255,255,0.25); }
.chg-dn-15 { color: #4fc07a; }
.dim-15    { color: #8a8a8a; }
/* 9:20 涨幅: 橙系 */
.chg-up-20 { color: #ffd566; text-shadow: 0 0 6px rgba(255, 180, 0, 0.3); }
.chg-dn-20 { color: #35b866; }
.dim-20    { color: #8a7a5a; }
/* 9:25 涨幅: 红系(亮=涨停封死, 暗=回落, 灰=平) - 9:25 最终竞价结果用 A 股主色红 */
.chg-up-25 { color: #ff6a6a; text-shadow: 0 0 6px rgba(255, 90, 90, 0.35); font-weight: 700; }
.chg-dn-25 { color: #30b060; }
.dim-25    { color: #b06b6b; }  /* 2026-09-21 对比度修正: #9a5a5a(3.69:1)→#b06b6b(4.77:1) 达 AA */
/* 封单额: 按时点主色, 弱色 */
.seal-col { font-variant-numeric: tabular-nums; }
.seal-col-15 { color: #d8dce4; }
.seal-col-20 { color: #ffb400; }
.seal-col-25 { color: #ff6a6a; }
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
.seal-mode { display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; white-space: nowrap; }
.seal-mode-strong { color: #ffb400; border: 1px solid #ffb400; background: rgba(255, 180, 0, 0.12); }
.seal-mode-mid { color: #6ad66a; border: 1px solid #4a9e28; background: rgba(106, 214, 106, 0.12); }
.seal-mode-weak { color: #9a9a9a; border: 1px solid #8a8a8a; background: rgba(154, 154, 154, 0.12); }
.seal-mode-danger { color: #ff6a6a; border: 1px solid #ff6a6a; background: rgba(255, 106, 106, 0.12); }
.seal-mode-flat { color: var(--text-muted); border: 1px solid var(--border-soft); background: transparent; }
body[data-bg="light"] .seal-mode-strong { color: #8a5a00; border-color: #c79100; background: rgba(255, 180, 0, 0.12); }
body[data-bg="light"] .seal-mode-mid { color: #2d7020; border-color: #4a9e28; }
body[data-bg="light"] .seal-mode-weak { color: #6b6b6b; border-color: #8a8a8a; }
body[data-bg="light"] .seal-mode-danger { color: #c82020; border-color: #c82020; }

.snap-empty { text-align: center; color: var(--text-dim); padding: 30px 0; font-size: 0.8125rem; }

/* 浅色主题: 加深原 scoped 内的浅色文字 */
body[data-bg="light"] .auc-title { color: #8a5500; }
body[data-bg="light"] .auc-title .fa { color: #c79100; }
body[data-bg="light"] .auc-tab:hover { color: #8a5500; background: rgba(199,145,0,0.08); }
body[data-bg="light"] .auc-tab.active { color: #8a5500; background: linear-gradient(135deg, rgba(255,180,0,0.2), rgba(255,140,50,0.12)); box-shadow: 0 2px 6px rgba(199,145,0,0.18); }
body[data-bg="light"] .page-back { color: #6b6b6b; }
body[data-bg="light"] .page-back:hover { color: #c79100; }
body[data-bg="light"] .auc-panel { background: rgba(255,255,255,0.85); border-color: var(--border-soft); }
body[data-bg="light"] .qc-panel { background: rgba(255,255,255,0.85); border-color: var(--border-soft); }
body[data-bg="light"] .qc-panel-title { color: #5a4a3a; }
body[data-bg="light"] .qc-mode-switch button { color: #6b6b6b; border-color: var(--border-soft); background: rgba(255,255,255,0.6); }
body[data-bg="light"] .qc-mode-switch button.active { color: #5a4a3a; background: rgba(255,180,0,0.15); border-color: #c79100; }
body[data-bg="light"] .qc-mode-switch button:hover { color: #5a4a3a; border-color: #c79100; }
body[data-bg="light"] .qc-panel .stock-table th { color: #fff; border-bottom-color: transparent; }
body[data-bg="light"] .qc-panel .stock-table td { border-bottom-color: rgba(0,0,0,0.08); }
body[data-bg="light"] .qc-panel .stock-table tbody tr:hover { background: rgba(184,48,16,0.04); }
body[data-bg="light"] .lb-badge { color: #b83010; border-color: rgba(184,48,16,0.5); background: rgba(255,80,80,0.1); }
body[data-bg="light"] .bk-hot { color: #b83010; }
body[data-bg="light"] .snap-empty { color: #8a8a8a; }
body[data-bg="light"] .modal-mask { background: rgba(0,0,0,0.45); }

/* ---- 涨停原因列(点击链接) ---- */
.reason-cell { cursor: pointer; text-align: center; font-size: 0.75rem; padding: 6px 10px; white-space: nowrap; }
.reason-link { display: inline-flex; align-items: center; gap: 4px; color: #ffb400; font-weight: 600; padding: 2px 8px; border-radius: 10px; background: rgba(255, 180, 0, 0.1); border: 1px solid rgba(255, 180, 0, 0.3); transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease; }
.reason-link:hover { background: rgba(255, 180, 0, 0.2); color: #ffd270; border-color: rgba(255, 180, 0, 0.6); }
.reason-link .fa { font-size: 0.75rem; }
body[data-bg="light"] .reason-link { color: #b83010; background: rgba(184,48,16,0.08); border-color: rgba(184,48,16,0.3); }
body[data-bg="light"] .reason-link:hover { color: #8a1a00; background: rgba(184,48,16,0.15); }

/* ---- 涨停原因弹窗 ---- */
.reason-modal { position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%); background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: 12px; width: min(560px, 94vw); max-height: 80vh; overflow: hidden; display: flex; flex-direction: column; box-shadow: 0 12px 36px rgba(0,0,0,0.5); z-index: 1001; }
.reason-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border-soft); background: linear-gradient(180deg, rgba(255,180,0,0.08), transparent); }
.reason-title { font-size: 0.9375rem; font-weight: 700; color: #ffe0a0; display: flex; align-items: center; gap: 6px; }
.reason-title .fa-fire { color: #ff7a3d; }
.reason-title .small { font-size: 0.75rem; font-weight: 500; margin-left: 6px; }
.reason-body { padding: 14px 16px; overflow-y: auto; flex: 1; }
.reason-block, .reason-board { margin-bottom: 12px; }
.reason-block:last-child { margin-bottom: 0; }
.reason-label { font-size: 0.75rem; color: var(--text-muted); margin-bottom: 6px; font-weight: 600; letter-spacing: 0.5px; }
.reason-primary .reason-label { color: #ffb400; }
.reason-text { font-size: 0.875rem; line-height: 1.7; color: var(--text-primary); white-space: pre-wrap; word-break: break-word; }
.reason-board-txt { display: inline-block; font-size: 0.8125rem; line-height: 1.6; color: var(--text-primary); white-space: pre-line; padding: 6px 10px; background: rgba(255,255,255,0.04); border-radius: 6px; border: 1px dashed var(--border-soft); }
.reason-primary { padding: 10px 12px; background: rgba(255, 180, 0, 0.06); border: 1px solid rgba(255, 180, 0, 0.25); border-radius: 8px; }
body[data-bg="light"] .reason-title { color: #5a4a3a; }
body[data-bg="light"] .reason-title .fa-fire { color: #b83010; }
body[data-bg="light"] .reason-primary { background: rgba(184,48,16,0.05); border-color: rgba(184,48,16,0.3); }
body[data-bg="light"] .reason-primary .reason-label { color: #b83010; }
body[data-bg="light"] .reason-board-txt { background: rgba(184,48,16,0.04); }

/* ===================== 移动端适配 (<=768px 手机/小平板) ===================== */
/* 2026-08-20 右栏嵌入首页后: 右栏约占 50% 宽, 在 1100-1300px 区间右栏约 470-570px,
   10 个 tab 换行堆 2-3 行严重挤压 → 把"单行横滚"方案提前到 1300px 断点 */
@media (max-width: 1280px) {
  .auc-tab { padding: 5px 6px; font-size: 0.75rem; }
}

@media (max-width: 768px) {
  /* 宽表格横向滚动 */
  .auc-panel { overflow-x: auto; -webkit-overflow-scrolling: touch; padding: 10px 8px; }
  .auc-panel .stock-table { min-width: 880px; }
  /* ===== 2026-08-20 用户明确: PC端 tab 用两端对齐(美观), 手机端必须"自动换行, 不然会重叠"
     覆盖方案: 只在 <=768px 生效, 绝不碰桌面端 baseline space-between 样式.
     1) 去掉 flex:1 均分 (否则 10 个 tab 挤 430px 每个只 43px, 文字截断/重叠)
     2) flex-wrap:wrap 自动 2-3 行 (按真实宽度换行)
     3) justify-content:flex-start 左对齐(换行时行首整齐, space-between 会把单独的行尾 tab
        拉到最右, 视觉跳动, 用户不想要; flex-start + gap 每行均匀, 也不重叠)
     4) overflow-x:visible 既然换行就不需要横滚条, 也不会被裁切导致"右边被遮住" */
  .auc-tab { flex: 0 0 auto !important; padding: 7px 12px; font-size: 0.75rem; min-width: 0; }
  .auc-tabs {
    flex-wrap: wrap !important;
    overflow-x: visible !important;
    justify-content: flex-start !important;
    align-content: flex-start;
    gap: 6px 8px !important; /* 行间距 6px, 列间距 8px, 保证文字不互叠 */
    padding: 8px;
  }
  /* 滚动/吸附没用了, 关掉(因为已换成 wrap) */
  .auc-tabs { scroll-snap-type: none; }
  .auc-tabs .auc-tab { scroll-snap-align: none; }
  /* 头部紧凑: 标题+日期+按钮同行, 不换行 */
  .auc-head { gap: 6px; flex-wrap: nowrap; }
  .auc-title { font-size: 0.9375rem; flex-shrink: 0; }
  .auc-head-spacer { flex: 1 1 auto; min-width: 0; }
  /* 日期选择器缩窄 */
  .auc-head input[type="date"].rot-date { max-width: 120px; font-size: 0.75rem; min-height: 28px; padding: 3px 6px; }
  .auc-head button.rot-reset-btn { padding: 3px 6px; font-size: 0.75rem; }
  /* 表格字号/行高压缩 */
  .stock-table th { padding: 7px 4px; font-size: 0.75rem; }
  .stock-table td { padding: 6px 4px; font-size: 0.75rem; }
  /* S3 封单榜超窄屏: 名字列更窄, 三时点列压到 44px */
  .s3-table th:nth-of-type(1) { width: 72px !important; }
  .s3-table th.tp-th, .s3-table td.tp-th-15, .s3-table td.tp-th-20, .s3-table td.tp-th-25 { width: 48px !important; font-size: 0.75rem; }
  .s3-table th.board-col { width: 90px !important; white-space: pre-line; }
  .s3-table td.concept-cell { width: 90px; max-width: 90px; white-space: pre-line; }
  /* 操作按钮触控加大(可点区域 ≥40px) */
  .pool-add-btn { padding: 5px 10px; font-size: 0.75rem; min-height: 28px; }
  /* 三时点提示条 */
  .s3-hint { font-size: 0.75rem; padding: 7px 10px; }
  /* 页面留白压缩 */
  .page-back { font-size: 0.75rem; margin-bottom: 8px; }
  /* 涨停原因列: 缩小点按区 */
  .reason-cell { padding: 4px 6px; }
  .reason-link { padding: 2px 6px; font-size: 0.75rem; }
  /* 涨停原因弹窗: 手机端更紧凑 */
  .reason-modal { width: 94vw; max-height: 85vh; }
  .reason-head { padding: 10px 12px; }
  .reason-body { padding: 12px; }
  .reason-text { font-size: 0.8125rem; }
  /* 合并代码+名称列: 手机缩小 name 字号 */
  .stock-info-cell { min-width: 68px; }
  .stock-info-cell .stock-name { font-size: 0.75rem !important; }
  .stock-info-cell .stock-code { font-size: 0.75rem !important; letter-spacing: 0; }
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
  .auc-panel .lhb-table th:nth-of-type(8),
  .auc-panel .lhb-table td:nth-of-type(8),
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
  .auc-tab { padding: 5px 8px !important; font-size: 0.75rem !important; }
  .auc-title { font-size: 0.875rem; }
  .stock-table th, .stock-table td { padding: 5px 2px; font-size: 0.75rem !important; }
  .stock-info-cell .stock-name { font-size: 0.75rem !important; }
  .stock-info-cell .stock-code { font-size: 0.75rem !important; }
  .auc-panel .stock-table { min-width: 800px; }
}
</style>
