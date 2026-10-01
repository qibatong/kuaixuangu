<template>
  <div>
    <h1 class="visually-hidden">选股</h1>
    <!-- 2026-10-01: 手机端「快捷入口」宫格（10 格常驻、可编辑）。
         ⚠️ 必须放在**最上面** —— 表头 sticky 高度由 setupStickyOffsets() 量 .home-filter
            等的 offsetHeight 写入 --sticky-thead-top；插在筛选区与表格之间会让表头错位。 -->
    <!-- 2026-10-01 主人拍板（已看效果图确认）: 手机端**首页** = 盯盘台
         10 格宫格 -> 指数 -> 滚动快讯 -> 盘中最强资金 -> 昨日涨停今日表现 -> 今日票战报。
         原来首页的「选股 / 竞价异动」两栏在手机首页**不再显示**，改由宫格四格进工作台：
           `/?wb=1&t=auction|spot|zhpick|yijiner`（桌面端一律不受影响，仍是原双栏）。
         复用「盘中」页同一套组件(MarketView home-mode) => 零新接口、零额外出网、零重复实现。 -->
    <MarketView v-if="isHomeDash" home-mode />

    <!-- 手机工作台(/?wb=1) 与 桌面端首页 共用下面这段（一字未改） -->
    <template v-if="!isHomeDash">
    <!-- 窄屏(<1100px) 切换栏: 选股 / 竞价异动 (宽屏 50/50 并排, 本栏隐藏) -->
    <div class="home-mob-toggle">
      <button class="home-mob-tab" :class="{ active: mobilePane === 'stock' }" @click="mobilePane = 'stock'">
        <i class="fa fa-sun-o"></i> 选股
      </button>
      <button class="home-mob-tab" :class="{ active: mobilePane === 'auction' }" @click="mobilePane = 'auction'">
        <i class="fa fa-bullhorn"></i> 竞价异动
      </button>
    </div>

    <!-- 首页两块布局: 左=选股主流程, 右=竞价异动; 宽屏 50/50 并排, 窄屏按 mobilePane 切一块 -->
    <div class="home-grid">
      <!-- 市场情绪面板: 横跨左右视图置于最顶部 -->
      <div class="home-sentiment"><SentimentPanel /></div>

      <!-- 左栏: 选股主流程 -->
      <div class="home-col home-col-left" :class="{ 'home-col-hidden': mobilePane !== 'stock' }">
        <!-- 模式切换(内联): 竞价 / 盘中实时 / AI预测 三组
             2026-09-05 主人需求: ① 去掉原顶部提示文案(竞价「9:30前可重新选股…」与
             AI预测「AI 竞价预测…」两行) ② 原右上角「锁定」按钮移除(下方 FilterPanel
             已有同功能锁定) ③ 「刷新」按钮下移至 FilterPanel 的 应用/重置/锁定 组
             2026-09-28 v4.11.75: 新增「盘中实时」—— 后端 /api/stocks_spot 重建后接入 -->
        <div class="alert-rule alert-rule-compact">
          <span class="mode-tabs mode-tabs-inline">
            <!-- ★ 2026-09-28 主人要求改 tab 文案: 「AI选股」→「竞价选股」、「盘中实时」→「实时动态选股」。
                 只改**展示文案**, `leftTab` 取值('auction'/'spot')与所有分支判据一字不动。 -->
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'auction' }" @click="switchTab('auction')"><i class="fa fa-sun-o"></i> 竞价选股</button>
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'spot' }" @click="switchTab('spot')"><i class="fa fa-bolt"></i> 实时动态选股</button>
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'aipick' }" @click="switchTab('aipick')"><i class="fa fa-android"></i> AI预测·金睛</button>
            <!-- 2026-09-25: 火眼(LightGBM) 平行链路(与 AI预测 同构, 只换模型); 手机端一并生效(本组 tab 在左栏内部) -->
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'aipick_lgb' }" @click="switchTab('aipick_lgb')"><i class="fa fa-flask"></i> AI预测·火眼</button>
            <!-- 2026-09-28: 竞价一进二（昨日主板首板 → 今日二连板潜力）。
                 名单来自独立端点 /api/yijiner，**不读 stock store 的数据流**；
                 门禁(严格 VIP)/取数/评分全部在 YijinerView 内部，与竞价那条链路互不影响。 -->
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'yijiner' }" @click="switchTab('yijiner')"><i class="fa fa-level-up"></i> 竞价一进二</button>
            <!-- 2026-09-29 主人要求: 竞价精选放到「竞价一进二」右侧(面板=ZhPicksPanel, 数据 /api/stats/zh-picks) -->
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'zhpick' }" @click="switchTab('zhpick')"><i class="fa fa-star"></i> 竞价精选</button>
          </span>
          <!-- 2026-09-27 v4.11.63《移动端清单》§二·4: 名单数据的更新时刻。
               用 .right-group 挂到本行右侧（该 class 自带 margin-left:auto，且本行已
               justify-content:flex-start ⇒ 它出现不会把左边那组 tab 挤到中间）。
               ⚠️ 它讲的是**名单数据**的取回时刻，与页头那个每秒跳的时钟无关。
               2026-09-28: 竞价一进二 下隐藏 —— 它不读 store 数据流, 显示 store 的时间戳
               会误导（时间戳不是这份名单的取回时刻）。 -->
          <span v-if="leftTab !== 'yijiner'" class="right-group"><DataStamp :at="dataAt" :ok="dataOk" :interval="autoOn ? 30 : 0" :stale="dataStale" /></span>
        </div>

        <!-- 筛选面板(竞价 / 盘中实时两种模式共用本组件, 组件内部按 store.strategy 分支渲染) -->
        <!-- 2026-09-05: 刷新按钮已下移到 FilterPanel(与 应用/重置/锁定 同组),
             这里监听其 emit 并执行本视图的刷新逻辑 -->
        <div v-if="leftTab === 'auction' || leftTab === 'spot'" class="home-filter"><FilterPanel @refresh="refreshRealTime" /></div>

        <!-- ===== tab1「AI选股」— 2026-09-28 v4.11.80 第三步: **锁定语义 + spot 引擎** =====
             🔴 主人需求原文:「ai竞价出来数据就锁定」—— tab1 保留**锁定**这个动作, 但锁的
               内容是 **spot(盘中实时)引擎**算出来的名单(不再用 9:25 竞价定格那套)。
             🔴 因此本分支的**数据源/列定义必须跟着 `stocks.strategy` 走**(不能用固定字面量):
               · strategy=spot  → 读 stocks.spotStocks, StockTable strategy="spot"
               · strategy=auction → 读 stocks.cachedStocks, StockTable strategy="auction"
               写死任一者都会出现"表里是 spot 名单、列却是竞价列(竞涨/竞额全 —)"的错位。
             ✅ 保留不动的**结构语义**(与 tab2 的区别所在):
               ① pickBlocked 9:26 闸门提示块 —— 仍显示(后端 spot 路径不受闸门, 故不会真拦,
                  但开关若被外部置 blocked 仍能提示; 且这是 tab1 的"锁定链路"身份标识)。
               ② freeze-notice 定格标注条 —— **2026-09-30 主人指令已删除**(见下方模板注释)。
               ③ 会员/配额门禁 —— 照旧。
               ④ 「锁定」按钮(FilterPanel 内, 见 store.strategy 判据)。 -->

        <!-- 2026-09-16 选股闸门(主人拍板: 开盘日竞价时段不支持选股)。
             🔴 2026-09-30 口径修正: 实际拦截段 = 09:15:00 ~ 09:26:30(utils/time.js
             PICK_BLOCK_FROM/TO), **不是 9:00**。9:00 是 serve_date 的逻辑交易日起点
             (09:00 起当日没数据也不许退上一交易日), 它不拦选股, 只决定看哪一天。 -->
        <!-- 优先于会员门禁: 该时段连会员也不可用(不是权限问题, 是当日定格尚未产生) -->
        <div v-if="leftTab === 'auction' && stocks.pickBlocked" class="pick-blocked-notice">
          <i class="fa fa-clock-o"></i>
          <span>{{ stocks.pickBlockedMsg }}</span>
        </div>

        <!-- 2026-09-30 主人指令: **删除「定格来源标注条」**。原内容 = "当前为 X 定格数据
             （上一交易日 / 回放），改条件可重选"; 原意是防止把上一交易日名单当成当日名单
             (该条 2026-09-18 引入, 起因是主人 9/18 反馈「刷出来是昨天的数据」)。
             🔴 主人 2026-09-30 **已知悉该背景, 仍决定去掉** ⇒ 页面现不再有任何
                "这份名单不是今天" 的提示: 深夜 / 盘前打开会直接看到上一交易日的票。
             ℹ️ 要恢复的话: 后端 /api/stocks **仍下发** freezeDate / freezeIsToday,
                store 里这两个字段也**保留未删**(见 stores/stocks.js), 照原样加回即可。
             ⚠️ 下方 .spot-notice 与它语义不同(那条说明"数据很新"), 未受影响。
             ⚠️ 动本文件块注释时注意: 注释正文里**不能出现块注释结束符那三个字符**, 也不能
                只替换注释**前半** —— 2026-09-30 曾因此把注释后半漏成裸文本渲染到页面上。 -->

        <!-- 会员门禁: 竞价选股 仅在工作日 9:15-15:00 要求会员; 其他时段放开 -->
        <VipGate v-if="leftTab === 'auction' && !user.isMember && isMemberOnlyTime()" title="竞价选股" />
        <!-- 配额门禁(2026-09-21 会员体系): 免费用户每日有限次数, 用尽后 VipGate 转配额引导模式 -->
        <VipGate
          v-else-if="leftTab === 'auction' && stocks.quotaExceeded"
          ref="pickGateRef"
          title="竞价选股"
        />

        <template v-if="leftTab === 'auction' && (user.isMember || !isMemberOnlyTime()) && !stocks.pickBlocked && !stocks.quotaExceeded">
          <!-- 5-1: 奖牌区已降级为 StockTable 行内徽标(前三行), 此处不再渲染三张重复卡片 -->
          <!-- 主表: 无数据时给加载态 -->
          <div v-if="!stocks.isDataCached" class="stock-table-container">
            <div class="loading-placeholder"><div class="spinner"></div><div>后台正在计算选股中...</div></div>
          </div>
          <!-- 2026-09-23 P0 口径条已按主人要求整条移除(15:4x): 名单上方的历史统计/口径说明
               不再显示。连板标签本身(StockTable 名称格)与自身悬停统计保留不变。 -->
          <template v-else>
            <!-- ★ 列定义/数据源按 strategy 分派: spot 读 spotStocks, auction 读 cachedStocks -->
            <StockTable
              v-if="stocks.isSpotStrategy"
              :stocks="stocks.spotStocks"
              strategy="spot"
              :bid-seal-map="bidSealMap"
            />
            <StockTable
              v-else
              :stocks="stocks.cachedStocks"
              strategy="auction"
              :bid-seal-map="bidSealMap"
            />
          </template>
        </template>

        <!-- ===== 盘中实时(spot) — 2026-09-28 v4.11.75 =====
             🔴 与竞价模式的四点结构差异(照抄竞价模板会出错):
               ① **无 9:26 闸门**: 竞价那套"当日定格尚未产生"的理由对 spot 不成立,
                  pickBlocked 提示块与置灰一律不适用(后端该端点也没有 pick_window_guard)。
               ② **无定格标注条**: spot 永远是"此刻", 不存在"上一交易日定格"这回事。
               ③ **无锁定**: spot 不落批次, 锁了也没有可回放的定格名单。
               ④ 会员门禁**照旧**(盘中实时属付费能力, 口径与竞价一致)。 -->
        <template v-else-if="leftTab === 'spot'">
          <VipGate v-if="!user.isMember && isMemberOnlyTime()" title="实时动态选股" />
          <template v-else-if="user.isMember || !isMemberOnlyTime()">
            <div v-if="!stocks.spotCached" class="stock-table-container">
              <div class="loading-placeholder">
                <div v-if="stocks.spotLoading" class="spinner"></div>
                <div v-else><i class="fa fa-bolt"></i> 点「应用」获取实时动态选股名单</div>
                <div v-if="stocks.spotLoading">正在扫描全市场实时行情…</div>
              </div>
            </div>
            <template v-else>
              <!-- 盘中数据的时效提示: spot 每次请求都是"此刻", 必须让用户知道数据有多新 -->
              <div class="spot-notice">
                <i class="fa fa-bolt"></i>
                <span>实时动态选股名单 · 共 <b>{{ stocks.spotStocks.length }}</b> 只 · 数据时刻 <b>{{ spotTimeStr }}</b><template v-if="spotBidLabel"> · 竞涨/竞额定格 <b>{{ spotBidLabel }}</b></template>（每次「应用」重新扫描，无需等 9:26 定格）</span>
              </div>
              <StockTable :stocks="stocks.spotStocks" strategy="spot" />
            </template>
          </template>
        </template>

        <!-- AI预测(2026-09-01 替换原盘中选股; 自带 VIP 门禁/日期回看/规则过滤) -->
        <!-- 2026-09-01: AI预测内嵌左视图; 隐藏日期回看(回看入口在导航栏「历史回看」页) -->
        <AipickView v-else-if="leftTab === 'aipick'" :embedded="true" :show-date-picker="false" />
        <!-- 2026-09-25: LightGBM 版(共用 AipickReport 组件, 只是 model='lgb' 取另一份产物) -->
        <AipickLgbView v-else-if="leftTab === 'aipick_lgb'" :embedded="true" :show-date-picker="false" />
        <!-- 2026-09-28: 竞价一进二（昨日主板首板 → 今日二连板潜力）。
             严格 VIP 门禁(requiredLevel=1, 与竞价异动同强度)、取数、评分全在组件内部；
             放在链尾作兜底分支（原 AipickLgbView 的 v-else 已改为显式 v-else-if）。 -->
        <YijinerView v-else-if="leftTab === 'yijiner'" :embedded="true" />
        <!-- 2026-09-29: 竞价精选(主人要求放「竞价一进二」右侧); 兜底分支保持一进二 -->
        <ZhPicksPanel v-else-if="leftTab === 'zhpick'" :embedded="true" />
        <YijinerView v-else :embedded="true" />
      </div><!-- /.home-col-left -->

      <!-- 右栏: 竞价异动 -->
      <div class="home-col home-col-right" :class="{ 'home-col-hidden': mobilePane !== 'auction' }">
        <AuctionView />
      </div>
    </div><!-- /.home-grid -->
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import FilterPanel from '../components/FilterPanel.vue'
import SentimentPanel from '../components/SentimentPanel.vue'
import StockTable from '../components/StockTable.vue'
import AuctionView from './AuctionView.vue'
import AipickView from './AipickView.vue'
import AipickLgbView from './AipickLgbView.vue'
import YijinerView from './YijinerView.vue'
import ZhPicksPanel from '../components/ZhPicksPanel.vue'
import VipGate from '../components/VipGate.vue'
import { useStocksStore } from '../stores/stocks'
import { usePoolStore } from '../stores/pool'
import { useUserStore } from '../stores/user'
import { kplBidSeal } from '../api/kpl'
import { trackUsage } from '../api/activity'
import { showToast } from '../utils/toast'
import { useYidongMonitor } from '../composables/useYidongMonitor'
// 2026-09-05: isBefore930 随「锁定」按钮移除后本视图不再使用, 从 import 中去掉
import { bjDateTimeStr, isIntradayNow, isMemberOnlyTime, todayBj } from '../utils/time'
// 2026-09-30: tab→引擎 映射收敛为**唯一纯函数**(可单测, 见 utils/strategy.test.js)。
//   此前映射直接内联在本文件里(白名单式, 漏掉 'auction' 自己) ⇒ 见下方 switchTab 的说明。
import { strategyForTab } from '../utils/strategy'
// 2026-09-27 v4.11.63《移动端清单》§二·4: 数据更新时刻（与页头时钟区分开）
import DataStamp from '../components/DataStamp.vue'
import MarketView from './MarketView.vue'
import { useIsNarrow } from '../composables/useIsNarrow'
import { useRoute } from 'vue-router'
import { useDataStamp } from '../composables/useDataStamp'

