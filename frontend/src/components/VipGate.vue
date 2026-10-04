<template>
  <div class="vip-gate">
    <div class="vip-card">
      <div class="vip-icon"><i class="fa" :class="quota ? 'fa-bolt' : 'fa-certificate'"></i></div>
      <div class="vip-title">{{ headline }}</div>
      <div class="vip-desc">
        <!-- 配额用尽模式(2026-09-21 会员体系): 免费用户掉到这里 -->
        <template v-if="quota">
          <p>
            今天 <b class="highlight">{{ quota.feature_label }}</b> 的免费次数
            （{{ quota.limit }} 次/天）已用完。
          </p>
          <p class="quota-list">
            <span v-for="q in quotaAll" :key="q.feature" class="quota-chip" :class="{ off: q.remain <= 0 }">
              {{ q.label }}<template v-if="q.privileged">∞</template><template v-else>{{ q.remain }}/{{ q.limit }}</template>
            </span>
          </p>
          <p class="ways">
            <span class="way"><i class="fa fa-calendar-check-o"></i> 每日签到 +{{ checkinBonus }} 次</span>
            <span class="way"><i class="fa fa-user-plus"></i> 邀请好友各得 5 天会员</span>
            <span class="way"><i class="fa fa-certificate"></i> 开通会员 → 不限次</span>
          </p>
        </template>
        <!-- requiredLevel=1: 严格模式(2026-08-17 竞价异动) -->
        <template v-else-if="requiredLevel === 1">
          此功能<span class="warn">仅限 VIP/付费会员</span>使用，免费用户不可用，
          请<span class="warn">联系管理员升级</span>后访问。
        </template>
        <!-- requiredLevel=0(默认): 兼容旧逻辑 -->
        <template v-else>
          <template v-if="user.expired">
            您的会员已<span class="warn">过期</span>，暂时无法使用{{ title || '该功能' }}，
            请<span class="warn">联系管理员开通权限</span>。
          </template>
          <template v-else-if="user.expireAt > 0">
            您的新用户试用期还剩 <b class="highlight">{{ days }} 天</b>，
            到期后请联系管理员开通权限。
          </template>
          <template v-else-if="user.memberLevel === 2">
            VIP 权限：{{ title || '该功能' }}永久可用。
          </template>
          <template v-else>
            此功能仅限会员使用，请联系管理员开通权限。
          </template>
        </template>
      </div>
      <div v-if="!quota" class="vip-tip">
        <i class="fa fa-phone"></i> 如需开通/续费会员，请联系管理员
        <div class="vip-wechat">微信: <b>poet-1986</b></div>
      </div>
      <div class="vip-actions">
        <button v-if="quota" class="vip-primary" @click="goMember"><i class="fa fa-certificate"></i> 我的会员 / 签到领次数</button>
        <button class="vip-back" @click="goBack"><i class="fa fa-arrow-left"></i> 返回可用功能</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '../stores/user'
import { memberQuota, memberPlans } from '../api/member'

// requiredLevel: 0=任何会员(默认, 兼容旧调用) / 1=严格 VIP/付费门禁
const props = defineProps({
  title: { type: String, default: '' },
  requiredLevel: { type: Number, default: 0 }
})

const user = useUserStore()
const router = useRouter()

// 配额用尽模式(2026-09-21): 父级收到 429 quota_exceeded 时把 detail 透传进来
const quota = ref(null)
const quotaAll = ref([])
const checkinBonus = ref(3)

const headline = computed(() => {
  if (quota.value) return `今日${quota.value.feature_label}次数已用完`
  return `${props.title || '该功能'}需要会员权限`
})

const days = computed(() => {
  if (!user.expireAt) return 0
  return Math.max(0, Math.ceil((user.expireAt - Date.now() / 1000) / 86400))
})

// 供父级调用: setupQuota({feature, feature_label, limit, used, msg})
function openQuota(info, allQuota) {
  quota.value = info || { feature: '', feature_label: '该功能', limit: 0, used: 0 }
  if (allQuota) quotaAll.value = allQuota
}
defineExpose({ openQuota })

function goBack() {
  router.replace('/')
}
function goMember() {
  router.push('/member')
}

onMounted(async () => {
  try {
    const d = await memberQuota()
    const q = d.quota || {}
    quotaAll.value = [
      { ...(q.picker || {}), label: '选股' },
      { ...(q.aipick || {}), label: 'AI预测' },
      { ...(q.auction || {}), label: '竞价' },
    ].filter((x) => x && x.label)
    if (d.checkin && d.checkin.reward) checkinBonus.value = d.checkin.reward
  } catch { /* 未登录/失败静默 */ }
  try {
    const p = await memberPlans()
    if (p.checkin_bonus) checkinBonus.value = p.checkin_bonus
  } catch { /* 忽略 */ }
})
</script>

<style scoped>
.vip-gate {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: var(--s8) var(--s3) var(--s5);
}
.vip-card {
  background: var(--bg-panel);
  border: 1px solid rgba(var(--accent-rgb), 0.35);
  border-radius: var(--r-lg);
  padding: var(--s8) var(--s8);
  text-align: center;
  max-width: 460px;
  width: 100%;
  box-shadow: 0 8px 32px rgba(var(--accent-rgb), 0.12);
}
.vip-icon {
  font-size: 2.75rem;
  color: var(--accent);
  margin-bottom: var(--s2);
}
.vip-title {
  font-size: var(--fs-2xl);
  font-weight: 700;
  color: var(--text-main);
  margin-bottom: var(--s3);
}
.vip-desc {
  font-size: var(--fs-base);
  color: var(--text-secondary);
  line-height: 1.7;
}
.vip-desc p { margin: 0 0 var(--s2); }
.vip-desc .warn { color: var(--accent-deep); font-weight: 700; }
.vip-desc .highlight { color: var(--accent-deep); font-size: var(--fs-xl); }
.quota-list { display: flex; gap: var(--s2); justify-content: center; flex-wrap: wrap; }
.quota-chip {
  font-size: var(--fs-xs);
  padding: 2px var(--s2);
  border-radius: var(--r-lg);
  border: 1px solid rgba(var(--accent-rgb), 0.35);
  color: var(--text-secondary);
}
.quota-chip.off { opacity: .5; border-color: var(--border-soft, #3a3f4b); }
.ways {
  display: flex;
  flex-direction: column;
  gap: var(--s1);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  text-align: left;
}
.way i { color: var(--accent); margin-right: var(--s2); }
.vip-tip {
  margin-top: var(--s4);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  background: rgba(var(--accent-rgb), 0.08);
  border-radius: var(--r-md);
  padding: var(--s2) var(--s3);
}
.vip-wechat {
  margin-top: var(--s1);
  font-size: var(--fs-base);
  color: var(--accent-deep);
}
.vip-wechat b { font-weight: 700; letter-spacing: 0.5px; }
.vip-actions {
  margin-top: var(--s4);
  display: flex;
  gap: var(--s2);
  justify-content: center;
  flex-wrap: wrap;
}
.vip-primary {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--r-md);
  padding: var(--s2) var(--s6);
  font-size: var(--fs-base);
  cursor: pointer;
  transition: opacity 0.15s;
}
.vip-primary:hover { opacity: 0.88; }
.vip-back {
  background: var(--accent-deep);
  color: #fff;
  border: none;
  border-radius: var(--r-md);
  padding: var(--s2) var(--s6);
  font-size: var(--fs-base);
  cursor: pointer;
  transition: opacity 0.15s;
}
.vip-back:hover { opacity: 0.88; }
</style>
