<template>
  <!--
    竞价一进二 —— 2026-09-28 新增
    ===========================================================================
    语义：昨日主板首板 → 今日竞价阶段评估**二连板潜力**，按综合评分降序。
    门禁：**严格 VIP/付费**（与「竞价异动」同强度，requiredLevel=1）—— 免费试用
          前端直接出 VipGate、后端也会 403（两道，前端那道只是省一次请求）。
    取数：全部走后端 /api/yijiner（后端复用 fetcher 涨停池 + 东财点查），
          **浏览器不直连东财**，因此门禁是真门禁、名单不会从前端泄露。
    评分：逐函数照搬主人提供的独立网页版（含其 f4(涨跌额)当昨收的缺陷，见
          backend/app/services/yijiner.py 头注释）。分数与网页版逐位一致。

    入口：**仅**首页左视图第 5 个 tab（StockView）。
    ⚠️ 刻意**未新增路由/导航项**：router 的 meta.group/meta.order 是导航栏渲染的数据源
    （2026-09-27 刚重构为 5 分组），新增一条路由会自动多出一个一级导航项 —— 属
    「扩大改动范围」，待主人明确要求再做。
  -->
  <div class="page-shell" :class="{ 'yj-embedded': embedded }">
    <h1 class="visually-hidden">竞价一进二</h1>

    <!-- 严格门禁：未登录/免费试用直接看到引导卡，不发请求 -->
    <VipGate v-if="!user.isVipOrPaid || needVip" title="竞价一进二" :required-level="1" />

    <template v-else>
      <div class="yj-panel">
        <div class="yj-head">
          <div class="yj-title">
            <i class="fa fa-level-up yj-icon"></i>
            竞价一进二
            <span v-if="meta.dataDate" class="yj-chip" title="实际取到的涨停池日期（休市日会自动往前找最近交易日）">首板日 {{ meta.dataDate }}</span>
            <span v-if="list.length" class="yj-chip yj-chip-accent">{{ list.length }} 只</span>
          </div>
          <button class="yj-refresh" :disabled="loading" @click="load">
            <i class="fa" :class="loading ? 'fa-spinner fa-spin' : 'fa-refresh'"></i> 刷新
          </button>
        </div>

        <div class="yj-desc">
          昨日<b>主板首板</b>（60/00，已剔除创业板/科创板/北交所与一字板）→ 今日竞价二连板潜力
          <br>
          <span class="yj-dim">评分 = 竞价涨幅 35 + 量能 30 + 封单质量 20 + 板块地位 15</span>
          <span class="yj-dim">· 红线（竞价翻绿 / 量比&lt;0.3）封顶 45 分</span>
        </div>

        <div v-if="loading && !list.length" class="loading-placeholder">
          <div class="spinner"></div>
          <div>正在扫描昨日首板、拉取今日行情…</div>
        </div>

        <div v-else-if="errMsg" class="yj-empty">
          <i class="fa fa-exclamation-circle"></i> {{ errMsg }}
          <button class="yj-retry" @click="load">重试</button>
        </div>

        <div v-else-if="!list.length" class="yj-empty">
          <i class="fa fa-filter"></i> 当前没有符合条件的一进二候选
          <div class="yj-empty-hint">{{ statsHint }}</div>
        </div>

        <template v-else>
          <div class="yj-scroll">
            <table class="stock-table yj-table">
              <thead>
                <tr>
                  <th class="yj-th-rank">排名</th>
                  <th>名称</th>
                  <th>综合评分</th>
                  <th>可信</th>
                  <th>竞价涨幅</th>
                  <th>实时涨幅</th>
                  <th>实体涨幅</th>
                  <th>流通市值</th>
                  <th>昨封板</th>
                  <th>昨炸板</th>
                  <th>行业</th>
                  <th>概念</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in list" :key="r.code">
                  <td class="yj-rank">
                    <i v-if="i < 3" class="fa fa-trophy" :class="'yj-trophy-' + (i + 1)" :title="'第 ' + (i + 1) + ' 名'"></i>
                    <span v-else>{{ i + 1 }}</span>
                  </td>
                  <td
                    class="name-col yj-name"
                    :data-stock-code="r.code"
                    :data-stock-name="r.name"
                    title="点击查看分时/日K/周K/月K"
                  >
                    <div class="yj-name-main">{{ r.name }}<PoolHoverBtn :item="r" /></div>
                    <div class="yj-name-sub">{{ r.code }}</div>
                  </td>
                  <td><span class="yj-score" :class="{ 'yj-score-low': r.redFlag }">{{ r.probability }}分</span></td>
                  <td class="yj-dim-cell">{{ r.confidence }}%</td>
                  <td :class="chgCls(r.bidChange)">{{ fmtPct(r.bidChange) }}</td>
                  <td :class="chgCls(r.realChange)">{{ fmtPct(r.realChange) }}</td>
                  <td :class="chgCls(r.entityChange)">{{ fmtPct(r.entityChange) }}</td>
                  <td>{{ fmtMv(r.circulationMV) }}</td>
                  <td class="yj-time">{{ fmtFbt(r.firstSealTime) }}</td>
                  <td :class="{ 'yj-warn': r.breakCount > 0 }">{{ r.breakCount }}次</td>
                  <td>{{ r.industry || '-' }}</td>
                  <td class="yj-concept" :title="r.concept || '暂无概念'">{{ shortConcept(r.concept) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="yj-note">
            <template v-if="meta.stats">
              昨日涨停池 {{ meta.stats.poolTotal }} 只 → 主板首板 {{ meta.stats.firstBoard }} 只（剔一字板 {{ meta.stats.oneWordDropped }}）→ 候选 {{ meta.stats.candidates }} 只 → 入选 {{ meta.stats.kept }} 只
              <template v-if="droppedText"> · 剔除：{{ droppedText }}</template>
            </template>
            <span v-if="meta.elapsedMs"> · 耗时 {{ meta.elapsedMs }}ms</span>
            <div class="yj-disclaimer">数据仅供研究参考，不构成任何投资建议</div>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import VipGate from '../components/VipGate.vue'
import PoolHoverBtn from '../components/PoolHoverBtn.vue'
import { fetchYijiner } from '../api/yijiner'
import { useUserStore } from '../stores/user'
import { showToast } from '../utils/toast'

// 2026-09-28: 嵌入首页左视图时传 embedded=true（与 AipickView 同约定，收紧间距）
defineProps({
  embedded: { type: Boolean, default: false },
})

const user = useUserStore()
const list = ref([])
const meta = ref({})           // dataDate / stats / filters / elapsedMs
const loading = ref(false)
const errMsg = ref('')
const needVip = ref(false)     // 后端 403 兜底（如会员刚过期）

// 剔除原因中文化（与后端 _passes_filters 的 key 一一对应）
const DROP_LABELS = {
  not_main_board: '非主板',
  st_or_suspend: 'ST/停牌',
  new_stock: '次新',
  bid_range: '竞价涨幅越界',
  mv_range: '市值越界',
  price_range: '股价越界',
}

const droppedText = computed(() => {
  const d = (meta.value.stats || {}).dropped || {}
  return Object.entries(d).map(([k, v]) => `${DROP_LABELS[k] || k} ${v}`).join('、')
})

const statsHint = computed(() => {
  const s = meta.value.stats
  if (!s) return '可稍后点「刷新」再试'
  return `昨日涨停池 ${s.poolTotal} 只 / 主板首板 ${s.firstBoard} 只，全部被过滤条件剔除`
})

function fmtPct(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%'
}
// A 股惯例：红涨绿跌（走全局 --accent 变量，自动适配深浅主题）
function chgCls(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? 'yj-up' : 'yj-down'
}
function fmtMv(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(1) + '亿' : '-'
}
// 后端 firstSealTime 与东财 fbt 同口径：HHMMSS 整数（93700 → 09:37:00）
function fmtFbt(t) {
  const n = Number(t)
  if (!Number.isFinite(n) || n <= 0) return '-'
  const s = String(Math.trunc(n)).padStart(6, '0')
  return `${s.slice(0, 2)}:${s.slice(2, 4)}:${s.slice(4, 6)}`
}
function shortConcept(c) {
  const s = String(c || '-')
  return s.length > 16 ? s.slice(0, 16) + '…' : s
}

async function load() {
  if (!user.isVipOrPaid) return        // 前端预判，省一次必然 403 的请求
  loading.value = true
  errMsg.value = ''
  try {
    const d = await fetchYijiner()
    if (d && d.ok) {
      list.value = d.list || []
      meta.value = { dataDate: d.dataDate, stats: d.stats, filters: d.filters, elapsedMs: d.elapsedMs }
    } else {
      // 后端取数失败（涨停池/行情异常）→ 如实提示，不清空已有名单
      errMsg.value = (d && d.msg) || '取数失败'
      if (!list.value.length) meta.value = {}
    }
  } catch (e) {
    if (e && e.status === 403) { needVip.value = true; return }
    errMsg.value = (e && e.message) || '请求失败'
    showToast('❌ ' + errMsg.value, 'error')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (user.isVipOrPaid) load()
})

defineExpose({ load })
</script>

<style scoped>
.yj-panel { padding: 2px 0 8px; }

/* ---- 头部 ---- */
.yj-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  margin: 2px 0 4px;
}
.yj-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-main);
}
.yj-icon { color: var(--accent); }
.yj-chip {
  font-size: 11px;
  font-weight: 500;
  padding: 1px 8px;
  border-radius: 10px;
  border: 1px solid var(--border-soft, #3a3f4b);
  color: var(--text-secondary);
}
.yj-chip-accent {
  border-color: rgba(var(--accent-rgb), 0.5);
  background: rgba(var(--accent-rgb), 0.12);
  color: var(--accent-text, var(--accent));
}
.yj-refresh {
  background: rgba(var(--accent-rgb), 0.12);
  border: 1px solid rgba(var(--accent-rgb), 0.45);
  color: var(--accent-text, var(--accent));
  border-radius: 6px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.yj-refresh:hover:not(:disabled) { background: rgba(var(--accent-rgb), 0.22); }
.yj-refresh:disabled { opacity: 0.6; cursor: default; }

/* ---- 口径说明 ---- */
.yj-desc {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-secondary);
  margin: 0 0 6px;
}
.yj-desc b { color: var(--accent-deep, var(--accent)); }
.yj-dim { color: var(--text-muted); }

/* ---- 空态 / 错误态 ---- */
.yj-empty {
  text-align: center;
  padding: 28px 12px;
  color: var(--text-secondary);
  font-size: 13px;
}
.yj-empty .fa { color: var(--accent); margin-right: 6px; }
.yj-empty-hint { margin-top: 6px; font-size: 11.5px; color: var(--text-muted); }
.yj-retry {
  margin-left: 8px;
  background: rgba(var(--accent-rgb), 0.15);
  border: 1px solid rgba(var(--accent-rgb), 0.45);
  color: var(--accent-text, var(--accent));
  border-radius: 6px;
  padding: 3px 12px;
  font-size: 12px;
  cursor: pointer;
}

/* ---- 表格 ---- */
.yj-scroll { overflow-x: auto; }
.yj-table { min-width: 1020px; font-size: 12px; }
.yj-table th { font-size: 11.5px; white-space: nowrap; }
/*
  2026-09-28: 关闭本表的 sticky 表头（有意为之，勿"顺手恢复"）。
  全局 main.css:314-324 给 `.home-col-left .stock-table thead th` 设了
  position:sticky + top:var(--sticky-thead-top)，而该变量由首页 JS 按 `.home-filter`
  高度写入 —— 本 tab **没有 .home-filter** ⇒ 变量沿用别的 tab 的旧值（实测 56px）。
  再叠加本表需要横向滚动（容器 overflow:auto ⇒ 成为滚动容器），表头被顶到首行之下：
  实测 thead.top=391 vs 首行.top=367，视觉上遮住第一条。
  本名单通常仅数行，sticky 收益为零 ⇒ 关掉换取稳定布局。
  选择器多一层 .yj-panel 是必要的：与全局规则同为 (0,2,2)，靠源码顺序获胜太脆弱。
*/
.yj-panel .yj-table thead th { position: static; }
.yj-table td { white-space: nowrap; }
.yj-th-rank { width: 44px; }
.yj-rank { text-align: center; color: var(--text-muted); font-weight: 600; }
.yj-trophy-1 { color: #e6b400; }
.yj-trophy-2 { color: #9aa0a6; }
.yj-trophy-3 { color: #b5763a; }

.yj-name { cursor: pointer; text-align: left; }
.yj-name-main { font-weight: 600; color: var(--text-main); }
.yj-name-sub { font-size: 10.5px; color: var(--text-muted); }

.yj-score {
  font-weight: 700;
  color: var(--accent);
}
/* 触发红线的票：分数被压到 ≤45，用中性灰降低视觉权重（避免与"高分红"混淆） */
.yj-score-low { color: var(--text-muted); }

.yj-up { color: var(--accent); }
.yj-down { color: #00a854; }
.yj-warn { color: var(--accent); }
.yj-dim-cell { color: var(--text-secondary); }
.yj-time { color: var(--text-secondary); }
.yj-concept {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text-secondary);
  text-align: left;
}

/* ---- 底部口径与统计 ---- */
.yj-note {
  margin-top: 6px;
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-muted);
}
.yj-disclaimer { text-align: center; margin-top: 2px; }

/* 嵌入首页左视图（半宽）时收紧 */
.yj-embedded .yj-desc { font-size: 11px; }
.yj-embedded .yj-title { font-size: 14px; }
.yj-embedded .yj-concept { max-width: 110px; }

/* 浅色主题：白底上不用亮红当文字，改用深变体 */
body[data-bg="light"] .yj-down { color: #1f7a45; }
body[data-bg="light"] .yj-trophy-1 { color: #8a5500; }
body[data-bg="light"] .yj-chip { color: #5a4a3a; }
</style>
