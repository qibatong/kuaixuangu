// 2026-10-05 (S1): **匿名可用**的公共配置（单例 + 请求去重）。
//
// GET /api/register/config 是后端少数几个免鉴权接口之一（backend/app/api/auth.py:244 无 Depends），
// 返回 { open, gift_days, invite_reward_days }。落地页/NavBar/登录页都要用它来渲染
// 「注册送 N 天会员」，若不收口会出现**同一时刻重复请求**（实测登录页原先就重复发了 2 次）。
//
// 用法：组件里 `const { giftDays, regOpen, load } = usePublicConfig()` 然后 onMounted 调 load()。
// 返回值是模块级 ref ⇒ 多个组件共享同一份数据，load() 只真正请求一次。
import { ref } from 'vue'
import { registerConfig } from '../api/auth'

const giftDays = ref(5)            // 默认 5：与后端 NEW_USER_DAYS 的常见配置一致，取不到时文案仍成立
const inviteRewardDays = ref(5)
const regOpen = ref(true)

let pending = null

export function usePublicConfig() {
  function load() {
    if (!pending) {
      pending = registerConfig()
        .then((cfg) => {
          if (!cfg) return
          if (cfg.gift_days !== undefined) giftDays.value = cfg.gift_days
          if (cfg.invite_reward_days !== undefined) inviteRewardDays.value = cfg.invite_reward_days
          if (cfg.open !== undefined) regOpen.value = !!cfg.open
        })
        .catch(() => { /* 未开放注册/网络异常：保留默认文案，不打断页面 */ })
    }
    return pending
  }
  return { giftDays, inviteRewardDays, regOpen, load }
}
