<template>
  <!--
    消息中心 /messages（2026-10-04 建立 / 2026-10-06 重构）
    主人需求：「系统消息比如系统更新提醒，会员到期提醒，等等」

    🔴 2026-10-06 三条设计约定（改这个页面前必读）：

    1. **三分类**（主人 2026-10-05 拍板精简，不要再加第四类）：
         system  系统公告/运营/邀请/签到/免费次数用尽
         account 会员到期与续费
         trade   竞价开始 / 名单就绪 / 尾盘抢筹（只在交易日固定时点出现）
       account 类**不是"信件"是"状态"** —— 续费后自动消失（后端靠 expire_at 快照自愈），
       所以不给"标记已读"，只给跳转动作。其余两类可标已读、可删除。

    2. **按条操作一律用 nkey，不用 id**。
       notices.id 无 AUTOINCREMENT，公告删掉后 id 会被新公告复用，回执会错挂。

    3. **推送偏好只管"推不推送"，站内消息永远可见**。
       站内消息是"想去就能找到"的，藏起来用户就找不到上次那条到期提醒了；
       真正打扰人的是推送。快选是时点型工具（一天只在 9:15–9:35 用），
       推送炸一次用户就会永久关掉，所以后端有硬上限（每天 3 条 / 运营 1 条）。
  -->
  <div class="page-shell msg-page">
    <h1>消息中心</h1>

    <div class="msg-head">
      <span class="msg-count">
        <template v-if="unread > 0">{{ unread }} 条未读</template>
        <template v-else>全部已读</template>
      </span>
      <div class="msg-ops">
        <button class="msg-btn" :disabled="loading" title="刷新" @click="load">
          <i class="fa fa-refresh" :class="{ spin: loading }"></i> 刷新
        </button>
        <button v-if="unread > 0" class="msg-btn" :disabled="marking" @click="markAll">全部标为已读</button>
      </div>
    </div>

    <!-- 分类 tab：未读角标按类统计（与后端红点同一口径：account 未读 或 warn/urgent 未读） -->
    <div class="msg-tabs">
      <button
        v-for="t in TABS"
        :key="t.key"
        class="msg-tab"
        :class="{ on: cat === t.key }"
        :aria-pressed="cat === t.key ? 'true' : 'false'"
        @click="cat = t.key"
      >
        {{ t.label }}
        <span v-if="unreadOf(t.key) > 0" class="msg-tab-badge">{{ unreadOf(t.key) }}</span>
      </button>
    </div>

    <!-- 推送开关：默认关，用户主动点开才订阅。不支持的环境（无 SW/PushManager、
         安卓壳内 FCM 不可达）整块不渲染 —— 不给一个点了必然失败的开关。 -->
    <div v-if="pushSupported" class="msg-push">
      <span class="msg-push-label"><i class="fa fa-mobile"></i> 手机推送</span>
      <button class="msg-btn" :disabled="pushBusy" @click="togglePush">
        {{ pushOn ? '已开启 · 点此关闭' : '开启通知' }}
      </button>
      <span v-if="pushMsg" class="msg-push-msg">{{ pushMsg }}</span>
      <span v-else class="msg-push-hint">竞价提醒、名单就绪、会员到期会直接推到手机</span>
    </div>

    <!-- 订阅偏好：默认收起。🔴 必须先有开关再开始大规模推送，否则用户唯一的
         退路就是关掉总开关，站外通道一次就废。 -->
    <details v-if="pushSupported" class="msg-prefs" @toggle="onPrefsToggle">
      <summary class="msg-prefs-sum">
        <i class="fa fa-sliders"></i> 通知设置
        <span class="msg-prefs-hint">每天最多 {{ dailyMax }} 条，运营类 {{ opsMax }} 条</span>
      </summary>
      <div v-if="prefsLoading" class="msg-prefs-tip">正在读取设置…</div>
      <ul v-else class="msg-prefs-list">
        <li v-for="k in prefKeys" :key="k.key" class="msg-pref-row">
          <label class="msg-pref-label">
            <input
              type="checkbox"
              :checked="!!prefs[k.key]"
              :disabled="!!prefSaving[k.key]"
              @change="togglePref(k.key, $event.target.checked)"
            >
            <span>{{ k.label }}</span>
            <em v-if="k.ops" class="msg-pref-ops">运营</em>
          </label>
        </li>
      </ul>
      <p class="msg-prefs-foot">关闭后不再推到手机，站内消息仍然可见。</p>
    </details>

    <!-- 🔴 失败态与空态**必须文案不同**（静态闸门 verify 会查）：不许都渲染成一个空列表 -->
    <div v-if="failed" class="msg-state err">
      <i class="fa fa-exclamation-triangle"></i>
      消息读取失败：{{ errMsg }}。这不是「没有消息」，请点刷新重试。
    </div>
    <div v-else-if="loading && !items.length" class="msg-state">正在读取消息…</div>
    <div v-else-if="!shown.length" class="msg-state empty">
      <i class="fa fa-bell"></i> {{ emptyText }}
    </div>

    <ul v-else class="msg-list">
      <li
        v-for="m in shown"
        :key="m.nkey || m.id"
        class="msg-item"
        :class="['lv-' + m.level, { read: m.read, account: m.category === 'account' }]"
      >
        <div class="msg-bar"><i class="fa" :class="iconOf(m)"></i></div>
        <div class="msg-main">
          <div class="msg-title">
            {{ m.title }}
            <span v-if="m.category === 'account'" class="msg-tag">账户</span>
            <span v-else-if="m.category === 'trade'" class="msg-tag">交易</span>
            <span v-else-if="!m.read" class="msg-tag unread">未读</span>
          </div>
          <div class="msg-body">{{ m.body }}</div>
          <div class="msg-foot">
            <span class="msg-time">{{ fmtTime(m.ts) }}</span>
            <!-- 到期/续费类给**处理入口**而不是"标已读"（它是状态，标了也没用） -->
            <template v-if="m.category === 'account'">
              <router-link class="msg-act" :to="m.action_value || '/member'" @click="onClick(m)">
                去处理 ›
              </router-link>
            </template>
            <template v-else>
              <button v-if="!m.read" class="msg-act" @click="markOne(m)">标为已读</button>
              <!-- 行动按钮：route=跳路由, copy=复制文本(如客服微信号) -->
              <router-link
                v-if="m.action_type === 'route' && m.action_value"
                class="msg-act"
                :to="m.action_value"
                @click="onClick(m)"
              >
                查看详情 ›
              </router-link>
              <button
                v-else-if="m.action_type === 'copy' && m.action_value"
                class="msg-act"
                @click="copyAct(m)"
              >
                复制
              </button>
            </template>
            <button class="msg-act danger" @click="removeOne(m)">删除</button>
          </div>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  fetchNotices, markNoticesRead, deleteNotices, trackNoticeClick,
  fetchNoticePrefs, saveNoticePrefs,
} from '../api/notices'
import { usePush } from '../composables/usePush'
import { showToast } from '../utils/toast'

