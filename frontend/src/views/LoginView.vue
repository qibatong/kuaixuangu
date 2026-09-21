<template>
  <div class="auth-overlay">
    <div class="login-box">
      <img src="/logo.jpg" class="login-logo" alt="快选 Kuaixuan">
      <div class="login-title">快选</div>
      <div class="login-sub">{{ subText }}</div>

      <!-- 登录 -->
      <template v-if="mode === 'login'">
        <form class="login-form" @submit.prevent="submit">
          <input v-model="username" type="text" placeholder="用户名 / 手机号 / 邮箱" autocomplete="username" maxlength="20">
          <input v-model="password" type="password" placeholder="密码" autocomplete="current-password">
          <label class="remember-row">
            <input v-model="remember" type="checkbox" class="remember-check" />
            <span>记住我，30 天内免登录</span>
          </label>
          <button class="login-btn" type="submit" :disabled="busy">{{ busy ? '登录中...' : '登录' }}</button>
          <div class="login-err" :class="{error: errIsError}">{{ err }}</div>
        </form>
        <div class="login-switch">
          <a href="javascript:void(0)" @click="mode = 'forgot'">忘记密码？</a>
        </div>
      </template>

      <!-- 邮箱验证(新注册强制, 2026-08-17) -->
      <template v-else-if="mode === 'verify'">
        <div class="login-sub">📧 验证邮件已发送至 <b>{{ verifyEmailAddr || '你的邮箱' }}</b></div>
        <div class="login-sub" style="font-size:0.75rem;color:#889;">请查收并输入 6 位验证码完成验证，之后才能登录</div>
        <input v-model="verifyCode" type="text" placeholder="6 位邮箱验证码" autocomplete="off" maxlength="6" inputmode="numeric" @keydown.enter="doVerify">
        <button class="login-btn" :disabled="busy" @click="doVerify">{{ busy ? '验证中...' : '完成验证' }}</button>
        <div class="login-err" :class="{error: errIsError}">{{ err }}</div>
        <div class="login-switch">
          <a href="javascript:void(0)" :disabled="busy" @click="doResend">没收到？重新发送</a>
          <span style="margin:0 6px;color:#334;">|</span>
          <a href="javascript:void(0)" @click="mode = 'login'">返回登录</a>
        </div>
      </template>

      <!-- 忘记密码: 手机验证码(主) / 邮箱重置(备) -->
      <template v-else-if="mode === 'forgot'">
        <div class="forgot-tabs">
          <button type="button" :class="['forgot-tab', { on: forgotTab === 'phone' }]" @click="forgotTab = 'phone'">手机验证码</button>
          <button type="button" :class="['forgot-tab', { on: forgotTab === 'email' }]" @click="forgotTab = 'email'">邮箱重置</button>
        </div>

        <!-- 手机号+短信验证码方式 -->
        <template v-if="forgotTab === 'phone'">
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
        </template>

        <!-- 邮箱方式(原逻辑保留) -->
        <template v-else>
          <form class="forgot-form" @submit.prevent="sendMail">
            <div class="login-sub">输入注册时绑定的邮箱，我们会发送重置链接</div>
            <input v-model="email" type="text" placeholder="绑定邮箱" autocomplete="off" maxlength="60" @keydown.enter="sendMail">
            <button class="login-btn" type="submit" :disabled="busy">{{ busy ? '发送中...' : '发送重置邮件' }}</button>
            <div class="login-switch">
              <a href="javascript:void(0)" @click="checkForgot">不记得绑定邮箱？输入用户名/手机号查询</a>
            </div>
          </form>
        </template>
        <div class="login-err" :class="{error: errIsError}">{{ err }}</div>
        <div class="login-switch">
          <a href="javascript:void(0)" @click="mode = 'login'">返回登录</a>
        </div>
      </template>

      <!-- 设置新密码(邮件重置链接) -->
      <template v-else-if="mode === 'reset'">
        <div class="login-sub">请输入新密码（至少 6 位）</div>
        <input v-model="resetPwd" type="password" placeholder="新密码" autocomplete="off" @keydown.enter="doReset">
        <input v-model="resetPwd2" type="password" placeholder="确认新密码" autocomplete="off" @keydown.enter="doReset">
        <button class="login-btn" :disabled="busy" @click="doReset">确认重置</button>
        <div class="login-err">{{ err }}</div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login as apiLogin, forgot as apiForgot, reset as apiReset, forgotCheck as apiForgotCheck, verifyEmail as apiVerifyEmail, resendVerify as apiResendVerify, sendForgotSms as apiSendForgotSms, resetByPhone as apiResetByPhone } from '../api/auth'