const route = useRoute()

// 2026-10-01 首页改版(见模板注释): 手机端首页 = 盯盘台; 选股工作台移到 `?wb=1`。
//   · 桌面端(isNarrow=false) => isHomeDash 恒 false => 首页/工作台都是原样(一字未改)
//   · 用 JS 判据(而不是只靠 CSS 藏) 是因为: 藏了仍会挂载 => 会白拉选股数据(竞价 spot 扫描是付费出网)
const isNarrow = useIsNarrow()
const wbMode = computed(() => String(route.query.wb || '') === '1')
const isHomeDash = computed(() => isNarrow.value && !wbMode.value)
const stocks = useStocksStore()
const pool = usePoolStore()
const user = useUserStore()
const bjTime = ref('--:--:--')
// ⚠️ 分解赋值（模板只自动解包顶层 ref，写 ds.at 会把 ref 对象渲染出来）
const { at: dataAt, ok: dataOk, stale: dataStale, mark: markData } = useDataStamp()
// 当前是否处于「30s 自动刷新」窗口（盘中）。收盘/盘前为 false ⇒ 不显示「每 30s 自动刷新」
const autoOn = ref(false)
const bidSealMap = ref({})        // 竞价涨停委买额 map: code -> {limitBoards, bidSealAmt, bidNetAmt}
// 配额引导页引用(2026-09-21): stocks.quotaExceeded 时把 detail 塞进 VipGate 配额模式
const pickGateRef = ref(null)
const { refreshYidongCodes } = useYidongMonitor()