// 推送开关：解构成普通变量，模板里直接用（ref 在 template 中会自动解包）
const {
  supported: pushSupported,
  subscribed: pushOn,
  busy: pushBusy,
  message: pushMsg,
  refresh: refreshPush,
  enable: enablePush,
  disable: disablePush,
} = usePush()

async function togglePush() {
  if (pushBusy.value) return
  if (pushOn.value) {
    const ok = await disablePush()
    if (ok) showToast('已关闭手机推送', 'success')
    return
  }
  const ok = await enablePush()
  if (ok) showToast('已开启，可先发布一条测试消息验证', 'success')
}

// ---------------- 分类 ----------------
// 🔴 三类是主人拍板的，别再加。key '' 表示"全部"。
const TABS = [
  { key: '', label: '全部' },
  { key: 'system', label: '系统' },
  { key: 'account', label: '账户会员' },
  { key: 'trade', label: '交易时点' },
]
const cat = ref('')

const items = ref([])
const unread = ref(0)
const loading = ref(false)
const marking = ref(false)
const failed = ref(false)
const errMsg = ref('')

const shown = computed(() => (cat.value ? items.value.filter((m) => (m.category || 'system') === cat.value) : items.value))

const emptyText = computed(() => {
  if (cat.value === 'account') return '暂无账户与会员消息。到期提醒会在到期前 7 天出现。'
  if (cat.value === 'trade') return '暂无交易时点提醒。竞价提醒只在交易日的 9:15 / 9:20 / 9:25 出现。'
  if (cat.value === 'system') return '暂无系统消息。系统更新与活动通知会在这里出现。'
  return '暂无消息。竞价提醒、名单就绪、会员到期提醒会在这里出现。'
})

