<template>
  <div v-if="visible" class="auth-overlay">
    <div class="login-box">
      <div class="login-title">修改密码</div>
      <input v-model="oldPwd" type="password" placeholder="旧密码" autocomplete="off" @keydown.enter="submit">
      <input v-model="newPwd" type="password" placeholder="新密码（至少6位）" autocomplete="off" @keydown.enter="submit">
      <input v-model="newPwd2" type="password" placeholder="确认新密码" autocomplete="off" @keydown.enter="submit">
      <button class="login-btn" :disabled="busy" @click="submit">确认修改</button>
      <div class="login-err">{{ err }}</div>
      <div class="login-switch"><a href="javascript:void(0)" @click="close">取消</a></div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { changePassword } from '../api/auth'
import { useUserStore } from '../stores/user'

const visible = ref(false)
const busy = ref(false)
const err = ref('')
const oldPwd = ref('')
const newPwd = ref('')
const newPwd2 = ref('')
const user = useUserStore()

function open() {
  err.value = ''
  oldPwd.value = newPwd.value = newPwd2.value = ''
  visible.value = true
}
function close() { visible.value = false }

async function submit() {
  if (busy.value) return
  err.value = ''
  if (!oldPwd.value) { err.value = '请输入旧密码'; return }
  if (newPwd.value.length < 6) { err.value = '新密码至少 6 位'; return }
  if (newPwd.value !== newPwd2.value) { err.value = '两次输入的新密码不一致'; return }
  busy.value = true
  err.value = '提交中...'
  try {
    const data = await changePassword(oldPwd.value, newPwd.value)
    err.value = '✅ ' + data.msg
    setTimeout(() => {
      user.clearSession()
      window.location.href = '/login'
    }, 1200)
  } catch (e) {
    err.value = e.message || '修改失败'
  } finally {
    busy.value = false
  }
}

defineExpose({ open })
</script>