// 2026-09-01: 左视图模式切换 竞价 / 盘中实时 / AI预测×2
// 2026-09-28 v4.11.75: 'spot' 重新可用 —— 切换时会 store.setStrategy('spot'),
//   让 FilterPanel 渲染盘中参数行、并把数据源切到 spotStocks(与竞价 cachedStocks 隔离)。
// ★ 2026-09-28 v4.11.80 第三步: 曾让 tab1「AI选股」也改走 spot 引擎(需求
//   「AI竞价出来的数据就锁定」)—— 保留 tab1 的全套锁定语义(定格标注条 / 9:26
//   闸门 / 会员配额门禁 / merge 实时 / 进自选池)。
//
//   🔴 2026-09-30 主人拍板(方案 A): **回退第三步的"引擎"部分** —— tab1 重归竞价引擎。
//     · 为什么不影响原需求: 「出来数据就锁定」属**锁定动作**; 回退只换"算名单的引擎",
//       tab1 的全套锁定语义(落批次/可回放/闸门/配额/自选池)**一字未动**。
//     · 回退动因: tab 已改名「竞价选股」而引擎是 spot ⇒ 名实不符; 更要紧的是, 它把竞价
//       口径下**真正生效**的门槛(竞额下限 bidAmtFloor —— 单放宽它 1 只→67 只)藏进了
//       spot 面板 ⇒ 主人"看不到任何条件却几乎不出数据"。
//     · 现状: tab1 ⇒ leftTab='auction' + strategy='auction'; tab2 ⇒ leftTab='spot'
//       + strategy='spot'。**映射唯一真相源 = utils/strategy.js::strategyForTab()**。
//     · tab2「实时动态选股」不受影响: 它的值 'spot' 与旧写法兜底值同名(歪打正着)。
//   ⚠️ 为什么不让 tab1 直接切到 leftTab='spot': 那样会丢掉定格标注条/锁定按钮/闸门提示,
//     而主人要的恰恰是「锁定」这个动作。
const leftTab = ref('auction')