/** 未读计数口径必须和后端一致：account 未读 或 warn/urgent 未读 才计数（info 级不亮红点） */
function unreadOf(key) {
  const list = key ? items.value.filter((m) => (m.category || 'system') === key) : items.value
  return list.filter((m) => !m.read && (m.category === 'account' || m.level === 'warn' || m.level === 'urgent')).length
}

// ---------------- 偏好 ----------------
const prefs = ref({})
const prefKeys = ref([])
const prefsLoading = ref(false)
const prefSaving = ref({})
const dailyMax = ref(3)
const opsMax = ref(1)

async function loadPrefs() {
  prefsLoading.value = true
  try {
    const d = await fetchNoticePrefs()
    if (d && d.ok) {
      prefs.value = d.prefs || {}
      prefKeys.value = d.keys || []
      dailyMax.value = Number(d.daily_max || 3)
      opsMax.value = Number(d.ops_max || 1)
    }
  } catch (_) {
    /* 偏好读不到不影响消息列表 */
  } finally {
    prefsLoading.value = false
  }
}

function onPrefsToggle(e) {
  // 展开时才拉，避免每次进页面都多发一个请求（details 的默认状态是收起）
  if (e.target.open && !Object.keys(prefs.value).length) loadPrefs()
}

async function togglePref(key, val) {
  prefSaving.value = { ...prefSaving.value, [key]: 1 }
  try {
    const d = await saveNoticePrefs({ [key]: val })
    if (d && d.ok) {
      prefs.value = d.prefs || { ...prefs.value, [key]: val }
      showToast(val ? '已开启该类推送' : '已关闭该类推送（站内仍可见）', 'success')
    } else {
      showToast('保存失败', 'error')
    }
  } catch (_) {
    showToast('保存失败', 'error')
  } finally {
    const s = { ...prefSaving.value }
    delete s[key]
    prefSaving.value = s
  }
}

// ---------------- 列表 ----------------
function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(Number(ts) * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function iconOf(m) {
  if (m.category === 'trade') return 'fa-clock-o'
  if (m.category === 'account') return m.level === 'urgent' ? 'fa-exclamation-circle' : 'fa-clock-o'
  return m.level === 'urgent' ? 'fa-exclamation-circle' : (m.level === 'warn' ? 'fa-exclamation-triangle' : 'fa-bullhorn')
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    const d = await fetchNotices()
    if (d && d.ok) {
      items.value = (d.items || []).map((m) => ({ ...m, category: m.category || 'system' }))
      unread.value = Number(d.unread || 0)
    } else {
      failed.value = true
      errMsg.value = (d && d.msg) || '未知原因'
    }
  } catch (e) {
    failed.value = true
    errMsg.value = e.message || '网络异常'
  } finally {
    loading.value = false
  }
}

async function markOne(m) {
  marking.value = true
  try {
    const d = await markNoticesRead({ nkeys: [m.nkey] })
    if (d && d.ok) { m.read = 1; unread.value = Math.max(0, unread.value - 1) }
    else showToast('标记已读失败', 'error')
  } catch (_) {
    showToast('标记已读失败', 'error')
  } finally {
    marking.value = false
  }
}

