<template>
  <div class="page-shell">
    <div class="page-back" @click="$router.push('/')"><i class="fa fa-arrow-left"></i> 返回选股</div>

    <!-- 会员门禁: 竞价异动仅在工作日 9:15-15:00 要求会员; 其他时段放开 -->
    <VipGate v-if="!user.isMember && isMemberOnlyTime()" title="竞价异动" />

    <template v-else>
    <div class="auc-head">
      <span class="auc-title"><i class="fa fa-bullhorn"></i> 竞价异动</span>
      <span class="auc-sub">多时点对比 · 竞价委买/爆量/净额/上榜/炸板（开盘啦 + 东财）</span>
      <span class="auc-time">{{ bjTime }}</span>
    </div>

    <!-- 顶部多时点对比(最近4个交易日) -->
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
            <td v-for="d in days" :key="d.date + tp.key" class="ov-cell ov-click" title="点击查看该时点个股"
                @click="showSnapshot(d.date, tp.key)">
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

    <!-- Tab 切换 -->
    <div class="auc-tabs">
      <button class="auc-tab" :class="{ active: tab === 'seal' }" @click="switchTab('seal')"><i class="fa fa-gavel"></i> 竞价委买</button>
      <button class="auc-tab" :class="{ active: tab === 'boom' }" @click="switchTab('boom')"><i class="fa fa-bolt"></i> 竞价爆量</button>
      <button class="auc-tab" :class="{ active: tab === 'net' }" @click="switchTab('net')"><i class="fa fa-exchange"></i> 竞价净额</button>
      <button class="auc-tab" :class="{ active: tab === 'qc' }" @click="switchTab('qc')" title="9:15-9:30 竞价抢筹(异动板块大单)"><i class="fa fa-fire"></i> 竞价抢筹</button>
      <button class="auc-tab" :class="{ active: tab === 'yestZt' }" @click="switchTab('yestZt')"><i class="fa fa-sun-o"></i> 昨日涨停</button>
      <button class="auc-tab" :class="{ active: tab === 'yestBroken' }" @click="switchTab('yestBroken')"><i class="fa fa-bell-slash"></i> 昨断板</button>
      <button class="auc-tab" :class="{ active: tab === 'lhb' }" @click="switchTab('lhb')"><i class="fa fa-list-alt"></i> 昨上榜</button>
      <button class="auc-tab" :class="{ active: tab === 'brokenYest' }" @click="switchTab('brokenYest')"><i class="fa fa-history"></i> 昨炸板</button>
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
            <td class="name-col"><div class="name-main">{{ z.name }}</div>
              <span v-if="z.stillLimit" class="lb-badge">连板</span></td>
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
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { kplBidSeal, kplBidBoom, kplBidQiangcang, kplBroken, kplLhb, kplYestBroken, kplYestZt } from '../api/kpl'
import { auctionOverview, auctionSnapshot } from '../api/stats'
import { linkToSoftware } from '../utils/tdx'
import { bjTimeStr, isMemberOnlyTime } from '../utils/time'
import { usePoolStore } from '../stores/pool'
import { showToast } from '../utils/toast'
import { useSortable } from '../composables/useSortable'
import { useUserStore } from '../stores/user'
import VipGate from '../components/VipGate.vue'

const user = useUserStore()
const pool = usePoolStore()
const tab = ref('seal')
const days = ref([])
const sealRaw = ref([])
const boomList = ref([])
const lhbList = ref([])
const brokenYestList = ref([])
const brokenTodayList = ref([])
const qcList = ref([])
const qcChgList = ref([])     // 涨幅抢筹(9:25涨幅−9:20涨幅, 全市场快照)
const qcLastList = ref([])
const yestZtList = ref([])
const yestBrokenList = ref([])
const loading = ref(true)
const bjTime = ref('--:--:--')
const qc20Mode = ref('amt')   // 左表口径: amt=竞额抢筹(开盘啦净额) / chg=涨幅抢筹(快照涨幅差)
let clockTimer = null
let refreshTimer = null

// 各表独立排序实例
const sealSort = useSortable()
const qcSort = useSortable()
const qcLastSort = useSortable()
const yestZtSort = useSortable()
const yestBrokenSort = useSortable()
const lhbSort = useSortable()
const brokenSort = useSortable()
const snapSort = useSortable()

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
const brokenTitle = computed(() => (tab.value === 'brokenYest' ? '昨炸板' : '今炸板'))

function yi(v) { return (v / 1e8).toFixed(2) }
function signed(v) { return (v > 0 ? '+' : '') + Number(v).toFixed(2) }
// 金额自适应: >=1亿 显示亿(2位), 否则显示万
function amtText(v) {
  if (!v || v <= 0) return '-'
  return v >= 1e8 ? (v / 1e8).toFixed(2) + '亿' : (v / 1e4).toFixed(0) + '万'
}
function fmtAvg(v) { return v === null || v === undefined ? '-' : (v > 0 ? '+' : '') + v.toFixed(2) + '%' }
function fmtT(ts) {
  if (!ts) return '-'
  const d = new Date((ts + 8 * 3600) * 1000)
  return d.toISOString().slice(11, 16)
}

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
function wan(v) { return v ? Number(v).toFixed(0) : '0' }

async function loadAll() {
  try {
    const [ov, seal, boom, qc, yestZt, yestBroken, lhb, brokenYest, brokenToday] = await Promise.all([
      auctionOverview(), kplBidSeal(), kplBidBoom(), kplBidQiangcang(), kplYestZt(), kplYestBroken(),
      kplLhb(), kplBroken('yesterday'), kplBroken()
    ])
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
  } catch (e) { /* 静默 */ } finally {
    loading.value = false
  }
}

onMounted(() => {
  bjTime.value = bjTimeStr()
  clockTimer = setInterval(() => { bjTime.value = bjTimeStr() }, 1000)
  loadAll()
  refreshTimer = setInterval(loadAll, 60000)
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped>
.page-shell { max-width: 1500px; margin: 0 auto; padding: 16px; }
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
