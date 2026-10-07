<template>
  <!--
    双脑竞价 · 首页看板（2026-10-06 原生版，取代原先 iframe 套独立 Flask 页）

    🔴 动态数据：本页所有数字均来自主站现有接口（无任何写死数据），
       盘中(09:15~15:05)每 30s 自动轮询；非盘中不轮询（收盘数据不再变）。
    🔴 逐块独立降级：任一接口失败只影响它自己那格，不影响整页（失败格显示 —）。
    🔴 口径纪律：接口给什么显示什么；某日无值显示「—」而**不是 0**（0 会被误读成真实值）。

    数据映射（全部为主站已有接口，零新增后端）：
      ① 大盘状态 (指数带 + 情绪 5 卡)  → components/SentimentPanel.vue（/api/kpl/index-brief）
      ② 竞价预判                      → /api/stats/auction-overview
      ③ 连板梯队 + 晋级率              → /api/kpl/zt-echelon
      ④ 昨日涨停今日表现               → /api/kpl/yest-zt + /api/kpl/index-brief 的 emo
      ⑤ 主线雷达                      → /api/kpl/board-rank（+ emo 口径的板块状态为前端规则派生）
      ⑥ 今日机会清单                   → /api/chaozhi/overview 的 picks

    样式：沿用主站令牌，字号**只用 2 档**（--fs-sm 13px 内容 / --fs-xl 18px 标题与关键数字）。
  -->
  <div class="czh">
    <!-- ① 大盘状态：直接复用主站组件（指数带 + 市场量能/涨跌家数/涨跌停/高度板/亏钱效应 5 卡） -->
    <SentimentPanel />

    <!-- ② 竞价预判（2026-10-06 加「vs 昨日同期」：每格下方一行小字给同日同时点差值） -->
    <section class="czh-sec">
      <div class="czh-head">
        <span class="czh-t">竞价预判 · 9:25 快照</span>
        <span class="czh-sub">
          {{ auc.date || '—' }}<template v-if="auc.prevDate"> ｜ 对比 {{ auc.prevDate }}</template>
        </span>
      </div>
      <div class="czh-card">
        <div class="czh-kpis">
          <div class="czh-kpi">
            <span class="czh-k">竞价资金</span>
            <b class="czh-v">{{ fmtAmt(auc.amt) }}</b>
            <span v-if="diffAmtText(auc.dAmt)" class="czh-d" :class="dirCls(auc.dAmt)">{{ diffAmtText(auc.dAmt) }}</span>
          </div>
          <div class="czh-kpi">
            <span class="czh-k">红盘率</span>
            <b class="czh-v up">{{ auc.red != null ? auc.red.toFixed(1) + '%' : '—' }}</b>
            <span v-if="diffText(auc.dRed, true)" class="czh-d" :class="dirCls(auc.dRed)">{{ diffText(auc.dRed, true) }}</span>
          </div>
          <div class="czh-kpi">
            <span class="czh-k">平均高开</span>
            <b class="czh-v" :class="dirCls(auc.avg)">{{ fmtPct(auc.avg) }}</b>
            <span v-if="diffText(auc.dAvg, true)" class="czh-d" :class="dirCls(auc.dAvg)">{{ diffText(auc.dAvg, true) }}</span>
          </div>
          <div class="czh-kpi">
            <span class="czh-k">竞价涨停</span>
            <b class="czh-v up">{{ auc.zt ?? '—' }}</b>
            <span v-if="diffText(auc.dZt)" class="czh-d" :class="dirCls(auc.dZt)">{{ diffText(auc.dZt) }}</span>
          </div>
          <div class="czh-kpi">
            <span class="czh-k">竞价样本</span>
            <b class="czh-v">{{ auc.count ?? '—' }}</b>
          </div>
        </div>
        <div v-if="auc.leaders.length" class="czh-chips czh-leaders">
          <span class="czh-ltag">龙头竞价</span>
          <!-- 2026-10-07 主人反馈「有的个股点了不弹详情」：chip 缺 data-stock-code ⇒
               App.vue 全局委托认不出，补上即与核按钮/机会清单同一套弹窗 -->
          <span
            v-for="l in auc.leaders" :key="l.code" class="czh-chip czh-chip-go"
            :data-stock-code="l.code" :data-stock-name="l.name"
            :title="'点击查看 ' + l.name + ' 个股详情 / 分时 / 日K'"
          >
            <b>{{ l.name }}</b>
            <i :class="dirCls(l.bid_change)">{{ fmtPct(l.bid_change) }}</i>
            <!-- 「新」角标（2026-10-06 主人券商式建议第 3 条）：涨幅**超出该股涨跌停限制** ⇒
                 新股 / 次新 / 无涨跌幅限制股，必须标出来，不得与正常涨停混淆 -->
            <span
              v-if="l.isNew" class="czh-new"
              title="涨幅超出该板块涨跌停限制：新股 / 次新 / 无涨跌幅限制，勿与正常涨停混淆"
            >新</span>
            <em>{{ l.tag }}</em>
          </span>
        </div>
        <!-- 口径提示（主人采纳建议原话）：9:15-9:20 可撤单 ⇒ 该时段数据仅供参考 -->
        <div class="czh-note">
          竞价资金 = 全市场 9:25 集合竞价成交额；同比为**昨日同一时点**。
          9:15–9:20 可撤单阶段数据仅供参考，9:20–9:25 不可撤单为真实竞价。
        </div>
      </div>
    </section>

    <!-- ⑦ 核按钮 / 大幅低开榜（2026-10-06 主人口径）
         档1 跌停开·极端核按钮（无论昨日是否涨停）/ 档2 核按钮（昨涨停 + 竞价 ≤−5%）
         / 档3 大幅低开（昨涨停或昨涨幅>5% + 竞价 −3%~−5%）。分档在后端算，前端只展示。
         每枚 chip 带 data-stock-code ⇒ 沿用 App.vue 全局委托弹个股详情，与机会清单同一套。 -->
    <section class="czh-sec">
      <div class="czh-head">
        <span class="czh-t">核按钮 · 大幅低开榜</span>
        <span class="czh-sub">
          {{ risk.date || '—' }} 竞价<template v-if="risk.prevDate"> ｜ 昨: {{ risk.prevDate }}</template>
        </span>
      </div>
      <div class="czh-card">
        <div v-if="!risk.tiers.length" class="czh-empty">{{ risk.note || '暂无数据' }}</div>
        <div v-else class="czh-risk">
          <div v-for="t in risk.tiers" :key="t.tier" class="czh-rtier" :class="'czh-rt' + t.tier">
            <div class="czh-rtier-h">
              <span class="czh-rlabel">档{{ t.tier }} {{ t.label }}</span>
              <span class="czh-rcnt">{{ t.total }} 只</span>
            </div>
            <div v-if="!t.items.length" class="czh-rempty">今日无</div>
            <div v-else class="czh-chips">
              <span
                v-for="it in t.items" :key="it.code" class="czh-chip czh-rchip"
                :data-stock-code="it.code" :data-stock-name="it.name"
                :title="it.name + ' ' + it.code + '｜' + it.reason + '｜' + (it.concept || '暂无概念')"
              >
                <b>{{ it.name }}</b>
                <i class="down">{{ fmtPct(it.bidChange) }}</i>
                <em v-if="it.ydayLb > 0">昨{{ it.ydayLb }}板</em>
                <em v-else-if="it.ydayZt">昨涨停</em>
                <em v-else-if="it.ydayChg !== null && it.ydayChg !== undefined">昨{{ fmtPct(it.ydayChg) }}</em>
              </span>
            </div>
          </div>
        </div>
        <div class="czh-note">
          口径：昨日涨停 = 昨日**收盘封住**涨停（盘中触及但炸板不计）；竞价涨幅取 9:25 定格价；
          涨跌停限制按个股实际判定（主板 10% / 创业·科创 20% / ST 5% / 北交所 30%）。
        </div>
      </div>
    </section>

    <!--
      ③ 连板梯队（2026-10-07 主人拍板去重）
      🔴 与 /ladder「连板天梯」同源（都走 fetch_ladder_all + rebin_ladder），
         天梯页已有分层明细 + 晋级率 + 题材主线 + 点票详情 ⇒ 这里的分层 chips 属重复展示。
      ⇒ 降级为**一行感知 + 跳转**：只留「最高 X 板 / 共 N 只 / 首板 M 只」，
         明细统一去 /ladder 看。lad.levels 仍用于算 total/first，只是不再渲染个股。
    -->
    <section class="czh-sec">
      <div class="czh-card czh-ladgo">
        <div class="czh-ladgo-n">
          最高 <b>{{ lad.max }}</b> 板 · 共 <b>{{ lad.total }}</b> 只 · 首板 <b>{{ lad.first }}</b> 只
        </div>
        <router-link class="czh-ladgo-a" to="/ladder">
          查看完整天梯<i class="fa fa-angle-right" aria-hidden="true"></i>
        </router-link>
      </div>
    </section>

    <!-- ④ 昨日涨停今日表现（可折叠） -->
    <section class="czh-sec">
      <details class="czh-fold" :open="open.yest" @toggle="onFold('yest', $event)">
        <summary class="czh-sum">
          <span class="czh-t">昨日涨停今日表现</span>
          <span class="czh-brief">
            样本 <b>{{ yest.count ?? '—' }}</b> 只 · 平均高开 <b>{{ fmtPct(yest.avgOpen) }}</b> · 现溢价 <b>{{ fmtPct(yest.avgNow) }}</b>
          </span>
          <span class="czh-cue">{{ open.yest ? '收起' : '展开' }} ›</span>
        </summary>
        <div class="czh-fold-body">
          <div class="czh-kpis">
            <div class="czh-kpi">
              <span class="czh-k">昨日涨停</span>
              <b class="czh-v">{{ yest.count ?? '—' }}</b>
            </div>
            <div class="czh-kpi">
              <span class="czh-k">平均高开</span>
              <b class="czh-v" :class="dirCls(yest.avgOpen)">{{ fmtPct(yest.avgOpen) }}</b>
            </div>
            <div class="czh-kpi">
              <span class="czh-k">现溢价</span>
              <b class="czh-v" :class="dirCls(yest.avgNow)">{{ fmtPct(yest.avgNow) }}</b>
            </div>
            <div class="czh-kpi">
              <span class="czh-k">连板高度</span>
              <b class="czh-v gold">{{ emo.l17 != null ? emo.l17 + ' 板' : '—' }}</b>
            </div>
            <div class="czh-kpi">
              <span class="czh-k">炸板率</span>
              <b class="czh-v">{{ emo.fp108 != null ? emo.fp108.toFixed(1) + '%' : '—' }}</b>
            </div>
          </div>
          <div v-if="yest.list.length" class="czh-chips czh-yest-list">
            <!-- 2026-10-07 补 data-stock-code ⇒ 点击弹个股详情（此前点了没反应） -->
            <span
              v-for="s in yest.list.slice(0, 20)" :key="s.code"
              class="czh-chip czh-chip-go" :class="dirCls(s.change)"
              :data-stock-code="s.code" :data-stock-name="s.name"
              :title="'点击查看 ' + s.name + ' 个股详情 / 分时 / 日K'"
            >
              <b>{{ s.name }}</b><i>{{ fmtPct(s.change) }}</i>
            </span>
          </div>
        </div>
      </details>
    </section>

    <!-- ⑤ 主线雷达 -->
    <section class="czh-sec">
      <div class="czh-head">
        <span class="czh-t">主线雷达</span>
        <span class="czh-sub">钱在哪个方向 · 按板块涨幅排序</span>
      </div>
      <div v-if="!boards.length" class="czh-card czh-empty">板块榜暂无数据</div>
      <div v-else class="czh-boards">
        <div v-for="b in boards" :key="b.name" class="czh-board">
          <div class="czh-bt">
            <span>{{ b.name }}</span>
            <span class="czh-pill" :class="'p-' + pillCls(b.status)">{{ b.status }}</span>
          </div>
          <div class="czh-flow">
            板块 <b :class="dirCls(b.change)">{{ fmtSigned(b.change) }}</b>
            · 净流入 <b :class="dirCls(b.flow)">{{ b.flow.toFixed(1) }}亿</b>
          </div>
          <div class="czh-lrow">
            <span class="czh-ltag">龙头</span>
            <template v-if="b.leader"><span
              class="czh-chip-go" :data-stock-code="b.leader.code" :data-stock-name="b.leader.name"
              :title="'点击查看 ' + b.leader.name + ' 个股详情 / 分时 / 日K'"
            >{{ b.leader.name }}</span> <b :class="dirCls(b.leader.gap)">{{ b.leader.gap }}</b> · {{ b.leader.state }}</template>
            <template v-else>—</template>
          </div>
          <div class="czh-lrow">
            <span class="czh-ltag core">中军</span>
            <template v-if="b.core"><span
              class="czh-chip-go" :data-stock-code="b.core.code" :data-stock-name="b.core.name"
              :title="'点击查看 ' + b.core.name + ' 个股详情 / 分时 / 日K'"
            >{{ b.core.name }}</span> <b :class="dirCls(b.core.gap)">{{ b.core.gap }}</b> · {{ b.core.state }}<span v-if="b.core.amt" class="czh-amt">成交 {{ b.core.amt.toFixed(1) }}亿</span></template>
            <template v-else>—</template>
          </div>
          <div v-if="b.news" class="czh-news">{{ b.news }}</div>
          <div class="czh-health" :class="'h-' + healthCls(b.health)">{{ b.health }}</div>
        </div>
      </div>
    </section>

    <!-- ⑥ 今日机会清单 -->
    <section class="czh-sec">
      <div class="czh-head">
        <span class="czh-t">今日机会清单 · AI 综合评分</span>
        <span class="czh-sub">{{ shownPicks.length }} 只</span>
      </div>
      <div class="czh-card">
        <div class="czh-tabs">
          <span
            v-for="t in strats" :key="t" class="czh-tab"
            :class="{ on: curSt === t }" @click="curSt = t"
          >{{ t }}</span>
        </div>
        <!-- 窄屏可发现性（同 AipickReport 的 .ap-swipe-hint）：手机不显示滚动条，
             不提示用户不知道右边还有列。仅窄屏显示，桌面不占位。 -->
        <div class="czh-swipe-hint"><i class="fa fa-arrows-h"></i> 左右滑动查看全部列</div>
        <div v-if="!shownPicks.length" class="czh-empty">当前条件下今日无入池标的</div>
        <!-- 列口径（不许误读）：
             综合评分 = 后端 scoreFused，**池内相对强度**(百分位加权)，不是上涨概率；
             金睛/火眼 = 两模型**原始概率**(%)。两者同屏，才能看出"排名靠前但概率很低"的票。
             标签 = 后端 _tag 规则（任一模型概率 <60 即为「谨慎」）。
             表头可点击排序（# 列恢复默认序），规则与 AI 精选报告一致。 -->
        <div v-else class="czh-table-scroll">
          <table class="czh-table">
            <thead>
              <tr>
                <th class="rk czh-th" @click="resetSort()" title="恢复默认排序（综合评分降序）">#</th>
                <th class="name-col" :class="thCls('name')" @click="toggleSort('name')" title="点击按名称排序">股票</th>
                <th class="sc" :class="thCls('scoreFused')" @click="toggleSort('scoreFused')"
                    title="池内相对强度（百分位加权），非上涨概率；点击排序">综合评分</th>
                <th class="pr" :class="thCls('scoreXgb')" @click="toggleSort('scoreXgb')"
                    title="金睛模型输出（未校准，仅用于排序）；点击排序">金睛</th>
                <th class="pr" :class="thCls('scoreLgb')" @click="toggleSort('scoreLgb')"
                    title="火眼模型输出（未校准，仅用于排序）；点击排序">火眼</th>
                <th class="tg" :class="thCls('tag')" @click="toggleSort('tag')" title="点击按标签排序">标签</th>
                <th class="chg" :class="thCls('bidChange')" @click="toggleSort('bidChange')" title="点击按竞价涨幅排序">竞价涨幅</th>
                <th class="chg" :class="thCls('realtime')" @click="toggleSort('realtime')"
                    title="盘中实时涨幅；休市 / 回看时回退显示该交易日收盘涨幅；点击排序">实时涨幅</th>
                <th class="cc" :class="thCls('concept')" @click="toggleSort('concept')" title="开盘啦概念；点击排序">概念</th>
              </tr>
            </thead>
            <tbody>
            <tr v-for="(p, i) in shownPicks" :key="p.code">
              <td class="rk">{{ i + 1 }}</td>
              <!-- 股票列：名称 /（代码 · 连板）**两行**，结构照 AI 精选报告（.name-main + .name-sub）。
                   单元格带 data-stock-code ⇒ App.vue 的全局委托自动弹「个股详情 / 分时 / 日K」，
                   与其他板块同一套弹窗，本页不另写逻辑。 -->
              <td class="name-col" :data-stock-code="p.code" :data-stock-name="p.name"
                  :title="'点击查看个股详情 / 分时 / 日K' + (boardText(p) ? '｜' + boardText(p) + ' = 上一交易日收盘时的连板高度（本清单盘前生成，不含当日结果）' : '')">
                <div class="name-main">{{ p.name }}</div>
                <div class="name-sub">
                  {{ p.code }}<span v-if="boardText(p)" class="czh-board">{{ boardText(p) }}</span>
                </div>
              </td>
              <td class="sc">{{ p.scoreFused ?? '—' }}</td>
              <td class="pr">{{ p.scoreXgb ?? '—' }}</td>
              <td class="pr">{{ p.scoreLgb ?? '—' }}</td>
              <td class="tg"><span class="czh-tag" :class="tagCls(p.tag)">{{ p.tag || '—' }}</span></td>
              <td class="chg" :class="dirCls(p.bidChange)">{{ fmtPct(p.bidChange) }}</td>
              <td class="chg" :class="rtCls(p.code)">{{ rtText(p.code) }}</td>
              <td class="cc" :title="p.concept || '暂无概念'">{{ conceptText(p.concept) }}</td>
            </tr>
          </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import SentimentPanel from '../components/SentimentPanel.vue'
