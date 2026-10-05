<template>
  <div v-if="visible" class="auth-overlay">
    <div class="login-box">
      <div class="login-title">个人信息</div>
      <input v-model="phone" type="text" placeholder="手机号(用于注册/找回密码)" autocomplete="off" maxlength="11">
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
const phone = ref('')
const wxName = ref('')

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
      phone.value = d.profile.phone || ''
      wxName.value = d.profile.wx_name || ''
    }
  } catch (e) { /* 静默 */ }
  finally { busy.value = false }
}
function close() { visible.value = false }

async function submit() {
  if (busy.value) return
  err.value = ''
  if (phone.value.trim() && !/^1\d{10}$/.test(phone.value.trim())) { err.value = '手机号格式不正确'; return }
  busy.value = true
  err.value = '保存中...'
  try {
    const body = {}
    if (phone.value.trim()) body.phone = phone.value.trim()
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
