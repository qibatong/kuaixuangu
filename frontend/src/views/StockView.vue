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
        <!-- 模式切换(内联): 竞价 / AI预测 (2026-09-01: 原"盘中"替换为 AI预测)
             2026-09-05 主人需求: ① 去掉原顶部提示文案(竞价「9:30前可重新选股…」与
             AI预测「AI 竞价预测…」两行) ② 原右上角「锁定」按钮移除(下方 FilterPanel
             已有同功能锁定) ③ 「刷新」按钮下移至 FilterPanel 的 应用/重置/锁定 组 -->
        <div class="alert-rule alert-rule-compact">
          <span class="mode-tabs mode-tabs-inline">
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'auction' }" @click="switchTab('auction')"><i class="fa fa-sun-o"></i> AI选股</button>
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'aipick' }" @click="switchTab('aipick')"><i class="fa fa-android"></i> AI预测·金睛</button>
            <!-- 2026-09-25: 火眼(LightGBM) 平行链路(与 AI预测 同构, 只换模型); 手机端一并生效(本组 tab 在左栏内部) -->
            <button class="mode-tab mode-tab-compact" :class="{ active: leftTab === 'aipick_lgb' }" @click="switchTab('aipick_lgb')"><i class="fa fa-flask"></i> AI预测·火眼</button>
          </span>
        </div>

        <!-- 筛选面板(仅竞价模式) -->
        <!-- 2026-09-05: 刷新按钮已下移到 FilterPanel(与 应用/重置/锁定 同组),
             这里监听其 emit 并执行本视图的刷新逻辑 -->
        <div v-if="leftTab === 'auction'" class="home-filter"><FilterPanel @refresh="refreshRealTime" /></div>

        <template v-if="leftTab === 'auction'">
          <!-- 2026-09-16 选股闸门(主人拍板: 开盘日 9:00-9:26 不支持选股) -->
          <!-- 优先于会员门禁: 该时段连会员也不可用(不是权限问题, 是当日定格尚未产生) -->
          <div v-if="stocks.pickBlocked" class="pick-blocked-notice">
            <i class="fa fa-clock-o"></i>
            <span>{{ stocks.pickBlockedMsg }}</span>
          </div>
          <template v-else>
            <!-- 2026-09-18 (v4.11.29) 定格来源标注: 盘前/非交易日按设计出的是**上一交易日**
                 9:25 定格名单(PREOPEN/CLOSED), 必须让用户一眼看出"这不是当日名单"。
                 主人 9/18 反馈「刷出来是昨天的数据」就有这一类误解的成分。
                 🔴 必须放在 v-else 分支**内部**: 放在 v-if 与 v-else 之间会打断
                 v-if/v-else 链(Vue 编译期报 X_V_ELSE_NO_ADJACENT_IF, 构建直接失败),
                 而且标注条要**与名单并存**, 不能用 v-else-if 顶掉名单。 -->
            <div v-if="stocks.isDataCached && !stocks.freezeIsToday" class="freeze-notice">
              <i class="fa fa-history"></i>
              <span>当前为 <b>{{ stocks.freezeDate }}</b> 定格数据（上一交易日 / 回放），改条件可重选</span>
            </div>
            <!-- 会员门禁: 竞价选股 仅在工作日 9:15-15:00 要求会员; 其他时段放开 -->
            <VipGate v-if="!user.isMember && isMemberOnlyTime()" title="AI选股" />
            <!-- 配额门禁(2026-09-21 会员体系): 免费用户每日有限次数, 用尽后 VipGate 转配额引导模式 -->
            <VipGate
              v-else-if="stocks.quotaExceeded"
              ref="pickGateRef"
              title="AI选股"
            />
            <template v-else-if="user.isMember || !isMemberOnlyTime()">
              <!-- 5-1: 奖牌区已降级为 StockTable 行内徽标(前三行), 此处不再渲染三张重复卡片 -->
              <!-- 主表 -->
              <div v-if="!stocks.isDataCached" class="stock-table-container">
                <div class="loading-placeholder"><div class="spinner"></div><div>后台正在计算选股中...</div></div>
              </div>
              <!-- 2026-09-23 P0 口径条已按主人要求整条移除(15:4x): 名单上方的历史统计/口径说明
                   不再显示。连板标签本身(StockTable 名称格)与自身悬停统计保留不变。
                   注意: 这里必须保留 template v-else 包裹 —— 直接插在 v-if 与 v-else 之间
                   会打断 v-if/v-else 链, 构建直接失败(见上方 freeze-notice 注释)。 -->
              <template v-else>
                <StockTable :stocks="stocks.cachedStocks" strategy="auction" :bid-seal-map="bidSealMap" />
              </template>
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
        <AuctionView/>
      </div>
    </div><!-- /.home-grid -->
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
import { copyText, downloadBlkFile } from '../utils/tdx'
// 2026-09-05: isBefore930 随「锁定」按钮移除后本视图不再使用, 从 import 中去掉
import { bjDateTimeStr, isIntradayNow, isMemberOnlyTime } from '../utils/time'

