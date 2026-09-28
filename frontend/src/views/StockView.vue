<template>
  <div>
    <h1 class="visually-hidden">选股</h1>
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
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'auction' }" @click="switchTab('auction')"><i class="fa fa-sun-o"></i> AI选股</button>
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'spot' }" @click="switchTab('spot')"><i class="fa fa-bolt"></i> 盘中实时</button>
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'aipick' }" @click="switchTab('aipick')"><i class="fa fa-android"></i> AI预测·金睛</button>
            <!-- 2026-09-25: 火眼(LightGBM) 平行链路(与 AI预测 同构, 只换模型); 手机端一并生效(本组 tab 在左栏内部) -->
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'aipick_lgb' }" @click="switchTab('aipick_lgb')"><i class="fa fa-flask"></i> AI预测·火眼</button>
          </span>
          <!-- 2026-09-27 v4.11.63《移动端清单》§二·4: 名单数据的更新时刻。
               用 .right-group 挂到本行右侧（该 class 自带 margin-left:auto，且本行已
               justify-content:flex-start ⇒ 它出现不会把左边那组 tab 挤到中间）。
               ⚠️ 它讲的是**名单数据**的取回时刻，与页头那个每秒跳的时钟无关。 -->
          <span class="right-group"><DataStamp :at="dataAt" :ok="dataOk" :interval="autoOn ? 30 : 0" :stale="dataStale" /></span>
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
               ② freeze-notice 定格标注条 —— 仍显示(锁定名单可能有 freezeDate)。
               ③ 会员/配额门禁 —— 照旧。
               ④ 「锁定」按钮(FilterPanel 内, 见 store.strategy 判据)。 -->

        <!-- 2026-09-16 选股闸门(主人拍板: 开盘日 9:00-9:26 不支持选股) -->
        <!-- 优先于会员门禁: 该时段连会员也不可用(不是权限问题, 是当日定格尚未产生) -->
        <div v-if="leftTab === 'auction' && stocks.pickBlocked" class="pick-blocked-notice">
          <i class="fa fa-clock-o"></i>
          <span>{{ stocks.pickBlockedMsg }}</span>
        </div>

        <!-- 2026-09-18 (v4.11.29) 定格来源标注: 盘前/非交易日按设计出的是**上一交易日**
             9:25 定格名单(PREOPEN/CLOSED), 必须让用户一眼看出"这不是当日名单"。
             主人 9/18 反馈「刷出来是昨天的数据」就有这一类误解的成分。
             ★ v4.11.80: 只在 **auction 策略**下出这条 —— spot 名单永远是"此刻",
               不存在"上一交易日定格"(数据源不同, 见上方分支注释)。
             必须与名单并存 → 用独立 v-if, 不接入下方 v-if/v-else 链。 -->
        <div v-if="leftTab === 'auction' && !stocks.isSpotStrategy && stocks.isDataCached && !stocks.freezeIsToday" class="freeze-notice">
          <i class="fa fa-history"></i>
          <span>当前为 <b>{{ stocks.freezeDate }}</b> 定格数据（上一交易日 / 回放），改条件可重选</span>
        </div>

        <!-- 会员门禁: 竞价选股 仅在工作日 9:15-15:00 要求会员; 其他时段放开 -->
        <VipGate v-if="leftTab === 'auction' && !user.isMember && isMemberOnlyTime()" title="AI选股" />
        <!-- 配额门禁(2026-09-21 会员体系): 免费用户每日有限次数, 用尽后 VipGate 转配额引导模式 -->
        <VipGate
          v-else-if="leftTab === 'auction' && stocks.quotaExceeded"
          ref="pickGateRef"
          title="AI选股"
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
          <VipGate v-if="!user.isMember && isMemberOnlyTime()" title="盘中实时" />
          <template v-else-if="user.isMember || !isMemberOnlyTime()">
            <div v-if="!stocks.spotCached" class="stock-table-container">
              <div class="loading-placeholder">
                <div v-if="stocks.spotLoading" class="spinner"></div>
                <div v-else><i class="fa fa-bolt"></i> 点「应用」获取盘中实时名单</div>
                <div v-if="stocks.spotLoading">正在扫描全市场实时行情…</div>
              </div>
            </div>
            <template v-else>
              <!-- 盘中数据的时效提示: spot 每次请求都是"此刻", 必须让用户知道数据有多新 -->
              <div class="spot-notice">
                <i class="fa fa-bolt"></i>
                <span>盘中实时名单 · 共 <b>{{ stocks.spotStocks.length }}</b> 只 · 数据时刻 <b>{{ spotTimeStr }}</b>（每次「应用」重新扫描，无需等 9:26 定格）</span>
              </div>
              <StockTable :stocks="stocks.spotStocks" strategy="spot" />
            </template>
          </template>
        </template>

        <!-- AI预测(2026-09-01 替换原盘中选股; 自带 VIP 门禁/日期回看/规则过滤) -->
        <!-- 2026-09-01: AI预测内嵌左视图; 隐藏日期回看(回看入口在导航栏「历史回看」页) -->
        <AipickView v-else-if="leftTab === 'aipick'" :embedded="true" :show-date-picker="false" />
        <!-- 2026-09-25: LightGBM 版(共用 AipickReport 组件, 只是 model='lgb' 取另一份产物) -->
        <AipickLgbView v-else :embedded="true" :show-date-picker="false" />
      </div><!-- /.home-col-left -->

      <!-- 右栏: 竞价异动 -->
      <div class="home-col home-col-right" :class="{ 'home-col-hidden': mobilePane !== 'auction' }">
        <AuctionView />
      </div>
    </div><!-- /.home-grid -->
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
import VipGate from '../components/VipGate.vue'
import { useStocksStore } from '../stores/stocks'
import { usePoolStore } from '../stores/pool'
import { useUserStore } from '../stores/user'
import { kplBidSeal } from '../api/kpl'
import { trackUsage } from '../api/activity'
import { showToast } from '../utils/toast'
import { useYidongMonitor } from '../composables/useYidongMonitor'
// 2026-09-05: isBefore930 随「锁定」按钮移除后本视图不再使用, 从 import 中去掉
import { bjDateTimeStr, isIntradayNow, isMemberOnlyTime } from '../utils/time'
// 2026-09-27 v4.11.63《移动端清单》§二·4: 数据更新时刻（与页头时钟区分开）
import DataStamp from '../components/DataStamp.vue'
import { useDataStamp } from '../composables/useDataStamp'

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
// ★ 2026-09-28 v4.11.80 第三步: **tab1「AI选股」也改走 spot 引擎**(主人需求
//   「AI竞价出来的数据就锁定」)—— 但**保留 tab1 的全套锁定语义**(定格标注条 / 9:26
//   闸门 / 会员配额门禁 / merge 实时 / 进自选池)。
//
//   🔴 关键设计: **tab(leftTab) 与 策略(store.strategy) 是两个维度, 不再 1:1 绑定**。
//     · tab1「AI选股」   ⇒ leftTab='auction'(渲染锁定那套) + strategy='spot'(用实时引擎)
//     · tab2「盘中实时」 ⇒ leftTab='spot'(渲染实时那套)    + strategy='spot'
//     两者**共用 spot 引擎**, 差别只在 UI 语义: tab1 出的是「锁定名单」(可落批次/可回放),
//     tab2 出的是「此刻的答案」(不落批次/不锁定)。
//   ⚠️ 为什么不让 tab1 直接切到 leftTab='spot': 那样会丢掉定格标注条/锁定按钮/闸门提示,
//     而主人要的恰恰是「锁定」这个动作 —— 只是**锁的内容换成 spot 引擎算的名单**。
const leftTab = ref('auction')