import {
  chaozhiOverview, chaozhiRiskList, kplBoardRank, kplBoardStocks, kplHotPlates, kplIndexBrief, kplYestZt, kplZtEchelon,
} from '../api/kpl'
import { aipickRealtime } from '../api/aipick'
import { auctionOverview } from '../api/stats'
import { isIntradayNow } from '../utils/time'

// 2026-10-07: ladder 折叠键随③降级下线（③不再是可折叠块）；yest 保持可折叠
const DEFAULT_OPEN = { yest: true }                 // 折叠状态（记 localStorage）
const open = reactive({ ...DEFAULT_OPEN })
try {
  const saved = JSON.parse(localStorage.getItem('czh_fold') || 'null')
  if (saved && typeof saved === 'object') Object.assign(open, saved)
} catch (e) { /* 忽略损坏的本地状态 */ }
function onFold(k, ev) {
  open[k] = ev.target.open
  try { localStorage.setItem('czh_fold', JSON.stringify({ ...open })) } catch (e) { /* 隐私模式等 */ }
}

// ---------------- ② 竞价 ----------------
// red_ratio / zt_count / leaders 由后端在 /api/stats/auction-overview 内按**全市场快照**算出
// （口径 = 旧独立页 realdata._build_auction：红盘率 = 竞价明细里 bid_change>0 的占比）
// ★ 2026-10-06（主人采纳券商式建议 P0）：加「vs 昨日同期」。
//   后端本接口**一直返回最近 4 个交易日 × 3 时点**(9:15/9:20/9:25)，此前前端只用了 days[0] ⇒
//   同比数据白躺着。这里补读 days[1] 的同一时点(9:25)算差值 —— **纯前端，零新增接口**。
const auc = ref({
  date: '', amt: null, avg: null, count: null, yizi: null, red: null, zt: null, leaders: [],
  prevDate: '', dAmt: null, dRed: null, dAvg: null, dZt: null,
})
async function loadAuc() {
  try {
    const d = await auctionOverview()
    const days = (d && d.days) || []
    const day = days[0] || null
    const prev = days[1] || null                       // 昨日（同一份响应里就有）
    const p = (day && day.points && day.points['9_25']) || null
    const q = (prev && prev.points && prev.points['9_25']) || null
    // 任一侧缺值 ⇒ null（前端显示 — 而非 0；0 会被误读成"真的持平"）
    const sub = (a, b) => ((a === null || a === undefined || b === null || b === undefined)
      ? null : a - b)
    auc.value = {
      date: (day && day.date) || '',
      amt: p ? p.total_amt : null,
      avg: p ? p.avg_change : null,
      count: p ? p.count : null,
      yizi: day ? day.yizi_count : null,
      red: p ? p.red_ratio : null,
      zt: p ? p.zt_count : null,
      leaders: (p && p.leaders) || [],
      prevDate: (prev && prev.date) || '',
      dAmt: sub(p && p.total_amt, q && q.total_amt),
      dRed: sub(p && p.red_ratio, q && q.red_ratio),
      dAvg: sub(p && p.avg_change, q && q.avg_change),
      dZt: sub(p && p.zt_count, q && q.zt_count),
    }
  } catch (e) { /* 静默：保持 — */ }
}
/** 竞价资金同比：金额用「放量/缩量」表述（金额符号本身不好读） */
function diffAmtText(v) {
  if (v === null || v === undefined) return ''
  if (v === 0) return '较昨持平'
  return (v > 0 ? '较昨放量 ' : '较昨缩量 ') + fmtAmt(Math.abs(v))
}
/** 其余指标的同比：+x / -x（pp = 百分点） */
function diffText(v, pp = false) {
  if (v === null || v === undefined) return ''
  return '较昨 ' + (v > 0 ? '+' : '') + v.toFixed(pp ? 1 : 0) + (pp ? 'pp' : '')
}