// 盘中名单的数据时刻(秒 → HH:MM:SS, 北京时间)。spot 每次请求都是"此刻", 必须显式告知。
// 2026-10-01 P2-6(清单 3.2): 「竞涨/竞额」两列的**定格来源日**(后端 bidDate)。
//   **只在不是今天**时才显示 —— 正常盘中(当日已有 9_25 行)不显示, 零干扰;
//   盘前/非交易时段按设计回退上一交易日定格时才提示, 解决"页面是不是昨天的"这类误读。
const spotBidLabel = computed(() => {
  const d = stocks.spotBidDate
  if (!d || d === todayBj()) return ''
  return String(d).slice(5)          // YYYY-MM-DD → MM-DD
})

const spotTimeStr = computed(() => {
  if (!stocks.spotDataAt) return '--:--:--'
  const d = new Date(stocks.spotDataAt * 1000)
  const p = (n) => String(n).padStart(2, '0')
  // toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'}) 在部分 webview 上不稳,
  // 直接用 UTC+8 偏移算(与 utils/time 的 bjDateTimeStr 同思路, 不依赖环境时区)。
  const bj = new Date(d.getTime() + (d.getTimezoneOffset() * 60000) + 8 * 3600000)
  return p(bj.getHours()) + ':' + p(bj.getMinutes()) + ':' + p(bj.getSeconds())
})

// 2026-09-23 P0 口径条文案已随提示条一并移除(lbNote/lbFoot 不再使用)

// 窄屏切换: 选股 / 竞价异动
const mobilePane = ref('stock')

// 图表弹窗: 由 App.vue 全局托管(uiBus.chartModal 驱动); 左视图已无自选池入口,
// 股票点击图表由 StockTable 内部通过 uiBus 触发
async function loadBidSeal() {
  try {
    const d = await kplBidSeal()
    const map = {}
    ;(d.list || []).forEach((it) => { map[it.code] = it })
    bidSealMap.value = map
  } catch (e) { /* 竞价委买额可选, 失败静默 */ }
}

