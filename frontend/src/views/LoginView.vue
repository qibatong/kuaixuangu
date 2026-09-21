<template>
  <div class="auth-overlay">
    <div class="login-box">
      <img src="/logo.jpg" class="login-logo" alt="快选 Kuaixuan">
      <div class="login-title">快选</div>
      <div class="login-sub">{{ subText }}</div>

      <!-- 登录 -->
      <template v-if="mode === 'login'">
        <form class="login-form" @submit.prevent="submit">
          <input v-model="username" type="text" placeholder="用户名 / 手机号" autocomplete="username" maxlength="20">
          <input v-model="password" type="password" placeholder="密码" autocomplete="current-password">
          <label class="remember-row">
            <input v-model="remember" type="checkbox" class="remember-check" />
            <span>记住我，30 天内免登录</span>
          </label>
          <!-- 2026-09-21 主人需求: 电脑+手机每次都要重新输入账号密码 → 本地记住凭据自动填充 -->
          <label class="remember-row">
            <input v-model="savePwd" type="checkbox" class="remember-check" />
            <span>记住密码（本机自动填充）</span>
          </label>
          <button class="login-btn" type="submit" :disabled="busy">{{ busy ? '登录中...' : '登录' }}</button>
          <div class="login-err" :class="{error: errIsError}">{{ err }}</div>
        </form>
        <div class="login-switch">
          <a href="javascript:void(0)" @click="mode = 'forgot'">忘记密码？</a>
          <span class="switch-sep">|</span>
          <a v-if="regOpen" href="javascript:void(0)" @click="toRegister">注册新账号</a>
        </div>
      </template>

      <!-- 注册(手机号 + 短信验证码, 2026-09-21 放开) -->
      <template v-else-if="mode === 'register'">
        <form class="forgot-form" @submit.prevent="doRegister">
          <div class="gift-tip">
            🎁 注册即送 <b>{{ giftDays }}</b> 天会员完整体验
            <template v-if="inviteRewardDays > 0">，填邀请码双方各得 <b>{{ inviteRewardDays }}</b> 天</template>
          </div>
          <div class="phone-row">
            <input v-model="phone" type="text" placeholder="手机号" autocomplete="off" maxlength="11" inputmode="numeric" @keydown.enter="sendRegSms">
            <button type="button" class="sms-btn" :disabled="smsBusy || countdown > 0" @click="sendRegSms">
              {{ countdown > 0 ? countdown + 's 后重发' : '获取验证码' }}
            </button>
          </div>
          <input v-model="smsCode" type="text" placeholder="短信验证码" autocomplete="one-time-code" maxlength="6" inputmode="numeric" @keydown.enter="doRegister">
          <input v-model="resetPwd" type="password" placeholder="设置密码（至少 6 位）" autocomplete="new-password" @keydown.enter="doRegister">
          <input v-model="resetPwd2" type="password" placeholder="确认密码" autocomplete="new-password" @keydown.enter="doRegister">
          <input v-model="inviteCode" type="text" placeholder="邀请码（选填）" autocomplete="off" maxlength="16" @keydown.enter="doRegister">
          <div v-if="inviterName" class="inviter-tip">✅ 邀请人：{{ inviterName }}，注册后你可得 {{ inviteRewardDays }} 天会员</div>
          <button class="login-btn" type="submit" :disabled="busy">{{ busy ? '注册中...' : '注册并登录' }}</button>
        </form>
        <div class="login-err" :class="{error: errIsError}">{{ err }}</div>
        <div class="login-switch">
          <a href="javascript:void(0)" @click="mode = 'login'">已有账号？去登录</a>
        </div>
      </template>

      <!-- 忘记密码: 手机验证码 -->
      <template v-else-if="mode === 'forgot'">
        <form class="forgot-form" @submit.prevent="doResetByPhone">
          <div class="login-sub">输入已绑定的手机号，通过短信验证码重置密码</div>
          <div class="phone-row">
            <input v-model="phone" type="text" placeholder="绑定手机号" autocomplete="off" maxlength="11" @keydown.enter="sendForgotSms">
            <button type="button" class="sms-btn" :disabled="smsBusy || countdown > 0" @click="sendForgotSms">
              {{ countdown > 0 ? countdown + 's 后重发' : '获取验证码' }}
            </button>
          </div>
          <!-- one-time-code: 短信验证码专用, 避免浏览器自动填账号; new-password: 让浏览器知道这是新密码, 不自动填旧密码 -->
          <input v-model="smsCode" type="text" placeholder="短信验证码" autocomplete="one-time-code" maxlength="6" inputmode="numeric" @keydown.enter="doResetByPhone">
          <input v-model="resetPwd" type="password" placeholder="新密码（至少 6 位）" autocomplete="new-password" @keydown.enter="doResetByPhone">
          <input v-model="resetPwd2" type="password" placeholder="确认新密码" autocomplete="new-password" @keydown.enter="doResetByPhone">
          <button class="login-btn" type="submit" :disabled="busy">{{ busy ? '提交中...' : '重置密码' }}</button>
        </form>
        <div class="login-err" :class="{error: errIsError}">{{ err }}</div>
        <div class="login-switch">
          <a href="javascript:void(0)" @click="mode = 'login'">返回登录</a>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  login as apiLogin,
  register as apiRegister,
  sendRegisterSms as apiSendRegisterSms,
  registerConfig as apiRegisterConfig,
  inviteInfo as apiInviteInfo,
  sendForgotSms as apiSendForgotSms,
  resetByPhone as apiResetByPhone,
} from '../api/auth'
import { useUserStore } from '../stores/user'
import { showToast } from '../utils/toast'