// ---------------- ⑦ 核按钮 / 大幅低开榜（2026-10-06 主人口径） ----------------
// 分档判定在后端（chaozhi.load_risk_list）：档1 跌停开·极端核按钮 / 档2 核按钮 / 档3 大幅低开。
// 前端只做展示：三档色块（越靠上越红），每档内按低开幅度排序（后端已排好）。
const risk = ref({ date: '', prevDate: '', tiers: [], note: '' })
async function loadRisk() {
  try {
    const d = await chaozhiRiskList()
    risk.value = {
      date: (d && d.date) || '',
      prevDate: (d && d.prevDate) || '',
      tiers: (d && d.tiers) || [],
      note: (d && d.notes && d.notes[0]) || '',
    }
  } catch (e) { /* 静默：整块显示空 */ }
}

// ---------------- ③ 连板梯队 ----------------
const lad = reactive({ max: '—', total: 0, first: 0, promote: {}, levels: [] })
async function loadLadder() {
  try {
    const d = await kplZtEchelon()
    const stat = (d && d.stat) || {}
    const ladders = (d && d.ladders) || []
    const norm = ladders.map((L) => ({ ladder: Number(L.ladder) || 1, stocks: L.stocks || [] }))
      .sort((a, b) => b.ladder - a.ladder)
    lad.max = stat.maxLadder ?? '—'
    lad.levels = norm
    lad.total = norm.reduce((n, L) => n + L.stocks.length, 0)
    lad.first = (norm.find((L) => L.ladder === 1) || { stocks: [] }).stocks.length
    lad.promote = (d && d.promote) || {}
  } catch (e) { /* 静默 */ }
}

