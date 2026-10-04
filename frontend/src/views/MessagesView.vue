<template>
  <!--
    消息中心 /messages（2026-10-04）
    主人需求：「系统消息比如系统更新提醒，会员到期提醒，等等」

    🔴 两类消息的**语义差异**，UI 必须体现，否则用户会以为"标了已读怎么又来了"：
      · broadcast 站方广播 —— 可标已读，标了就消失在"未读"里，但**仍留在列表**（历史公告可回看）
      · account   账户事件 —— **没有"已读"概念**：它是状态（会员还有 3 天到期），
        标已读第二天照样还在。只有续费/到期后它才自动消失。故不给"标记已读"，
        只给一个跳转动作（到期 → /member 续费页）。
  -->
  <div class="page-shell msg-page">
    <h1>系统消息</h1>

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

    <!-- 2026-10-04 手机端真推送: **默认关**，用户主动点开才订阅。
         不支持的环境（无 SW/PushManager，或 http 访问）整行不渲染，不给开关。 -->
    <div v-if="pushSupported" class="msg-push">
      <span class="msg-push-label"><i class="fa fa-mobile"></i> 手机推送</span>
      <button class="msg-btn" :disabled="pushBusy" @click="togglePush">
        {{ pushOn ? '已开启 · 点此关闭' : '开启通知' }}
      </button>
      <span v-if="pushMsg" class="msg-push-msg">{{ pushMsg }}</span>
      <span v-else class="msg-push-hint">系统更新、会员到期会直接推到手机</span>
    </div>

    <!-- 🔴 失败态与空态**必须文案不同**（静态闸门 verify 会查）：不许都渲染成一个空列表 -->
    <div v-if="failed" class="msg-state err">
      <i class="fa fa-exclamation-triangle"></i>
      消息读取失败：{{ errMsg }}。这不是「没有消息」，请点刷新重试。
    </div>
    <div v-else-if="loading && !items.length" class="msg-state">正在读取消息…</div>
    <div v-else-if="!items.length" class="msg-state empty">
      <i class="fa fa-bell"></i> 暂无消息。系统更新、会员到期提醒会在这里出现。
    </div>

    <ul v-else class="msg-list">
      <li
        v-for="m in items"
        :key="m.id"
        class="msg-item"
        :class="['lv-' + m.level, { read: m.read, account: m.type === 'account' }]"
      >
        <div class="msg-bar"><i class="fa" :class="iconOf(m)"></i></div>
        <div class="msg-main">
          <div class="msg-title">
            {{ m.title }}
            <span v-if="m.type === 'account'" class="msg-tag">账户</span>
            <span v-else-if="!m.read" class="msg-tag unread">未读</span>
          </div>
          <div class="msg-body">{{ m.body }}</div>
          <div class="msg-foot">
            <span class="msg-time">{{ fmtTime(m.ts) }}</span>
            <!-- 账户事件是"状态"，给的是**处理入口**而不是"标已读" -->
            <router-link v-if="m.type === 'account'" to="/member" class="msg-act">去处理 ›</router-link>
            <button v-else-if="!m.read" class="msg-act" @click="markOne(m)">标为已读</button>
          </div>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { fetchNotices, markNoticesRead } from '../api/notices'
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

const items = ref([])
const unread = ref(0)
const loading = ref(false)
const marking = ref(false)
const failed = ref(false)
const errMsg = ref('')

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(Number(ts) * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function iconOf(m) {
  if (m.type === 'account') return m.level === 'urgent' ? 'fa-exclamation-circle' : 'fa-clock-o'
  return m.level === 'urgent' ? 'fa-exclamation-circle' : (m.level === 'warn' ? 'fa-exclamation-triangle' : 'fa-bullhorn')
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    const d = await fetchNotices()
    if (d && d.ok) {
      items.value = d.items || []
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
    const d = await markNoticesRead({ ids: [m.nid] })
    if (d && d.ok) { m.read = 1; unread.value = Math.max(0, unread.value - 1) }
    else showToast('标记已读失败', 'error')
  } catch (e) {
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
      items.value.forEach((it) => { if (it.type === 'broadcast') it.read = 1 })
      // 账户事件是状态, 标不了已读 ⇒ 未读数只会降到"剩余账户事件数", 不谎报 0
      unread.value = items.value.filter((it) => it.type === 'account' && (it.level === 'warn' || it.level === 'urgent')).length
    } else showToast('标记已读失败', 'error')
  } catch (e) {
    showToast('标记已读失败', 'error')
  } finally {
    marking.value = false
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
/* 推送开关行（默认关，用户主动开启） */
.msg-push {
  display: flex; align-items: center; gap: var(--s2); flex-wrap: wrap;
  margin-bottom: var(--s2); padding: var(--s2) var(--s2); border-radius: var(--r-md);
  background: var(--bg-card); border: 1px solid var(--border-color);
}
.msg-push-label { font-size: var(--fs-sm); color: var(--text-primary); }
.msg-push-hint { font-size: var(--fs-xs); color: var(--text-dim); }
.msg-push-msg { font-size: var(--fs-xs); color: var(--warn-amber); }
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
.msg-title { font-size: var(--fs-base); font-weight: 600; color: var(--text-main); }
.msg-tag {
  margin-left: var(--s2); padding: 1px var(--s1); font-size: var(--fs-xs); font-weight: 400;
  border-radius: var(--r-sm); border: 1px solid var(--border-soft); color: var(--text-muted);
}
.msg-tag.unread { color: var(--accent-text); border-color: var(--accent-border); background: var(--accent-bg2); }
.msg-body { margin-top: var(--s1); font-size: var(--fs-sm); color: var(--text-secondary); white-space: pre-wrap; line-height: 1.5; }
.msg-foot { margin-top: var(--s2); display: flex; align-items: center; gap: var(--s2); }
.msg-time { font-size: var(--fs-xs); color: var(--text-dim); }
.msg-act {
  font-size: var(--fs-xs); color: var(--accent-text); background: none;
  border: none; padding: 0; cursor: pointer; text-decoration: none;
}
.msg-act:hover { text-decoration: underline; }

@media (max-width: 768px) {
  .msg-page { padding: var(--s1) 2px var(--s6); }
  .msg-item { padding: var(--s2) var(--s2); }
}
</style>