let clockTimer = null
let autoAddTimer = null
let expiryTimer = null
let realTimeTimer = null
let pickGateTimer = null    // 2026-09-16 选股闸门巡检(9:26 到点自动解禁)

// 配额超限后, 把 detail 塞进 VipGate 的配额模式(2026-09-21)
watch(() => stocks.quotaExceeded, (v) => {
  if (!v) return
  nextTick(() => {
    if (pickGateRef.value && pickGateRef.value.openQuota) pickGateRef.value.openQuota(stocks.quotaInfo)
  })
})

async function init() {
  user.migrateLegacyKeys()
  // 初始化股票池
  pool.loadFromStorage()
  // 初始化筛选状态(本地锁定 > 账号偏好 > 全局默认 > 内置默认)
  await Promise.all([stocks.loadUserPrefs(), stocks.loadGlobalDefaults()])
  stocks.initFilterFromStorage()
  // 2026-09-16 选股闸门: 交易日 **09:15:00~09:26:30** 不发请求(该时段只能得到非当日定格的名单:
  // 09:15-09:25 竞价在变 / 09:25-09:26:30 当日定格尚未落库)。
  // 🔴 2026-09-30 口径修正: 原写"9:00-9:26 不发请求"与常量不符 —— 实际只挡 09:15:00~09:26:30;
  //   **09:00~09:15 会发请求**, 但按 serve_date 新规矩后端不回退 ⇒ 当日尚无数据 ⇒ 空名单。
  //   ⚠️ 该时段用户只会看到表里一句「暂无符合条件股票」, 无任何解释 —— 原先还有 freeze-notice
  //      定格标注条做兜底, 但它**已于 2026-09-30 按主人指令删除** ⇒ 这条缺口现在更明显了。
  //   原"9:00-9:15 上交易日"一句已彻底失效: 09:00 起不再拿上一交易日冒充当天。
  // 2026-09-17: force=true 首屏必探后端开关(pick_window_guard=0 → 不置灰、正常拉数据)
  await stocks.refreshPickGate(true)
  // 首次拉数据(禁用时段由闸门提示块代替, 到点由 pickGateTimer 自动选股)
  if (!stocks.pickBlocked) {
    try {
      await stocks.fetchAndCache()
      markData()          // 名单落定 ⇒ 推进「更新于」（失败走 catch，时间戳原地不动）
    } catch (e) {
      showToast('❌ ' + e.message, 'error')
    }
  }
  // 启动定时器: 时钟 / 自动收录 / 过期检查
  clockTimer = setInterval(() => { bjTime.value = bjDateTimeStr(); autoOn.value = isIntradayNow() }, 1000)
  autoAddTimer = setInterval(() => pool.autoAdd(currentList(), stocks.isDataCached), 20000)
  expiryTimer = setInterval(() => pool.checkExpiry(), 30000)
  pool.autoAdd(currentList(), stocks.isDataCached)
  // 2026-09-16 闸门巡检: 每 20s 一次 —— 9:26 到点**自动解禁并选股**(否则用户 9:10
  // 打开页面会一直停在禁用态, 必须手动刷新); 若后端因"当日定格尚未落库"继续拦,
  // 下一轮自动重试, 天然形成等待。
  pickGateTimer = setInterval(() => { stocks.refreshPickGate().catch(() => {}) }, 20000)
  // 盘中 9:30-15:00: 每 30s 自动刷新一次现涨/实时涨幅(静默, 不弹 toast)。
  // 后端行情缓存 TTL: 竞价30s, 30s 轮询既能跟上涨幅变化又不超压。
  realTimeTimer = setInterval(() => {
    if (!isIntradayNow()) return            // 盘前/收盘/周末: 不轮询, 现涨固定为当日收盘
    const safe = (p) => p.catch(() => {})  // 轮询失败静默, 不打断
    // 2026-09-28 v4.11.75: 按当前模式分流 —— 竞价走 updateRealTimeOnly(只刷现涨, 不动名单);
    // 盘中(spot)必须**整份重拉**: spot 的评分本身就依赖实时涨幅/量比/换手, 只换现涨
    // 而不重算是错的(名单会停在上一刻的评分上)。两条链路互斥, 不会叠加。
    if (leftTab.value === 'spot') {
      if (stocks.spotCached) safe(stocks.fetchSpotList({ silent: true }).then(() => markData()))
    } else if (leftTab.value === 'auction') {
      safe(stocks.updateRealTimeOnly({ silent: true }).then(() => markData()))
    }
  }, 30000)
}

// 当前模式的选股结果 —— 恒为竞价名单 cachedStocks, 与左视图 tab 无关。
// 2026-09-28 (v4.11.75): 左视图新增 spot tab 后仍有此行, 原因: autoAdd(自选池自动收录)
// 只认竞价名单(池内字段是 bidChange, spot 只有 realChange)。spot 名单另有 spotStocks 字段,
// 不进池。详情见 PoolView.vue / StockPoolPanel.vue 的同名注释。
function currentList() {
  return stocks.cachedStocks
}