const stocks = useStocksStore()
const pool = usePoolStore()
const user = useUserStore()
const bjTime = ref('--:--:--')
const bidSealMap = ref({})        // 竞价涨停委买额 map: code -> {limitBoards, bidSealAmt, bidNetAmt}
// 配额引导页引用(2026-09-21): stocks.quotaExceeded 时把 detail 塞进 VipGate 配额模式
const pickGateRef = ref(null)
const { refreshYidongCodes } = useYidongMonitor()

// 2026-09-01: 左视图模式切换 竞价 / AI预测(原"盘中"已被 AI预测替换)
// 使用本地 leftTab 而非 stocks.strategy: 不触发盘中数据流(spot 已于 2026-09-09 下线)
const leftTab = ref('auction')

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
    } catch (e) {
      showToast('❌ ' + e.message, 'error')
    }
  }
  // 启动定时器: 时钟 / 自动收录 / 过期检查
  clockTimer = setInterval(() => { bjTime.value = bjDateTimeStr() }, 1000)
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
    safe(stocks.updateRealTimeOnly({ silent: true }))
  }, 30000)
}

// 当前模式的选股结果(左视图仅竞价模式, 恒为 cachedStocks)
function currentList() {
  return stocks.cachedStocks
}

// 2026-09-05: 原 reLock() 随右上角「锁定」按钮一并移除(下方 FilterPanel 已提供
// 锁定/解锁, 且 store.reLockData() 保留供其使用), 此处不再需要包装函数。
function refreshRealTime() {
  // 2026-09-22 v4.11.35: 手动点「刷新」也是一次主动操作(有别于 30s 自动轮询)
  trackUsage('picker')
  stocks.updateRealTimeOnly().catch(e => showToast('❌ 更新失败：' + e.message, 'error'))
}
// 左视图模式切换: auction(竞价, 数据流与 store 联动) / aipick(AI预测) / aipick_lgb(LightGBM 版)
// 后两者都是 AipickView·AipickLgbView 自加载, 与 store 数据流无关。
function switchTab(m) {
  if (leftTab.value === m) return
  leftTab.value = m
  // 2026-09-22 v4.11.35: AI 预测的使用计数由 AipickView 自己在加载报表时上报,
  // 这里**不要**再记一次(同一动作会双计)。
  if (m === 'auction' && !stocks.isDataCached) {
    stocks.fetchAndCache().catch(e => showToast('❌ ' + e.message, 'error'))
  }
}
function downloadAll() { downloadBlkFile(stocks.cachedStocks, 0) }
function copyCodes() {
  if (!stocks.cachedStocks.length) { showToast('无数据', 'error'); return }
  copyText(stocks.cachedStocks.map(s => s.code).join('\n'), `✅ 已复制 ${stocks.cachedStocks.length} 个代码，可粘贴到电脑端导入`)
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