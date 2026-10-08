<template>
  <div v-if="visible" class="auth-overlay">
    <div class="login-box">
      <div class="login-title">更换手机号</div>
      <!-- 🔴 2026-10-08 主人要求「增加更换手机号功能」。
           安全设计：手机号是本产品的**身份锚点**（找回密码/改密都靠它收码），所以做成
           **双向短信验证** —— 当前号收一个码（证明是本人）+ 新号收一个码（证明新号在手）。
           只验新号 ⇒ 拿到登录态的人能把账号搬到自己号上，再走「忘记密码」永久接管；
           只验旧号 ⇒ 号码打错也照改，改完自己也收不到码。⇒ 两个都验。
           另：同日已堵掉 POST /api/profile 直接改手机号的老旁路（那里原先无任何短信验证）。 -->
      <p class="cph-tip">
        <template v-if="bound">为确认是本人操作，<b>当前手机号</b>与<b>新手机号</b>各需一次短信验证码。</template>
        <template v-else>当前账号还未绑定手机号，只需验证新手机号即可。</template>
      </p>

      <!-- 当前手机号：只有已绑定才需要验证 -->
      <template v-if="bound">
        <div class="cph-row">
          <label class="visually-hidden" for="cph-old">当前手机号收到的验证码</label>
          <input id="cph-old" v-model="oldCode" type="text" placeholder="当前手机号收到的验证码"
                 autocomplete="one-time-code" maxlength="6" inputmode="numeric" @keydown.enter="submit">
          <button type="button" class="cph-sms-btn" :disabled="oldBusy || oldCd > 0" @click="send('old')">
            {{ oldCd > 0 ? oldCd + 's 后重发' : '获取验证码' }}
          </button>
        </div>
        <p class="cph-cur">验证码将发到当前绑定：{{ curPhone || '加载中…' }}</p>
      </template>

      <!-- 新手机号 -->
      <div class="cph-row">
        <label class="visually-hidden" for="cph-new">新手机号</label>
        <input id="cph-new" v-model="newPhone" type="text" placeholder="新手机号" autocomplete="off"
               maxlength="11" inputmode="numeric">
        <button type="button" class="cph-sms-btn" :disabled="newBusy || newCd > 0" @click="send('new')">
          {{ newCd > 0 ? newCd + 's 后重发' : '获取验证码' }}
        </button>
      </div>
      <input v-model="newCode" type="text" placeholder="新手机号收到的验证码"
             autocomplete="one-time-code" maxlength="6" inputmode="numeric" @keydown.enter="submit">

      <button class="login-btn" :disabled="busy" @click="submit">确认更换</button>
      <div class="login-err" :class="{ error: errIsError }">{{ err }}</div>
      <p class="cph-note">
        换绑后请用<b>新手机号</b>登录（原用户名密码仍可用）。旧手机号已停用、收不到验证码？
        请联系管理员人工处理（微信 poet-1986）。
      </p>
      <div class="login-switch"><button type="button" class="lnk" @click="close">取消</button></div>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { changePhone, sendChangePhoneSms } from '../api/auth'
import { getProfile } from '../api/auth'

const visible = ref(false)
const busy = ref(false)
const err = ref('')
const errIsError = ref(false)
const newPhone = ref('')
const oldCode = ref('')
const newCode = ref('')
const curPhone = ref('')       // 当前绑定号的掩码（138****8888），仅供确认发到了哪台手机
const bound = ref(true)        // 账号是否已绑定手机号：未绑定则不需要（也无从）验证旧号

const oldBusy = ref(false)
const newBusy = ref(false)
const oldCd = ref(0)
const newCd = ref(0)
// 两个倒计时各自成对清理（v4.11.83 P1-7 的教训：手写 setInterval 漏清理会在组件卸载后空转）
const timers = { old: null, new: null }

function setErr(msg, isError = true) {
  err.value = msg
  errIsError.value = isError && !!msg
}

function mask(p) {
  const s = String(p || '').trim()
  return s.length === 11 ? s.slice(0, 3) + '****' + s.slice(-4) : s
}

