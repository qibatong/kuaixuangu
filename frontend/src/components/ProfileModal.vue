<template>
  <div v-if="visible" class="auth-overlay">
    <div class="login-box">
      <div class="login-title">个人信息</div>
      <!-- 🔴 2026-10-08 安全修复：**手机号改为只读展示**。
           原先这里是个可编辑输入框，提交后走 POST /api/profile 直接改 users.phone，
           而那个接口只校验格式与唯一性、**没有任何短信验证** ⇒ 拿到登录态的人可以把
           手机号换成自己的，再走「忘记密码」完成**永久接管**（改密的短信验证被整体绕过）。
           现在改手机号只能走「我的」→「更换手机号」（旧号 + 新号双向短信验证）。 -->
      <p class="pf-phone">
        手机号：<b>{{ phone || '未绑定' }}</b>
        <span class="pf-hint">如需更换，请到「我的」→「更换手机号」（需短信验证）</span>
      </p>
      <input v-model="wxName" type="text" placeholder="微信名(联系管理员时便于核对)" autocomplete="off" maxlength="40">
      <button class="login-btn" :disabled="busy" @click="submit">保存</button>
      <div class="login-err">{{ err }}</div>
      <!-- 2026-10-05 (L1 收尾): 同上，`<a javascript:void(0)>` 改真按钮。 -->
      <div class="login-switch"><button type="button" class="lnk" @click="close">取消</button></div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { getProfile, updateProfile } from '../api/auth'

// Promise 超时包装: 超过 ms 毫秒 reject, 防接口挂起
function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('timeout')), ms)
    promise.then(v => { clearTimeout(t); resolve(v) }, e => { clearTimeout(t); reject(e) })
  })
}

const visible = ref(false)
const busy = ref(false)
const err = ref('')
const phone = ref('')          // 只读展示（掩码）；改号走 ChangePhoneModal
const wxName = ref('')

/** 138****8888：接口回的是完整号码，展示层一律打码 */
function mask(p) {
  const s = String(p || '').trim()
  return s.length === 11 ? s.slice(0, 3) + '****' + s.slice(-4) : s
}

async function open() {
  err.value = ''
  // 2026-08-18 修复: 先显示弹窗再异步加载(原逻辑先 await getProfile 后 visible=true,
  // 弱网/接口挂起时弹窗永不显示 → 手机上点击"个人信息"无反应)
  visible.value = true
  busy.value = true
  try {
    // 8s 超时保护, 防止 getProfile 挂起导致 busy 无限转圈
    const d = await withTimeout(getProfile(), 8000)
    if (d && d.ok) {
      phone.value = mask(d.profile.phone)
      wxName.value = d.profile.wx_name || ''
    }
  } catch (e) { /* 静默 */ }
  finally { busy.value = false }
}
function close() { visible.value = false }

async function submit() {
  if (busy.value) return
  err.value = ''
  busy.value = true
  err.value = '保存中...'
  try {
    // 只提交微信名 —— 手机号已不由本接口修改（见模板顶部说明）
    const body = {}
    if (wxName.value.trim()) body.wx_name = wxName.value.trim()
    const data = await updateProfile(body)
    err.value = '✅ ' + data.msg
    setTimeout(close, 1000)
  } catch (e) {
    err.value = e.message || '保存失败'
  } finally {
    busy.value = false
  }
}

defineExpose({ open })
</script>

<style scoped>
.pf-phone {
  margin: 0 0 var(--s2); color: var(--text-muted); font-size: var(--fs-sm); line-height: 1.7;
}
.pf-phone b { color: var(--text-main); }
.pf-hint { display: block; color: var(--text-dim); font-size: var(--fs-xs); }
</style>