// ---------------- ④ 昨日涨停 + emo 口径 ----------------
const yest = reactive({ count: null, avgOpen: null, avgNow: null, list: [] })
const emo = ref({})
async function loadYest() {
  try {
    const d = await kplYestZt()
    const list = (d && d.list) || []
    yest.list = list
    yest.count = list.length
    yest.avgOpen = avgOf(list.map((it) => it.bidChange))
    yest.avgNow = avgOf(list.map((it) => it.change))
  } catch (e) { /* 静默 */ }
}
async function loadEmo() {
  try {
    const d = await kplIndexBrief()
    if (d && d.emo) emo.value = d.emo
  } catch (e) { /* 静默 */ }
}

// ---------------- ⑤ 主线雷达 ----------------
// 🔴 以下 4 个规则函数**照抄**旧独立双脑竞价页 backend realdata.py 的 _bstate / _bhealth / _pick_ld /
//    _stock_state（口径不得自创）。数据来源也照旧：
//      板块涨幅+主力净额 ← /api/kpl/board-rank（旧页 kpl.fetch_board_rank）
//      龙头/中军        ← /api/kpl/board-stocks（旧页 kpl.fetch_board_stocks，逐板块取成分股再选）
//      资讯             ← /api/kpl/hot-plates（旧页 kpl.fetch_hot_plates，板块名模糊匹配 description）
const boards = ref([])

function bstate(change) {
  if (change < -0.5) return '退潮'
  if (change >= 1.5) return '延续中'
  if (change > 0) return '新启动'
  return '分化'
}
function bhealth(change, flow) {
  if (change >= 2 && flow > 0) return '共振强势'
  if (change > 0) return '结构健康'
  if (change <= -1) return '退潮防守'
  return '纯情绪博弈'
}
/** 龙头 = 龙一标签（无标签则涨幅最高）；**中军 = 成交额最大**。
 *
 *  🔴 2026-10-06 主人指令改口径：中军按**成交额**选，不再按"涨幅次高"。
 *     依据（行业通行定义，主人授意核实）：中军是板块里**中大盘、成交额位居前 3、
 *     占板块总成交额 15~30%** 的那只，是大资金的主战场，作用是**压阵/稳定器** ——
 *     "龙头打高度，中军打厚度"；龙头涨停但中军大跌 ⇒ 板块内部分歧、行情不健康。
 *     ⇒ 用"涨幅次高"当军是错的：那只是第二强势股，不代表承接厚度。
 *  ⚠️ 旧实现（照抄旧独立页 `_pick_ld`）为 龙头=龙一/涨幅最高、中军=龙二/涨幅次高。
 *  若成交额最大者就是龙头 ⇒ 顺延取第 2 名，避免龙头与中军展示同一只。 */
function pickLd(sts) {
  if (!sts || !sts.length) return [null, null]
  const arr = [...sts]
  const byAmt = arr.slice().sort((a, b) => (Number(b.amount) || 0) - (Number(a.amount) || 0))
  const byChg = arr.slice().sort((a, b) => (Number(b.change) || 0) - (Number(a.change) || 0))
  const leader = arr.filter((s) => s.ladder === '龙一')[0] || byChg[0] || null
  const core = byAmt.find((s) => !leader || s.code !== leader.code) || null
  return [leader, core]
}
function stockState(s) {
  const c = Number(s.change) || 0
  if (c >= 9.8) return '涨停'
  if (c >= 5) return '加速'
  if (c >= 0) return '跟随'
  return '掉队'
}
function descOf(map, name) {
  if (map[name]) return map[name]
  for (const [hn, hd] of Object.entries(map)) {
    if (hd && (hn.includes(name) || name.includes(hn))) return hd
  }
  return ''
}
async function loadBoards() {
  try {
    const [rankRes, hotRes] = await Promise.all([
      kplBoardRank(),
      kplHotPlates().catch(() => null),      // 资讯缺失不影响板块主数据
    ])
    const descMap = {}
    for (const h of (hotRes && hotRes.list) || []) {
      if (h && h.name) descMap[h.name] = h.description || ''
    }
    const top = ((rankRes && rankRes.list) || []).slice(0, 8)
    boards.value = await Promise.all(top.map(async (b) => {
      const change = Number(b.change) || 0
      const flow = (Number(b.mainNet) || 0) / 1e8          // 元 → 亿（旧页同式）
      let leader = null
      let core = null
      if (b.boardCode) {
        try {
          const s = await kplBoardStocks(b.boardCode)
          const [l1, l2] = pickLd((s && s.list) || [])
          // 2026-10-07: 补 code —— 龙头/中军此前只留 name，全局点击委托认不出 ⇒ 点了不弹详情
          if (l1) leader = { code: l1.code, name: l1.name, gap: fmtSigned(l1.change), state: stockState(l1) }
          // 中军带成交额（元→亿）：它是"按成交额选出"的，把依据一并展示才可核对
          if (l2) {
            core = {
              code: l2.code,
              name: l2.name, gap: fmtSigned(l2.change), state: stockState(l2),
              amt: (Number(l2.amount) || 0) / 1e8,
            }
          }
        } catch (e) { /* 单板块成分股失败不影响其它板块 */ }
      }
      return {
        name: b.name || '—',
        change,
        flow,
        status: bstate(change),
        health: bhealth(change, flow),
        news: descOf(descMap, b.name || ''),
        leader,
        core,
      }
    }))
  } catch (e) { /* 静默 */ }
}