// 盘中名单的数据时刻(秒 → HH:MM:SS, 北京时间)。spot 每次请求都是"此刻", 必须显式告知。
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
  // 2026-09-16 选股闸门: 交易日 9:00-9:26 不发请求(该时段只能得到非当日定格的名单:
  // 9:00-9:15 上交易日 / 9:15-9:25 竞价在变 / 9:25-9:26 当日定格尚未落库)。
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
// 左视图模式切换: auction(竞价) / spot(盘中实时) / aipick(AI预测) / aipick_lgb(LightGBM 版)
// 后两者都是 AipickView·AipickLgbView 自加载, 与 store 数据流无关。
//
// ★ 2026-09-28 v4.11.80 第三步: **tab1 也走 spot 引擎** ⇒ tab → strategy 不再 1:1:
//   · tab1('auction') → strategy='spot'   ← 本轮改点(原来映射 'auction')
//   · tab2('spot')    → strategy='spot'
//   · aipick 两个 → 保持 'auction'(它们不读 store.strategy, 只是别留下脏值给 FilterPanel)
//   🔴 副作用提醒: 切到 aipick 时 store.strategy 仍是 'spot' —— FilterPanel 此刻已隐藏
//     (v-if 只放 auction/spot), 不影响渲染; 切回来时 switchTab 会重设, 不会残留错配。
function switchTab(m) {
  if (leftTab.value === m) return
  leftTab.value = m
  // 2026-09-28 v4.11.80 第三步: tab1/tab2 都归 spot(见上方注释), aipick 归 auction。
  stocks.setStrategy(m === 'aipick' || m === 'aipick_lgb' ? 'auction' : 'spot')
  // 2026-09-22 v4.11.35: AI 预测的使用计数由 AipickView 自己在加载报表时上报,
  // 这里**不要**再记一次(同一动作会双计)。
  // 2026-09-28 v4.11.80: tab1 走 spot 后**首屏取数逻辑不变** —— fetchAndCache 内部按
  //   this.strategy 分支(spot 写 spotStocks), 见 stores/stocks.js。
  if (m === 'auction' && !stocks.isDataCached) {
    stocks.fetchAndCache().then(() => markData()).catch(e => showToast('❌ ' + e.message, 'error'))
  }
  // 盘中实时: 切过来自动拉一次(否则用户看到的是"点应用"空态, 多一步操作)。
  // 失败静默 —— 面板仍在, 用户可手点「应用」重试; 弹错会打断刚切 tab 的动作。
  // ⚠️ tab1 与 tab2 现在共用 spotStocks: tab1 若已取到名单, tab2 切过来直接复用, 不重拉
  //   (避免刚锁完又打一次网络); 名单时效由顶栏 DataStamp / spot-notice 显式告知。
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

onMounted(() => {
  bjTime.value = bjDateTimeStr()
  autoOn.value = isIntradayNow()
  init()
  loadBidSeal()
  refreshYidongCodes()   // 首页选股/竞价异动 标记异动监管股票
  setupStickyOffsets()
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
/* 2026-09-16 选股闸门提示块(交易日 9:00-9:26): 替代主表位置, 说明为何暂时看不到名单。
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

/* 2026-09-18 (v4.11.29) 定格来源标注条(上一交易日 / 回放): 黄色提示系(项目五色内),
   与涨跌红绿语义无关。语义比"拦截"轻 —— 数据可用, 只是不是当日的。 */
.freeze-notice {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 0;
  padding: 8px 12px;
  border: 1px solid rgba(230, 180, 0, 0.38);
  border-radius: 6px;
  background: rgba(230, 180, 0, 0.07);
  color: var(--text-main);
  font-size: 0.75rem;
  line-height: 1.5;
}
.freeze-notice .fa { color: #e6b400; }
.freeze-notice b { color: #e6b400; font-weight: 600; }
body[data-bg="light"] .freeze-notice { color: #8a5500; }
body[data-bg="light"] .freeze-notice b { color: #8a5500; }

/* 2026-09-28 v4.11.75 盘中实时(spot)名单的时效提示条。
   与上方 freeze-notice(黄=提醒"这不是当日数据")**语义不同**: 这条是蓝色信息系,
   说明"这份数据很新" —— 两者不会同时出现(spot 无定格概念, 竞价无实时概念)。
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