// 2026-09-05: 原 reLock() 随右上角「锁定」按钮一并移除(下方 FilterPanel 已提供
// 锁定/解锁, 且 store.reLockData() 保留供其使用), 此处不再需要包装函数。
function refreshRealTime() {
  // 2026-09-22 v4.11.35: 手动点「刷新」也是一次主动操作(有别于 30s 自动轮询)
  trackUsage('picker')
  stocks.updateRealTimeOnly().then(() => markData())
    .catch(e => showToast('❌ 更新失败：' + e.message, 'error'))
}
// 左视图模式切换: auction(竞价选股) / spot(实时动态选股) / aipick(AI预测) /
// aipick_lgb(LightGBM) / yijiner(竞价一进二) / zhpick(竞价精选)。
// 后四个的名单由各自视图/端点自加载, 与 store 数据流无关 ⇒ 只需一个**中性**的
// strategy 值, 重点是别给 FilterPanel 留脏值。
//
// ★ tab → strategy 映射的**唯一真相源** = `utils/strategy.js::strategyForTab()`
//   (2026-09-30 收敛; 单测见 utils/strategy.test.js) —— 此处**不再内联映射**。
//   · tab1('auction') → 'auction'  ← **2026-09-30 修回**
//   · tab2('spot')    → 'spot'
//   · 其余四个        → 'auction'(中性值)
//   🔴 沿革: 9/28 第三步曾让 tab1 也走 spot 引擎(需求「AI竞价出来的数据就锁定」),
//     但白名单写法**漏掉了 'auction' 自己** ⇒ 点「竞价选股」拿到 'spot' ⇒ 面板隐藏
//     「竞涨/竞额」、取数走实时分支(生产实测仅 1~3 只)。9/30 主人拍板回退映射的 tab1
//     部分(方案 A) —— **锁定那套 UI 与语义一字未动, 只换回算名单的引擎**。
//     详见 switchTab 内注释与 utils/strategy.js 的文件头。
function switchTab(m) {
  if (leftTab.value === m) return
  leftTab.value = m
  // 🔴 2026-09-30 主人拍板(方案 A): 映射**收敛为 `strategyForTab()`**(utils/strategy.js)。
  //   旧写法是**白名单式**内联在这里：
  //     m === 'aipick' || m === 'aipick_lgb' || m === 'yijiner' || m === 'zhpick'
  //       ? 'auction' : 'spot'
  //   它**漏掉了 `'auction'` 自己** ⇒ 点「竞价选股」落进"其余"桶 ⇒ strategy='spot'
  //   ⇒ ① FilterPanel 走 spot 分支 ⇒ **「竞涨」「竞额」被 v-if="!isSpot" 隐藏**；
  //     ② 表格改读 spotStocks；③ 取数走 /api/stocks?strategy=spot(生产实测仅 1~3 只)。
  //   生产实证(uid=6, 2026-09-30 11:2x): 「竞价选股」只出 1 只；而竞价口径下**真正卡住
  //   结果**的门槛是「竞额下限」(bidAmtFloor，单放宽它 1 只→67 只) —— 它恰好被这个
  //   错配藏进了 spot 面板 ⇒ 主人"看不到任何条件却几乎不出数据"。
  //   ⚠️ 最阴的一点: tab2 的值 `'spot'` 与旧兜底值**同名** ⇒ 它歪打正着、完全正常，
  //     于是故障只落在 tab1 身上，看起来像"tab1 特有的怪问题"。
  //   ⇒ 现改为「只有 'spot'（实时动态选股）走 spot 引擎，其余一律 auction」，
  //     tab 名 / 引擎 / 面板三者重新一致。aipick 那组的既有处置(归 auction 防脏值)不变。
  //   ⚠️ 若要恢复 9/28 口径(让 tab1 用实时引擎), 改 `strategyForTab` 一处即可 ——
  //     但**必须同时解决"面板隐藏竞价字段"**，否则会重现本次事故。
  stocks.setStrategy(strategyForTab(m))
  // 2026-09-22 v4.11.35: AI 预测的使用计数由 AipickView 自己在加载报表时上报,
  // 这里**不要**再记一次(同一动作会双计)。
  // fetchAndCache 内部按 `this.strategy` 分支写入: auction → cachedStocks / spot → spotStocks
  //   (见 stores/stocks.js)。2026-09-30 起 tab1 的 strategy 回到 'auction'
  //   ⇒ 首屏落 cachedStocks(9/28-9/30 期间它曾落 spotStocks)。
  if (m === 'auction' && !stocks.isDataCached) {
    stocks.fetchAndCache().then(() => markData()).catch(e => showToast('❌ ' + e.message, 'error'))
  }
  // 盘中实时: 切过来自动拉一次(否则用户看到的是"点应用"空态, 多一步操作)。
  // 失败静默 —— 面板仍在, 用户可手点「应用」重试; 弹错会打断刚切 tab 的动作。
  // ⚠️ 2026-09-30 起 tab1(cachedStocks) 与 tab2(spotStocks) **各自独立**, 不再共用名单
  //   (9/28-9/30 期间两者共用 spotStocks)。这里各有命中守卫(!isDataCached / !spotCached)
  //   ⇒ 切回来时已缓存就不重打网络; 名单时效由顶栏 DataStamp / spot-notice 显式告知。
  if (m === 'spot' && !stocks.spotCached) {
    stocks.fetchSpotList({ silent: true }).then(() => markData()).catch(() => {})
  }
}
// 2026-08-24: 首页表格固定表头 — 测量 sticky 元素(filter/tabs)高度写入 CSS 变量
let _stickyResizeFn = null
function setupStickyOffsets() {
  const update = () => {
    const leftCol = document.querySelector('.home-col-left')
    const rightCol = document.querySelector('.home-col-right')
    if (leftCol) {
      const f = leftCol.querySelector('.home-filter')
      if (f) leftCol.style.setProperty('--sticky-thead-top', f.offsetHeight + 'px')
    }
    if (rightCol) {
      const t = rightCol.querySelector('.auc-tabs')
      if (t) rightCol.style.setProperty('--sticky-thead-top', t.offsetHeight + 'px')
    }
  }
  setTimeout(update, 200)
  _stickyResizeFn = update
  window.addEventListener('resize', _stickyResizeFn)
}

