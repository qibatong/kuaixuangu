<template>
  <div class="vip-gate">
    <div class="vip-card">
      <div class="vip-icon"><i class="fa fa-crown"></i></div>
      <div class="vip-title">{{ title || '该功能' }}需要会员权限</div>
      <div class="vip-desc">
        <template v-if="user.expired">
          您的会员已<span class="warn">过期</span>，暂时无法使用{{ title || '该功能' }}。
        </template>
        <template v-else-if="user.expireAt > 0">
          您的新用户试用期还剩 <b class="highlight">{{ days }} 天</b>，到期后需开通会员。
        </template>
        <template v-else>
          此功能仅限会员使用，请联系管理员开通。
        </template>
      </div>
      <div class="vip-tip">
        <i class="fa fa-phone"></i> 如需开通/续费会员，请联系管理员
      </div>
      <button class="vip-back" @click="goBack"><i class="fa fa-arrow-left"></i> 返回可用功能</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '../stores/user'

defineProps({ title: { type: String, default: '' } })

const user = useUserStore()
const router = useRouter()

const days = computed(() => {
  if (!user.expireAt) return 0
  return Math.max(0, Math.ceil((user.expireAt - Date.now() / 1000) / 86400))
})

function goBack() {
  router.replace('/')
}
</script>

<style scoped>
.vip-gate {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 40px 12px 20px;
}
.vip-card {
  background: var(--bg-panel);
  border: 1px solid rgba(var(--accent-rgb), 0.35);
  border-radius: 14px;
  padding: 32px 40px;
  text-align: center;
  max-width: 460px;
  width: 100%;
  box-shadow: 0 8px 32px rgba(var(--accent-rgb), 0.12);
}
.vip-icon {
  font-size: 44px;
  color: var(--accent);
  margin-bottom: 10px;
}
.vip-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-main);
  margin-bottom: 12px;
}
.vip-desc {
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.7;
}
.vip-desc .warn { color: var(--accent-deep); font-weight: 700; }
.vip-desc .highlight { color: var(--accent-deep); font-size: 18px; }
.vip-tip {
  margin-top: 14px;
  font-size: 13px;
  color: var(--text-muted);
  background: rgba(var(--accent-rgb), 0.08);
  border-radius: 8px;
  padding: 8px 12px;
}
.vip-back {
  margin-top: 18px;
  background: var(--accent-deep);
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 9px 22px;
  font-size: 14px;
  cursor: pointer;
  transition: opacity 0.15s;
}
.vip-back:hover { opacity: 0.88; }
</style>
