<template>
  <div v-if="visible" class="auth-overlay">
    <div class="login-box">
      <div class="login-title">修改密码</div>
      <!-- 🔴 2026-10-08 主人指示「去掉旧密码那一栏」：旧密码输入框与后端字段**一起删除**，
           只剩「短信验证码 + 新密码 + 确认新密码」。
           依据（先核代码再定，不是图省事）：本系统本来就有一条**只认短信**的改密通道
           （登录页「忘记密码？」→ /api/reset-by-phone：公开接口、无需旧密码）⇒ 拿到手机
           的人走那条路照样能改密码 ⇒ **旧密码从来不是安全边界**，留着它只会拦住
           "忘了旧密码的正常用户"。真正的身份锚点是短信验证码（注册即手机号注册）。 -->
      <!-- 2026-10-08 主人要求「修改密码增加手机短信验证」：验证码为**必填**，
           号码由服务端按登录态取（前端不传手机号，见 api/auth.js 注释）。 -->
      <div class="cp-row">
        <label class="visually-hidden" for="cp-sms">短信验证码</label>
        <input id="cp-sms" v-model="smsCode" type="text" placeholder="短信验证码" autocomplete="one-time-code"
               maxlength="6" inputmode="numeric" @keydown.enter="submit">
        <button type="button" class="cp-sms-btn" :disabled="smsBusy || countdown > 0" @click="sendSms">
          {{ countdown > 0 ? countdown + 's 后重发' : '获取验证码' }}
        </button>
      </div>
      <input v-model="newPwd" type="password" placeholder="新密码（至少6位）" autocomplete="off" @keydown.enter="submit">
      <input v-model="newPwd2" type="password" placeholder="确认新密码" autocomplete="off" @keydown.enter="submit">
      <button class="login-btn" :disabled="busy" @click="submit">确认修改</button>
      <div class="login-err" :class="{ error: errIsError }">{{ err }}</div>
      <!-- 2026-10-05 (L1 收尾): 原 `<a href="javascript:void(0)">` —— 无按钮语义、状态栏不见 URL、
           也没法新标签打开。改真按钮（样式走 main.css 的 .login-switch button.lnk）。 -->
      <div class="login-switch"><button type="button" class="lnk" @click="close">取消</button></div>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { changePassword, sendChangePwdSms } from '../api/auth'
import { useUserStore } from '../stores/user'

const visible = ref(false)
const busy = ref(false)
const err = ref('')
const errIsError = ref(false)
const newPwd = ref('')
const newPwd2 = ref('')
// 短信验证码（2026-10-08 新增；旧密码字段已于同日删除）
const smsCode = ref('')
const smsBusy = ref(false)
const countdown = ref(0)
let countdownTimer = null
const user = useUserStore()

function setErr(msg, isError = true) {
  err.value = msg
  errIsError.value = isError && !!msg
}

function stopCountdown() {
  if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null }
}

function startCountdown() {
  countdown.value = 60
  stopCountdown()
  countdownTimer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) stopCountdown()
  }, 1000)
}

function open() {
  setErr('', false)
  newPwd.value = newPwd2.value = smsCode.value = ''
  countdown.value = 0
  stopCountdown()
  visible.value = true
}
function close() {
  visible.value = false
  stopCountdown()          // 关窗即停，避免后台空转
}

async function sendSms() {
  if (smsBusy.value || countdown.value > 0) return
  setErr('', false)
  smsBusy.value = true
  try {
    const data = await sendChangePwdSms()
    setErr('✅ ' + (data.msg || '验证码已发送，请查收'), false)
    startCountdown()
  } catch (e) {
    setErr(e.message || '发送失败')
  } finally {
    smsBusy.value = false
  }
}

async function submit() {
  if (busy.value) return
  setErr('', false)
  if (!/^\d{4,6}$/.test(smsCode.value.trim())) { setErr('请输入短信验证码'); return }
  if (newPwd.value.length < 6) { setErr('新密码至少 6 位'); return }
  if (newPwd.value !== newPwd2.value) { setErr('两次输入的新密码不一致'); return }
  busy.value = true
  setErr('提交中...', false)
  try {
    const data = await changePassword(newPwd.value, smsCode.value.trim())
    setErr('✅ ' + data.msg, false)
    stopCountdown()
    setTimeout(() => {
      user.clearSession()
      window.location.href = '/login'
    }, 1200)
  } catch (e) {
    setErr(e.message || '修改失败')
  } finally {
    busy.value = false
  }
}

// 2026-09-30 v4.11.83 (P1-7) 的同一教训：手写 setInterval 必须成对清理，
// 否则弹窗卸载后倒计时仍在跑（本组件与 LoginView 是全站仅有的两处手写倒计时）。
onBeforeUnmount(stopCountdown)

defineExpose({ open })
</script>

<style scoped>
/* 验证码行：手机号/验证码框与「获取验证码」按钮同一行。
   注：`.sms-btn`/`.phone-row` 是 LoginView 的 scoped 样式，跨组件不可用 ⇒ 这里自带一套，
   色值/圆角/间距全部走既有 token（不新增裸色，避开 color_guard 棘轮）。 */
.login-box .cp-row { display: flex; gap: var(--s2); margin-bottom: var(--s2); }
.login-box .cp-row input { flex: 1 1 auto; min-width: 0; margin-bottom: 0; }
.cp-sms-btn {
  flex: 0 0 auto;
  background: var(--bg-input);
  border: 1px solid var(--accent);
  color: var(--accent);
  border-radius: var(--r-md);
  padding: 0 var(--s3);
  font-size: var(--fs-xs);
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  min-height: 43px;
  box-sizing: border-box;
}
.cp-sms-btn:disabled { opacity: .45; cursor: not-allowed; }
.cp-sms-btn:hover:not(:disabled) { background: var(--accent-bg); }
body[data-bg="light"] .cp-sms-btn { color: var(--brand-deep); border-color: var(--brand-deep); }
</style>