// 2026-10-01: 宫格「快捷入口」跳首页某个 mode-tab 用 `?t=<leftTab>`。
//   已在首页时 router.push 只改 query ⇒ watch 到变化就切 tab（不整页重载）。
const TAB_KEYS = ['auction', 'spot', 'aipick', 'aipick_lgb', 'yijiner', 'zhpick']
function applyTabFromQuery() {
  const t = String(route.query.t || '')
  if (TAB_KEYS.indexOf(t) >= 0) switchTab(t)
}
watch(() => route.query.t, () => applyTabFromQuery())

onMounted(() => {
  bjTime.value = bjDateTimeStr()
  autoOn.value = isIntradayNow()
  applyTabFromQuery()
  // 2026-10-01: 盯盘台首页不拉选股数据(按需加载 -- 竞价/动态选股都要真出网, 不能在没看表时白打)
  if (isHomeDash.value) return
  init()
  loadBidSeal()
  refreshYidongCodes()   // 首页选股/竞价异动 标记异动监管股票
  setupStickyOffsets()
})

// 2026-10-01: 盯盘台 -> 工作台 是**同一路由只变 query**(?wb=1), 组件不会重新挂载
//   => 必须在这里补一次首屏加载, 否则从首页点宫格进工作台会看到空表。
watch(wbMode, (on) => {
  if (!on) return
  nextTick(() => {
    init()
    loadBidSeal()
    refreshYidongCodes()
    setupStickyOffsets()
  })
})
onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (autoAddTimer) clearInterval(autoAddTimer)
  if (expiryTimer) clearInterval(expiryTimer)
  if (realTimeTimer) clearInterval(realTimeTimer)
  if (pickGateTimer) clearInterval(pickGateTimer)
  if (_stickyResizeFn) window.removeEventListener('resize', _stickyResizeFn)
})
</script>

<style scoped>
/* 2026-09-16 选股闸门提示块(交易日 09:15:00~09:26:30): 替代主表位置, 说明为何暂时看不到名单。
   配色用黄色提示系(项目五色内), 与涨跌红绿语义无关。 */