// ---------------- ⑥ 机会清单 ----------------
const picks = ref([])
const scores = ref({})
async function loadPicks() {
  try {
    const d = await chaozhiOverview()
    if (d && d.ok === true) {
      picks.value = d.picks || []
      scores.value = d.scores || {}
    }
  } catch (e) { /* 静默 */ }
}

// ---------------- 策略筛选 ----------------
const curSt = ref('全部')
const strats = computed(() => {
  const set = []
  for (const p of picks.value) {
    const s = p.strategy || p.tag
    if (s && !set.includes(s)) set.push(s)
  }
  return ['全部', ...set]
})
// ===== 列排序（2026-10-06 主人指令：表头可点击排序）=====
// 结构照 AI 精选报告 AipickReport.vue（sortKey/sortDir/sortVal/sortCompare/toggleSort/thCls）——
// 全站只有这一套排序约定：数值列首点降序、文本列首点升序；空值恒排末尾；
// 方向反馈**用高亮不用箭头**（2026-09-05 主人明确"所有表格去掉排序箭头"）。
const sortKey = ref('scoreFused')
const sortDir = ref('desc')
const NUM_COLS = ['scoreFused', 'scoreXgb', 'scoreLgb', 'bidChange', 'realtime']
function sortVal(p, k) {
  switch (k) {
    case 'name': return p.name || ''
    case 'concept': return p.concept || ''
    case 'tag': return p.tag || ''
    case 'scoreFused': return p.scoreFused ?? null
    case 'scoreXgb': return p.scoreXgb ?? null
    case 'scoreLgb': return p.scoreLgb ?? null
    case 'bidChange': return p.bidChange ?? null
    case 'realtime': return rtVal(p.code)
    default: return null
  }
}
function sortCompare(a, b) {
  const va = sortVal(a, sortKey.value); const vb = sortVal(b, sortKey.value)
  let cmp
  if (va == null && vb == null) cmp = 0
  else if (va == null) cmp = 1                 // 空值恒排末尾
  else if (vb == null) cmp = -1
  else if (typeof va === 'string' && typeof vb === 'string') cmp = va.localeCompare(vb, 'zh')
  else cmp = (Number(va) || 0) - (Number(vb) || 0)
  return sortDir.value === 'asc' ? cmp : -cmp
}
function toggleSort(key) {
  if (sortKey.value === key) { sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'; return }
  sortKey.value = key
  sortDir.value = NUM_COLS.includes(key) ? 'desc' : 'asc'
}
function thCls(key) {
  if (sortKey.value !== key) return 'czh-th'
  return sortDir.value === 'asc' ? 'czh-th sort-asc' : 'czh-th sort-desc'
}
/** # 列：一键回到后端默认序（综合评分降序） */
function resetSort() { sortKey.value = 'scoreFused'; sortDir.value = 'desc' }

const shownPicks = computed(() => {
  const base = curSt.value === '全部'
    ? picks.value
    : picks.value.filter((p) => (p.strategy || p.tag) === curSt.value)
  // slice() 防原地排序污染 picks（上方"共 N 只"与实时行情取码都读 picks）
  return base.slice().sort(sortCompare)
})

// ---------------- ⑥b 实时涨幅 / 连板 / 概念（2026-10-06 主人指令） ----------------
// 实时行情与模型无关；休市 / 回看时后端返回空 ⇒ 回退显示该交易日的收盘涨幅(dayChg)，
// 否则假期打开页面这一列会整片空白（后端已一并下发 dayChg）。
const quotes = ref({})
async function loadRealtime() {
  // ⚠️ 取码必须读 `picks` 而不是 `shownPicks`：排序键 'realtime' 经 sortVal→rtVal 依赖 quotes，
  //    若此处再依赖 shownPicks，就形成 picks→shownPicks→quotes→shownPicks 的**循环依赖**（watch 无限打圈）。
  const codes = picks.value.map((p) => p.code).filter(Boolean)
  if (!codes.length) { quotes.value = {}; return }
  try {
    const d = await aipickRealtime(codes)
    const q = (d && (d.quotes || d.data || d)) || {}
    quotes.value = (q && typeof q === 'object') ? q : {}
  } catch (e) { quotes.value = {} }
}
watch(picks, () => { loadRealtime() })

function rtVal(code) {
  const q = quotes.value[code]
  if (q && q.change !== null && q.change !== undefined) return Number(q.change)
  const p = picks.value.find((x) => x.code === code)
  return (p && p.dayChg !== null && p.dayChg !== undefined) ? Number(p.dayChg) : null
}
function rtText(code) { return fmtPct(rtVal(code)) }
function rtCls(code) { return dirCls(rtVal(code)) }

// 连板文案：档位写法**沿用连板天梯**（LadderView.vue 的 ladderText：N板 / ≥8 显示「8板+」），
//   不自造「首板 / N连板」——全站只能有一套连板档位写法。
//   但**必须带「昨」前缀**（2026-10-06 主人确认）：本清单是盘前 9:26 生成，`ydayLb` 口径为
//   「截至上一交易日收盘的连板高度」，而连板天梯显示的是**当日**高度 —— 同一天同一只票
//   会差一层（实测新华传媒：此处 6 板 / 天梯 7 板）。不加前缀用户会当成当日数、判为错。
//   后端只给原始值（ydayZt=昨日是否涨停 / ydayLb=截至昨日连板数）；非连板 / 无数据 → 不渲染。
function boardText(p) {
  const lb = Number((p && p.ydayLb) || 0)
  if (!p || !p.ydayZt || lb < 1) return ''
  return '昨' + (lb >= 8 ? '8板+' : lb + '板')
}

// 概念：只取前 2 个，避免把列撑爆（全量走 title）
function conceptText(c) {
  const s = String(c || '').trim()
  if (!s) return '—'
  const parts = s.split(/[、,，;；|]/).map((x) => x.trim()).filter(Boolean)
  return parts.length > 2 ? parts.slice(0, 2).join('、') : (parts.join('、') || '—')
}

// ---------------- 格式化（缺值一律「—」，不显示 0） ----------------
function avgOf(arr) {
  const vs = (arr || []).filter((v) => v !== null && v !== undefined && Number.isFinite(Number(v)))
  if (!vs.length) return null
  return vs.reduce((a, b) => a + Number(b), 0) / vs.length
}
function fmtPct(v) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  const n = Number(v)
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%'
}
function fmtSigned(v) {                    // 带符号一位小数（板块/个股涨幅）
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return (n > 0 ? '+' : '') + n.toFixed(1) + '%'
}
function fmtAmt(v) {                       // 元 → 亿/万，自适应
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  const n = Number(v)
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(0) + ' 亿'
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(0) + ' 万'
  return String(n)
}
function pct(v) {                          // 入参已是百分数（如 scores.promote = 22.0）
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  return Number(v).toFixed(1) + '%'
}
// 2026-10-07: pctRatio（zt-echelon.promote 晋级率格式化）随③降级下线——晋级率只在 /ladder 展示
function dirCls(v) {
  /* 🔴 2026-10-07 修「主线雷达里涨幅是灰的」：
     龙头/中军的 gap 是**已格式化好的字符串**（`gap: fmtSigned(change)` ⇒ "+20.0%"），
     Number("+20.0%") = NaN ⇒ 旧代码按"无数据"返回 'dim'，而 CSS 里**根本没有 .dim 规则**
     ⇒ 数字退回父级灰色（.czh-lrow 的 --text-secondary / .czh-flow 的 --text-muted）
     ⇒ 看上去就是"明明涨了 20% 却是灰的"。这里先剥掉 +/%/空格再解析。 */
  const n = Number(String(v).replace(/[+%\s,]/g, ''))
  if (v === null || v === undefined || v === '' || !Number.isFinite(n)) return 'dim'
  if (n === 0) return 'dim'
  return n > 0 ? 'up' : 'down'
}
// 2026-10-07: boardOf（chip 上的首题材）随③分层 chips 下线
/** 板块状态 → pill 配色类（延续中/新启动=金，分化/退潮=灰，与旧页 .pill.gold/.gray 对应） */
function pillCls(status) {
  return (status === '延续中' || status === '新启动') ? 'run' : 'out'
}
/** 健康度 → 配色类（共振强势/结构健康=好，退潮防守=坏，纯情绪博弈=中） */
function healthCls(h) {
  if (h === '共振强势') return 'good'
  if (h === '结构健康') return 'ok'
  if (h === '退潮防守') return 'bad'
  return 'warn'
}
/** 评分标签 → 配色（关注/观察/待定/谨慎，规则见后端 chaozhi._tag） */
function tagCls(tag) {
  if (tag === '关注') return 't-good'
  if (tag === '观察') return 't-ok'
  if (tag === '待定') return 't-warn'
  return 't-dim'
}
// 2026-10-07: ladText（"N板"文案）随③分层 chips 下线；/ladder 有自己的 ladderText

