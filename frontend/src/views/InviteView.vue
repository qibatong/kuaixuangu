<template>
  <div class="page-shell">
    <div class="page-back">
      <router-link to="/" class="tdx-export-btn nav-btn nav-invite"><i class="fa fa-arrow-left"></i> 返回选股</router-link>
    </div>
    <div class="invite-panel" style="width:min(480px, 94vw); margin:0 auto;">
      <div class="invite-head">
        <span class="invite-title"><i class="fa fa-share-alt"></i> 邀请推广</span>
      </div>
      <div class="invite-body">
        <div class="invite-code-box">
          <div class="invite-code-label">我的专属邀请码（新用户注册时填写）</div>
          <div class="invite-code-text">{{ inviteCode || '----' }}</div>
          <div class="invite-code-actions">
            <button class="invite-btn" @click="copyCode"><i class="fa fa-copy"></i> 复制邀请码</button>
            <button class="invite-btn danger" @click="refresh"><i class="fa fa-refresh"></i> 刷新邀请码</button>
          </div>
        </div>
        <div class="invite-stats">已邀请 <strong>{{ invitedCount }}</strong> 人注册</div>
        <div class="invite-list">
          <div class="invite-list-title">被邀请的用户</div>
          <div v-if="loading" class="invite-empty">加载中...</div>
          <div v-else-if="!invitees.length" class="invite-empty">还没有人通过你的邀请码注册</div>
          <div v-for="u in invitees" v-else :key="u.username" class="invite-item">
            <span>{{ u.username }}</span><span class="invite-item-date">{{ fmtTsDate(u.created_at) }}</span>
          </div>
        </div>
        <div class="invite-tip">把邀请码分享给朋友，朋友注册时填写即可建立邀请关系。邀请码可随时刷新，刷新后旧码立即失效。</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { getInvite, refreshInvite } from '../api/invite'
import { showToast } from '../utils/toast'
import { copyText } from '../utils/tdx'
import { fmtTsDate } from '../utils/time'

const inviteCode = ref('')
const invitedCount = ref(0)
const invitees = ref([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const data = await getInvite()
    inviteCode.value = data.invite_code
    invitedCount.value = data.invited_count || 0
    invitees.value = data.invitees || []
  } catch (e) {
    showToast('❌ ' + e.message, 'error')
  } finally {
    loading.value = false
  }
}

function copyCode() {
  if (!inviteCode.value) return
  copyText(inviteCode.value, '✅ 邀请码已复制')
}

async function refresh() {
  if (!confirm('刷新后旧邀请码立即失效，已建立的邀请关系不受影响。确定刷新？')) return
  try {
    const data = await refreshInvite()
    inviteCode.value = data.invite_code
    showToast('✅ 邀请码已刷新', 'success')
  } catch (e) {
    showToast('❌ ' + e.message, 'error')
  }
}

onMounted(load)
</script>