import { useUserStore } from '../stores/user'
import { showToast } from '../utils/toast'

const route = useRoute()
const router = useRouter()
const user = useUserStore()

const mode = ref('login')          // login | forgot | reset | verify
const busy = ref(false)
const err = ref('')
// 错误提示是否显示红色背景块: 仅"真正的错误"才上色 (loading/✅成功 不算)
const errIsError = ref(false)
const remember = ref(true)         // 「记住我」默认勾选: 30 天免登录
const username = ref('')
const password = ref('')
const resetPwd = ref('')
const resetPwd2 = ref('')
const resetToken = ref(null)
// 找回密码-短信验证码(2026-08-30)
const forgotTab = ref('phone')     // phone | email
const phone = ref('')
const smsCode = ref('')
const smsBusy = ref(false)
const countdown = ref(0)
let countdownTimer = null
// 邮箱验证(2026-08-17)
const verifyUid = ref(0)
const verifyEmailAddr = ref('')
const verifyCode = ref('')

const subText = computed(() =>
  mode.value === 'login' ? '登录' :
  mode.value === 'verify' ? '邮箱验证' :
  mode.value === 'forgot' ? '重置密码' : '设置新密码')

function setErr(msg, isError = true) {
  err.value = msg
  errIsError.value = isError && !!msg
}

async function submit() {
  if (busy.value) return
  setErr('', false)
  if (!username.value.trim()) { setErr('请输入用户名/手机号/邮箱'); return }
  if (!password.value) { setErr('请输入密码'); return }
  busy.value = true
  err.value = '登录中...'
  errIsError.value = false
  try {
    const body = { login: username.value.trim(), password: password.value, remember: remember.value }
    const data = await apiLogin(body)
    // 注册已停止; 此分支仅兼容历史: 登录自动登录
    user.setSession(data.username, data.token, data.is_admin, data.expire_at, data.expired, remember.value, data.member_level)
    if (data.expired) {
      showToast('⚠️ 账号已过期，请联系管理员续费(微信 poet-1986)', 'error')
    }
    const redirect = route.query.redirect || '/'
    router.replace(redirect)
  } catch (e) {
    // 未验证邮箱登录被拦截 → 切到验证模式
    if (e && e.need_verify_email) {
      verifyUid.value = e.uid
      verifyEmailAddr.value = e.email || email.value.trim()
      verifyCode.value = ''
      setErr('', false)
      mode.value = 'verify'
    } else {
      setErr(e.message || '操作失败')
    }
  } finally {
    busy.value = false
  }
}

// ---------- 邮箱验证(2026-08-17) ----------
async function doVerify() {
  if (busy.value) return
  setErr('', false)
  if (!verifyUid.value) { setErr('缺少验证信息，请返回登录重试'); return }
  if (!/^\d{6}$/.test(verifyCode.value.trim())) { setErr('请输入 6 位数字验证码'); return }
  busy.value = true
  err.value = '验证中...'
  errIsError.value = false
  try {
    const data = await apiVerifyEmail(verifyUid.value, verifyCode.value.trim())
    user.setSession(data.username, data.token, data.is_admin, data.expire_at, data.expired, false, data.member_level)
    showToast('✅ 邮箱验证成功', 'success')
    const redirect = route.query.redirect || '/'
    router.replace(redirect)
  } catch (e) {
    setErr(e.message || '验证失败')
  } finally {
    busy.value = false
  }
}

