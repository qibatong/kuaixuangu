<template>
  <!-- 全站顶部导航栏: 左品牌 logo+导航入口, 右主题/字号/账户工具 -->
  <nav class="nav-bar" aria-label="主导航">
    <div class="nav-left">
      <!-- 2026-10-04 主人指令「app端右下角我的去掉」的**配套补偿**：
           底部 tab 没了「我的」⇒ 手机端必须另有账户入口，否则 /member（会员/个人信息/
           修改密码/退出登录）在手机上**无处可进**（该页还有登录守卫，未登录会被卡死）。
           ⇒ 顶部**最左**加「用户中心」：**首字母头像**（主人指定），点进 /member；
             未登录则指向 /login（手机上原「登录/注册」两个按钮也一并收进这个入口）。
           ⚠️ 仅 ≤768px 渲染：桌面端顶部导航仍有「我的」一级分组，不需要重复。 -->
      <router-link
        :to="user.isLoggedIn ? '/member' : '/login'"
        class="nav-user-btn"
        :title="user.isLoggedIn ? '用户中心：' + user.username : '登录 / 注册'"
        :aria-label="user.isLoggedIn ? '用户中心' : '登录'"
      >
        <span v-if="user.isLoggedIn" class="nav-avatar-initial">{{ userInitial }}</span>
        <i v-else class="fa fa-user" aria-hidden="true"></i>
      </router-link>
      <!-- 2026-09-28: 品牌副标题随 tab 改名同步（AI选股 → 竞价选股），避免站点 tooltip 指向不存在的一级概念 -->
      <router-link to="/" class="nav-brand" title="快选 · 竞价选股">
        <img src="/logo.jpg" class="nav-logo" alt="快选">
        <div class="nav-brand-text">
          <span class="nav-brand-name">快选</span>
          <span class="nav-brand-slogan">一键筛选 · 高效复盘</span>
        </div>
      </router-link>
      <!-- 2026-09-27 v4.11.58 信息架构改造(工单 三.2): 9 个平铺 tab → 一级分组
           （v4.11.61 起为 6 个：竞价 / 盘前资讯 / 盘中 / 复盘 / 自选 / 我的）。
           组内二级页由 NavBar 下方的 GroupNav pill 行切换（标了 hidePills 的组没有);
           手机端(≤768px)本块整体隐藏，改用底部 AppTabBar。
           ★ 组定义唯一来源 = composables/useNavGroups.js，勿在此另抄一份。
           ★ 手动 active：/ 作为「竞价」入口时，router-link 自动 active 会前缀匹配全站恒亮。 -->
      <div v-if="user.isLoggedIn" class="nav-tabs">
        <router-link
          v-for="g in NAV_GROUPS"
          :key="g.key"
          :to="g.entry"
          class="nav-item"
          :class="{ active: activeGroup === g.key }"
          active-class=""
          exact-active-class=""
          :title="g.items.map((i) => i.label).join(' · ')"
        >
          <i class="fa" :class="g.icon"></i> {{ g.label }}
        </router-link>
      </div>
      <!-- 2026-10-05 (S1): 未登录（落地页）**不渲染**一级分组 tab —— 它们全部指向需登录的页面,
           点了只会被守卫弹到 /login（死链式点击）。匿名的「登录 / 注册」入口由右侧 .user-tools
           承担（本来就存在），此处不重复给 CTA，避免同一个动作出现两处。
           整块 v-if 而非 CSS 隐藏 ⇒ 落地页不残留无用 DOM。 -->
    </div>

    <div class="nav-tools">
      <!-- 2026-09-27 v4.11.63《快选移动端追加清单》§三: 全局股票快速搜索(桌面入口)。
           手机端(≤768px)本组件自我隐藏，改由底部 AppTabBar 第 7 格承担 ——
           因为 .nav-bar 不 sticky，滚一屏就够不着这里了。 -->
      <StockSearch variant="nav" />
      <!-- 2026-10-04 主人需求: 右上角「系统消息」(系统更新提醒 / 会员到期提醒…)。
           桌面端与手机端**同一个组件**(NoticeBell)，手机端由 CSS 把它顶到栏尾 = 右上角；
           未登录时不渲染(v-if，非 CSS 隐藏) —— 接口必然 401，没必要发。 -->
      <NoticeBell />
      <!-- 🔴 2026-09-27 v4.11.65：已登录的「用户名 + 下拉菜单（我的会员 / 个人信息 / 修改密码 /
           退出登录 / 字号 / 字体族）」**整块从顶部移除**，收进「我的」页
           （views/MemberView.vue 的「账户」卡片）。主人原话：
             「3、首页的用户收进 我的 里面。」+ 澄清「是，从顶部移除、整块收进『我的』」。
           顶部只保留：品牌 / 一级分组 / 全局搜索 / 主题圆点（+ 未登录时的登录·注册）。
           ★ 未登录时**必须**留登录入口 —— /member 有登录守卫，若顶部也不给入口，
             未登录用户将无处可登录。 -->
      <div v-if="!user.isLoggedIn" class="user-tools">
        <router-link to="/login" class="mini-btn login-btn">登录</router-link>
        <!-- 2026-09-21 注册放开: 未登录时暴露注册入口 -->
        <router-link v-if="regOpen" :to="{ path: '/login', query: { mode: 'register' } }" class="mini-btn reg-btn">注册</router-link>
      </div>
    </div>
  </nav>