// ---------------- 轮询：盘中每 30s，非盘中只取一次 ----------------
let timer = null
function loadAll() {
  loadAuc(); loadRisk(); loadLadder(); loadYest(); loadEmo(); loadBoards(); loadPicks()
}
onMounted(() => {
  loadAll()
  timer = setInterval(() => { if (isIntradayNow()) loadAll() }, 30000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
/* 字号只用 2 档：--fs-sm(13px) 内容 / --fs-xl(18px) 标题与关键数字（其余层级靠颜色与字重区分） */
.czh { display: flex; flex-direction: column; gap: var(--s3); }

.czh-sec { display: flex; flex-direction: column; gap: var(--s2); }
.czh-head { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--s1) var(--s2); }
/* 2026-10-06 字号收敛为 **3 层**（主人："4 层也多了"）：
     12px(--fs-xs) 次要信息 / 13px(--fs-sm) 正文表格 / 18px(--fs-xl) 标题与关键数字。
   🔴 这正是本页**最初的设计**（见文件头注释："字号只用 2 档：--fs-sm 13px 内容 /
      --fs-xl 18px 标题与关键数字"）—— 中间几轮为局部强调多开的档位已全部收回。
   标题与关键数值**同档**，靠 字重 700 + --text-main 最高对比 与正文区分。 */
.czh-t { font-size: var(--fs-xl); font-weight: 700; color: var(--text-main); white-space: nowrap; }
.czh-sub { font-size: var(--fs-sm); color: var(--text-muted); }

.czh-card {
  background: var(--bg-panel);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-lg);
  padding: var(--s3);
}

/* 指标格：标签 / 值（值用重点档） */
.czh-kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: var(--s2); }
.czh-kpi {
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
  padding: var(--s2) var(--s3);
  text-align: center;
  min-width: 0;
}
.czh-k { display: block; font-size: var(--fs-sm); color: var(--text-muted); }
.czh-v {
  display: block;
  /* 2026-10-06 收敛为 3 层：KPI 值回到 --fs-xl(18px)，与区块标题同档。
     与标题的区分不靠字号，而靠 **font-weight 700 + --text-main 最高对比**（规范原话：
     "其余层级靠颜色与字重区分"）—— 这才是"少档位但不丢层级"的做法。 */
  font-size: var(--fs-xl); font-weight: 700;
  font-family: var(--font-mono);      /* 规范：数字列必须等宽，否则金额/涨幅对不齐 */
  color: var(--text-main);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.czh-v.up { color: var(--up); }
.czh-v.down { color: var(--down); }
/* 同比副行（2026-10-06「vs 昨日同期」）：KPI 值下方一行小字，颜色 = 差值方向 */
.czh-d {
  display: block;
  font-size: var(--fs-xs); font-weight: 500;
  font-family: var(--font-mono);      /* 同比数字同样要等宽 */
  color: var(--text-muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.czh-d.up { color: var(--up); }
.czh-d.down { color: var(--down); }
/* 口径说明小字（竞价时段可撤单提示等） */
.czh-note {
  margin-top: var(--s2);
  font-size: var(--fs-xs); color: var(--text-dim); line-height: 1.5;
}
/* 核按钮 / 大幅低开榜（2026-10-06）：三档纵向排布，越靠上越红 —— 用左侧色条 + 淡底表达，
   与全站"收敛红色滥用"的约定一致（不用整块大红底，只做档位标识）。 */
.czh-risk { display: flex; flex-direction: column; gap: var(--s3); }
.czh-rtier {
  border-left: 3px solid var(--border-soft);
  border-radius: var(--r-sm);
  padding: var(--s2) var(--s3);
  background: var(--bg-subtle);
}
.czh-rtier-h {
  display: flex; align-items: center; gap: var(--s2);
  margin-bottom: var(--s2);
}
.czh-rlabel { font-size: var(--fs-sm); font-weight: 700; }
.czh-rcnt { font-size: var(--fs-xs); color: var(--text-muted); font-family: var(--font-mono); }
.czh-rempty { font-size: var(--fs-xs); color: var(--text-dim); }
/* 档1 深红 / 档2 红 / 档3 橙 —— 全部走令牌（裸色值由 color_guard 棘轮闸门只许减不许增） */
.czh-rt1 { border-left-color: var(--accent-deep); background: var(--accent-bg); }
.czh-rt1 .czh-rlabel { color: var(--brand-soft); }
.czh-rt2 { border-left-color: var(--accent); background: var(--accent-bg); }
.czh-rt2 .czh-rlabel { color: var(--accent-text); }
.czh-rt3 { border-left-color: var(--warn); background: var(--warn-bg); }
.czh-rt3 .czh-rlabel { color: var(--warn); }
/* 榜内 chip：沿用 .czh-chip 基础样式，仅加等宽数字与可点击手感 */
.czh-rchip { cursor: pointer; font-family: var(--font-mono); }
.czh-rchip:hover { border-color: var(--accent); }
.czh-v.dim { color: var(--text-muted); }
.czh-v.gold { color: var(--gold); }

/* 折叠块 */
.czh-fold {
  background: var(--bg-panel);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-lg);
}
.czh-sum {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--s1) var(--s2);
  padding: var(--s3); cursor: pointer; list-style: none;
}
.czh-sum::-webkit-details-marker { display: none; }
.czh-brief { font-size: var(--fs-sm); color: var(--text-secondary); font-family: var(--font-mono); }
.czh-brief b { color: var(--text-main); font-weight: 700; }
.czh-cue { margin-left: auto; font-size: var(--fs-sm); color: var(--text-dim); white-space: nowrap; }
.czh-fold-body { padding: 0 var(--s3) var(--s3); border-top: 1px solid var(--border-soft); }

.czh-promo { display: flex; flex-wrap: wrap; gap: var(--s2); padding: var(--s2) 0; font-size: var(--fs-sm); color: var(--text-muted); }
.czh-promo-i { background: var(--bg-subtle); border: 1px solid var(--border-soft); border-radius: var(--r-sm); padding: 2px var(--s2); }
.czh-promo-i b, .czh-promo-d b { color: var(--text-main); font-weight: 700; font-family: var(--font-mono); }
.czh-promo-d { margin-left: auto; }

/* ③ 连板梯队降级后的一行卡片（2026-10-07）：感知数字 + 跳 /ladder
   （原 .czh-lad/.czh-lad-n 分层 chips 样式随重复展示一起下线） */
.czh-ladgo {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
}
.czh-ladgo-n { color: var(--text-secondary); font-size: var(--fs-sm); }
.czh-ladgo-n b { color: var(--text-main); font-size: var(--fs-md); }
.czh-ladgo-a {
  display: inline-flex;
  align-items: center;
  gap: var(--s1);
  color: var(--accent);
  font-size: var(--fs-sm);
  text-decoration: none;
  white-space: nowrap;
}
.czh-ladgo-a:hover { text-decoration: underline; }
.czh-chips { display: flex; flex-wrap: wrap; gap: var(--s2); min-width: 0; }
.czh-yest-list { padding-top: var(--s2); }
.czh-chip {
  display: inline-flex; align-items: center; gap: var(--s1);
  background: var(--bg-subtle); border: 1px solid var(--border-soft);
  border-radius: var(--r-md); padding: var(--s1) var(--s2);
  font-size: var(--fs-sm); color: var(--text-secondary);
}
.czh-chip b { color: var(--text-main); font-weight: 700; }
.czh-chip em { font-style: normal; color: var(--text-dim); }
.czh-chip i { font-style: normal; font-family: var(--font-mono); }
.czh-chip.up { border-color: rgba(var(--accent-rgb), 0.45); }
/* 2026-10-07: 带 data-stock-code 的可点个股（chip / 龙头·中军名）——给出可点提示，
   点击由 App.vue 全局委托弹「个股详情 / 分时 / 日K」 */
.czh-chip-go { cursor: pointer; }
.czh-chip-go:hover { border-color: var(--accent); color: var(--text-main); }
.czh-lrow .czh-chip-go:hover { text-decoration: underline; }
.czh-chip.up i { color: var(--up); }
.czh-chip.down i { color: var(--down); }

/* 主线雷达 */
.czh-boards { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--s2); }
.czh-board {
  background: var(--bg-card); border: 1px solid var(--border-soft);
  border-radius: var(--r-lg); padding: var(--s3);
}
.czh-bt { display: flex; justify-content: space-between; align-items: center; gap: var(--s2); font-weight: 700; color: var(--text-main); }
.czh-pill { font-size: var(--fs-sm); font-weight: 600; border-radius: var(--r-pill); padding: 2px var(--s2); white-space: nowrap; }
.czh-pill.p-run { background: var(--gold-bg); color: var(--gold); }
.czh-pill.p-split { background: var(--bg-subtle); color: var(--text-dim); }
.czh-pill.p-out { background: var(--bg-subtle); color: var(--text-dim); }
.czh-flow { font-size: var(--fs-sm); color: var(--text-muted); margin-top: var(--s1); }
.czh-flow b { font-family: var(--font-mono); }
.czh-flow b.up { color: var(--up); }
.czh-flow b.down { color: var(--down); }
/* 2026-10-07: .dim（0 值 / 无数据）此前**没有任何规则** ⇒ 继承父级灰，看着像"颜色坏了"。
   现在给一个明确的中性色：它是刻意的"中性"，不再靠继承撞运气。 */
