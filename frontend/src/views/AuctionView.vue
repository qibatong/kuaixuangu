<template>
  <div class="page-shell">
    <div class="page-back" @click="$router.push('/')"><i class="fa fa-arrow-left"></i> 返回选股</div>

    <!-- 会员门禁: 竞价异动仅在工作日 9:15-15:00 要求会员; 其他时段放开 -->
    <VipGate v-if="!user.isMember && isMemberOnlyTime()" title="竞价异动" />

    <template v-else>
    <div class="auc-head">
      <span class="auc-title"><i class="fa fa-bullhorn"></i> 竞价异动</span>
      <span class="auc-sub">多时点对比 · 竞价委买/爆量/净额/上榜/炸板</span>
      <span class="auc-time">{{ bjTime }}</span>
    </div>

    <!-- 日期回看: 选历史交易日查看当天竞价异动(周末/节假日自动对齐最近交易日) -->
    <div class="rot-toolbar">
      <span class="rot-tip"><i class="fa fa-info-circle"></i> 选日期回看</span>
      <input v-model="datePicker" type="date" class="rot-date" title="选择历史交易日" @change="loadAll(true)">
      <button class="rot-reset-btn" title="回到实时" @click="clearDate"><i class="fa fa-bolt"></i></button>
      <span v-if="dataDate && datePicker" class="rot-data-date">
        <i class="fa fa-calendar"></i> 数据日期 {{ dataDate }}
        <template v-if="dataDate !== datePicker">（{{ datePicker }} 非交易日，自动对齐）</template>
      </span>
    </div>

    <!-- 顶部多时点对比(最近4个交易日 / 指定日期) -->
    <div class="ov-panel">
      <table class="ov-table">
        <thead>
          <tr>
            <th class="ov-dim">时点</th>
            <th v-for="d in days" :key="d.date" class="ov-day">
              <div class="ov-date">{{ d.date.slice(5) }}</div>
              <div v-if="d.yizi_count !== null" class="ov-yizi">一字 <b>{{ d.yizi_count }}</b> 个 · 封单 <b>{{ yi(d.yizi_amt) }}亿</b></div>
              <div v-else class="ov-yizi dim">无数据</div>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="tp in timePoints" :key="tp.key">
            <td class="ov-dim">{{ tp.label }}</td>
            <td
v-for="d in days" :key="d.date + tp.key" class="ov-cell ov-click" title="点击查看该时点个股"
                @click="showSnapshot(d.date, tp.key)"
