<template>
  <div class="page-shell member-page">
    <h1 class="visually-hidden">我的会员</h1>

    <div v-if="loading" class="loading-placeholder"><div class="spinner"></div><div>加载会员信息...</div></div>

    <template v-else>
      <!-- 概览卡: 等级 + 到期倒计时 -->
      <div class="mb-hero" :class="'lv-' + level">
        <div class="mb-hero-left">
          <div class="mb-crown"><i class="fa" :class="level === 2 ? 'fa-crown' : level === 1 ? 'fa-star' : 'fa-user'"></i></div>
          <div>
            <div class="mb-name">{{ username }}</div>
            <div class="mb-level">{{ memberLabel }}</div>
          </div>
        </div>
        <div class="mb-hero-right">
          <template v-if="member.permanent">
            <div class="mb-days-txt">永久有效</div>
          </template>
          <template v-else-if="member.expired">
            <div class="mb-days-txt danger">已过期</div>
            <div class="mb-days-sub">{{ member.expire_date }}</div>
          </template>
          <template v-else>
            <div class="mb-days-txt">剩余 <b>{{ member.days_left }}</b> 天</div>
            <div class="mb-days-sub">到期 {{ member.expire_date }}</div>
          </template>
        </div>
      </div>

      <!-- 到期提醒条 -->
      <div v-if="!member.permanent && !member.expired && member.days_left <= 3 && !member.privileged" class="mb-warn">
        <i class="fa fa-exclamation-triangle"></i>
        会员将在 {{ member.days_left }} 天后到期，到期后每日免费次数降为 {{ freePicker }} 次。
        可<b>邀请好友</b>（每位 +{{ invite.reward_days }} 天）或联系管理员开通。
      </div>
      <div v-if="member.expired" class="mb-warn strong">
        <i class="fa fa-times-circle"></i>
        会员已过期，当前按免费用户计次。邀请好友或联系管理员开通即可恢复不限次。
      </div>

      <!-- 今日配额 -->
      <div class="mb-card">
        <div class="mb-card-head">
          <span class="mb-card-title"><i class="fa fa-tachometer"></i> 今日可用次数</span>
          <span class="mb-card-note">每日 0 点重置（北京时间）</span>
        </div>
        <div class="mb-quota-grid">
          <div v-for="q in quota" :key="q.feature" class="mb-quota-item" :class="{ exhausted: !q.privileged && q.remain <= 0 }">
            <div class="mb-q-name">{{ q.label }}</div>
            <div class="mb-q-num">
              <template v-if="q.privileged"><b>不限次</b></template>
              <template v-else><b :class="{ zero: q.remain <= 0 }">{{ q.remain }}</b><span>/ {{ q.limit }}</span></template>
            </div>
            <div v-if="!q.privileged" class="mb-q-bar"><i :style="{ width: pct(q) + '%' }"></i></div>
            <div v-if="q.bonus > 0" class="mb-q-bonus">含签到奖励 +{{ q.bonus }}</div>
          </div>
        </div>
      </div>

      <!-- 签到 -->
      <div class="mb-card">
        <div class="mb-card-head">
          <span class="mb-card-title"><i class="fa fa-calendar-check-o"></i> 每日签到</span>
          <span class="mb-card-note">连续签到 {{ checkin.streak }} 天</span>
        </div>
        <button class="mb-checkin-btn" :disabled="checkin.done_today || checkinBusy" @click="doCheckin">
          <template v-if="checkin.done_today"><i class="fa fa-check"></i> 今日已签到</template>
          <template v-else-if="checkinBusy">签到中...</template>
          <template v-else><i class="fa fa-gift"></i> 签到领 {{ checkin.reward }} 次选股额度</template>
        </button>
        <div v-if="history.length" class="mb-checkin-hist">
          <span v-for="h in history" :key="h.date" class="mb-hist-dot" :class="{ on: h.reward > 0 }" :title="h.date + ' +' + h.reward">{{ h.date.slice(8) }}</span>
        </div>
      </div>

      <!-- 邀请 -->
      <div class="mb-card">
        <div class="mb-card-head">
          <span class="mb-card-title"><i class="fa fa-user-plus"></i> 邀请好友</span>
          <span class="mb-card-note">每成功邀请 1 位，双方各得 {{ invite.reward_days }} 天会员</span>
        </div>
        <div class="mb-invite-row">
          <div class="mb-code-box">
            <span class="mb-code-label">我的邀请码</span>
            <span class="mb-code">{{ invite.code || '——' }}</span>
          </div>
          <button class="mb-mini-btn" @click="copyCode"><i class="fa fa-copy"></i> 复制邀请码</button>
          <button class="mb-mini-btn" @click="copyLink"><i class="fa fa-link"></i> 复制邀请链接</button>
          <button class="mb-mini-btn ghost" @click="doRefreshCode"><i class="fa fa-refresh"></i> 更换邀请码</button>
        </div>
        <div class="mb-invite-stats">
          <span>已邀请 <b>{{ invite.invited_count }}</b> 人</span>
          <span class="sep">·</span>
          <span>累计获得 <b>{{ invite.earned_days }}</b> 天会员</span>
        </div>
        <div v-if="invite.invitees && invite.invitees.length" class="mb-invitee-list">
          <div v-for="(it, i) in invite.invitees" :key="i" class="mb-invitee">
            <i class="fa fa-user-circle-o"></i>
            <span class="mb-invitee-name">{{ it.username || '已注销' }}</span>
            <span class="mb-invitee-time">{{ fmtDay(it.created_at) }}</span>
          </div>
        </div>
        <div v-else class="mb-empty-small">还没有邀请记录，把邀请码发给好友即可开始</div>
      </div>

      <!-- 权益对照 -->
      <div class="mb-card">
        <div class="mb-card-head">
          <span class="mb-card-title"><i class="fa fa-table"></i> 权益对照</span>
        </div>
        <div class="mb-table-wrap">
          <table class="mb-table">
            <thead>
              <tr>
                <th>功能</th>
                <th :class="{ mine: level === 0 }">{{ plans.free.label }}</th>
                <th :class="{ mine: level === 1 }">{{ plans.member.label }}</th>
                <th :class="{ mine: level === 2 }">{{ plans.vip.label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>选股快照</td>
                <td :class="{ mine: level === 0 }">{{ lim(plans.free.picker) }}</td>
                <td :class="{ mine: level === 1 }">{{ lim(plans.member.picker) }}</td>
                <td :class="{ mine: level === 2 }">{{ lim(plans.vip.picker) }}</td>
              </tr>
              <tr>
                <td>AI 预测</td>
                <td :class="{ mine: level === 0 }">{{ lim(plans.free.aipick) }}</td>
                <td :class="{ mine: level === 1 }">{{ lim(plans.member.aipick) }}</td>
                <td :class="{ mine: level === 2 }">{{ lim(plans.vip.aipick) }}</td>
              </tr>
              <tr>
                <td>竞价异动</td>
                <td :class="{ mine: level === 0 }">{{ lim(plans.free.auction) }}</td>
                <td :class="{ mine: level === 1 }">{{ lim(plans.member.auction) }}</td>
                <td :class="{ mine: level === 2 }">{{ lim(plans.vip.auction) }}</td>
              </tr>
              <tr>
                <td>签到奖励</td>
                <td :class="{ mine: level === 0 }" colspan="3">每日 +{{ plans.checkin_bonus }} 次选股额度</td>
              </tr>
              <tr>
                <td>邀请奖励</td>
                <td :class="{ mine: level === 0 }" colspan="3">每邀请 1 人，双方各 +{{ plans.invite_reward_days }} 天会员</td>
              </tr>
              <tr>
                <td>新用户赠送</td>
                <td :class="{ mine: level === 0 }" colspan="3">{{ plans.new_user_days }} 天会员完整体验</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="mb-contact">
          <i class="fa fa-phone"></i> 开通 / 续费会员请联系管理员
          <span class="mb-wechat">微信: <b>poet-1986</b></span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { memberOverview, memberPlans, memberCheckin, doCheckin as apiCheckin, refreshInvite } from '../api/member'
import { showToast } from '../utils/toast'

const loading = ref(true)
const checkinBusy = ref(false)
const member = ref({})
const quota = ref([])
const checkin = ref({ done_today: false, reward: 3, streak: 0 })
const invite = ref({ code: '', invited_count: 0, earned_days: 0, reward_days: 5, invitees: [] })
const plans = ref({ free: {}, member: {}, vip: {}, checkin_bonus: 3, invite_reward_days: 5, new_user_days: 5 })
const history = ref([])

const username = computed(() => member.value.username || '')
const level = computed(() => member.value.member_level || 0)
const memberLabel = computed(() => member.value.member_label || '免费试用')
const freePicker = computed(() => plans.value.free.picker ?? 3)

const QUOTA_ORDER = { picker: '选股', aipick: 'AI 预测', auction: '竞价异动' }

function pct(q) {
  if (!q || !q.limit) return 0
  return Math.max(0, Math.min(100, Math.round((q.remain / q.limit) * 100)))
}
function lim(v) {
  return v === -1 || v === undefined || v === null ? '不限次' : v + ' 次/天'
}
function fmtDay(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

async function load() {
  loading.value = true
  try {
    const d = await memberOverview()
    member.value = d.member || {}
    quota.value = (d.quota || []).map((q) => ({
      ...q,
      label: q.label || QUOTA_ORDER[q.feature] || q.feature,
    }))
    checkin.value = d.checkin || checkin.value
    invite.value = d.invite || invite.value
  } catch (e) {
    showToast('❌ 会员信息加载失败: ' + (e.message || ''), 'error')
  } finally {
    loading.value = false
  }
  try { plans.value = await memberPlans() } catch { /* 用默认 */ }
  try {
    const c = await memberCheckin()
    history.value = c.history || []
    checkin.value = { ...checkin.value, ...c }
  } catch { /* 忽略 */ }
}

async function doCheckin() {
  if (checkinBusy.value || checkin.value.done_today) return
  checkinBusy.value = true
  try {
    const d = await apiCheckin()
    checkin.value.done_today = true
    checkin.value.streak = d.streak || checkin.value.streak
    if (d.quota) {
      quota.value = quota.value.map((q) => (q.feature === d.quota.feature ? { ...q, ...d.quota, label: q.label } : q))
    }
    showToast('✅ ' + (d.msg || '签到成功'), 'success')
    const c = await memberCheckin()
    history.value = c.history || []
  } catch (e) {
    if (e && e.done_today) {
      checkin.value.done_today = true
      showToast(e.message || '今日已签到', 'info')
    } else {
      showToast('❌ ' + (e.message || '签到失败'), 'error')
    }
  } finally {
    checkinBusy.value = false
  }
}

function inviteUrl(code) {
  return window.location.origin + '/login?invite=' + encodeURIComponent(code)
}

async function copyText(text, okMsg) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    showToast(okMsg, 'success')
  } catch (e) {
    showToast('复制失败，请手动选择：' + text, 'error')
  }
}

function copyCode() {
  if (!invite.value.code) return showToast('邀请码尚未生成', 'error')
  copyText(invite.value.code, '✅ 邀请码已复制')
}
function copyLink() {
  if (!invite.value.code) return showToast('邀请码尚未生成', 'error')
  copyText(inviteUrl(invite.value.code), '✅ 邀请链接已复制，发给好友即可')
}

async function doRefreshCode() {
  if (!confirm('更换后旧邀请码立即失效（已注册的好友不受影响），确定更换？')) return
  try {
    const d = await refreshInvite()
    invite.value.code = d.invite_code || invite.value.code
    showToast('✅ 邀请码已更换', 'success')
  } catch (e) {
    showToast('❌ ' + (e.message || '更换失败'), 'error')
  }
}

onMounted(load)
</script>

<style scoped>
.member-page { max-width: 860px; margin: 0 auto; padding: 16px 12px 40px; display: flex; flex-direction: column; gap: 14px; }

/* 概览卡 */
.mb-hero {
  display: flex; justify-content: space-between; align-items: center;
  gap: 12px; flex-wrap: wrap;
  padding: 18px 20px; border-radius: 14px;
  background: var(--bg-panel); border: 1px solid var(--border-soft);
  position: relative; overflow: hidden;
}
.mb-hero.lv-1 { border-color: rgba(var(--accent-rgb), .5); box-shadow: 0 6px 24px rgba(var(--accent-rgb), .12); }
.mb-hero.lv-2 { border-color: rgba(255, 176, 32, .55); box-shadow: 0 6px 24px rgba(255, 176, 32, .14); }
.mb-hero-left { display: flex; align-items: center; gap: 14px; }
.mb-crown {
  width: 46px; height: 46px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 1.375rem; color: var(--accent);
  background: rgba(var(--accent-rgb), .12);
}
.mb-hero.lv-2 .mb-crown { color: #ffb020; background: rgba(255, 176, 32, .14); }
.mb-name { font-size: 1.0625rem; font-weight: 700; color: var(--text-main); }
.mb-level { font-size: 0.8125rem; color: var(--text-muted); margin-top: 2px; }
.mb-hero-right { text-align: right; }
.mb-days-txt { font-size: 0.9375rem; color: var(--text-secondary); }
.mb-days-txt b { font-size: 1.5rem; color: var(--accent-deep); }
.mb-days-txt.danger { color: #ff6a6a; font-weight: 700; }
.mb-days-sub { font-size: 0.75rem; color: var(--text-muted); margin-top: 2px; }

/* 提醒条 */
.mb-warn {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 0.8125rem; line-height: 1.6;
  padding: 10px 14px; border-radius: 10px;
  background: rgba(255, 176, 32, .1); border: 1px solid rgba(255, 176, 32, .35);
  color: var(--text-secondary);
}
.mb-warn.strong { background: rgba(255, 106, 106, .1); border-color: rgba(255, 106, 106, .35); }
.mb-warn b { color: var(--accent-deep); margin: 0 2px; }

/* 通用卡片 */
.mb-card {
  background: var(--bg-panel); border: 1px solid var(--border-soft);
  border-radius: 12px; padding: 16px 18px;
}
.mb-card-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.mb-card-title { font-size: 0.9375rem; font-weight: 700; color: var(--text-main); }
.mb-card-title i { color: var(--accent); margin-right: 6px; }
.mb-card-note { font-size: 0.75rem; color: var(--text-muted); }

/* 配额 */
.mb-quota-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.mb-quota-item {
  border: 1px solid var(--border-soft); border-radius: 10px; padding: 12px 14px;
  background: rgba(255, 255, 255, .015);
}
.mb-quota-item.exhausted { border-color: rgba(255, 106, 106, .35); }
.mb-q-name { font-size: 0.8125rem; color: var(--text-muted); margin-bottom: 6px; }
.mb-q-num { font-size: 0.875rem; color: var(--text-secondary); }
.mb-q-num b { font-size: 1.375rem; color: var(--accent-deep); margin-right: 4px; }
.mb-q-num b.zero { color: #ff6a6a; }
.mb-q-bar { height: 4px; border-radius: 2px; background: var(--bg-input); margin-top: 8px; overflow: hidden; }
.mb-q-bar i { display: block; height: 100%; background: var(--accent); border-radius: 2px; transition: width .3s; }
.mb-q-bonus { font-size: 0.6875rem; color: #2bb673; margin-top: 6px; }

/* 签到 */
.mb-checkin-btn {
  width: 100%; padding: 11px 0; border: none; border-radius: 9px;
  background: var(--accent); color: #fff; font-size: 0.9375rem; cursor: pointer;
  transition: opacity .15s;
}
.mb-checkin-btn:hover:not(:disabled) { opacity: .88; }
.mb-checkin-btn:disabled { background: var(--bg-input); color: var(--text-muted); cursor: default; }
.mb-checkin-hist { display: flex; gap: 6px; margin-top: 10px; }
.mb-hist-dot {
  width: 26px; height: 26px; border-radius: 50%; font-size: 0.6875rem;
  display: flex; align-items: center; justify-content: center;
  background: var(--bg-input); color: var(--text-muted);
}
.mb-hist-dot.on { background: rgba(43, 182, 115, .16); color: #2bb673; font-weight: 700; }

/* 邀请 */
.mb-invite-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.mb-code-box {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 12px; border-radius: 8px;
  background: rgba(var(--accent-rgb), .08); border: 1px dashed rgba(var(--accent-rgb), .4);
}
.mb-code-label { font-size: 0.75rem; color: var(--text-muted); }
.mb-code { font-size: 1rem; font-weight: 700; color: var(--accent-deep); letter-spacing: 2px; }
.mb-mini-btn {
  padding: 7px 12px; font-size: 0.8125rem; border-radius: 7px; cursor: pointer;
  border: 1px solid var(--accent); background: transparent; color: var(--accent);
}
.mb-mini-btn.ghost { border-color: var(--border-soft); color: var(--text-muted); }
.mb-mini-btn:hover { opacity: .82; }
.mb-invite-stats { font-size: 0.8125rem; color: var(--text-secondary); margin-top: 12px; }
.mb-invite-stats b { color: var(--accent-deep); font-size: 1rem; margin: 0 2px; }
.mb-invite-stats .sep { margin: 0 6px; color: var(--text-muted); }
.mb-invitee-list { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; max-height: 190px; overflow-y: auto; }
.mb-invitee { display: flex; align-items: center; gap: 8px; font-size: 0.8125rem; color: var(--text-secondary); }
.mb-invitee i { color: var(--accent); }
.mb-invitee-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mb-invitee-time { font-size: 0.75rem; color: var(--text-muted); }
.mb-empty-small { margin-top: 10px; font-size: 0.8125rem; color: var(--text-muted); }

/* 权益表 */
.mb-table-wrap { overflow-x: auto; }
.mb-table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; }
.mb-table th, .mb-table td { padding: 9px 10px; text-align: center; border-bottom: 1px solid var(--border-soft); }
.mb-table th:first-child, .mb-table td:first-child { text-align: left; color: var(--text-secondary); }
.mb-table th { color: var(--text-muted); font-weight: 600; }
.mb-table td { color: var(--text-secondary); }
.mb-table .mine { background: rgba(var(--accent-rgb), .09); color: var(--text-main); font-weight: 600; }
.mb-contact {
  margin-top: 12px; font-size: 0.8125rem; color: var(--text-muted);
  background: rgba(var(--accent-rgb), .06); border-radius: 8px; padding: 9px 12px;
}
.mb-wechat { margin-left: 8px; color: var(--accent-deep); }
.mb-wechat b { letter-spacing: .5px; }

/* 浅色主题微调 */
body[data-bg="light"] .mb-quota-item { background: rgba(0, 0, 0, .015); }
body[data-bg="light"] .mb-code-box { background: rgba(var(--accent-rgb), .08); }
</style>