const route = useRoute()
const router = useRouter()
const user = useUserStore()

const mode = ref('login')          // login | register | forgot
const busy = ref(false)
const err = ref('')
// 错误提示是否显示红色背景块: 仅"真正的错误"才上色 (loading/✅成功 不算)
const errIsError = ref(false)
const remember = ref(true)         // 「记住我」默认勾选: 30 天免登录
// 2026-09-21 主人需求「电脑和手机端都不能保存用户名和密码」: 登录成功后把凭据存
// localStorage, 下次进登录页自动填充(免输)。私有工具站, 主人明确要求记住明文凭据。
const SAVED_LOGIN_KEY = 'kuaixuan_saved_login'
const savePwd = ref(true)          // 「记住密码」默认勾选
const username = ref('')
const password = ref('')
const resetPwd = ref('')
const resetPwd2 = ref('')
const phone = ref('')
const smsCode = ref('')
const smsBusy = ref(false)
const countdown = ref(0)
let countdownTimer = null

/* ---------- 注册(2026-09-21) ---------- */
const regOpen = ref(true)
const giftDays = ref(5)
const inviteRewardDays = ref(5)
const inviteCode = ref('')
const inviterName = ref('')

const subText = computed(() =>
  mode.value === 'login' ? '登录' :
  mode.value === 'register' ? '注册' : '重置密码')

function setErr(msg, isError = true) {
  err.value = msg
  errIsError.value = isError && !!msg
}

function startCountdown() {
  countdown.value = 60
  if (countdownTimer) clearInterval(countdownTimer)
  countdownTimer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) clearInterval(countdownTimer)
  }, 1000)
}

async function submit() {
  if (busy.value) return
  setErr('', false)
  if (!username.value.trim()) { setErr('请输入用户名/手机号'); return }
  if (!password.value) { setErr('请输入密码'); return }
  busy.value = true
  err.value = '登录中...'
  errIsError.value = false
  try {
    const body = { login: username.value.trim(), password: password.value, remember: remember.value }
    const data = await apiLogin(body)
    // 2026-09-21: 记住密码 —— 登录成功才落盘; 未勾选则清除旧凭据
    try {
      if (savePwd.value) {
        localStorage.setItem(SAVED_LOGIN_KEY, JSON.stringify({ login: username.value.trim(), password: password.value }))
      } else {
        localStorage.removeItem(SAVED_LOGIN_KEY)
      }
    } catch { /* 存储不可用(隐私模式等)静默跳过 */ }
    user.setSession(data.username, data.token, data.is_admin, data.expire_at, data.expired, remember.value, data.member_level)
    if (data.expired) {
      showToast('⚠️ 账号已过期，请重新开通或联系管理员续费', 'error')
    }
    const redirect = route.query.redirect || '/'
    router.replace(redirect)
  } catch (e) {
    setErr(e.message || '操作失败')
  } finally {
    busy.value = false
  }
}

// ---------- 注册 ----------
function toRegister() {
  mode.value = 'register'
  setErr('', false)
  phone.value = smsCode.value = resetPwd.value = resetPwd2.value = ''
  // 从邀请链接进入时自动带邀请码
  const ic = String(route.query.invite || route.query.icode || '').trim()
  if (ic) inviteCode.value = ic
}