async function doResend() {
  if (busy.value) return
  setErr('', false)
  if (!verifyUid.value) { setErr('缺少验证信息，请返回登录重试'); return }
  busy.value = true
  try {
    const data = await apiResendVerify(verifyUid.value)
    setErr('✅ ' + (data.msg || '已重新发送，请查收'), false)
  } catch (e) {
    setErr(e.message || '发送失败')
  } finally {
    busy.value = false
  }
}

async function sendMail() {
  if (busy.value) return
  setErr('', false)
  if (!email.value.trim()) { setErr('请输入邮箱'); return }
  busy.value = true
  err.value = '发送中...'
  errIsError.value = false
  try {
    const data = await apiForgot(email.value.trim())
    err.value = '✅ ' + (data.msg || '已发送，请查收邮件')
    errIsError.value = false   // 成功不显示错误块
  } catch (e) {
    setErr(e.message || '发送失败')
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
    // 60s 倒计时防刷
    countdown.value = 60
    if (countdownTimer) clearInterval(countdownTimer)
    countdownTimer = setInterval(() => {
      countdown.value--
      if (countdown.value <= 0) clearInterval(countdownTimer)
    }, 1000)
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

// 忘记密码: 输入用户名/手机号查询是否绑定邮箱(未绑定提示联系管理员)
async function checkForgot() {
  setErr('', false)
  const login = window.prompt('请输入你的用户名或手机号(用于查询绑定邮箱)')
  if (!login || !login.trim()) return
  busy.value = true
  try {
    const data = await apiForgotCheck(login.trim())
    if (data.ok) {
      err.value = '✅ ' + (data.msg || '已绑定邮箱，请在输入框填该邮箱')
      errIsError.value = false
    } else {
      setErr(data.msg || '未绑定邮箱，请联系管理员')
    }
  } catch (e) {
    setErr(e.message || '查询失败')
  } finally {
    busy.value = false
  }
}

async function doReset() {
  if (busy.value) return
  setErr('', false)
  if (!resetPwd.value || resetPwd.value.length < 6) { setErr('新密码至少 6 位'); return }
  if (resetPwd.value !== resetPwd2.value) { setErr('两次输入的密码不一致'); return }
  busy.value = true
  err.value = '提交中...'
  errIsError.value = false
  try {
    const data = await apiReset(resetToken.value, resetPwd.value)
    err.value = '✅ ' + data.msg
    errIsError.value = false
    setTimeout(() => {
      mode.value = 'login'
      resetPwd.value = resetPwd2.value = ''
      // 清除 URL 里的 reset token
      if (history.replaceState) history.replaceState({}, '', window.location.pathname)
    }, 1500)
  } catch (e) {
    setErr(e.message || '重置失败')
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  // 邮件里的重置链接: ?reset=TOKEN → 直接进入设置新密码
  const resetTok = new URLSearchParams(window.location.search).get('reset')
  if (resetTok) {
    resetToken.value = resetTok
    mode.value = 'reset'
  } else {
    username.value = ''
    password.value = ''
    setTimeout(() => document.querySelector('input')?.focus(), 50)
  }
})
</script>

<style scoped>
.login-form { display: flex; flex-direction: column; gap: 10px; }
/* 忘记密码表单: 同 login-form 间距 + 强制所有 input 统一高度 43px, 避免单独 input 与 phone-row 内 input 高度差 */
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
.forgot-tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}
.forgot-tab {
  flex: 1;
  padding: 7px 0;
  font-size: 0.8125rem;
  border: 1px solid var(--border, #3a3f4b);
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: background-color .15s, border-color .15s, color .15s;
}
.forgot-tab.on {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
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
</style>