</template>

<script setup>
// 全站导航栏: 左品牌 + 一级分组入口, 右全局搜索 / 主题圆点
//   ★ 2026-09-27 v4.11.65: 账户区块（用户名下拉 + 我的会员/个人信息/修改密码/退出登录/字号/字体族）
//     已整体迁出到「我的」页 —— 见 views/MemberView.vue 的「账户」卡片。
//     本组件不再 import ChangePwdModal / ProfileModal，也不再持有 useTheme 的 font/fontFam。
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useUserStore } from '../stores/user'
import { NAV_GROUPS, groupKeyOfRoute } from '../composables/useNavGroups'
import StockSearch from './StockSearch.vue'
import NoticeBell from './NoticeBell.vue'
// 2026-10-05 (S1): 未登录顶栏要显示「免费注册 · 领 N 天会员」——N 走匿名公共配置（单例去重）
import { usePublicConfig } from '../composables/usePublicConfig'

const { regOpen, load: loadPublicConfig } = usePublicConfig()

const route = useRoute()
const user = useUserStore()
// 当前路由落在哪个一级分组（竞价 / 盘前资讯 / 盘中 / 复盘 / 自选 / 我的）——顶部一级 tab 高亮依据
const activeGroup = computed(() => groupKeyOfRoute(route))

// 2026-10-04：左上角用户中心的**首字母头像**（主人指定）。
//   · 中文名取首字（"张三" → "张"）；英文/数字取首字母并大写（"jack" → "J"）
//     —— 用 codePointAt 而非 charAt，避免用户名含 emoji/生僻字时取到半个代理对（渲染成乱码方块）。
//   · 空用户名兜底「我」：绝不渲染空白圆块（空白圆块在浅色主题下等于"按钮坏了"）。
const userInitial = computed(() => {
  const u = String(user.username || '').trim()
  if (!u) return '我'
  // 2026-10-05 (M2): 绝大多数用户用**手机号**注册（用户名 = 11 位数字）⇒ 首字符恒为「1」，
  //   配红底圆形后与"未读消息红点"几乎无法区分（生产实测：左上角一个红圈写着 1，用户以为有未读）。
  //   纯数字用户名改取**后两位**（每个账号不同、可辨识），非数字仍取首字符。
  if (/^\d{4,}$/.test(u)) return u.slice(-2)
  const ch = String.fromCodePoint(u.codePointAt(0))
  return /[a-z]/i.test(ch) ? ch.toUpperCase() : ch
})