>
              <template v-if="d.points[tp.key]">
                <div :class="d.points[tp.key].avg_change !== null && d.points[tp.key].avg_change >= 0 ? 'up' : 'down'">
                  {{ fmtAvg(d.points[tp.key].avg_change) }}
                </div>
                <div class="ov-amt dim">{{ yi(d.points[tp.key].total_amt) }}亿</div>
              </template>
              <span v-else class="dim">-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 个股三时点封单: 一个视图看 9:15/9:20/9:25 三个时刻封单变化 -->
    <div class="stock3-toolbar">
      <span class="rot-tip"><i class="fa fa-eye"></i> 个股三时点封单（9:15 / 9:20 / 9:25）</span>
      <input v-model="stock3.code" class="stock3-input" placeholder="输入6位代码，如 300410" maxlength="6" @keydown.enter="loadStock3" />
      <button class="stock3-btn" @click="loadStock3"><i class="fa fa-search"></i> 查询</button>
    </div>

    <!-- Tab 切换 -->
    <div class="auc-tabs">
      <button class="auc-tab" :class="{ active: tab === 's3' }" title="全市场三时点封单榜: 9:25涨停→9:20涨停回落→9:15涨停回落 三层排序" @click="switchTab('s3')"><i class="fa fa-th-list"></i> 三时点封单</button>
      <button class="auc-tab" :class="{ active: tab === 'seal' }" @click="switchTab('seal')"><i class="fa fa-gavel"></i> 竞价委买</button>
      <button class="auc-tab" :class="{ active: tab === 'boom' }" @click="switchTab('boom')"><i class="fa fa-bolt"></i> 竞价爆量</button>
      <button class="auc-tab" :class="{ active: tab === 'net' }" @click="switchTab('net')"><i class="fa fa-exchange"></i> 竞价净额</button>
      <button class="auc-tab" :class="{ active: tab === 'qc' }" title="9:15-9:30 竞价抢筹(异动板块大单)" @click="switchTab('qc')"><i class="fa fa-fire"></i> 竞价抢筹</button>
      <button class="auc-tab" :class="{ active: tab === 'yestZt' }" @click="switchTab('yestZt')"><i class="fa fa-sun-o"></i> 昨日涨停</button>
      <button class="auc-tab" :class="{ active: tab === 'yestBroken' }" @click="switchTab('yestBroken')"><i class="fa fa-bell-slash"></i> 昨断板</button>
      <button class="auc-tab" :class="{ active: tab === 'brokenYest' }" @click="switchTab('brokenYest')"><i class="fa fa-history"></i> 昨炸板</button>
      <button class="auc-tab" :class="{ active: tab === 'lhb' }" @click="switchTab('lhb')"><i class="fa fa-list-alt"></i> 昨上榜</button>
      <button class="auc-tab" :class="{ active: tab === 'brokenToday' }" @click="switchTab('brokenToday')"><i class="fa fa-chain-broken"></i> 今炸板</button>
    </div>

    <div class="auc-panel">
      <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载中...</div></div>

      <!-- 竞价委买/爆量/净额 共用表 -->
      <table v-else-if="tab === 'seal' || tab === 'boom' || tab === 'net'" class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable" :class="{ active: sealSort.keyOf('code') }" @click="sealSort.onSort('code', 'string')">代码<span class="sort-ind">{{ sealSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('name') }" @click="sealSort.onSort('name', 'string')">名称<span class="sort-ind">{{ sealSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('realChange') }" @click="sealSort.onSort('realChange')">实时涨幅<span class="sort-ind">{{ sealSort.ind('realChange') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('bidChange') }" @click="sealSort.onSort('bidChange')">竞价涨幅<span class="sort-ind">{{ sealSort.ind('bidChange') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf(tab === 'boom' ? 'bidAmt' : 'bidSealAmt') }" @click="sealSort.onSort(tab === 'boom' ? 'bidAmt' : 'bidSealAmt')">{{ tab === 'boom' ? '竞价成交额(亿)' : '涨停委买额(亿)' }}<span class="sort-ind">{{ sealSort.ind(tab === 'boom' ? 'bidAmt' : 'bidSealAmt') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('bidNetAmt') }" @click="sealSort.onSort('bidNetAmt')">竞价净额(亿)<span class="sort-ind">{{ sealSort.ind('bidNetAmt') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('limitBoards') }" @click="sealSort.onSort('limitBoards')">连板<span class="sort-ind">{{ sealSort.ind('limitBoards') }}</span></th>
            <th class="sortable" :class="{ active: sealSort.keyOf('board') }" @click="sealSort.onSort('board', 'string')">板块<span class="sort-ind">{{ sealSort.ind('board') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(it, idx) in sealSort.sorted(sealList)" :key="it.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(it.code)">{{ it.code }}</td>
            <td class="name-col"><div class="name-main">{{ it.name }}</div></td>
            <td :class="it.realChange > 0 ? 'up' : 'down'">{{ signed(it.realChange) }}%</td>
            <td :class="it.bidChange > 0 ? 'up' : 'down'">{{ signed(it.bidChange) }}%</td>
            <td v-if="tab === 'boom'" :class="it.bidAmt > 0 ? 'up' : 'dim'">{{ yi(it.bidAmt) }}</td>
            <td v-else :class="it.bidSealAmt > 0 ? 'up' : 'dim'">{{ yi(it.bidSealAmt) }}</td>
            <td :class="it.bidNetAmt > 0 ? 'up' : it.bidNetAmt < 0 ? 'down' : 'dim'">{{ yi(it.bidNetAmt) }}</td>
            <td><span v-if="it.limitBoards > 0" class="lb-badge">{{ it.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td class="dim" style="max-width:150px;white-space:pre-wrap;">{{ it.board }}</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(it.code) }" @click.stop="addToPool(it)">{{ inPool(it.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
        </tbody>
      </table>

      <!-- 三时点封单榜(短线侠式三层排序: 9:25涨停 > 9:20涨停回落 > 9:15涨停回落) -->
      <template v-else-if="tab === 's3'">
      <div v-if="sealMissing" class="s3-hint">
        <i class="fa fa-info-circle"></i> 该日期<b>封单额与竞价额均未采集</b>（历史委托数据不提供，无法回填），
        下个交易日 9:15 / 9:20 / 9:25 自动采集后生效。涨幅/概念/流通市值不受影响。
      </div>
      <div v-else-if="sealDegraded" class="s3-hint s3-hint-soft">
        <i class="fa fa-info-circle"></i> 历史日期无封单采集，当前显示<b>竞价额</b>（前缀「竞」）作为强弱参考；<b>下个交易日 9:15/9:20/9:25 采集后显示真实封单额</b>。
      </div>
      <table class="stock-table s3-table">
        <thead>
          <tr>
            <th>#</th>
            <th>代码</th>
            <th>名称</th>
            <th>状态</th>
            <th>加单趋势</th>
            <th class="board-col">概念(所属板块)</th>
            <th class="tp-th tp-th-15">9:15 封单</th>
            <th class="tp-th tp-th-20">9:20 封单</th>
            <th class="tp-th tp-th-25">9:25 封单</th>
            <th class="tp-th tp-th-25">竞价涨幅</th>
            <th>实时涨幅</th>
            <th>流通市值(亿)</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(it, idx) in s3List" :key="it.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(it.code)">{{ it.code }}</td>
            <td class="name-col"><div class="name-main">{{ it.name || it.code }}</div></td>
            <td><span class="s3-tag" :class="'s3-tag-' + it.layer">{{ it.tag }}</span></td>
            <td>
              <span v-if="sealMode(it)" class="seal-mode" :class="'seal-mode-' + sealMode(it).cls" :title="sealMode(it).tip">{{ sealMode(it).label }}</span>
              <span v-else class="dim">-</span>
            </td>
            <td class="board-col" :title="it.board"><span class="board-text">{{ boardText(it.board) }}</span></td>
            <td class="seal-col seal-col-15">{{ tpSeal(it, '9_15') }}</td>
            <td class="seal-col seal-col-20">{{ tpSeal(it, '9_20') }}</td>
            <td class="seal-col seal-col-25">{{ tpSeal(it, '9_25') }}</td>
            <td class="tp-th-25 chg-col" :class="tpChgCls(it, '9_25')">{{ tpChg(it, '9_25') }}</td>
            <td class="real-chg-col" :class="realChgCls(it)">{{ realChg(it) }}</td>
            <td class="dim">{{ mvText(it) }}</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(it.code) }" @click.stop="addToPool(it)">{{ inPool(it.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
          <tr v-if="!s3List.length"><td colspan="13" class="snap-empty">暂无三时点封单数据（需交易日 9:15/9:20/9:25 自动采集后才有）</td></tr>
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
          <table class="stock-table">
            <thead>
              <tr>
                <th>排名</th>
                <th class="sortable" :class="{ active: qcSort.keyOf('code') }" @click="qcSort.onSort('code', 'string')">代码<span class="sort-ind">{{ qcSort.ind('code') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('name') }" @click="qcSort.onSort('name', 'string')">名称<span class="sort-ind">{{ qcSort.ind('name') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('realChange') }" @click="qcSort.onSort('realChange')">实时涨幅<span class="sort-ind">{{ qcSort.ind('realChange') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('bidAmt') }" @click="qcSort.onSort('bidAmt')">竞价金额<span class="sort-ind">{{ qcSort.ind('bidAmt') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf(qc20Mode === 'amt' ? 'qcDelta' : 'qcDeltaChg') }" @click="qcSort.onSort(qc20Mode === 'amt' ? 'qcDelta' : 'qcDeltaChg')">抢筹幅度<span class="sort-ind">{{ qcSort.ind(qc20Mode === 'amt' ? 'qcDelta' : 'qcDeltaChg') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('bidRatio') }" @click="qcSort.onSort('bidRatio')">竞额/昨比<span class="sort-ind">{{ qcSort.ind('bidRatio') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('bidChange') }" @click="qcSort.onSort('bidChange')">竞价涨幅<span class="sort-ind">{{ qcSort.ind('bidChange') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('floatMv') }" @click="qcSort.onSort('floatMv')">流通<span class="sort-ind">{{ qcSort.ind('floatMv') }}</span></th>
                <th class="sortable" :class="{ active: qcSort.keyOf('board') }" @click="qcSort.onSort('board', 'string')">概念<span class="sort-ind">{{ qcSort.ind('board') }}</span></th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(q, idx) in qcSort.sorted(qc20Mode === 'amt' ? qcList : qcChgList)" :key="'a' + q.code + qc20Mode">
                <td class="rank-col">{{ idx + 1 }}</td>
                <td class="code-click" @click="linkToSoftware(q.code)">{{ q.code }}</td>
                <td class="name-col"><div class="name-main">{{ q.name }}</div></td>
                <td :class="q.realChange > 0 ? 'up' : q.realChange < 0 ? 'down' : 'dim'">{{ q.realChange !== null && q.realChange !== undefined ? signed(q.realChange) + '%' : '-' }}</td>
                <td :class="q.bidAmt > 0 ? 'up' : 'dim'">{{ amtText(q.bidAmt) }}</td>
                <td :class="(qc20Mode === 'amt' ? q.qcDelta : q.qcDeltaChg) > 0 ? 'up' : (qc20Mode === 'amt' ? q.qcDelta : q.qcDeltaChg) < 0 ? 'down' : 'dim'"><b>{{ signed(qc20Mode === 'amt' ? q.qcDelta : q.qcDeltaChg) }}%</b></td>
                <td :class="q.bidRatio !== null && q.bidRatio !== undefined ? (q.bidRatio >= 20 ? 'ratio-hot' : q.bidRatio >= 10 ? 'ratio-warm' : '') : 'dim'" :title="q.bidRatio !== null && q.bidRatio !== undefined ? ('今日竞价额 ÷ 昨日全天成交额 = ' + q.bidRatio.toFixed(2) + '%') : ''">{{ q.bidRatio !== null && q.bidRatio !== undefined ? q.bidRatio.toFixed(2) + '%' : '-' }}</td>
                <td :class="q.bidChange > 0 ? 'up' : q.bidChange < 0 ? 'down' : 'dim'">{{ signed(q.bidChange) }}%</td>
                <td>{{ q.floatMv ? (q.floatMv / 1e8).toFixed(1) + '亿' : '-' }}</td>
                <td class="dim qc-board" :title="q.board">{{ q.board ? q.board.split('、').join(' ') : '-' }}</td>
                <td><button class="pool-add-btn" :class="{ added: inPool(q.code) }" @click.stop="addToPool(q)">{{ inPool(q.code) ? '已入池' : '＋池' }}</button></td>
              </tr>
              <tr v-if="(qc20Mode === 'amt' ? qcList : qcChgList).length === 0">
                <td colspan="11" class="snap-empty">{{ qc20Mode === 'amt' ? '9:20-9:25 竞额抢筹数据 9:15-9:30 竞价时段可用' : '9:20-9:25 涨幅抢筹数据 9:20/9:25 快照采集后可用' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="qc-panel">
          <div class="qc-panel-title"><i class="fa fa-bolt"></i> 最后一秒竞价涨幅</div>
          <table class="stock-table">
            <thead>
              <tr>
                <th>排名</th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('code') }" @click="qcLastSort.onSort('code', 'string')">代码<span class="sort-ind">{{ qcLastSort.ind('code') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('name') }" @click="qcLastSort.onSort('name', 'string')">名称<span class="sort-ind">{{ qcLastSort.ind('name') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('realChange') }" @click="qcLastSort.onSort('realChange')">实时涨幅<span class="sort-ind">{{ qcLastSort.ind('realChange') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('bidAmt') }" @click="qcLastSort.onSort('bidAmt')">竞价金额<span class="sort-ind">{{ qcLastSort.ind('bidAmt') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('qcDeltaLast') }" @click="qcLastSort.onSort('qcDeltaLast')">抢筹幅度<span class="sort-ind">{{ qcLastSort.ind('qcDeltaLast') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('bidRatio') }" @click="qcLastSort.onSort('bidRatio')">竞额/昨比<span class="sort-ind">{{ qcLastSort.ind('bidRatio') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('bidChange') }" @click="qcLastSort.onSort('bidChange')">竞价涨幅<span class="sort-ind">{{ qcLastSort.ind('bidChange') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('floatMv') }" @click="qcLastSort.onSort('floatMv')">流通<span class="sort-ind">{{ qcLastSort.ind('floatMv') }}</span></th>
                <th class="sortable" :class="{ active: qcLastSort.keyOf('board') }" @click="qcLastSort.onSort('board', 'string')">概念<span class="sort-ind">{{ qcLastSort.ind('board') }}</span></th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(q, idx) in qcLastSort.sorted(qcLastList)" :key="'b' + q.code">
                <td class="rank-col">{{ idx + 1 }}</td>
                <td class="code-click" @click="linkToSoftware(q.code)">{{ q.code }}</td>
                <td class="name-col"><div class="name-main">{{ q.name }}</div></td>
                <td :class="q.realChange > 0 ? 'up' : q.realChange < 0 ? 'down' : 'dim'">{{ q.realChange !== null && q.realChange !== undefined ? signed(q.realChange) + '%' : '-' }}</td>
                <td :class="q.bidAmt > 0 ? 'up' : 'dim'">{{ amtText(q.bidAmt) }}</td>
                <td :class="q.qcDeltaLast > 0 ? 'up' : q.qcDeltaLast < 0 ? 'down' : 'dim'"><b>{{ signed(q.qcDeltaLast) }}%</b></td>
                <td :class="q.bidRatio !== null && q.bidRatio !== undefined ? (q.bidRatio >= 20 ? 'ratio-hot' : q.bidRatio >= 10 ? 'ratio-warm' : '') : 'dim'" :title="q.bidRatio !== null && q.bidRatio !== undefined ? ('今日竞价额 ÷ 昨日全天成交额 = ' + q.bidRatio.toFixed(2) + '%') : ''">{{ q.bidRatio !== null && q.bidRatio !== undefined ? q.bidRatio.toFixed(2) + '%' : '-' }}</td>
                <td :class="q.bidChange > 0 ? 'up' : q.bidChange < 0 ? 'down' : 'dim'">{{ signed(q.bidChange) }}%</td>
                <td>{{ q.floatMv ? (q.floatMv / 1e8).toFixed(1) + '亿' : '-' }}</td>
                <td class="dim qc-board" :title="q.board">{{ q.board ? q.board.split('、').join(' ') : '-' }}</td>
                <td><button class="pool-add-btn" :class="{ added: inPool(q.code) }" @click.stop="addToPool(q)">{{ inPool(q.code) ? '已入池' : '＋池' }}</button></td>
              </tr>
              <tr v-if="!qcLastList.length">
                <td colspan="11" class="snap-empty">最后一秒数据 9:25 后可用（9:24 时点采集后）</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 昨日涨停(今日竞价表现) -->
      <table v-else-if="tab === 'yestZt'" class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('code') }" @click="yestZtSort.onSort('code', 'string')">代码<span class="sort-ind">{{ yestZtSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('name') }" @click="yestZtSort.onSort('name', 'string')">名称<span class="sort-ind">{{ yestZtSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('limitUpDays') }" @click="yestZtSort.onSort('limitUpDays')">连板<span class="sort-ind">{{ yestZtSort.ind('limitUpDays') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('change') }" @click="yestZtSort.onSort('change')">实时涨幅<span class="sort-ind">{{ yestZtSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('bidTurnover') }" @click="yestZtSort.onSort('bidTurnover')">竞价换手<span class="sort-ind">{{ yestZtSort.ind('bidTurnover') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('bidAmt') }" @click="yestZtSort.onSort('bidAmt')">竞额(亿)<span class="sort-ind">{{ yestZtSort.ind('bidAmt') }}</span></th>
            <th class="sortable" :class="{ active: yestZtSort.keyOf('board') }" @click="yestZtSort.onSort('board', 'string')">概念<span class="sort-ind">{{ yestZtSort.ind('board') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(z, idx) in yestZtSort.sorted(yestZtList)" :key="z.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(z.code)">{{ z.code }}</td>
            <td class="name-col">
<div class="name-main">{{ z.name }}</div>
              <span v-if="z.stillLimit" class="lb-badge">连板</span>
</td>
            <td><span v-if="z.limitUpDays > 0" class="lb-badge">{{ z.limitUpDays }}板</span><span v-else class="dim">-</span></td>
            <td :class="z.change > 0 ? 'up' : z.change < 0 ? 'down' : 'dim'">{{ z.change !== null && z.change !== undefined ? signed(z.change) + '%' : '-' }}</td>
            <td>{{ z.bidTurnover ? z.bidTurnover.toFixed(2) : '-' }}</td>
            <td>{{ z.bidAmt ? amtText(z.bidAmt) : '-' }}</td>
            <td class="dim" style="max-width:150px;white-space:pre-wrap;">{{ z.board || '-' }}</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(z.code) }" @click.stop="addToPool(z)">{{ inPool(z.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
        </tbody>
      </table>

      <!-- 昨断板(昨涨停今断) -->
      <table v-else-if="tab === 'yestBroken'" class="stock-table">
        <thead>
          <tr>
            <th>排名</th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('code') }" @click="yestBrokenSort.onSort('code', 'string')">代码<span class="sort-ind">{{ yestBrokenSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('name') }" @click="yestBrokenSort.onSort('name', 'string')">名称<span class="sort-ind">{{ yestBrokenSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('yestChange') }" @click="yestBrokenSort.onSort('yestChange')">昨涨幅<span class="sort-ind">{{ yestBrokenSort.ind('yestChange') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('bidChange') }" @click="yestBrokenSort.onSort('bidChange')">今日竞价涨幅<span class="sort-ind">{{ yestBrokenSort.ind('bidChange') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('bidAmt') }" @click="yestBrokenSort.onSort('bidAmt')">竞额(亿)<span class="sort-ind">{{ yestBrokenSort.ind('bidAmt') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('bidTurnover') }" @click="yestBrokenSort.onSort('bidTurnover')">竞价换手<span class="sort-ind">{{ yestBrokenSort.ind('bidTurnover') }}</span></th>
            <th class="sortable" :class="{ active: yestBrokenSort.keyOf('board') }" @click="yestBrokenSort.onSort('board', 'string')">概念<span class="sort-ind">{{ yestBrokenSort.ind('board') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(b2, idx) in yestBrokenSort.sorted(yestBrokenList)" :key="b2.code">
            <td class="rank-col">{{ idx + 1 }}</td>
            <td class="code-click" @click="linkToSoftware(b2.code)">{{ b2.code }}</td>
            <td class="name-col"><div class="name-main">{{ b2.name }}</div></td>
            <td :class="b2.yestChange > 0 ? 'up' : 'down'">{{ signed(b2.yestChange) }}%</td>
            <td :class="b2.bidChange > 0 ? 'up' : b2.bidChange < 0 ? 'down' : 'dim'">{{ b2.bidChange !== null && b2.bidChange !== undefined ? signed(b2.bidChange) + '%' : '-' }}</td>
            <td>{{ b2.bidAmt ? amtText(b2.bidAmt) : '-' }}</td>
            <td>{{ b2.bidTurnover ? b2.bidTurnover.toFixed(2) : '-' }}</td>
            <td class="dim" style="max-width:150px;white-space:pre-wrap;">{{ b2.board || '-' }}</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(b2.code) }" @click.stop="addToPool(b2)">{{ inPool(b2.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
        </tbody>
      </table>

      <!-- 昨上榜(龙虎榜) -->
      <table v-else-if="tab === 'lhb'" class="stock-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: lhbSort.keyOf('code') }" @click="lhbSort.onSort('code', 'string')">代码<span class="sort-ind">{{ lhbSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('name') }" @click="lhbSort.onSort('name', 'string')">名称<span class="sort-ind">{{ lhbSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('change') }" @click="lhbSort.onSort('change')">涨幅%<span class="sort-ind">{{ lhbSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('limitBoards') }" @click="lhbSort.onSort('limitBoards')">连板<span class="sort-ind">{{ lhbSort.ind('limitBoards') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('buyIn') }" @click="lhbSort.onSort('buyIn')">买入(亿)<span class="sort-ind">{{ lhbSort.ind('buyIn') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('amount') }" @click="lhbSort.onSort('amount')">成交额(亿)<span class="sort-ind">{{ lhbSort.ind('amount') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('turnover') }" @click="lhbSort.onSort('turnover')">换手%<span class="sort-ind">{{ lhbSort.ind('turnover') }}</span></th>
            <th class="sortable" :class="{ active: lhbSort.keyOf('amplitude') }" @click="lhbSort.onSort('amplitude')">振幅%<span class="sort-ind">{{ lhbSort.ind('amplitude') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in lhbSort.sorted(lhbList)" :key="l.code">
            <td class="code-click" @click="linkToSoftware(l.code)">{{ l.code }}</td>
            <td class="name-col"><div class="name-main">{{ l.name }}</div></td>
            <td :class="l.change > 0 ? 'up' : 'down'">{{ signed(l.change) }}%</td>
            <td><span v-if="l.limitBoards > 0" class="lb-badge">{{ l.limitBoards }}板</span><span v-else class="dim">-</span></td>
            <td :class="l.buyIn > 0 ? 'up' : 'dim'">{{ yi(l.buyIn) }}</td>
            <td>{{ yi(l.amount) }}</td>
            <td>{{ l.turnover.toFixed(2) }}</td>
            <td>{{ l.amplitude.toFixed(2) }}</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(l.code) }" @click.stop="addToPool(l)">{{ inPool(l.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
        </tbody>
      </table>

      <!-- 炸板(昨/今) -->
      <table v-else-if="tab === 'brokenYest' || tab === 'brokenToday'" class="stock-table">
        <thead>
          <tr>
            <th class="sortable" :class="{ active: brokenSort.keyOf('code') }" @click="brokenSort.onSort('code', 'string')">代码<span class="sort-ind">{{ brokenSort.ind('code') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('name') }" @click="brokenSort.onSort('name', 'string')">名称<span class="sort-ind">{{ brokenSort.ind('name') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('change') }" @click="brokenSort.onSort('change')">涨幅%<span class="sort-ind">{{ brokenSort.ind('change') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('limitUpDays') }" @click="brokenSort.onSort('limitUpDays')">连板<span class="sort-ind">{{ brokenSort.ind('limitUpDays') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('breakTimes') }" @click="brokenSort.onSort('breakTimes')">炸板次数<span class="sort-ind">{{ brokenSort.ind('breakTimes') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('firstLimitUp') }" @click="brokenSort.onSort('firstLimitUp')">涨停时间<span class="sort-ind">{{ brokenSort.ind('firstLimitUp') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('firstBreak') }" @click="brokenSort.onSort('firstBreak')">炸板时间<span class="sort-ind">{{ brokenSort.ind('firstBreak') }}</span></th>
            <th class="sortable" :class="{ active: brokenSort.keyOf('reason') }" @click="brokenSort.onSort('reason', 'string')">涨停原因<span class="sort-ind">{{ brokenSort.ind('reason') }}</span></th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in brokenSort.sorted(brokenList)" :key="b.code">
            <td class="code-click" @click="linkToSoftware(b.code)">{{ b.code }}</td>
            <td class="name-col"><div class="name-main">{{ b.name }}</div></td>
            <td :class="b.change > 0 ? 'up' : 'down'">{{ signed(b.change) }}%</td>
            <td><span v-if="b.limitUpDays > 0" class="lb-badge">{{ b.limitUpDays }}板</span><span v-else class="dim">-</span></td>
            <td><span v-if="b.breakTimes > 1" class="bk-hot">{{ b.breakTimes }}次</span><span v-else>{{ b.breakTimes }}</span></td>
            <td class="dim">{{ fmtT(b.firstLimitUp) }}</td>
            <td class="dim">{{ fmtT(b.firstBreak) }}</td>
            <td class="dim" style="max-width:220px;white-space:pre-wrap;font-size:12px;">{{ b.reason || '-' }}</td>
            <td><button class="pool-add-btn" :class="{ added: inPool(b.code) }" @click.stop="addToPool(b)">{{ inPool(b.code) ? '已入池' : '＋池' }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 时点个股弹窗(点击多时点对比卡) -->
    <div v-if="snapModal.show" class="modal-mask" @click.self="snapModal.show = false">
      <div class="snap-modal">
        <div class="snap-head">
          <span class="snap-title">{{ snapModal.date }} {{ snapPointLabel }} · 竞价个股 <span class="dim">(点击格子查看, 共 {{ snapModal.list.length }} 只)</span></span>
          <span class="snap-close" @click="snapModal.show = false">✕</span>
        </div>
        <table v-if="snapModal.list.length" class="stock-table">
          <thead>
            <tr>
              <th>#</th>
              <th class="sortable" :class="{ active: snapSort.keyOf('code') }" @click="snapSort.onSort('code', 'string')">代码<span class="sort-ind">{{ snapSort.ind('code') }}</span></th>
              <th class="sortable" :class="{ active: snapSort.keyOf('name') }" @click="snapSort.onSort('name', 'string')">名称<span class="sort-ind">{{ snapSort.ind('name') }}</span></th>
              <th class="sortable" :class="{ active: snapSort.keyOf('bid_change') }" @click="snapSort.onSort('bid_change')">竞价涨幅<span class="sort-ind">{{ snapSort.ind('bid_change') }}</span></th>
              <th class="sortable" :class="{ active: snapSort.keyOf('bid_amt') }" @click="snapSort.onSort('bid_amt')">竞价额(万)<span class="sort-ind">{{ snapSort.ind('bid_amt') }}</span></th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in snapSort.sorted(snapModal.list)" :key="s.code">
              <td class="rank-col">{{ i + 1 }}</td>
              <td class="code-click" @click="linkToSoftware(s.code)">{{ s.code }}</td>
              <td>{{ s.name || s.code }}</td>
              <td :class="s.bid_change > 0 ? 'up' : s.bid_change < 0 ? 'down' : 'dim'">{{ signed(s.bid_change) }}%</td>
              <td>{{ wan(s.bid_amt) }}</td>
              <td><button class="pool-add-btn" :class="{ added: inPool(s.code) }" @click.stop="addToPool(s)">{{ inPool(s.code) ? '已入池' : '＋池' }}</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="snap-empty">该时点暂无数据</div>
      </div>
    </div>

    <!-- 个股三时点封单弹窗: 同一视图对比 9:15/9:20/9:25 -->
    <div v-if="stock3.show" class="modal-mask" @click.self="stock3.show = false">
      <div class="snap-modal">
        <div class="snap-head">
          <span class="snap-title">{{ stock3.data.name || stock3.code }}（{{ stock3.code }}）· {{ stock3.data.date }} 三时点封单</span>
          <span class="snap-close" @click="stock3.show = false">✕</span>
        </div>
        <table class="stock-table">
          <thead>
            <tr>
              <th>时点</th>
              <th>竞价涨幅</th>
              <th>封单额(万)</th>
              <th>流通市值(亿)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="tp in stock3Points" :key="tp">
              <td class="stock3-tp">{{ tpLabel(tp) }}</td>
              <td
v-if="stock3.data.points && stock3.data.points[tp]"
                  :class="stock3.data.points[tp].bid_change > 0 ? 'up' : stock3.data.points[tp].bid_change < 0 ? 'down' : 'dim'"
>
                {{ signed(stock3.data.points[tp].bid_change) }}%
              </td>
              <td v-else class="dim">-</td>
              <td v-if="stock3.data.points && stock3.data.points[tp] && stock3.data.points[tp].bid_buy_amt">{{ (stock3.data.points[tp].bid_buy_amt / 1e4).toFixed(0) }}</td>
              <td v-else class="dim">-</td>
              <td v-if="stock3.data.points && stock3.data.points[tp]">{{ (stock3.data.points[tp].float_mv / 1e8).toFixed(1) }}</td>
              <td v-else class="dim">-</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!hasStock3Points" class="snap-empty">该日该股暂无三时点快照（需交易日 9:15/9:20/9:25 自动采集后才有）</div>
      </div>
    </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { usePolling } from '../composables/usePolling'
import { kplBidSeal, kplBidBoom, kplBidQiangcang, kplBroken, kplLhb, kplYestBroken, kplYestZt } from '../api/kpl'
import { auctionOverview, auctionSnapshot, bidSnapshotStock, bidSnapshot3points } from '../api/stats'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr, isMemberOnlyTime, todayBj } from '../utils/time'
import { usePoolStore } from '../stores/pool'
import { showToast } from '../utils/toast'
import { useSortable } from '../composables/useSortable'
import { useUserStore } from '../stores/user'
import { yi, signed, amtText, fmtAvg, fmtT, wan } from '../utils/format'
import VipGate from '../components/VipGate.vue'

const user = useUserStore()
const pool = usePoolStore()
const tab = ref('s3')   // 默认选中三时点封单
const days = ref([])
const sealRaw = ref([])
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
const qcList = ref([])
const qcChgList = ref([])     // 涨幅抢筹(9:25涨幅−9:20涨幅, 全市场快照)
const qcLastList = ref([])
const yestZtList = ref([])
const yestBrokenList = ref([])
const loading = ref(true)
const bjTime = ref('--:--:--')
const qc20Mode = ref('amt')   // 左表口径: amt=竞额抢筹(开盘啦净额) / chg=涨幅抢筹(快照涨幅差)
const datePicker = ref('')    // 用户选的日期(空=实时)
const dataDate = ref('')      // 后端实际返回的数据日期(可能被对齐)
let autoFallback = false      // 已自动回退(避免清空后无限循环)

// ---- 个股三时点封单(9:15/9:20/9:25 一视图对比) ----
const stock3 = ref({ show: false, code: '', data: { date: '', name: '', points: {} }, loaded: false })
const stock3Points = ['9_15', '9_20', '9_25']
const TP_LABEL = { '9_15': '9:15', '9_20': '9:20', '9_25': '9:25' }
function tpLabel(tp) { return TP_LABEL[tp] || tp }

const hasStock3Points = computed(() => {
  const pts = stock3.value && stock3.value.data && stock3.value.data.points
  if (!pts) return false
  return Object.values(pts).some((v) => v)
})

async function loadStock3() {
  const code = (stock3.value.code || '').trim()
  if (!/^\d{6}$/.test(code)) { showToast('请输入 6 位股票代码', 'error'); return }
  stock3.value.loaded = false
  try {
    const d = await bidSnapshotStock(datePicker.value || todayBj(), code)
    stock3.value.data = d
    stock3.value.loaded = true
    stock3.value.show = true
  } catch (e) {
    showToast(e.message || '查询失败', 'error')
  }
}

// 各表独立排序实例
const sealSort = useSortable()
const qcSort = useSortable()
const qcLastSort = useSortable()
const yestZtSort = useSortable()
const yestBrokenSort = useSortable()
const lhbSort = useSortable()
const brokenSort = useSortable()
const snapSort = useSortable()

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
  if (s1 === 1 && s2 === 1) return { label: '持续加单', cls: 'strong', tip: '9:15→9:20→9:25 封单持续放大, 主力不断加单' + alt }
  if (s1 === -1 && s2 === 1) return { label: '尾盘回补', cls: 'mid', tip: '9:20 撤单后 9:25 重新加单, 关注是否回封' + alt }
  if (s1 === 1 && s2 === -1) return { label: '冲高回落', cls: 'weak', tip: '9:20 加单后 9:25 大幅撤单, 警惕炸板' + alt }
  if (s1 === -1 && s2 === -1) return { label: '持续撤单', cls: 'danger', tip: '9:15→9:20→9:25 封单持续减少, 开板风险高' + alt }
  if (s1 === 1 && s2 === 0) return { label: '加单后走平', cls: 'mid', tip: '9:20 加单, 9:25 持平' + alt }
  if (s1 === 0 && s2 === 1) return { label: '尾盘加单', cls: 'mid', tip: '9:25 相对 9:20 加单, 尾盘资金抢筹' + alt }
  if (s1 === 0 && s2 === -1) return { label: '尾盘撤单', cls: 'weak', tip: '9:25 相对 9:20 撤单' + alt }
  if (s1 === -1 && s2 === 0) return { label: '撤单后走平', cls: 'weak', tip: '9:20 撤单, 9:25 持平' + alt }
  return { label: '封单走平', cls: 'flat', tip: '三个时点封单额基本持平' + alt }
}
// 概念文本: 取前 3 个标签(开盘啦 board 是逗号分隔的多概念, 避免一格撑破)
function boardText(b) {
  if (!b) return '-'
  const tags = b.split(',').map(s => s.trim()).filter(Boolean)
  return tags.slice(0, 3).join('·') + (tags.length > 3 ? '…' : '')
}
function mvText(it) {
  const p = it.points && (it.points['9_25'] || it.points['9_20'] || it.points['9_15'])
  return p && p.float_mv ? (p.float_mv / 1e8).toFixed(1) : '-'
}

// 切 Tab 时清掉排序(避免跨表残留的 key 干扰)
function switchTab(t) {
  tab.value = t
  sealSort.clear(); qcSort.clear(); qcLastSort.clear(); yestZtSort.clear()
  yestBrokenSort.clear(); lhbSort.clear(); brokenSort.clear()
}

const timePoints = [
  { key: '9_15', label: '9:15 竞价' },
  { key: '9_20', label: '9:20 竞价' },
  { key: '9_25', label: '9:25 竞价' }
]

// 竞价委买/爆量/净额共用表格: 数据源按 Tab 切换
const sealList = computed(() => {
  if (tab.value === 'boom') return boomList.value
  if (tab.value === 'net') return [...sealRaw.value].sort((a, b) => (b.bidNetAmt || 0) - (a.bidNetAmt || 0))
  return sealRaw.value
})

// 炸板: 昨/今 按 Tab 切换
const brokenList = computed(() => (tab.value === 'brokenYest' ? brokenYestList.value : brokenTodayList.value))

function addToPool(s) {
  const n = pool.addStocks([{ code: s.code, name: s.name }])
  showToast(n ? `✅ ${s.code} ${s.name} 已加入股票池` : `${s.code} 已在池中`, n ? 'success' : 'info')
}

// 是否已在股票池(与主页面 StockTable 一致: 已入池按钮变绿禁用)
function inPool(code) {
  return pool.stockPool.some(x => x.code === code)
}

// ---- 时点个股弹窗 ----
const snapModal = ref({ show: false, date: '', tp: '', list: [] })
const snapPointLabel = computed(() => {
  const t = timePoints.find(x => x.key === snapModal.value.tp)
  return t ? t.label : snapModal.value.tp
})
async function showSnapshot(date, tp) {
  snapModal.value = { show: true, date, tp, list: [] }
  try {
    const d = await auctionSnapshot(date, tp)
    snapModal.value.list = d.list || []
  } catch (e) { /* 静默 */ }
}

async function loadAll(fromUser = false) {
  const dt = datePicker.value
  loading.value = true
  try {
    // 历史回看: 所有接口带 date; 实时: 不带
    const [ov, seal, boom, qc, yestZt, yestBroken, lhb, brokenYest, brokenToday, s3] = await Promise.all([
      auctionOverview(dt), kplBidSeal(dt), kplBidBoom(dt), kplBidQiangcang(dt), kplYestZt(dt),
      kplYestBroken(dt), kplLhb(dt), kplBroken('', dt), kplBroken(dt ? '' : 'yesterday', dt),
      bidSnapshot3points(dt || todayBj())
    ])
    // 非交易时段(周末/节假日/盘前盘后)自动回退: 实时模式且各 tab 全空时,
    // 自动切到最近交易日(overview.days[0].date = 最近有 snapshot_bid 数据的日期)
    if (!dt && !autoFallback && ov.days && ov.days.length) {
      const lastTrading = ov.days[0].date
      const allEmpty = !(seal.list && seal.list.length) && !(boom.list && boom.list.length) &&
                       !(qc.list20 && qc.list20.length) && !(qc.list20Chg && qc.list20Chg.length)
      if (allEmpty && lastTrading) {
        autoFallback = true
        datePicker.value = lastTrading
        showToast(`当前非交易时段，自动显示最近交易日 ${lastTrading} 的数据`, 'info')
        loadAll(false)
        return
      }
    }
    autoFallback = false
    days.value = ov.days || []
    sealRaw.value = seal.list || []
    boomList.value = boom.list || []
    qcList.value = qc.list20 || []
    qcChgList.value = qc.list20Chg || []
    qcLastList.value = qc.listLast || []
    yestZtList.value = yestZt.list || []
    yestBrokenList.value = yestBroken.list || []
    lhbList.value = lhb.list || []
    brokenYestList.value = brokenYest.list || []
    brokenTodayList.value = brokenToday.list || []
    s3List.value = s3.list || []
    // 记录实际数据日期(后端可能对齐到最近交易日)
    const d = seal.date || (ov.days && ov.days.length ? ov.days[0].date : '')
    dataDate.value = d || dt || ''
    if (dt && fromUser) {
      if (!dataDate.value || dataDate.value !== dt) {
        // 对齐了或该日无历史: 提示
        if (dataDate.value) {
          showToast(`数据日期 ${dataDate.value}${dataDate.value !== dt ? '（非交易日自动对齐）' : ''}`, 'info')
        } else {
          showToast('该日期暂无历史数据（15:30 落库后可用）', 'warning')
        }
      }
    }
  } catch (e) { /* 静默 */ } finally {
    loading.value = false
  }
}

function clearDate() {
  datePicker.value = ''
  dataDate.value = ''
  autoFallback = false
  loadAll(true)
}

onMounted(() => {
  bjTime.value = bjTimeStr()
  usePolling(() => { bjTime.value = bjTimeStr() }, 1000, { immediate: false })
  loadAll()
  // 历史回看模式暂停实时刷新(每分钟拉历史无意义)
  usePolling(() => { if (!datePicker.value) loadAll() }, 60000)
})
</script>

<style scoped>
.page-back { color: var(--text-muted); cursor: pointer; font-size: 13px; margin-bottom: 12px; display: inline-block; }
.page-back:hover { color: #ffb400; }
.auc-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.auc-title { font-size: 20px; font-weight: 700; color: #ffe0a0; }
.auc-title .fa { color: #ffb400; }
.auc-sub { color: var(--text-muted); font-size: 13px; }
.auc-time { margin-left: auto; color: #aaa; font-size: 14px; font-family: monospace; }
.ov-panel { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
.ov-table { width: 100%; border-collapse: collapse; }
.ov-table th, .ov-table td { padding: 8px 10px; text-align: center; border-bottom: 1px solid var(--border-soft); }
.ov-dim { color: var(--text-muted); font-size: 12px; text-align: center; width: 90px; }
.ov-day { color: #ffe0a0; font-size: 13px; }
.ov-date { font-size: 14px; font-weight: 700; }
.ov-yizi { font-size: 12px; color: #ffb400; margin-top: 2px; }
.ov-yizi b { color: #ff6a6a; }
.ov-cell { font-size: 14px; font-weight: 600; }
.ov-amt { font-size: 11px; font-weight: 400; }
.auc-tabs { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.auc-tab {
  padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border-soft);
  background: var(--bg-hover); color: var(--text-secondary); font-size: 14px; cursor: pointer; transition: all 0.2s;
}
.auc-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.auc-tab.active { background: rgba(255,180,0,0.15); border-color: #ffb400; color: #ffd700; font-weight: 600; }
.auc-panel { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px; }
/* 竞价抢筹左右双表 */
.qc-dual { display: flex; flex-direction: column; gap: 10px; }
.qc-panel { width: 100%; background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 10px; overflow-x: auto; }
.qc-panel-title { font-size: 14px; font-weight: 700; color: #ffe0a0; margin-bottom: 8px; display: flex; align-items: center; gap: 10px; }
.qc-mode-switch { display: inline-flex; gap: 4px; margin-left: auto; }
.qc-mode-switch button {
  font-size: 11px; padding: 2px 10px; border-radius: 4px; cursor: pointer;
  background: var(--border-soft); border: 1px solid rgba(255,255,255,0.18);
  color: #aaa; transition: all 0.2s;
}
.qc-mode-switch button.active { background: rgba(255,180,0,0.18); border-color: #ffb400; color: #ffd700; font-weight: 600; }
.qc-mode-switch button:hover { border-color: #ffb400; color: #ffe0a0; }
.qc-panel .stock-table { width: 100%; table-layout: fixed; border-collapse: collapse; }
/* 所有列间距紧凑: padding 一律 4px 2px(列间 4px, 数值紧凑) */
.qc-panel .stock-table th, .qc-panel .stock-table td { padding: 4px 2px; font-size: 12px; white-space: nowrap; }
.qc-panel .stock-table th { color: #ffe0a0; font-weight: 600; border-bottom: 1px solid rgba(255,180,0,0.3); }
.qc-panel .stock-table td { border-bottom: 1px solid rgba(255,255,255,0.04); }
/* 缩列宽到适应窄屏(>=600px panel): 11 列总宽 ≈ 582px(概念列随内容, 不换行) */
.qc-panel .stock-table th:nth-child(1), .qc-panel .stock-table td:nth-child(1) { width: 26px; text-align: center; }
.qc-panel .stock-table th:nth-child(2), .qc-panel .stock-table td:nth-child(2) { width: 56px; }
.qc-panel .stock-table th:nth-child(3), .qc-panel .stock-table td:nth-child(3) { width: 72px; }
.qc-panel .stock-table th:nth-child(4), .qc-panel .stock-table td:nth-child(4) { width: 56px; }
.qc-panel .stock-table th:nth-child(5), .qc-panel .stock-table td:nth-child(5) { width: 62px; }
.qc-panel .stock-table th:nth-child(6), .qc-panel .stock-table td:nth-child(6) { width: 64px; }
.qc-panel .stock-table th:nth-child(7), .qc-panel .stock-table td:nth-child(7) { width: 42px; }
.qc-panel .stock-table th:nth-child(8), .qc-panel .stock-table td:nth-child(8) { width: 50px; }
.qc-panel .stock-table th:nth-child(9), .qc-panel .stock-table td:nth-child(9) { width: 50px; }
.qc-panel .stock-table th:nth-child(10), .qc-panel .stock-table td:nth-child(10) { width: 240px; overflow: hidden; text-overflow: ellipsis; }
/* 操作列(普通列, 不 sticky, 避免 flex 失衡; 通过 overflow-x: auto 横向滚动可见) */
.qc-panel .stock-table th:nth-child(11), .qc-panel .stock-table td:nth-child(11) { width: 52px; text-align: center; padding: 4px 2px; }
.qc-panel .name-main { font-size: 13px; line-height: 1.3; }
/* 窄屏(<1280px) 纵向堆叠; <1100 已原有 fallback */
@media (max-width: 1280px) { .qc-dual { gap: 8px; } }
.loading-placeholder { text-align: center; padding: 40px; color: var(--text-muted); }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(255,180,0,0.3); border-top-color: #ffb400; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 10px; }
@keyframes spin { to { transform: rotate(360deg); } }
.lb-badge { display: inline-block; color: #ff8a5c; border: 1px solid rgba(255,80,40,0.5); border-radius: 4px; padding: 0 5px; font-size: 11px; background: rgba(255,80,40,0.12); }
.bk-hot { color: var(--accent-deep); font-weight: 700; }
.ov-click { cursor: pointer; }
.ov-click:hover { background: rgba(255,180,0,0.08); }

/* 时点个股弹窗(脱离 flex, 固定定位自居中, 不受 flex item 收缩影响) */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 1000; }

/* 个股三时点封单工具条 */
.stock3-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  flex-wrap: wrap;
}
.stock3-input {
  background: var(--bg-input);
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  color: var(--text-main);
  padding: 6px 10px;
  font-size: 13px;
  width: 200px;
  box-sizing: border-box;
}
.stock3-input:focus { outline: none; border-color: var(--accent-warm, #ffb400); }
.stock3-btn {
  background: rgba(0, 180, 255, 0.15);
  border: 1px solid #00b4ff;
  color: #a0e0ff;
  border-radius: 6px;
  padding: 6px 16px;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}
.stock3-btn:hover { background: rgba(0, 180, 255, 0.28); }
.stock3-tp { color: var(--accent-warm, #ffb400); font-weight: 600; }

/* 三时点封单榜状态标签: 层1(9:25封死)=红金 / 层2(9:20回落)=橙 / 层3(9:15回落)=黄 */
.s3-tag { display: inline-block; padding: 1px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }
.s3-tag-1 { color: #ffd700; border: 1px solid #ffd700; background: rgba(255, 215, 0, 0.1); }
.s3-tag-2 { color: #ffb347; border: 1px solid #ffb347; background: rgba(255, 180, 71, 0.1); }
.s3-tag-3 { color: #a0e0ff; border: 1px solid #00b4ff; background: rgba(0, 180, 255, 0.1); }
body[data-bg="light"] .s3-tag-1 { color: #8a6a00; border-color: #c79100; }
body[data-bg="light"] .s3-tag-2 { color: #a05a10; border-color: #c79100; }
body[data-bg="light"] .s3-tag-3 { color: #005c8a; border-color: #0080a0; }

/* === 三时点封单榜表格样式: 三色分组 + 概念列 + 拆列 === */
.s3-hint {
  margin: 8px 0 12px;
  padding: 9px 12px;
  border: 1px dashed var(--accent-warm, #ffb400);
  border-radius: 8px;
  background: rgba(255, 180, 0, 0.06);
  color: var(--text-secondary);
  font-size: 12.5px;
  line-height: 1.6;
}
.s3-hint b { color: var(--accent-warm, #ffb400); }
.s3-hint-soft { border-color: var(--border-soft); background: rgba(127, 224, 192, 0.05); color: var(--text-secondary); }
.s3-hint-soft b { color: #7fe0c0; }
body[data-bg="light"] .s3-hint-soft b { color: #1a7a60; }
.s3-table { table-layout: auto; }
.s3-table .board-col { max-width: 180px; min-width: 120px; }
.board-text { color: var(--text-secondary); font-size: 12px; line-height: 1.3; }
.s3-table th.tp-th { text-align: center; font-weight: 600; }
.s3-table th.tp-th-15 { color: #5fb4ff; border-bottom: 2px solid rgba(95, 180, 255, 0.35); }
.s3-table th.tp-th-20 { color: #ffb400; border-bottom: 2px solid rgba(255, 180, 0, 0.35); }
.s3-table th.tp-th-25 { color: #ff5a5a; border-bottom: 2px solid rgba(255, 90, 90, 0.4); }
.s3-table td.tp-th-15, .s3-table td.tp-th-20, .s3-table td.tp-th-25 { text-align: center; white-space: nowrap; }
.s3-table td.chg-col { font-weight: 600; }
/* 实时涨幅列(开盘啦 realChange): 涨=红, 跌=蓝(A股忌讳绿, 避开绿色系) */
.real-chg-col { text-align: center; white-space: nowrap; font-weight: 600; font-variant-numeric: tabular-nums; }
.real-chg-col.up { color: #ff5a5a; }
.real-chg-col.down { color: #6aa0ff; }
.real-chg-col.dim { color: var(--text-muted); }
/* 9:15 涨幅: 青蓝系(亮=涨, 暗=跌) */
.chg-up-15 { color: #80d4ff; text-shadow: 0 0 6px rgba(95, 180, 255, 0.3); }
.chg-dn-15 { color: #5080c0; }
.dim-15    { color: #5a7898; }
/* 9:20 涨幅: 橙系 */
.chg-up-20 { color: #ffd566; text-shadow: 0 0 6px rgba(255, 180, 0, 0.3); }
.chg-dn-20 { color: #c08600; }
.dim-20    { color: #8a7a5a; }
/* 9:25 涨幅: 红系(亮=涨停封死, 暗=回落, 灰=平) - 9:25 最终竞价结果用 A 股主色红 */
.chg-up-25 { color: #ff6a6a; text-shadow: 0 0 6px rgba(255, 90, 90, 0.35); font-weight: 700; }
.chg-dn-25 { color: #c04848; }
.dim-25    { color: #9a5a5a; }
/* 封单额: 按时点主色, 弱色 */
.seal-col { font-variant-numeric: tabular-nums; }
.seal-col-15 { color: #80d4ff; }
.seal-col-20 { color: #ffb400; }
.seal-col-25 { color: #ff6a6a; }
body[data-bg="light"] .seal-col-15 { color: #0068b4; }
body[data-bg="light"] .seal-col-20 { color: #b07800; }
body[data-bg="light"] .seal-col-25 { color: #c82020; }
body[data-bg="light"] .chg-up-15 { color: #0068b4; }
body[data-bg="light"] .chg-up-20 { color: #8a5a00; }
body[data-bg="light"] .chg-up-25 { color: #c82020; }

/* === 加单趋势标签 === */
.seal-mode { display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.seal-mode-strong { color: #ffb400; border: 1px solid #ffb400; background: rgba(255, 180, 0, 0.12); }
.seal-mode-mid { color: #7fe0c0; border: 1px solid #4fc0a0; background: rgba(79, 192, 160, 0.12); }
.seal-mode-weak { color: #a0b8d0; border: 1px solid #6a88a8; background: rgba(106, 136, 168, 0.12); }
.seal-mode-danger { color: #ff6a6a; border: 1px solid #ff6a6a; background: rgba(255, 106, 106, 0.12); }
.seal-mode-flat { color: var(--text-muted); border: 1px solid var(--border-soft); background: transparent; }
body[data-bg="light"] .seal-mode-strong { color: #8a5a00; border-color: #c79100; background: rgba(255, 180, 0, 0.12); }
body[data-bg="light"] .seal-mode-mid { color: #1a7a60; border-color: #2a9a7a; }
body[data-bg="light"] .seal-mode-weak { color: #486080; border-color: #6a88a8; }
body[data-bg="light"] .seal-mode-danger { color: #c82020; border-color: #c82020; }

.snap-modal { position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%); background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: 12px; width: min(1100px, 98vw); max-height: 85vh; overflow: auto; padding: 12px 14px; box-sizing: border-box; }
.snap-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.snap-title { font-size: 15px; font-weight: 700; color: #ffe0a0; }
.snap-close { cursor: pointer; color: var(--text-muted); font-size: 16px; padding: 2px 6px; }
.snap-close:hover { color: #ffb400; }
.snap-empty { text-align: center; color: var(--text-dim); padding: 30px 0; font-size: 13px; }
/* 弹窗表格: 列间距紧凑, 6 列全部可见 */
.snap-modal .stock-table { width: 100%; }
.snap-modal .stock-table th, .snap-modal .stock-table td {
  padding: 3px 6px;
  font-size: 12px;
  white-space: nowrap;
}
.snap-modal .stock-table th:nth-child(1), .snap-modal .stock-table td:nth-child(1) { width: 28px; text-align: center; padding-left: 0; padding-right: 4px; }
.snap-modal .stock-table th:nth-child(2), .snap-modal .stock-table td:nth-child(2) { width: 72px; }
.snap-modal .stock-table th:nth-child(4), .snap-modal .stock-table td:nth-child(4) { width: 74px; }

/* 浅色主题: 加深原 scoped 内的浅色文字 */
body[data-bg="light"] .auc-title { color: #8a5500; }
body[data-bg="light"] .auc-title .fa { color: #c79100; }
body[data-bg="light"] .auc-tab:hover { color: #8a5500; border-color: #c79100; }
body[data-bg="light"] .auc-tab.active { color: #8a5500; background: rgba(255,180,0,0.12); border-color: #c79100; }
body[data-bg="light"] .ov-day { color: #8a5500; }
body[data-bg="light"] .ov-yizi { color: #8a5500; }
body[data-bg="light"] .ov-yizi b { color: #b83010; }
body[data-bg="light"] .page-back { color: #5a6b85; }
body[data-bg="light"] .page-back:hover { color: #c79100; }
body[data-bg="light"] .auc-panel { background: rgba(255,255,255,0.85); border-color: var(--border-soft); }
body[data-bg="light"] .qc-panel { background: rgba(255,255,255,0.85); border-color: var(--border-soft); }
body[data-bg="light"] .qc-panel-title { color: #5a4a3a; }
body[data-bg="light"] .qc-mode-switch button { color: #5a6b85; border-color: var(--border-soft); background: rgba(255,255,255,0.6); }
body[data-bg="light"] .qc-mode-switch button.active { color: #5a4a3a; background: rgba(255,180,0,0.15); border-color: #c79100; }
body[data-bg="light"] .qc-mode-switch button:hover { color: #5a4a3a; border-color: #c79100; }
body[data-bg="light"] .qc-panel .stock-table th { color: #5a4a3a; border-bottom-color: rgba(199,145,0,0.4); }
body[data-bg="light"] .qc-panel .stock-table td { border-bottom-color: rgba(0,0,0,0.08); }
body[data-bg="light"] .qc-panel .stock-table tbody tr:hover { background: rgba(184,48,16,0.04); }
body[data-bg="light"] .lb-badge { color: #b83010; border-color: rgba(184,48,16,0.5); background: rgba(255,80,80,0.1); }
body[data-bg="light"] .bk-hot { color: #b83010; }
body[data-bg="light"] .snap-title { color: #5a4a3a; }
body[data-bg="light"] .snap-close { color: #5a6b85; }
body[data-bg="light"] .snap-close:hover { color: #c79100; }
body[data-bg="light"] .snap-empty { color: #6a7a90; }
body[data-bg="light"] .modal-mask { background: rgba(0,0,0,0.45); }
</style>
