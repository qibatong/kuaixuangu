<template>
  <div class="auth-overlay">
    <div class="login-box">
      <img src="/logo.jpg" class="login-logo" alt="快选 Kuaixuan">
      <div class="login-title">快选</div>
      <div class="login-sub">{{ subText }}</div>

      <!-- 登录/注册 -->
      <template v-if="mode === 'login' || mode === 'register'">
        <form class="login-form" @submit.prevent="submit">
          <input v-model="username" type="text" :placeholder="mode === 'login' ? '用户名 / 手机号 / 邮箱' : '用户名(必填)'" :autocomplete="mode === 'login' ? 'username' : 'off'" maxlength="40">
          <input v-model="password" type="password" placeholder="密码" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'">
          <template v-if="mode === 'register'">
            <input v-model="invite" type="text" placeholder="邀请码(选填, 填了邀请人+5天使用时间)" autocomplete="off" maxlength="12">
            <input v-model="phone" type="text" placeholder="手机号(必填, 用于账号追溯+找回)" autocomplete="off" maxlength="11">
            <input v-model="email" type="text" placeholder="邮箱(必填, 用于账号追溯+找回)" autocomplete="off" maxlength="60">
          </template>
          <label v-if="mode === 'login'" class="remember-row">
            <input v-model="remember" type="checkbox" class="remember-check" />
            <span>记住我，30 天内免登录</span>
          </label>
          <button class="login-btn" type="submit" :disabled="busy">{{ mode === 'login' ? '登录' : '注册' }}</button>
          <div class="login-err">{{ err }}</div>
        </form>
        <div class="login-switch">
          还没有账号？<a href="javascript:void(0)" @click="toggleMode">{{ mode === 'login' ? '注册一个' : '返回登录' }}</a>
          <span style="margin:0 6px;color:#334;">|</span>
          <a href="javascript:void(0)" @click="mode = 'forgot'">忘记密码？</a>
        </div>
      </template>

      <!-- 忘记密码 -->
      <template v-else-if="mode === 'forgot'">
        <div class="login-sub">输入注册时绑定的邮箱，我们会发送重置链接</div>
        <input v-model="email" type="text" placeholder="绑定邮箱" autocomplete="off" maxlength="60" @keydown.enter="sendMail">
        <button class="login-btn" :disabled="busy" @click="sendMail">发送重置邮件</button>
        <div class="login-err">{{ err }}</div>
        <div class="login-switch"><a href="javascript:void(0)" @click="mode = 'login'">返回登录</a></div>
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
import { login as apiLogin, register as apiRegister, forgot as apiForgot, reset as apiReset } from '../api/auth'
import { useUserStore } from '../stores/user'
import { showToast } from '../utils/toast'

const route = useRoute()
const router = useRouter()
const user = useUserStore()

const mode = ref('login')          // login | register | forgot | reset
const busy = ref(false)
const err = ref('')
const remember = ref(true)         // 「记住我」默认勾选: 30 天免登录
const username = ref('')
const password = ref('')
const invite = ref('')
const phone = ref('')
const email = ref('')
const resetPwd = ref('')
const resetPwd2 = ref('')
const resetToken = ref(null)

const subText = computed(() =>
  mode.value === 'login' ? '登录' :
  mode.value === 'register' ? '注册新账号' :
  mode.value === 'forgot' ? '重置密码' : '设置新密码')

function toggleMode() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  err.value = ''
}

async function submit() {
  if (busy.value) return
  err.value = ''
  if (!username.value.trim()) { err.value = '请输入用户名/手机号/邮箱'; return }
  if (!password.value) { err.value = '请输入密码'; return }
  if (mode.value === 'register') {
    if (!phone.value.trim()) { err.value = '请填写手机号(必填, 用于账号追溯+找回)'; return }
    if (!email.value.trim()) { err.value = '请填写邮箱(必填, 用于账号追溯+找回)'; return }
  }
  busy.value = true
  err.value = mode.value === 'login' ? '登录中...' : '注册中...'
  try {
    const body = mode.value === 'register'
      ? { username: username.value.trim(), password: password.value, invite_code: invite.value.trim(), phone: phone.value.trim(), email: email.value.trim() }
      : { login: username.value.trim(), password: password.value, remember: remember.value }
    const data = mode.value === 'register' ? await apiRegister(body) : await apiLogin(body)
    // 注册自动登录的 token 为 12h 会话, 存 sessionStorage; 登录按「记住我」选择
    user.setSession(data.username, data.token, data.is_admin, data.expire_at, data.expired, mode.value === 'login' && remember.value, data.member_level)
    if (data.expired) {
      showToast('⚠️ 账号已过期，请联系管理员续费', 'error')
    }
    const redirect = route.query.redirect || '/'
    router.replace(redirect)
  } catch (e) {
    err.value = e.message || '操作失败'
  } finally {
    busy.value = false
  }
}

async function sendMail() {
  if (busy.value) return
  err.value = ''
  if (!email.value.trim()) { err.value = '请输入邮箱'; return }
  busy.value = true
  err.value = '发送中...'
  try {
    const data = await apiForgot(email.value.trim())
    err.value = '✅ ' + (data.msg || '已发送，请查收邮件')
  } catch (e) {
    err.value = e.message || '发送失败'
  } finally {
    busy.value = false
  }
}

async function doReset() {
  if (busy.value) return
  err.value = ''
  if (!resetPwd.value || resetPwd.value.length < 6) { err.value = '新密码至少 6 位'; return }
  if (resetPwd.value !== resetPwd2.value) { err.value = '两次输入的密码不一致'; return }
  busy.value = true
  err.value = '提交中...'
  try {
    const data = await apiReset(resetToken.value, resetPwd.value)
    err.value = '✅ ' + data.msg
    setTimeout(() => {
      mode.value = 'login'
      resetPwd.value = resetPwd2.value = ''
      // 清除 URL 里的 reset token
      if (history.replaceState) history.replaceState({}, '', window.location.pathname)
    }, 1500)
  } catch (e) {
    err.value = e.message || '重置失败'
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
.remember-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
  cursor: pointer;
  user-select: none;
  margin-top: 2px;
}
.remember-check { width: 14px; height: 14px; accent-color: var(--accent); cursor: pointer; }
</style>