// 注册入口开关(2026-09-21 放开注册, 由后端 /api/register/config 决定)
// 2026-10-05 (S1): regOpen / giftDays 统一由 usePublicConfig() 提供（模块级单例 + 请求去重）——
//   原先本组件自己 import('../api/auth') 拉一次、落地页又拉一次、登录页再拉一次，
//   同一时刻会重复请求（实测登录页重复 2 次 /api/register/config）。
onMounted(() => {
  if (!user.isLoggedIn) loadPublicConfig()
})
</script>

<style scoped>
.nav-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-lg);
  /* 与 .page-shell 内容对齐: container 4 + 自有 4 = 8px 缩进 */
  padding: var(--s2) var(--s2);
  margin: 0 0 var(--s4);
}
/* 左侧: 品牌 logo + 导航入口 */
.nav-left { display: flex; align-items: center; gap: var(--s3); flex-wrap: wrap; min-width: 0; }
.nav-brand {
  display: inline-flex; align-items: center; gap: var(--s2);
  text-decoration: none; padding: 2px var(--s2) 2px var(--s1);
  border-right: 1px solid var(--border-soft);
}
.nav-logo {
  width: 34px; height: 34px;
  border-radius: var(--r-md); object-fit: cover;
  display: block;
}
.nav-brand-name {
  font-size: var(--fs-xl); font-weight: 700;
  color: var(--accent); letter-spacing: 10px;
  line-height: 1.1;
  padding-left: var(--s1); /* 补偿 letter-spacing 末尾 10px 空白, 让"快选"视觉中点 = 几何中点 */
  margin-left: var(--s1); /* 2026-08-17 主人反馈"往右边移一点点": 整体右移 4px */
}
.nav-brand-text { display: flex; flex-direction: column; align-items: center; gap: var(--s1); line-height: 1.1; }
.nav-brand-slogan {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  opacity: 0.85;
  white-space: nowrap;
  letter-spacing: 0.5px;
  line-height: 1.1;
}
.nav-tabs { display: flex; align-items: center; gap: var(--s2); flex-wrap: wrap; }
.nav-tools { display: flex; align-items: center; gap: var(--s2); flex-wrap: wrap; }
.nav-item {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s2) var(--s4);
  border-radius: var(--r-md);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  text-decoration: none;
  border: 1px solid transparent;
  /* 2026-08-17 主人反馈: 非选中态完全透明像普通文字, 看上去不可点击.
     加淡背景让所有 tab 看上去都是「按钮胶囊」, active 态用橙高亮区分 */
  background: rgba(255, 255, 255, 0.04);
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.nav-item:hover { background: var(--bg-hover); color: var(--text-main); }
/* 白色主题: 克制导航栏(2026-09-21 主人要求去红渐变, 改白底细边;
   品牌红仅保留在激活 tab 文字/边框作点缀, 不再大面积铺红) */