.pick-blocked-notice {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 0;
  padding: 10px 12px;
  border: 1px solid rgba(230, 180, 0, 0.5);
  border-radius: 6px;
  background: rgba(230, 180, 0, 0.10);
  color: var(--text-main);
  font-size: 0.7812rem;
  line-height: 1.5;
}
.pick-blocked-notice .fa { color: #e6b400; }
body[data-bg="light"] .pick-blocked-notice { color: #8a5500; }

/* 2026-09-30 主人指令: 定格来源标注条(原 .freeze-notice, 黄色提示系)已删除, 样式一并移除。
   原语义: 黄 = 提醒"这不是当日数据"(上一交易日 / 回放), 与涨跌红绿无关。 */

/* 2026-09-28 v4.11.75 盘中实时(spot)名单的时效提示条。
   与原有的 freeze-notice(黄=提醒"这不是当日数据", **2026-09-30 已按主人指令删除**)
   **语义不同**: 这条是蓝色信息系, 说明"这份数据很新" —— 两者不会同时出现
   (spot 无定格概念, 竞价无实时概念)。
   用项目蓝色系(--accent 在浅色下是深红, 故这里显式用 info 蓝, 与涨跌红绿无关)。 */
.spot-notice {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 0;
  padding: 8px 12px;
  border: 1px solid rgba(64, 148, 255, 0.38);
  border-radius: 6px;
  background: rgba(64, 148, 255, 0.08);
  color: var(--text-main);
  font-size: 0.75rem;
  line-height: 1.5;
}
.spot-notice .fa { color: #4094ff; }
.spot-notice b { color: #4094ff; font-weight: 600; }
body[data-bg="light"] .spot-notice { color: #1a4a8a; }
body[data-bg="light"] .spot-notice .fa,
body[data-bg="light"] .spot-notice b { color: #1a5fb4; }

/* 2026-09-23 P0 口径条样式已移除(.lb-note / .lb-note-foot): 该提示条已按主人要求整条下线。
   连板标签自身样式在 components/StockTable.vue(.lb-tag / .lb-tag-lv0..5), 不受影响。 */

/* 奖牌(金银铜) + 自选股票池 在左栏内: 保留原有紧凑处理 */
.medal-section {
  display: flex;
  flex-wrap: nowrap;
  gap: 8px;
  justify-content: center;
  align-items: stretch;
}
.yizi-card {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: 10px;
  padding: 3px 10px;
  border: 1px solid rgba(var(--accent-rgb), 0.4);
  border-radius: 12px;
  background: rgba(var(--accent-rgb), 0.08);
  color: var(--accent-text);
  font-size: 0.75rem;
  white-space: nowrap;
  cursor: default;
}
.yizi-card b { color: var(--accent-deep); }

/* 紧凑规则条: 规则文本 + 内联模式切换 + 操作按钮 一行排布 */
.alert-rule.alert-rule-compact {
  padding: 4px 10px;
  margin: 4px 0 6px;
  gap: 8px;
  border-radius: 6px;
  border-left-width: 3px;
  flex-wrap: wrap;
  /* 覆盖 main.css .alert-rule 的 space-between: 让 rule-text + mode-tabs 始终紧贴左侧,
     不再因 right-group 是否存在而在「居中/靠右」之间漂移 (2026-09-01 反馈) */
  justify-content: flex-start;
}
.alert-rule.alert-rule-compact .rule-text { font-size: 0.75rem; gap: 4px; flex: 0 1 auto; min-width: 0; }
.alert-rule.alert-rule-compact .rule-text strong { font-size: 0.75rem; }
.alert-rule.alert-rule-compact .right-group {
  gap: 6px;
  flex: 0 1 auto;
  flex-wrap: wrap;
  display: inline-flex;
  align-items: center;
  /* right-group 自身推到右侧; 缺失时不影响 rule-text + mode-tabs 紧贴布局 */
  margin-left: auto;
}
.mode-tabs.mode-tabs-inline {
  margin: 0;
  gap: 4px;
  flex: 0 1 auto;
  flex-wrap: wrap;
  display: inline-flex;
  align-items: center;
}
.mode-tab.mode-tab-compact {
  /* 2026-09-05 主人需求: 竞价/AI预测 tab 适当放大(3px 10px → 6px 16px, 12→13px),
     active 态同尺寸避免切换跳动 */
  padding: 6px 16px;
  font-size: 0.8125rem;
  border-radius: 6px;
  gap: 4px;
  line-height: 1.2;
  font-weight: 500;
  box-shadow: none;
}
.mode-tab.mode-tab-compact:hover { transform: none; box-shadow: none; border-color: #ffb400; }
.mode-tab.mode-tab-compact.active {
  padding: 6px 16px;
  font-size: 0.8125rem;
  font-weight: 700;
  /* 2026-09-05 主人需求: tab 选中后变为红色。
     用 var(--accent) 自动适配双主题(深色 #ff5c5c / 浅色已重定义深红 #c62828,
     白底保持可读), 渐变向下加深保证立体感 */
  color: #fff;
  background: linear-gradient(180deg, var(--accent), var(--accent-deep));
  border-color: var(--accent);
  box-shadow: 0 2px 6px rgba(var(--accent-rgb), 0.45);
  transform: none;
}
.mode-tab.mode-tab-compact.active:after { display: none; }
/* 浅色主题: 由 :root 重定义的深红 --accent 变量自动适配(白字 on 深红渐变可读),
   无需单独规则; 保留该选择器占位注释避免误以为遗漏 */
.alert-rule.alert-rule-compact .tdx-export-btn { padding: 3px 8px; font-size: 0.75rem; gap: 3px; border-radius: 4px; font-weight: 500; }
.alert-rule.alert-rule-compact .tdx-export-btn:hover { transform: none; box-shadow: 0 2px 6px rgba(var(--accent-rgb),0.25); }
/* 左栏筛选面板紧凑 */
.home-col-left .filter-custom { padding: 5px 9px; margin: 4px 0; gap: 3px; border-radius: 7px; }
.home-col-left .filter-row-1 { gap: 5px; }
.home-col-left .filter-custom label,
.home-col-left .filter-row-2 .filter-cell { font-size: 0.75rem; }
.home-col-left .filter-row-2 input[type=number] { font-size: 0.75rem; }
.home-col-left .filter-apply { padding: 3px 8px; font-size: 0.75rem; }
.home-col-left .filter-reset,
.home-col-left .filter-lock { padding: 3px 7px; font-size: 0.75rem; }
/* 左栏奖牌区 (2026-09-20 回滚三张卡布局; 字号由 MedalPanel scoped + main.css 统一控制,
   不再需要 :deep 覆盖子元素字号 —— 旧 40px/18px 大字覆盖是"字体不统一"的元凶, 保持删除) */
.home-col-left .medal-section { padding: 5px; margin: 4px 0; gap: 8px; border-radius: 8px; }
@media (max-width: 1099px) {
  .alert-rule.alert-rule-compact { flex-wrap: wrap; gap: 6px; }
}
@media (max-width: 768px) {
  .alert-rule.alert-rule-compact { padding: 4px 6px !important; margin: 2px 0 4px !important; gap: 4px !important; row-gap: 4px !important; flex-wrap: wrap !important; }
  .alert-rule.alert-rule-compact .rule-text,
  .alert-rule.alert-rule-compact .rule-text strong { font-size: 0.75rem !important; }
  .mode-tabs.mode-tabs-inline { gap: 3px !important; }
  .mode-tab.mode-tab-compact { padding: 4px 9px !important; font-size: 0.75rem !important; min-height: 26px; }
  .alert-rule.alert-rule-compact .tdx-export-btn { padding: 4px 6px !important; font-size: 0.75rem !important; min-height: 26px; }
}

/* 浅色主题覆盖 */
body[data-bg="light"] .mode-tab:hover {  color: #5a4a3a;  }
body[data-bg="light"] .mode-tab {  color: #5a4a3a; border-color: #b83010; background: rgba(255,255,255,0.6);  }
body[data-bg="light"] .mode-tab.active .mode-desc {  color: #6a5a20;  }
body[data-bg="light"] .page-back {  color: #6b6b6b;  }
body[data-bg="light"] .page-back:hover {  color: #c79100;  }
body[data-bg="light"] .yizi-card {  color: #8a5500; background: rgba(255,150,50,0.12); border-color: rgba(255,150,50,0.5);  }
body[data-bg="light"] .yizi-card b {  color: #8a4a00;  }
body[data-bg="light"] .filter-tag {  color: #8a5500; background: rgba(255,80,80,0.12);  }
body[data-bg="light"] .filter-divider {  color: rgba(0,0,0,0.3);  }
</style>