.czh-flow b.dim { color: var(--text-secondary); }
/* 龙头/中军行 —— 结构照旧页 .lrow/.ltag */
.czh-lrow { display: flex; align-items: center; gap: var(--s1); font-size: var(--fs-sm); color: var(--text-secondary); margin-top: var(--s1); }
.czh-lrow b { font-family: var(--font-mono); }
.czh-lrow b.up { color: var(--up); }
.czh-lrow b.down { color: var(--down); }
.czh-lrow b.dim { color: var(--text-secondary); }
.czh-ltag {
  flex: none; font-size: var(--fs-sm); padding: 2px var(--s1); border-radius: var(--r-sm);
  background: var(--bg-subtle); color: var(--text-muted);
}
/* 中军标记：蓝色走图表的 --chart-bar（规范里唯一的中性蓝令牌），底色用同色淡值 */
.czh-ltag.core { background: var(--chart-bar-bg); color: var(--chart-bar); }
/* 中军成交额：中军本来就是"按成交额选出"的，把依据一并展示才可核对 */
.czh-amt {
  margin-left: var(--s1);
  color: var(--text-muted); font-size: var(--fs-xs);
  font-family: var(--font-mono);
}
/* 「新」角标（新股 / 无涨跌幅限制）：用金色中性底，**不用涨跌红绿** —— 它不是行情信号 */
.czh-new {
  display: inline-block; margin: 0 var(--s1); padding: 0 var(--s1);
  border-radius: var(--r-sm);
  font-size: var(--fs-xs); font-weight: 700;
  color: var(--star); background: var(--gold-bg);
}
/* 板块资讯（hot-plates 的 description，模糊匹配） */
.czh-news { font-size: var(--fs-sm); color: var(--text-dim); margin-top: var(--s2); line-height: 1.5; }
/* 健康度（旧页 _bhealth 的四档） */
.czh-health {
  margin-top: var(--s2); font-size: var(--fs-sm); font-weight: 600;
  border-radius: var(--r-sm); padding: var(--s1) var(--s2); display: inline-block;
}
/* 健康度四档 → 语义色令牌。
   ⚠️ 原 h-good 用 var(--down)（**跌**色）表达"健康"是语义混淆（同一绿色既表示"跌"又表示"好"），
   改用规范里的 --success / --success-text（成功绿），语义与色相才一致。 */
