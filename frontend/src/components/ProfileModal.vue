<template>
  <div v-if="visible" class="auth-overlay">
    <div class="login-box">
      <div class="login-title">个人信息</div>
      <input v-model="phone" type="text" placeholder="手机号(用于账号追溯+找回)" autocomplete="off" maxlength="11">
      <input v-model="email" type="text" placeholder="邮箱(用于找回密码)" autocomplete="off" maxlength="60">
      <input v-model="wxName" type="text" placeholder="微信名(用于找回密码)" autocomplete="off" maxlength="40">
      <button class="login-btn" :disabled="busy" @click="submit">保存</button>
      <div class="login-err">{{ err }}</div>
      <div class="login-switch"><a href="javascript:void(0)" @click="close">取消</a></div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { getProfile, updateProfile } from '../api/auth'

const visible = ref(false)
const busy = ref(false)
const err = ref('')
const phone = ref('')
const email = ref('')
const wxName = ref('')

async function open() {
  err.value = ''
  busy.value = true
  try {
    const d = await getProfile()
    if (d.ok) {
      phone.value = d.profile.phone || ''
      // 接口返回脱敏邮箱, 不回填完整邮箱避免覆盖: 只展示, 用户自行修改
      email.value = d.profile.email || ''
      wxName.value = d.profile.wx_name || ''
    }
  } catch (e) { /* 静默 */ }
  finally { busy.value = false }
  visible.value = true
}
function close() { visible.value = false }

async function submit() {
  if (busy.value) return
  err.value = ''
  if (phone.value.trim() && !/^1\d{10}$/.test(phone.value.trim())) { err.value = '手机号格式不正确'; return }
  if (email.value.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())) { err.value = '邮箱格式不正确'; return }
  busy.value = true
  err.value = '保存中...'
  try {
    const body = {}
    if (phone.value.trim()) body.phone = phone.value.trim()
    if (email.value.trim()) body.email = email.value.trim()
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
