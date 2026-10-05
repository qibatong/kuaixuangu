// 客服/开通 联系方式 —— 全站唯一来源。
// 2026-10-05 (S4/S6): 此前微信号在 VipGate.vue / MemberView.vue 各硬编码了一份，
//   落地页、页脚、合规页还要再引 ⇒ 统一收口到这里，改一处全站生效。
// ⚠️ 客服微信号属于运营信息（非机密），但仍只在此处维护。
export const SUPPORT_WECHAT = 'poet-1986'

// 开通/续费话术（各处复用，避免文案漂移）
export const OPEN_TIP = '开通 / 续费会员请加客服微信'

// 客服微信二维码（2026-10-05 主人提供的微信名片图）。
//   🔴 文件必须放在 frontend/public/wechat-qr.png（→ 线上 /wechat-qr.png）。
//   · 用 public/ 而非 src/assets：主人随时替换图片即可生效，不需要改代码/重新 import；
//   · 组件 components/ContactQr.vue 对**缺图**做了降级（@error ⇒ 整块不渲染），
//     所以在图片落库之前部署也不会出现裂图，只是不显示二维码。
//   · 替换图片时请保持"白色二维码 + 四周留白"，否则深色主题下边缘可能不干净。
export const SUPPORT_WECHAT_QR = '/wechat-qr.png'