async function loadRegConfig() {
  try {
    const data = await apiRegisterConfig()
    regOpen.value = data.open !== false
    if (data.gift_days !== undefined) giftDays.value = data.gift_days
    if (data.invite_reward_days !== undefined) inviteRewardDays.value = data.invite_reward_days
  } catch { /* 配置拉取失败保持默认 */ }
}

// 邀请码预校验: 输入 4 位以上且已失焦/停顿后查询邀请人
let inviteTimer = null
watch(inviteCode, (v) => {
  inviterName.value = ''
  const code = String(v || '').trim()
  if (!code) return
  if (inviteTimer) clearTimeout(inviteTimer)
  inviteTimer = setTimeout(async () => {
    try {
      const data = await apiInviteInfo(code)
      inviterName.value = data.inviter || ''
    } catch { /* 无效邀请码: 提交时再报错 */ }
  }, 500)
})

async function sendRegSms() {
  if (smsBusy.value || countdown.value > 0) return
  setErr('', false)
  if (!/^1[3-9]\d{9}$/.test(phone.value.trim())) { setErr('请输入正确的 11 位手机号'); return }
  smsBusy.value = true
  try {
    const data = await apiSendRegisterSms(phone.value.trim())
    setErr('✅ ' + (data.msg || '验证码已发送，请查收'), false)
    startCountdown()
  } catch (e) {
    setErr(e.message || '发送失败')
  } finally {
    smsBusy.value = false
  }
}

async function doRegister() {
  if (busy.value) return
  setErr('', false)
  if (!/^1[3-9]\d{9}$/.test(phone.value.trim())) { setErr('请输入正确的 11 位手机号'); return }
  if (!/^\d{4,6}$/.test(smsCode.value.trim())) { setErr('请输入短信验证码'); return }
  if (!resetPwd.value || resetPwd.value.length < 6) { setErr('密码至少 6 位'); return }
  if (resetPwd.value !== resetPwd2.value) { setErr('两次输入的密码不一致'); return }
  busy.value = true
  err.value = '注册中...'
  errIsError.value = false
  try {
    const payload = {
      phone: phone.value.trim(),
      code: smsCode.value.trim(),
      password: resetPwd.value,
    }
    const ic = inviteCode.value.trim()
    if (ic) payload.invite_code = ic
    const data = await apiRegister(payload)
    // 注册成功即登录(持久会话)
    user.setSession(data.username, data.token, false, data.expire_at, false, true, data.member_level)
    let tip = `✅ 注册成功，已赠送 ${giftDays.value} 天会员`
    if (data.invite_rewarded) tip += '，邀请奖励已到账'
    showToast(tip, 'success')
    const redirect = route.query.redirect || '/'
    router.replace(redirect)
  } catch (e) {
    setErr(e.message || '注册失败')
  } finally {
    busy.value = false
  }
}

// ---------- 找回密码-短信验证码(2026-08-30) ----------
async function sendForgotSms() {
  if (smsBusy.value || countdown.value > 0) return
  setErr('', false)
  if (!/^1[3-9]\d{9}$/.test(phone.value.trim())) { setErr('请输入正确的 11 位手机号'); return }
  smsBusy.value = true
  try {
    const data = await apiSendForgotSms(phone.value.trim())
    setErr('✅ ' + (data.msg || '验证码已发送，请查收'), false)
    startCountdown()
  } catch (e) {
    setErr(e.message || '发送失败')
  } finally {
    smsBusy.value = false
  }
}