async function markAll() {
  marking.value = true
  try {
    const d = await markNoticesRead({ all: true })
    if (d && d.ok) {
      items.value.forEach((it) => { if (it.category !== 'account') it.read = 1 })
      // 账户事件是状态, 标不了已读 ⇒ 未读数只会降到"剩余账户事件数", 不谎报 0
      unread.value = items.value.filter((it) => it.category === 'account' && (it.level === 'warn' || it.level === 'urgent')).length
    } else showToast('标记已读失败', 'error')
  } catch (_) {
    showToast('标记已读失败', 'error')
  } finally {
    marking.value = false
  }
}

async function removeOne(m) {
  try {
    const d = await deleteNotices([m.nkey])
    if (d && d.ok) {
      items.value = items.value.filter((x) => x.nkey !== m.nkey)
      showToast('已删除', 'success')
    } else showToast('删除失败', 'error')
  } catch (_) {
    showToast('删除失败', 'error')
  }
}

function onClick(m) {
  if (m && m.nkey) trackNoticeClick(m.nkey).catch(() => {})
}

async function copyAct(m) {
  try {
    await navigator.clipboard.writeText(m.action_value)
    showToast('已复制', 'success')
    onClick(m)
  } catch (_) {
    showToast('复制失败，请手动选取', 'error')
  }
}

onMounted(() => {
  load()
  refreshPush()   // 推送状态独立加载，失败也不该挡住消息列表
})
</script>

<style scoped>
.msg-page { padding: var(--s1) var(--s2) var(--s6); }
h1 { font-size: var(--fs-2xl); margin: 0 0 var(--s3); color: var(--text-main); }

.msg-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: var(--s2); margin-bottom: var(--s2); flex-wrap: wrap;
}
.msg-count { font-size: var(--fs-sm); color: var(--text-secondary); }
.msg-ops { display: flex; gap: var(--s2); }
.msg-btn {
  padding: var(--s1) var(--s2); font-size: var(--fs-xs); cursor: pointer;
  background: var(--bg-card); color: var(--text-secondary);
  border: 1px solid var(--border-soft); border-radius: var(--r-md);
}
.msg-btn:hover:not(:disabled) { color: var(--accent-text); border-color: var(--accent-border); }
.msg-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.spin { animation: msg-spin 1s linear infinite; }
@keyframes msg-spin { to { transform: rotate(360deg); } }

/* 分类 tab */
.msg-tabs { display: flex; gap: var(--s1); flex-wrap: wrap; margin-bottom: var(--s2); }
.msg-tab {
  display: inline-flex; align-items: center; gap: 6px;
  padding: var(--s1) var(--s2); font-size: var(--fs-xs); cursor: pointer;
  background: var(--bg-card); color: var(--text-secondary);
  border: 1px solid var(--border-soft); border-radius: var(--r-pill);
}
.msg-tab.on { color: var(--on-accent); background: var(--accent-solid); border-color: var(--accent-solid); }
.msg-tab-badge {
  min-width: 16px; padding: 0 4px; font-size: var(--fs-xs); line-height: 16px; text-align: center;
  border-radius: var(--r-md); background: var(--accent-deep); color: var(--on-accent);
}
.msg-tab.on .msg-tab-badge { background: var(--on-accent); color: var(--accent-solid); }

/* 推送开关行（默认关，用户主动开启） */
.msg-push {
  display: flex; align-items: center; gap: var(--s2); flex-wrap: wrap;
  margin-bottom: var(--s2); padding: var(--s2); border-radius: var(--r-md);
  background: var(--bg-card); border: 1px solid var(--border-soft);
}
.msg-push-label { font-size: var(--fs-sm); color: var(--text-primary); }
.msg-push-hint { font-size: var(--fs-xs); color: var(--text-dim); }
.msg-push-msg { font-size: var(--fs-xs); color: var(--warn-amber); }