function stopCd(which) {
  if (timers[which]) { clearInterval(timers[which]); timers[which] = null }
}
function startCd(which) {
  const cd = which === 'old' ? oldCd : newCd
  cd.value = 60
  stopCd(which)
  timers[which] = setInterval(() => {
    cd.value--
    if (cd.value <= 0) stopCd(which)
  }, 1000)
}
function stopAll() { stopCd('old'); stopCd('new') }

async function open() {
  setErr('', false)
  newPhone.value = oldCode.value = newCode.value = ''
  oldCd.value = newCd.value = 0
  stopAll()
  // 先显示弹窗再异步取资料（2026-08-18 ProfileModal 的教训：先 await 会让弱网下"点了没反应"）
  visible.value = true
  curPhone.value = ''
  bound.value = true
  try {
    const d = await getProfile()
    const p = (d && d.profile && d.profile.phone) || ''
    curPhone.value = mask(p)
    bound.value = !!String(p).trim()
  } catch (e) {
    // 静默：拿不到就按"已绑定"渲染（更保守 —— 多要一个旧号验证码，而不是少要）
  }
}

function close() {
  visible.value = false
  stopAll()
}

async function send(which) {
  const busyRef = which === 'old' ? oldBusy : newBusy
  const cd = which === 'old' ? oldCd : newCd
  if (busyRef.value || cd.value > 0) return
  setErr('', false)
  if (which === 'new' && !/^1[3-9]\d{9}$/.test(newPhone.value.trim())) {
    setErr('请输入正确的 11 位新手机号'); return
  }
  busyRef.value = true
  try {
    const d = await sendChangePhoneSms(which, newPhone.value.trim())
    setErr('✅ ' + (d.msg || '验证码已发送，请查收'), false)
    if (d.current_phone) curPhone.value = d.current_phone
    startCd(which)
  } catch (e) {
    setErr(e.message || '发送失败')
  } finally {
    busyRef.value = false
  }
}

async function submit() {
  if (busy.value) return
  setErr('', false)
  if (!/^1[3-9]\d{9}$/.test(newPhone.value.trim())) { setErr('请输入正确的 11 位新手机号'); return }
  if (bound.value && !/^\d{4,6}$/.test(oldCode.value.trim())) { setErr('请输入当前手机号收到的验证码'); return }
  if (!/^\d{4,6}$/.test(newCode.value.trim())) { setErr('请输入新手机号收到的验证码'); return }
  busy.value = true
  setErr('提交中...', false)
  try {
    const d = await changePhone(newPhone.value.trim(), oldCode.value.trim(), newCode.value.trim())
    setErr('✅ ' + d.msg, false)
    stopAll()
    // 服务端刻意**不踢下线**（已双向验明本人）⇒ 只刷新让「个人信息」等回显新号
    setTimeout(() => window.location.reload(), 1600)
  } catch (e) {
    setErr(e.message || '更换失败')
  } finally {
    busy.value = false
  }
}

onBeforeUnmount(stopAll)

defineExpose({ open })
</script>

<style scoped>
/* 行布局 / 按钮样式与 ChangePwdModal 同款（LoginView 的 .sms-btn/.phone-row 是 scoped 样式，
   跨组件不可用 ⇒ 自带一套），色值全部走既有 token，不新增裸色（避开 color_guard 棘轮）。 */
.login-box .cph-row { display: flex; gap: var(--s2); margin-bottom: var(--s2); }
.login-box .cph-row input { flex: 1 1 auto; min-width: 0; margin-bottom: 0; }
.cph-sms-btn {
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
.cph-sms-btn:disabled { opacity: .45; cursor: not-allowed; }
.cph-sms-btn:hover:not(:disabled) { background: var(--accent-bg); }
body[data-bg="light"] .cph-sms-btn { color: var(--brand-deep); border-color: var(--brand-deep); }
.cph-tip { margin: 0 0 var(--s3); color: var(--text-muted); font-size: var(--fs-xs); line-height: 1.7; }
.cph-tip b { color: var(--text-main); }
.cph-cur { margin: 0 0 var(--s3); color: var(--text-dim); font-size: var(--fs-xs); }
.cph-note { margin: var(--s2) 0 0; color: var(--text-dim); font-size: var(--fs-xs); line-height: 1.7; }
.cph-note b { color: var(--text-secondary); }
</style>