async function doResetByPhone() {
  if (busy.value) return
  setErr('', false)
  if (!/^1[3-9]\d{9}$/.test(phone.value.trim())) { setErr('请输入正确的 11 位手机号'); return }
  if (!/^\d{4,6}$/.test(smsCode.value.trim())) { setErr('请输入短信验证码'); return }
  if (!resetPwd.value || resetPwd.value.length < 6) { setErr('新密码至少 6 位'); return }
  if (resetPwd.value !== resetPwd2.value) { setErr('两次输入的密码不一致'); return }
  busy.value = true
  err.value = '提交中...'
  errIsError.value = false
  try {
    const data = await apiResetByPhone(phone.value.trim(), smsCode.value.trim(), resetPwd.value)
    err.value = '✅ ' + (data.msg || '密码已重置')
    errIsError.value = false
    setTimeout(() => {
      mode.value = 'login'
      phone.value = smsCode.value = resetPwd.value = resetPwd2.value = ''
    }, 1500)
  } catch (e) {
    setErr(e.message || '重置失败')
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  loadRegConfig()
  // 邀请链接 ?invite=CODE → 直接进注册页并预填
  const ic = String(route.query.invite || route.query.icode || '').trim()
  if (ic) {
    inviteCode.value = ic
    mode.value = 'register'
    return
  }
  // 导航栏「注册」入口 → /login?mode=register
  if (route.query.mode === 'register') {
    mode.value = 'register'
    return
  }
  // 2026-09-21 主人反馈「每次都要重新输入」: 原来这里强制清空 username/password,
  // 且 SPA 表单浏览器也不弹保存 → 改为读取本机保存的凭据自动填充, 免输直达。
  try {
    const saved = JSON.parse(localStorage.getItem(SAVED_LOGIN_KEY) || 'null')
    if (saved && saved.login) {
      username.value = saved.login || ''
      password.value = saved.password || ''
    }
  } catch { /* 损坏的存储值当不存在 */ }
  if (!username.value) setTimeout(() => document.querySelector('input')?.focus(), 50)
})
</script>

<style scoped>
.login-form { display: flex; flex-direction: column; gap: 10px; }
/* 忘记密码/注册表单: 同 login-form 间距 + 强制所有 input 统一高度 43px, 避免单独 input 与 phone-row 内 input 高度差 */
.forgot-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.forgot-form > input {
  min-height: 43px;
  margin: 0 !important;        /* 覆盖全局 input 的 margin-bottom:10px, 间距由 gap 统一控制 */
  box-sizing: border-box;
}
.forgot-form .phone-row {
  display: flex;
  gap: 8px;
  align-items: stretch;        /* 子项自动等高 */
}
.forgot-form .phone-row input {
  flex: 1;
  min-width: 0;
  min-height: 43px;            /* 显式高度, 与下方 input 一致 */
  margin: 0 !important;
  box-sizing: border-box;
}
.forgot-form .sms-btn {
  flex-shrink: 0;
  padding: 0 12px;
  font-size: 0.75rem;
  white-space: nowrap;
  border: 1px solid var(--accent);
  border-radius: 6px;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
  line-height: 1.2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  min-height: 43px;            /* 与所有 input 同高 */
}
.remember-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.75rem;
  color: var(--text-muted);
  cursor: pointer;
  user-select: none;
  margin-top: 2px;
}
.remember-check { width: 14px; height: 14px; accent-color: var(--accent); cursor: pointer; }
.phone-row {
  display: flex;
  gap: 8px;
  align-items: stretch;         /* 子项自动等高 */
}
.phone-row input {
  flex: 1;
  min-width: 0;
  margin-bottom: 0 !important;  /* 覆盖全局 input 的 margin-bottom:10px, 避免拉伸时多 10px */
}
.sms-btn {
  flex-shrink: 0;
  /* 高度与 input 完全一致: 全局 input = padding 11px 上下 + font-size 14px 行高 + border */
  /* 用 align-items:stretch + 自身不设 padding 上下, 由 flex 拉伸到 input 同高 */
  padding: 0 12px;
  font-size: 0.75rem;
  white-space: nowrap;
  border: 1px solid var(--accent);
  border-radius: 6px;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
  line-height: 1.2;
  display: inline-flex;
  align-items: center;          /* 文字垂直居中 */
  justify-content: center;
  box-sizing: border-box;       /* 与全局 input 一致, 高度含 border */
  min-height: 43px;             /* 保险: 即使 stretch 失效也有最小高度 */
}
.sms-btn:disabled { opacity: .45; cursor: not-allowed; }
/* 注册页赠送提示 */
.gift-tip {
  font-size: 0.75rem;
  line-height: 1.5;
  color: var(--text-muted);
  background: rgba(255, 176, 32, .1);
  border: 1px solid rgba(255, 176, 32, .35);
  border-radius: 6px;
  padding: 8px 10px;
}
.gift-tip b { color: #ffb020; }
.inviter-tip {
  font-size: 0.75rem;
  color: #2bb673;
  margin-top: -2px;
}
.switch-sep { margin: 0 6px; color: #334; }
</style>