.czh-health.h-good { background: var(--success-bg); color: var(--success-text); }
.czh-health.h-ok { background: var(--bg-subtle); color: var(--text-secondary); }
.czh-health.h-warn { background: var(--warn-bg); color: var(--warn); }
.czh-health.h-bad { background: var(--accent-bg); color: var(--accent); }
/* 龙头竞价条 */
.czh-leaders { margin-top: var(--s2); align-items: center; }
.czh-chip i.up { color: var(--up); }
.czh-chip i.down { color: var(--down); }

/* 机会清单表 */
.czh-tabs { display: flex; gap: var(--s1); margin-bottom: var(--s2); flex-wrap: wrap; }
.czh-tab {
  font-size: var(--fs-sm); padding: var(--s1) var(--s3); border-radius: var(--r-pill);
  border: 1px solid var(--border-soft); background: var(--bg-subtle);
  color: var(--text-secondary); cursor: pointer;
}
.czh-tab.on { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); font-weight: 700; }
.czh-table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); table-layout: fixed; }
.czh-table th {
  text-align: center; font-size: var(--fs-sm); font-weight: 600; color: var(--text-muted);
  padding: var(--s2); border-bottom: 1px solid var(--border-soft);
}
.czh-table td {
  padding: var(--s2); border-bottom: 1px solid var(--border-soft);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  /* 表头与数据同对齐（2026-10-06 主人："表头对应下面的信息要居中"）。
     口径同 AI 精选报告 .ap-stock-table：整表居中，含名称列与概念列。 */
  text-align: center;
}
.czh-table tbody tr:last-child td { border-bottom: none; }
/* 列宽与对齐：数字列一律定宽 + 居中（与 AI 精选报告 .ap-stock-table 同风格）。
   ⚠️ table-layout:fixed 下"不给宽度"的列会平分剩余空间 —— 必须把每个数据列都显式定宽，
   否则综合评分/概念会被拉得很宽、数字列被挤到左边（2026-10-06 主人反馈"排列都干成啥了"）。*/
/* 数字列统一：等宽字体 + 定宽 + 居中。
   规范：--font-mono 用于数字（旧 4 种写法收敛为 1），否则跨行金额/涨幅对不齐。
   （原只用 tabular-nums：它只保证**同一字体内**数字等宽，不解决字体本身不一致。） */
.czh-table .rk { color: var(--gold); font-weight: 700; width: 34px; font-family: var(--font-mono); }
.czh-table .sc { font-weight: 700; width: 62px; font-family: var(--font-mono); }
.czh-table .pr { color: var(--text-secondary); width: 52px; font-family: var(--font-mono); }
.czh-table .tg { width: 64px; }
.czh-table .chg { width: 74px; font-family: var(--font-mono); }
/* 表头可点击排序（口径与 AI 精选报告一致）：高亮作反馈，**不加箭头**
   （2026-09-05 主人明确"所有表格去掉排序箭头"）。 */
.czh-table th.czh-th { cursor: pointer; user-select: none; }
.czh-table th.czh-th:hover { color: var(--text-main); background: var(--bg-hover); }
.czh-table th.sort-asc, .czh-table th.sort-desc { color: var(--gold); }
/* 唯一横向滚动容器（照 AipickReport 的 .ap-table-scroll 经验：**无条件** overflow-x:auto ——
   只在窄屏开会让 769~900px 区间(横屏手机 / 窄窗)没有兜底）。 */
.czh-table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
/* 窄屏"可横滑"提示：手机不显示滚动条，不提示用户不知道右边还有列（仅窄屏显示） */
.czh-swipe-hint { display: none; }
/* 表头不折行：定宽后"综合评分"曾被压成两行（"综合评/分"） */
.czh-table th { white-space: nowrap; }
/* ⚠️ table-layout:fixed **只认第一行(`<thead>` 的 th)上的宽度** —— 宽度写在 td 上等于没写，
   该列会被判为"无宽度"并吃掉全部剩余空间（2026-10-06 主人实测：股票列被撑到 460px，
   右侧留出一大片空白）。所以下方每个列的宽度都必须写在 th 上（见 .rk/.sc/.pr/.tg/.chg）。*/
.czh-table th.name-col { width: 116px; }
.czh-table th.cc { width: 190px; }
/* 股票列：名称 /（代码 · 连板）**两行** —— 结构照 AI 精选报告 .name-main / .name-sub */
.czh-table td.name-col { cursor: pointer; }
.czh-table .name-col .name-main {
  font-size: var(--fs-sm); font-weight: 600; color: var(--text-main);
  line-height: 1.35; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.czh-table .name-col .name-sub {
  font-size: var(--fs-xs); color: var(--text-muted);
  letter-spacing: 0.5px; line-height: 1.3; margin-top: 1px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
/* 连板徽章：文案与连板天梯 ladderText 同一口径（N板 / ≥8 → 8板+），不另造写法 */
.czh-table .name-col .czh-board {
  display: inline-block; margin-left: var(--s1); padding: 0 var(--s1);
  border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 500;
  color: var(--gold); background: var(--gold-bg);
}
/* 概念列：吃剩余宽度，超宽省略号（悬浮 title 看全量） */
.czh-table .cc { color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.czh-tag { font-size: var(--fs-sm); padding: 2px var(--s1); border-radius: var(--r-sm); white-space: nowrap; }
.czh-tag.t-good { background: var(--success-bg); color: var(--success-text); }   /* 同 h-good：不再借跌色 */
.czh-tag.t-ok { background: var(--gold-bg); color: var(--gold); }
.czh-tag.t-warn { background: var(--warn-bg); color: var(--warn); }
.czh-tag.t-dim { background: var(--bg-subtle); color: var(--text-dim); }
.czh-table .cp { color: var(--text-muted); }
.czh-table td.up { color: var(--up); }
.czh-table td.down { color: var(--down); }
.czh-table td.dim { color: var(--text-muted); }
.czh-empty { font-size: var(--fs-sm); color: var(--text-dim); padding: var(--s3); text-align: center; }

@media (max-width: 768px) {
  .czh-kpis { grid-template-columns: repeat(2, 1fr); }
  .czh-boards { grid-template-columns: 1fr 1fr; }
  /* 宽表格横向滚动（2026-10-06 主人："手机端不能横向滑动，要增加这个功能"）。
     min-width = 各列定宽之和 718px(34+116+62+52+52+64+74+74+190) ⇒ 取 720 触发横滑；
     经验同 AipickReport：min-width 只是"地板"，调小是安全操作。滚动容器贴边铺满避免被裁。 */
  .czh-table-scroll { margin: 0 calc(-1 * var(--s2)); padding: 0 var(--s2) var(--s1); }
  .czh-table { min-width: 720px; }
  .czh-swipe-hint {
    display: block; margin: 0 0 var(--s1);
    font-size: var(--fs-xs); color: var(--text-muted); text-align: right;
  }
}
@media (max-width: 480px) {
  .czh-kpis { grid-template-columns: 1fr 1fr; }
  .czh-boards { grid-template-columns: 1fr; }
  /* 手机上让位给综合分：单模型概率与标签列收起（信息仍可从综合分与条长判断强弱） */
  .czh-table .pr,
  .czh-table .tg { display: none; }
  /* 收起 2 列后定宽之和降为 614px ⇒ 横滑地板跟着降到 620（否则右侧会白滚一屏） */
  .czh-table { min-width: 620px; }
}
</style>