body[data-bg="light"] .nav-bar {
  background: #ffffff;
  border-color: #d9dde5;
  box-shadow: 0 1px 4px rgba(30, 40, 60, 0.06);
}
body[data-bg="light"] .nav-brand-name { color: #1a1d26; }
/* 2026-10-05 (S2): 原 #8a8f9c 在白底仅 3.24:1(不达 AA) → 改用 --text-muted(#6b7280, 4.83:1) */
body[data-bg="light"] .nav-brand-slogan { color: var(--text-muted); }
body[data-bg="light"] .nav-brand { border-right-color: #e3e6ec; }
body[data-bg="light"] .nav-item {
  background: #f2f4f8;
  color: #3a3f4c;
  border-color: #e0e3ea;
}
body[data-bg="light"] .nav-item:hover { background: #e8ebf1; color: #1a1d26; }
body[data-bg="light"] .nav-item.active {
  background: #fff;
  color: #c62828;
  border-color: rgba(198, 40, 40, 0.45);
  font-weight: 700;
}
body[data-bg="light"] .mini-btn {
  background: #f2f4f8;
  border-color: #e0e3ea;
  color: #3a3f4c;
}
body[data-bg="light"] .mini-btn:hover { background: #e8ebf1; color: #1a1d26; }
.nav-item.active {
  background: rgba(255, 180, 0, 0.15);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}

/* 账户工具（仅未登录时渲染：登录 / 注册） */
.user-tools { display: flex; align-items: center; gap: var(--s2); }
/* 🔴 2026-09-27 v4.11.65：此处原有约 130 行「用户名按钮 + Teleport 到 body 的下拉菜单
   （我的会员 / 个人信息 / 修改密码 / 退出登录 / 字号 / 字体族）」样式
   （.user-dropdown / .user-name-btn / .caret-up / .user-menu / .menu-* / .menu-dot），
   随该 UI 一起**整块删除**，对应样式已重写为「我的」页的 scoped 样式（views/MemberView.vue）。
   纪律：UI 迁走时同步删样式，不留引用不到的死 CSS（否则下次改样式会改到"看不见的副本"）。 */
.mini-btn {
  background: var(--bg-input); border: 1px solid var(--border-soft);
  color: var(--text-secondary); border-radius: var(--r-md);
  font-size: var(--fs-xs); padding: var(--s1) var(--s2); cursor: pointer;
  text-decoration: none; transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.mini-btn:hover { background: var(--bg-hover); color: var(--text-main); border-color: var(--accent); }
.login-btn { color: var(--accent); border-color: var(--accent); }
.reg-btn { color: #fff; background: var(--accent-solid); border-color: var(--accent); }
.reg-btn:hover { background: var(--accent-solid); color: #fff; filter: brightness(1.1); }

/* 会员等级徽标（.member-badge / .vip-badge / .paid-badge / .admin-badge / .trial-badge /
   .renew-badge）2026-09-27 v4.11.65 随账户区块一并迁到 views/MemberView.vue 的 scoped 样式，
   本文件已无引用，故整块删除。 */

/* 浅色主题高亮(红色导航栏已在上方统一处理 router-link-active 白底红字) */

/* ===================== 左上角「用户中心」（2026-10-04） =====================
   底部「我的」tab 去掉后，手机端唯一的账户入口。**桌面端不渲染**（顶部有「我的」一级分组）。
   配色走 accent 系 token（浅底 + 深色字），深浅两套主题都可读。 */
.nav-user-btn {
  display: none;                 /* 桌面隐藏；≤768px 内改 flex */
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--accent-bg2);
  border: 1px solid var(--accent-border);
  color: var(--accent-text);
  font-size: var(--fs-base);
  font-weight: 600;
  text-decoration: none;
  line-height: 1;
}
.nav-user-btn:active { transform: scale(0.92); }        /* 与底部 tabbar 一致的点击反馈 */
.nav-avatar-initial { display: block; user-select: none; }

/* ===================== 移动端适配 (<=768px) ===================== */
@media (max-width: 768px) {
  /* 2026-10-04 主人要求「搜索栏居中」: 顶栏改**三列栅格** —— 左右两列都是 1fr(等宽),
     中间一列自然落在屏幕正中 —— 即便头像(38px)与信封(约36px)宽度不等也不会把搜索挤偏。
     .nav-tools 用 display:contents, 让搜索框和信封"提升"为栅格项(分别进第2、3列);
     中间列宽度 = min(230px, 100%-120px): 230 是视觉上限, 120 保证两侧至少各留 60px 给头像/信封。 */
  .nav-bar {
    display: grid;
    grid-template-columns: 1fr min(230px, calc(100% - 120px)) 1fr;
    column-gap: var(--s2);
    align-items: center;
    padding: var(--s2) var(--s2);
    margin-bottom: var(--s2);
  }
  .nav-left { gap: var(--s2); width: auto; flex: 0 0 auto; justify-self: start; }
  .nav-brand { display: none; }
  .nav-tools { display: contents; }
  /* 信封进第 3 列并贴右头像列对称(DOM 顺序本来就在搜索之后, order 可省) */
  .nav-tools :deep(.notice-bell) { justify-self: end; }
  /* 栅格项: 占满中间那一列(flex 属性在栅格里无效) */
  .nav-tools :deep(.ss-root--nav) { width: 100%; min-width: 0; }
  .nav-tools :deep(.ss-inline) { flex: 1 1 auto; min-width: 0; }
  .nav-tools :deep(.ss-inline-input),
  .nav-tools :deep(.ss-inline-input:focus) { width: 100%; min-width: 0; }
  /* 未登录时的「登录/注册」按钮在手机上隐藏 —— 左上角头像已指向 /login，留着是重复且挤 */
  .user-tools { display: none; }
  /* 用户中心头像在手机端显示（它是底部「我的」tab 的替代入口）。
     2026-10-04 参考开盘啦 App 顶栏：入口加大到 38px 圆钮（32px 点击目标偏小） */
  .nav-user-btn { display: flex; width: 38px; height: 38px; font-size: var(--fs-lg); }
  /* 2026-10-04 参考开盘啦 App 顶栏：搜索框改**全圆角胶囊** + 提亮底色
     （原 --bg-input 深黑底几乎融进顶栏）；浅色主题反向压暗保持可读 */
  .nav-tools :deep(.ss-inline) {
    border-radius: var(--r-pill);
    background: rgba(255, 255, 255, 0.13);
    border-color: rgba(255, 255, 255, 0.10);
    padding: 0 var(--s3);
    height: 40px;   /* 2026-10-04 顶栏竖向拉长后, 36→40 胶囊同步加高更饱满 */
  }
  .nav-tools :deep(.ss-inline-input) { font-size: var(--fs-base); }
  body[data-bg="light"] .nav-tools :deep(.ss-inline) {
    background: rgba(0, 0, 0, 0.05);
    border-color: rgba(0, 0, 0, 0.10);
  }
  .nav-brand { gap: var(--s1); padding: 0 var(--s2) 0 2px; }
  .nav-logo { width: 26px; height: 26px; border-radius: var(--r-md); }
  .nav-brand-name { font-size: var(--fs-base); }
  /* 2026-10-05：把副标语的**手机端字号预先写好**（10px），供"哪天恢复手机端品牌块"时直接生效。
     🔴 但必须讲清楚：**这条规则今天是惰性的** —— 上面第 281 行 `.nav-brand { display: none }`
       （2026-10-04 主人要求「搜索栏居中」⇒ 顶栏改三列等宽栅格，左列只留头像，**整个品牌块
       logo + 「快选」+ 标语一起让位**）把父节点藏掉了。只改标语自己的 display/size 是没用的：
       实测 390px 下 `.nav-brand` 与 `.nav-brand-slogan` 的盒子都是 0×0。
       ⇒ 真要在手机端显示它，动的是**顶栏布局**（会与「搜索居中」冲突），需要主人拍板。
     ✅ 桌面/平板（>768px）不受影响：实测 1440 / 1024 / 834 宽 × 深/浅两主题**均可见**
        （深色 #9a9a9a、浅色 #6b7280，112×13）。 */
  .nav-brand-slogan { font-size: 10px; letter-spacing: 0.2px; opacity: 0.9; }

  /* ===== 2026-10-04 主人需求(参考开盘啦 App 截图): 手机端浅色模式顶栏改**品牌红**，
     与系统状态栏连成一体(状态栏红条见 main.css 的 body::before，iOS PWA
     black-translucent 内容顶到状态栏下，正好露红)。
     · 通栏：负 margin 抵消 body 的 4px 左右内边距 ⇒ 左右贴屏；去圆角/边框/阴影。
     · 桌面端**不动** —— 2026-09-21 主人拍板过「白底细边、红色只作点缀」，
       大面积铺红只发生在手机端浅色主题。 */
  body[data-bg="light"] .nav-bar {
    /* 2026-10-04 二次反馈: #c62828 太深, 调浅为同花顺红 #e5484d */
    background: var(--accent-solid);
    border-color: transparent;
    border-radius: 0;
    box-shadow: none;
    margin: 0 -var(--s1) var(--s2);
    /* 竖向拉长(主人二次反馈, 参考同花顺/开盘啦的顶栏高度): 上下 6px → 13px */
    padding: var(--s3) var(--s2);
  }
  /* 搜索胶囊恢复**原始尺寸**：不再 flex 吃光剩余宽度，限宽 230px 居其位，
     多出的横向空间留给红底留白（对照开盘啦：胶囊不大，四周都是红）。 */
  .nav-tools :deep(.ss-inline) { max-width: 230px; }
  body[data-bg="light"] .nav-brand-name,
  body[data-bg="light"] .nav-item { color: #fff; }
  /* 头像在红底上改白底红字(对比最强, 对照开盘啦的金色头像圈) */
  body[data-bg="light"] .nav-user-btn {
    background: #fff;
    border-color: rgba(255, 255, 255, 0.9);
    color: var(--accent);
  }
  /* 右上角信封(NoticeBell 是子组件, :deep 穿透)在红底上改透明底白描边 ——
     🔴 必须覆盖 background：它自身是 var(--bg-panel-solid)(浅色=白)，
     红底白底白字 = 信封图标隐形，只剩一个"白圆"(主人截图实测)。 */
  body[data-bg="light"] .nav-tools :deep(.notice-bell) {
    background: transparent;
    color: #fff;
    border-color: rgba(255, 255, 255, 0.55);
  }
  /* 未登录的登录/注册小钮(桌面显示, 手机隐藏 —— 保险起见红底适配) */
  body[data-bg="light"] .mini-btn {
    background: rgba(255, 255, 255, 0.92);
    border-color: transparent;
    color: #b71c1c;
  }
  /* 红底上搜索胶囊改**白底深红字**(对照开盘啦: 红栏内浅胶囊)；
     原浅色覆盖(rgba(0,0,0,0.05) 压暗底)是给白顶栏用的, 红顶栏下必须反转。 */
  body[data-bg="light"] .nav-tools :deep(.ss-inline) {
    background: rgba(255, 255, 255, 0.94);
    border-color: transparent;
  }
  body[data-bg="light"] .nav-tools :deep(.ss-inline-input) { color: #4a1515; }
  body[data-bg="light"] .nav-tools :deep(.ss-inline-input)::placeholder { color: #c98a8a; }
  body[data-bg="light"] .nav-tools :deep(.ss-inline-icon),
  body[data-bg="light"] .nav-tools :deep(.ss-inline-clear) { color: #b83010; }
  /* 2026-09-27 v4.11.58 信息架构改造: 手机端**隐藏**顶部一级分组导航,
     改由底部固定 AppTabBar(6 tab) 承担一级分组切换, 组内二级页用 GroupNav pill 换行/横滑。
     原先「9 个 tab 自动换行」的老行为不再需要。 */
  .nav-tabs { display: none; }
  /* 工具区(2026-10-04): 顶栏只剩 用户钮/搜索/信封 三件套，放得下 ⇒ 不再换行
     （旧规则 flex-wrap:wrap 会把信封挤到第二行被裁掉，实测截图确认过） */
  .nav-tools { gap: var(--s2); flex-wrap: nowrap; overflow: visible; max-width: 100%; }
  .nav-tools::-webkit-scrollbar { display: none; }
  .mini-btn { padding: var(--s1) var(--s2); font-size: var(--fs-xs); white-space: nowrap; }
}
</style>