/* 订阅偏好 */
.msg-prefs {
  margin-bottom: var(--s2); padding: var(--s2); border-radius: var(--r-md);
  background: var(--bg-card); border: 1px solid var(--border-soft);
}
.msg-prefs-sum { font-size: var(--fs-sm); color: var(--text-primary); cursor: pointer; }
.msg-prefs-hint { margin-left: var(--s2); font-size: var(--fs-xs); color: var(--text-dim); }
.msg-prefs-list { list-style: none; margin: var(--s2) 0 0; padding: 0; display: grid; gap: var(--s1); }
.msg-pref-row { display: flex; align-items: center; }
.msg-pref-label {
  display: flex; align-items: center; gap: var(--s1);
  font-size: var(--fs-sm); color: var(--text-secondary); cursor: pointer;
}
.msg-pref-ops {
  padding: 0 4px; font-size: var(--fs-xs); font-style: normal; border-radius: var(--r-sm);
  border: 1px solid var(--border-soft); color: var(--text-muted);
}
.msg-prefs-tip, .msg-prefs-foot { margin: var(--s1) 0 0; font-size: var(--fs-xs); color: var(--text-dim); }

.msg-state {
  padding: var(--s4) var(--s3); text-align: center; font-size: var(--fs-sm);
  color: var(--text-muted); background: var(--bg-card);
  border: 1px solid var(--border-soft); border-radius: var(--r-md);
}
.msg-state.err { color: var(--warn-text); background: var(--warn-bg); border-color: var(--warn); }
.msg-state.empty { color: var(--text-dim); }

.msg-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--s2); }
.msg-item {
  display: flex; gap: var(--s2); padding: var(--s2) var(--s3);
  background: var(--bg-card); border: 1px solid var(--border-soft);
  border-left: 3px solid var(--border-soft); border-radius: var(--r-md);
}
.msg-item.lv-warn { border-left-color: var(--warn); }
.msg-item.lv-urgent { border-left-color: var(--accent-deep); }
.msg-item.read { opacity: 0.62; }
.msg-bar { flex: 0 0 auto; color: var(--text-secondary); padding-top: 2px; }
.msg-item.lv-warn .msg-bar { color: var(--warn-text); }
.msg-item.lv-urgent .msg-bar { color: var(--accent-text); }
.msg-main { flex: 1 1 auto; min-width: 0; }
.msg-title { font-size: var(--fs-base); font-weight: 700; color: var(--text-main); }
.msg-tag {
  margin-left: var(--s2); padding: 1px var(--s1); font-size: var(--fs-xs); font-weight: 400;
  border-radius: var(--r-sm); border: 1px solid var(--border-soft); color: var(--text-muted);
}
.msg-tag.unread { color: var(--accent-text); border-color: var(--accent-border); background: var(--accent-bg2); }
.msg-body { margin-top: var(--s1); font-size: var(--fs-sm); color: var(--text-secondary); white-space: pre-wrap; line-height: 1.5; }
.msg-foot { margin-top: var(--s2); display: flex; align-items: center; gap: var(--s2); flex-wrap: wrap; }
.msg-time { font-size: var(--fs-xs); color: var(--text-dim); }
.msg-act {
  font-size: var(--fs-xs); color: var(--accent-text); background: none;
  border: none; padding: 0; cursor: pointer; text-decoration: none;
}
.msg-act:hover { text-decoration: underline; }
.msg-act.danger { margin-left: auto; color: var(--text-muted); }
.msg-act.danger:hover { color: var(--warn-text); }

@media (max-width: 768px) {
  .msg-page { padding: var(--s1) 2px var(--s6); }
  .msg-item { padding: var(--s2); }
  /* 手机端命中区 ≥34px（主人 2026-10-05 定的标准） */
  .msg-pref-label { min-height: 34px; }
  .msg-tab { min-height: 34px; }
}
</style>
