<template>
  <div class="admin-wrap">
    <div class="admin-head">
      <router-link to="/" class="tdx-export-btn nav-btn nav-pool-import"><i class="fa fa-arrow-left"></i> 返回选股</router-link>
      <h2 style="margin:0 auto;color:var(--accent-text);"><i class="fa fa-shield"></i> 管理后台</h2>
      <span style="width:120px;"></span>
    </div>

    <!-- 403 提示 -->
    <div v-if="denied" class="admin-card" style="text-align:center;padding:40px;color:#ff9a9a;">
      <i class="fa fa-lock" style="font-size:36px;"></i>
      <p style="margin-top:12px;">无管理员权限，请联系管理员开通</p>
      <router-link to="/" class="tdx-export-btn nav-btn nav-history">返回主页</router-link>
    </div>

    <template v-else>
      <!-- 用户统计卡片 -->
      <StatCards :stats="stats" />

      <!-- 用户列表 -->
      <div class="admin-card">
        <div class="card-title">
<i class="fa fa-users"></i> 用户列表
          <div style="display:flex;gap:8px;margin-left:auto;align-items:center;">
            <!-- 会员筛选 tab -->
            <div class="member-tabs">
              <button v-for="t in memberTabs" :key="t.key" class="member-tab"
                      :class="{ active: memberTab === t.key }" @click="setMemberTab(t.key)">{{ t.label }}</button>
            </div>
            <input v-model="keyword" class="admin-input" placeholder="搜用户名/手机/邮箱/微信名/备注" @keyup.enter="loadUsers(1)" />
            <button class="admin-search-btn" @click="loadUsers(1)"><i class="fa fa-search"></i> 搜索</button>
            <button class="admin-search-btn" style="background:rgba(255,160,40,0.15);border-color:#ffa028;color:#ffa028;" :disabled="!selectedIds.size" @click="openBatchExpire">
              <i class="fa fa-clock-o"></i> 批量设到期{{ selectedIds.size ? ' (' + selectedIds.size + ')' : '' }}
            </button>
            <button class="admin-search-btn" style="background:rgba(0,200,120,0.15);border-color:#00c878;color:#80ffaa;" @click="openCreate()"><i class="fa fa-plus"></i> 新建会员</button>
          </div>
        </div>
        <div class="table-scroll">
          <table class="admin-table">
            <thead>
              <tr>
                <th style="width:30px;"><input type="checkbox" :checked="pageAllChecked" @change="togglePageAll" title="全选本页" /></th>
                <th class="sortable" :class="{ active: userSort.keyOf('id') }" @click="userSort.onSort('id')">ID<span class="sort-ind">{{ userSort.ind('id') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('username') }" @click="userSort.onSort('username', 'string')">用户名<span class="sort-ind">{{ userSort.ind('username') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('wx_name') }" @click="userSort.onSort('wx_name', 'string')">微信名<span class="sort-ind">{{ userSort.ind('wx_name') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('phone') }" @click="userSort.onSort('phone', 'string')">手机/邮箱<span class="sort-ind">{{ userSort.ind('phone') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('remark') }" @click="userSort.onSort('remark', 'string')">备注<span class="sort-ind">{{ userSort.ind('remark') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('created_at') }" @click="userSort.onSort('created_at')">注册时间<span class="sort-ind">{{ userSort.ind('created_at') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('expire_at') }" @click="userSort.onSort('expire_at')">到期时间<span class="sort-ind">{{ userSort.ind('expire_at') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('invited_count') }" @click="userSort.onSort('invited_count')">邀请<span class="sort-ind">{{ userSort.ind('invited_count') }}</span></th>
                <th class="sortable" :class="{ active: userSort.keyOf('batch_count') }" @click="userSort.onSort('batch_count')">选股<span class="sort-ind">{{ userSort.ind('batch_count') }}</span></th>
                <th>会员等级</th>
                <th style="min-width:70px;">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in userSort.sorted(rows, userVal)" :key="u.id">
                <td><input type="checkbox" v-model="selectedIds" :value="u.id" :disabled="u.is_admin" :title="u.is_admin ? '管理员不可批量操作' : ''" /></td>
                <td>{{ u.id }}</td>
                <td>{{ u.username }}</td>
                <td>{{ u.wx_name || '-' }}</td>
                <td>{{ u.phone || u.email || '-' }}</td>
                <td>
                  <span v-if="u.remark" class="cell-note" :title="u.remark">{{ u.remark }}</span>
                  <span v-else>-</span>
                  <span v-if="u.pay_remark" class="cell-pay" :title="u.pay_remark">💰 {{ u.pay_remark }}</span>
                </td>
                <td>{{ fmtTsTime(u.created_at) }}</td>
                <td>
                  <!-- VIP(member_level=2) 默认按 expire_at 显示; 仅管理员/expire_at=0 显示永久 -->
                  <span v-if="u.is_admin || expireState(u) === 'forever'" class="user-tag">永久</span>
                  <span v-else-if="expireState(u) === 'expired'" class="expired-tag">已过期 {{ fmtBjDay(u.expire_at) }}</span>
                  <span v-else class="ok-tag">{{ fmtBjDay(u.expire_at) }}</span>
                </td>
                <td>{{ u.invited_count }}</td>
                <td>{{ u.batch_count }}</td>
                <td>
                  <!-- 管理员固定, 普通用户/付费/VIP 可下拉修改 -->
                  <span v-if="u.is_admin" class="role-badge role-admin">管理员</span>
                  <select v-else v-model.number="u._level" class="level-select" @change="setLevel(u)" title="修改会员等级">
                    <option :value="0">免费试用</option>
                    <option :value="1">付费会员</option>
                    <option :value="2">VIP</option>
                  </select>
                </td>
                <td>
                  <div class="row-actions">
                    <!-- ⋮ 操作下拉(teleport 到 body 避免短表格时溢出覆盖搜索栏) -->
                    <button class="mini-btn" @click.stop="toggleMenu(u, $event)">⋮</button>
                  </div>
                </td>
              </tr>
              <tr v-if="!rows.length"><td colspan="12" style="text-align:center;color:#888;padding:20px;">暂无用户</td></tr>
            </tbody>
          </table>
        </div>
        <div class="pager">
          <div class="pager-left">
            <span style="color:#888;font-size:12px;">每页</span>
            <select v-model.number="pageSize" class="admin-input" style="width:70px;padding:5px 8px;" @change="changePageSize">
              <option :value="10">10</option>
              <option :value="20">20</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
            </select>
            <span style="color:#888;font-size:12px;">条</span>
          </div>
          <div class="pager-right">
            <button class="page-btn" :disabled="page <= 1" @click="loadUsers(page - 1)">上一页</button>
            <span style="color:#bbb;">第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 人</span>
            <button class="page-btn" :disabled="page >= totalPages" @click="loadUsers(page + 1)">下一页</button>
          </div>
        </div>
      </div>

      <!-- 重置密码弹层 -->
      <div v-if="pwdTarget" class="pwd-mask" @click.self="closePwdReset">
        <div class="pwd-pop">
          <div class="pwd-title">🔑 重置密码：{{ pwdTarget.username }}<span style="color:#999;font-size:12px;margin-left:8px;">({{ pwdTarget.phone || pwdTarget.email || '-' }})</span></div>
          <input v-model="pwdNew" type="text" class="admin-input" style="width:100%;box-sizing:border-box;" placeholder="输入新密码(至少6位)" @keyup.enter="doResetPwd" />
          <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
            <button class="mini-btn" @click="closePwdReset">取消</button>
            <button class="mini-btn danger" :disabled="pwdSaving" @click="doResetPwd">{{ pwdSaving ? '重置中...' : '确认重置' }}</button>
          </div>
        </div>
      </div>

      <!-- 创建会员弹层(管理员代创建账号) -->
      <div v-if="createOpen" class="pwd-mask" @click.self="closeCreate">
        <div class="pwd-pop" style="width:480px;">
          <div class="pwd-title">➕ 新建会员账号</div>
          <div class="profile-grid">
            <label class="profile-label">用户名 <span style="color:#ff6a6a;">*</span>
              <input v-model="createForm.username" type="text" maxlength="20" class="admin-input" placeholder="2-20 位, 中英文/数字/下划线" /></label>
            <label class="profile-label">初始密码 <span style="color:#ff6a6a;">*</span>
              <input v-model="createForm.password" type="text" maxlength="32" class="admin-input" placeholder="至少 6 位 (建议告知用户)" /></label>
            <label class="profile-label">手机号 <span style="color:#ff6a6a;">*</span>
              <input v-model="createForm.phone" type="text" maxlength="11" class="admin-input" placeholder="11 位手机号" /></label>
            <label class="profile-label">邮箱 <span style="color:#ff6a6a;">*</span>
              <input v-model="createForm.email" type="text" maxlength="60" class="admin-input" placeholder="用于找回密码" /></label>
            <label class="profile-label">会员等级
              <select v-model.number="createForm.member_level" class="admin-input">
                <option :value="0">免费试用</option>
                <option :value="1">付费会员</option>
                <option :value="2">VIP</option>
              </select></label>
            <label class="profile-label">到期日
              <input v-model="createForm.expire_at" type="date" class="admin-input" :min="todayStr" /></label>
            <label class="profile-label">微信名
              <input v-model="createForm.wx_name" type="text" maxlength="40" class="admin-input" placeholder="联系用微信昵称" /></label>
            <label class="profile-label">管理员备注
              <input v-model="createForm.remark" type="text" maxlength="200" class="admin-input" placeholder="仅管理端可见" /></label>
            <label class="profile-label profile-pay" style="grid-column:1 / -1;">付款备注 (月费用户必填)
              <textarea v-model="createForm.pay_remark" maxlength="500" rows="2" class="admin-input"
                        placeholder="例: 8-16 微信月付 300元; 下次续费 9-16"></textarea></label>
          </div>
          <div class="profile-tip">带 * 的字段必填; 创建成功后会显示初始密码, 请告知用户</div>
          <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
            <button class="mini-btn" @click="closeCreate">取消</button>
            <button class="mini-btn danger" :disabled="createSaving" @click="doCreate">{{ createSaving ? '创建中...' : '创建账号' }}</button>
          </div>
          <!-- 创建成功结果展示 -->
          <div v-if="createResult" class="create-result">
            <div><b>{{ createResult.username }}</b> 创建成功!</div>
            <div style="margin-top:6px;font-size:12px;color:#999;">
              ID {{ createResult.uid }} · 到期 {{ fmtBjDay(createResult.expire_at) }} · 等级 {{ levelLabel(createResult.member_level) }}
            </div>
            <div style="margin-top:8px;padding:8px;background:rgba(255,200,80,0.15);border:1px solid #ffc850;border-radius:6px;color:#ffe0a0;">
              初始密码: <b style="user-select:all;font-family:monospace;">{{ createResult.password }}</b>
              <button class="mini-btn" style="margin-left:8px;" @click="copyText(createResult.password)">复制</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 编辑资料弹层(管理员代设置手机/邮箱/微信名/备注/付款备注) -->
      <div v-if="profileTarget" class="pwd-mask" @click.self="closeProfile">
        <div class="pwd-pop" style="width:560px;">
          <div class="pwd-title">📝 编辑资料：{{ profileTarget.username }}
            <span style="color:#999;font-size:12px;margin-left:8px;">(ID {{ profileTarget.id }})</span></div>
          <div class="profile-grid">
            <label class="profile-label">手机号
              <input v-model="profileForm.phone" type="text" maxlength="11" class="admin-input admin-input-lg" placeholder="11 位手机号" /></label>
            <label class="profile-label">邮箱
              <input v-model="profileForm.email" type="text" maxlength="60" class="admin-input admin-input-lg" placeholder="用于找回密码" /></label>
            <label class="profile-label">微信名
              <input v-model="profileForm.wx_name" type="text" maxlength="40" class="admin-input admin-input-lg" placeholder="联系用微信昵称" /></label>
            <label class="profile-label">管理员备注
              <input v-model="profileForm.remark" type="text" maxlength="200" class="admin-input admin-input-lg" placeholder="仅管理端可见" /></label>
            <label class="profile-label profile-pay" style="grid-column:1 / -1;">付款备注 (会员专属, 仅管理员可改)
              <textarea v-model="profileForm.pay_remark" maxlength="500" rows="3" class="admin-input admin-input-lg"
                        placeholder="例: 8-16微信月付300元; 到期 9-16 自动提醒续费"></textarea></label>
          </div>
          <div class="profile-tip">留空表示不修改该字段；手机号/邮箱有格式+唯一性校验</div>
          <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
            <button class="mini-btn" @click="closeProfile">取消</button>
            <button class="mini-btn danger" :disabled="profileSaving" @click="saveProfile">{{ profileSaving ? '保存中...' : '保存资料' }}</button>
          </div>
        </div>
      </div>

      <!-- 批量设置到期弹层(2026-08-17 主人需求: 多选用户统一设置会员时间) -->
      <div v-if="batchExpireOpen" class="pwd-mask" @click.self="closeBatchExpire">
        <div class="pwd-pop" style="width:480px;">
          <div class="pwd-title">⏰ 批量设置到期：已选 {{ selectedIds.size }} 人</div>
          <div class="batch-expire-box">
            <div class="pop-label">延长时长(在现有到期上累加)</div>
            <div class="pop-row" style="flex-wrap:wrap;">
              <button class="mini-btn batch-exp-btn" @click="batchExtend('week')">+1周</button>
              <button class="mini-btn batch-exp-btn" @click="batchExtend('month')">+1月</button>
              <button class="mini-btn batch-exp-btn" @click="batchExtend('quarter')">+1季</button>
              <button class="mini-btn batch-exp-btn" @click="batchExtend('year')">+1年</button>
            </div>
            <div class="pop-label" style="margin-top:12px;">自定义到期日(统一设为该日)</div>
            <div class="pop-row">
              <input v-model="batchExpireDate" type="date" class="mini-date" style="flex:1;padding:8px 12px;font-size:14px;" :max="'2099-12-31'" />
              <button class="mini-btn batch-exp-btn" @click="batchSetDate">设为该日</button>
            </div>
            <div class="pop-row" style="margin-top:12px;">
              <button class="mini-btn batch-exp-btn batch-forever" @click="batchForever">设为永久</button>
            </div>
            <div class="profile-tip">被选中的管理员账号已自动排除</div>
          </div>
          <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
            <button class="mini-btn" @click="closeBatchExpire">取消</button>
            <button class="mini-btn danger" :disabled="batchSaving" @click="batchExpire">{{ batchSaving ? '设置中...' : '确认批量设置' }}</button>
          </div>
        </div>
      </div>

      <!-- 评分权重配置(竞价, 盘中已合并为同一套逻辑) -->
      <div class="admin-card">
        <div class="card-title">
          <i class="fa fa-sliders"></i> 竞价评分·权重配置
          <span class="admin-tip">保存后立即生效（影响后续选股评分）</span>
        </div>
        <div class="table-scroll">
        <table class="admin-table weight-table">
          <thead><tr><th style="width:140px;">因子</th><th>权重(0~1)</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="wk in wKeys" :key="wk[0]">
              <td>{{ wk[1] }}</td>
              <td><input v-model.number="scoring[wk[0]]" type="number" step="0.01" min="0" max="1" class="admin-input" style="width:90px;" /></td>
              <td class="weight-desc">{{ wk[2] }}</td>
            </tr>
            <tr>
              <td colspan="2" class="weight-total">权重合计：{{ weightSum.toFixed(2) }} <span v-if="Math.abs(weightSum - 1) > 0.03" class="weight-warn">（需约等于 1 才能保存）</span></td>
              <td></td>
            </tr>
            <tr v-for="ck in confKeys" :key="ck[0]">
              <td>置信度·{{ ck[1] }}</td>
              <td><input v-model.number="scoring[ck[0]]" type="number" step="1" min="0" max="30" class="admin-input" style="width:90px;" /></td>
              <td class="weight-desc">{{ ck[2] }}</td>
            </tr>
          </tbody>
        </table>
        </div>
        <div style="display:flex;gap:10px;margin-top:14px;align-items:center;">
          <button class="tdx-export-btn admin-save-btn" :disabled="saving" @click="saveScoring">
            <i class="fa fa-save"></i> {{ saving ? '保存中...' : '保存并生效' }}
          </button>
          <span v-if="saveMsg" :class="saveErr ? 'admin-msg-err' : 'admin-msg-ok'">{{ saveMsg }}</span>
        </div>
      </div>

      <!-- 打分明细配置(因子 Tab 切换) -->
      <div class="admin-card">
        <div class="card-title"><i class="fa fa-table"></i> 竞价评分·打分明细 <span class="admin-tip">命中区间 [下限, 上限) 得对应分，未命中取默认分</span></div>
        <div class="factor-tabs">
          <button v-for="fk in factorOrder" :key="fk" class="factor-tab" :class="{ active: activeFactor === fk }" @click="activeFactor = fk">
            {{ factors[fk] ? factors[fk].label : fk }}
          </button>
        </div>
        <div v-if="factors[activeFactor] && factors[activeFactor].buckets" class="factor-box">
          <div class="factor-title">
{{ factors[activeFactor].label }} <span style="color:#888;font-size:12px;">（{{ factors[activeFactor].unit }}）</span>
            <span style="margin-left:auto;display:flex;align-items:center;gap:6px;">
              默认分 <input v-model.number="factors[activeFactor].default" type="number" step="0.05" min="0" max="1" class="admin-input" style="width:70px;" />
            </span>
          </div>
          <div class="table-scroll">
          <table class="admin-table bucket-table">
            <thead><tr><th style="width:120px;">下限</th><th style="width:120px;">上限</th><th>得分(0~1)</th><th style="width:70px;"></th></tr></thead>
            <tbody>
              <tr v-for="(b, idx) in factors[activeFactor].buckets" :key="idx">
                <td><input v-model.number="b[0]" type="number" step="0.1" class="admin-input" style="width:100px;" /></td>
                <td><input v-model.number="b[1]" type="number" step="0.1" class="admin-input" style="width:100px;" /></td>
                <td><input v-model.number="b[2]" type="number" step="0.05" min="0" max="1" class="admin-input" style="width:100px;" /></td>
                <td><button class="del-btn" @click="delBucket(activeFactor, idx)"><i class="fa fa-trash-o"></i></button></td>
              </tr>
              <tr><td colspan="4"><button class="add-btn" @click="addBucket(activeFactor)"><i class="fa fa-plus"></i> 新增分档</button></td></tr>
            </tbody>
          </table>
          </div>
        </div>
        <div v-else class="empty-state" style="padding:16px;">该因子暂未加载</div>
      </div>

      <!-- 全局默认筛选参数(所有用户未自定义时使用) -->
      <div class="admin-card">
        <div class="card-title"><i class="fa fa-filter"></i> 全局默认筛选参数 <span class="admin-tip">所有用户未自定义偏好时的默认值；「保存并强制生效」会清除所有用户已保存的筛选偏好(保留背景/字号)，全量立即生效</span></div>
        <div style="display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;padding:6px 0 2px;">
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            竞价金额下限(万) <input v-model.number="adminDefaults.bidAmtFloor" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            竞价涨幅上限(%) <input v-model.number="adminDefaults.bidGt" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            流通市值下限(亿) <input v-model.number="adminDefaults.floatMvFloor" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            流通市值上限(亿) <input v-model.number="adminDefaults.floatMvGt" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            股价上限(元) <input v-model.number="adminDefaults.priceGt" type="number" min="0" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;flex-direction:column;gap:4px;">
            评分下限 <input v-model.number="adminDefaults.probLt" type="number" min="0" max="100" class="admin-input" style="width:110px;" />
          </label>
          <label class="field-label" style="display:flex;align-items:center;gap:6px;font-size:13px;">
            <input v-model="adminDefaults.limitUp" type="checkbox" /> 剔除昨日涨停
          </label>
          <label class="field-label" style="display:flex;align-items:center;gap:6px;font-size:13px;">
            <input v-model="adminDefaults.stSuspend" type="checkbox" /> 剔除ST/停牌
          </label>
          <button class="tdx-export-btn admin-save-btn" :disabled="savingDefaults" @click="saveDefaults(false)">
            <i class="fa fa-save"></i> {{ savingDefaults ? '保存中...' : '保存默认值' }}
          </button>
          <button class="tdx-export-btn admin-save-btn save-force-btn" :disabled="savingDefaultsForce" @click="saveDefaults(true)">
            <i class="fa fa-bolt"></i> {{ savingDefaultsForce ? '生效中...' : '保存并强制生效' }}
          </button>
          <span v-if="defaultsMsg" :class="defaultsErr ? 'admin-msg-err' : 'admin-msg-ok'" style="font-size:12px;">{{ defaultsMsg }}</span>
        </div>
      </div>

      <!-- 历史竞价回放 -->
      <PlaybackCard />

      <!-- ⋮ 操作下拉/期限面板 (teleport 到 body, fixed 定位跟随触发按钮; 短表格不溢出覆盖搜索栏) -->
      <Teleport to="body">
        <div v-if="menuUid !== null && menuRect" class="row-menu"
             :style="{ position:'fixed', top: menuRect.top+'px', left: menuRect.left+'px' }"
             @click.stop>
          <button class="row-menu-item" @click="menuAction(currentMenuUser, 'profile')">📝 编辑资料</button>
          <template v-if="currentMenuUser && !currentMenuUser.is_admin">
            <button class="row-menu-item" @click="menuAction(currentMenuUser, 'expire')">⚙️ 设置期限</button>
            <button class="row-menu-item" @click="menuAction(currentMenuUser, 'pwd')">🔑 重置密码</button>
            <button class="row-menu-item row-menu-danger" @click="menuAction(currentMenuUser, 'delete')">🗑️ 删除</button>
          </template>
          <div v-if="menuSection === 'expire'" class="row-menu-expire" @click.stop>
            <div class="pop-label">延长时长</div>
            <div class="pop-row">
              <button class="mini-btn" @click="extendUser(currentMenuUser, 'week')">+1周</button>
              <button class="mini-btn" @click="extendUser(currentMenuUser, 'month')">+1月</button>
              <button class="mini-btn" @click="extendUser(currentMenuUser, 'quarter')">+1季</button>
              <button class="mini-btn" @click="extendUser(currentMenuUser, 'year')">+1年</button>
            </div>
            <div class="pop-label">自定义到期日</div>
            <div class="pop-row">
              <input v-model="currentMenuUser._expireDate" type="date" class="mini-date" :max="'2099-12-31'" />
              <button class="mini-btn" @click="extendUser(currentMenuUser, 'date')">设为该日</button>
            </div>
            <div class="pop-row">
              <button class="mini-btn danger" title="永久有效" @click="extendUser(currentMenuUser, 'forever')">设为永久</button>
            </div>
          </div>
        </div>
      </Teleport>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { adminCreateUser, adminDeleteUser, adminScoring, adminSetMemberLevel, adminSetUserProfile, adminUsers, getAdminDefaults, resetUserPassword, saveAdminDefaults, saveScoring as apiSaveScoring, setUserExpire, setUsersExpire } from '../api/admin'
import { showToast as toast } from '../utils/toast'
import { useSortable } from '../composables/useSortable'
import { expireState } from '../utils/admin'
import { fmtBjDay, fmtTsTime } from '../utils/time'
import StatCards from '../components/admin/StatCards.vue'
import PlaybackCard from '../components/admin/PlaybackCard.vue'

// 表格排序实例(用户列表)
const userSort = useSortable()

// 用户表取值函数: "手机/邮箱"列实际存 phone 或 email, 需合并取
function userVal(u) {
  const k = userSort.sortState.value ? userSort.sortState.value.key : null
  if (k === 'phone') return u.phone || u.email || ''
  return u[k]
}

// 全局默认筛选参数
const adminDefaults = reactive({ bidAmtFloor: 1000, bidGt: 7, floatMvFloor: 30, floatMvGt: 1000, priceGt: 300, probLt: 65, limitUp: true, stSuspend: true })
const savingDefaults = ref(false)
const savingDefaultsForce = ref(false)
const defaultsMsg = ref('')
const defaultsErr = ref(false)

async function loadDefaults() {
  try {
    const d = await getAdminDefaults()
    if (d.defaults) Object.assign(adminDefaults, d.defaults)
  } catch (e) {
    if (e.status === 403) denied.value = true
    else toast(e.message || '加载默认参数失败', 'error')
  }
}

async function saveDefaults(force = false) {
  // force=true: 保存默认值 + 强制清除所有用户筛选偏好(保留背景/字号), 全量立即生效
  if (force && !window.confirm('「保存并强制生效」将清除所有用户已保存的筛选偏好(保留背景/字号设置)，改为使用新的全局默认值。确定继续？')) return
  const saving = force ? savingDefaultsForce : savingDefaults
  defaultsMsg.value = ''
  saving.value = true
  try {
    const d = await saveAdminDefaults({ ...adminDefaults }, force)
    defaultsMsg.value = d.msg || '已保存'
    defaultsErr.value = false
    toast(force ? '已保存并强制所有用户生效' : '全局默认筛选参数已保存', 'success')
  } catch (e) {
    defaultsMsg.value = e.message || '保存失败'
    defaultsErr.value = true
  } finally {
    saving.value = false
  }
}

const menuUid = ref(null)        // ⋮ 下拉菜单当前打开的用户 id
const menuSection = ref('')      // 下拉内当前面板: '' 主菜单 | 'expire' 期限面板
const menuRect = ref(null)       // 菜单 fixed 定位 {top, left}; null=不显示
const currentMenuUser = computed(() => rows.value.find(x => x.id === menuUid.value) || null)

function closeMenu() {
  watchMenuPos(false)
  menuUid.value = null
  menuSection.value = ''
  menuRect.value = null
  menuBtnEl.value = null
}

const menuBtnEl = ref(null)      // 触发 ⋮ 的按钮元素(滚动跟随定位用)
const MENU_W = 200
const MENU_H = 56
const MENU_MARGIN = 6

function setMenuRect() {
  const btn = menuBtnEl.value
  if (!btn || !btn.isConnected) { closeMenu(); return }
  const r = btn.getBoundingClientRect()
  const menu = document.querySelector('.row-menu')
  const h = (menu && menu.offsetHeight) || MENU_H   // expire 面板更高, 用真实高度
  const up = (window.innerHeight - r.bottom) < (h + MENU_MARGIN + 12)
  menuRect.value = {
    top: up ? Math.max(8, r.top - h - MENU_MARGIN) : (r.bottom + MENU_MARGIN),
    left: Math.max(8, Math.min(window.innerWidth - MENU_W - 8, r.right - MENU_W))
  }
}

// 滚动/窗口缩放时跟随 ⋮ 按钮(捕获阶段能听到表格内层滚动), 避免菜单停在原地点不到
function onMenuScroll() {
  if (menuUid.value !== null) setMenuRect()
}
function watchMenuPos(on) {
  if (on) {
    window.addEventListener('scroll', onMenuScroll, true)
    window.addEventListener('resize', onMenuScroll)
  } else {
    window.removeEventListener('scroll', onMenuScroll, true)
    window.removeEventListener('resize', onMenuScroll)
  }
}

function toggleMenu(u, ev) {
  if (menuUid.value === u.id) { closeMenu(); return }
  menuUid.value = u.id
  menuSection.value = ''
  menuBtnEl.value = ev && ev.currentTarget
  setMenuRect()
  watchMenuPos(true)
}

// 点空白处关闭菜单(⋮ 按钮和菜单本身已 .stop, 不会被误关闭)
function onMenuOutsideClick(ev) {
  if (menuUid.value === null) return
  const el = document.querySelector('.row-menu')
  if (el && !el.contains(ev.target)) closeMenu()
}
onMounted(() => document.addEventListener('click', onMenuOutsideClick))
onBeforeUnmount(() => document.removeEventListener('click', onMenuOutsideClick))
function menuAction(u, act) {
  if (act === 'expire') {
    menuSection.value = 'expire'   // 下拉内切换到期限面板
    setTimeout(setMenuRect, 0)     // 面板渲染后高度变化, 重算定位(可能改向上展开)
  } else {
    closeMenu()
    if (act === 'profile') openProfile(u)
    else if (act === 'pwd') openPwdReset(u)
    else if (act === 'delete') deleteUser(u)
  }
}
function onDocClick(e) {
  if (!e.target.closest('.expire-cell') && !e.target.closest('.row-actions')) {
    closeMenu()
  }
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

// ---------- 管理员重置密码 ----------
const pwdTarget = ref(null)
const pwdNew = ref('')
const pwdSaving = ref(false)
function openPwdReset(u) {
  pwdTarget.value = u
  pwdNew.value = ''
  closeMenu()
}
function closePwdReset() {
  pwdTarget.value = null
  pwdNew.value = ''
}
async function doResetPwd() {
  if (!pwdTarget.value) return
  const pw = pwdNew.value.trim()
  if (pw.length < 6) { toast('新密码至少 6 位', 'error'); return }
  pwdSaving.value = true
  try {
    await resetUserPassword(pwdTarget.value.id, pw)
    toast(`${pwdTarget.value.username} 密码已重置`, 'success')
    closePwdReset()
  } catch (e) {
    toast(e.message || '重置失败', 'error')
  } finally {
    pwdSaving.value = false
  }
}

// ---------- 管理员代编辑用户资料(手机/邮箱/微信名/备注/付款备注) ----------
const profileTarget = ref(null)
const profileForm = reactive({ phone: '', email: '', wx_name: '', remark: '', pay_remark: '' })
const profileSaving = ref(false)
function openProfile(u) {
  profileTarget.value = u
  profileForm.phone = u.phone || ''
  profileForm.email = u.email || ''
  profileForm.wx_name = u.wx_name || ''
  profileForm.remark = u.remark || ''
  profileForm.pay_remark = u.pay_remark || ''
  closeMenu()
}
function closeProfile() {
  profileTarget.value = null
  profileForm.phone = profileForm.email = profileForm.wx_name = profileForm.remark = profileForm.pay_remark = ''
}
async function saveProfile() {
  if (!profileTarget.value) return
  const fields = {}
  if (profileForm.phone.trim() !== (profileTarget.value.phone || '')) fields.phone = profileForm.phone.trim()
  if (profileForm.email.trim() !== (profileTarget.value.email || '')) fields.email = profileForm.email.trim()
  if (profileForm.wx_name.trim() !== (profileTarget.value.wx_name || '')) fields.wx_name = profileForm.wx_name.trim()
  if (profileForm.remark.trim() !== (profileTarget.value.remark || '')) fields.remark = profileForm.remark.trim()
  if (profileForm.pay_remark.trim() !== (profileTarget.value.pay_remark || '')) fields.pay_remark = profileForm.pay_remark.trim()
  if (!Object.keys(fields).length) { toast('没有改动', 'error'); return }
  profileSaving.value = true
  try {
    await adminSetUserProfile(profileTarget.value.id, fields)
    toast(`${profileTarget.value.username} 资料已更新`, 'success')
    closeProfile()
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '保存失败', 'error')
  } finally {
    profileSaving.value = false
  }
}

// ---------- 管理员创建会员 ----------
const createOpen = ref(false)
const createSaving = ref(false)
const createResult = ref(null)
const createForm = reactive({
  username: '', password: '', phone: '', email: '',
  member_level: 1, expire_at: '', wx_name: '', remark: '', pay_remark: ''
})
const todayStr = new Date().toISOString().slice(0, 10)
function openCreate() {
  createOpen.value = true
  createResult.value = null
  // 默认 30 天后到期
  const d = new Date(); d.setDate(d.getDate() + 30)
  createForm.expire_at = d.toISOString().slice(0, 10)
  Object.assign(createForm, { username: '', password: '', phone: '', email: '',
    member_level: 1, wx_name: '', remark: '', pay_remark: '' })
}
function closeCreate() { createOpen.value = false; createResult.value = null }
async function doCreate() {
  if (!createForm.username.trim() || createForm.username.trim().length < 2) { toast('用户名 2-20 位', 'error'); return }
  if (createForm.password.length < 6) { toast('密码至少 6 位', 'error'); return }
  if (!/^1[3-9]\d{9}$/.test(createForm.phone.trim())) { toast('手机号格式不正确', 'error'); return }
  if (!/^[\w.+-]+@[\w-]+(\.[\w-]+)+$/.test(createForm.email.trim())) { toast('邮箱格式不正确', 'error'); return }
  createSaving.value = true
  try {
    const body = {
      username: createForm.username.trim(),
      password: createForm.password,
      phone: createForm.phone.trim(),
      email: createForm.email.trim(),
      member_level: createForm.member_level,
      expire_at: createForm.expire_at || undefined,
      wx_name: createForm.wx_name.trim() || undefined,
      remark: createForm.remark.trim() || undefined,
      pay_remark: createForm.pay_remark.trim() || undefined,
    }
    const d = await adminCreateUser(body)
    createResult.value = d
    toast(`${d.username} 创建成功`, 'success')
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '创建失败', 'error')
  } finally {
    createSaving.value = false
  }
}
async function copyText(t) {
  try {
    await navigator.clipboard.writeText(t)
    toast('已复制', 'success')
  } catch {
    toast('复制失败, 请手动选中', 'error')
  }
}

// ---------- 管理员删除账号 ----------
async function deleteUser(u) {
  if (!window.confirm(`确认删除「${u.username}」？此操作会清除该用户的所有会话/选股记录/重置链接, 不可恢复。`)) return
  if (!window.confirm(`再次确认: 真的要删除 ${u.username} (ID ${u.id}) 吗?`)) return
  try {
    await adminDeleteUser({ uid: u.id })
    toast(`${u.username} 已删除`, 'success')
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '删除失败', 'error')
  }
}

// ---------- 会员筛选 tab ----------
// 注: 不设"会员"总览 tab, 避免与 VIP/付费 歧义 (会员 = 付费 + VIP 的包含关系)
const memberTabs = [
  { key: 'all', label: '全部' },
  { key: 'paid', label: '付费会员' },
  { key: 'vip', label: 'VIP' },
  { key: 'normal', label: '普通用户' },
  { key: 'admin', label: '管理员' },
]
const memberTab = ref('all')
function setMemberTab(k) {
  memberTab.value = k
  page.value = 1
  loadUsers(1)
}
// 后端按 member_tab 参数过滤; 分页 total 由服务端给出, 直接展示
async function loadUsers(p) {
  try {
    const d = await adminUsers({ page: p, pageSize: pageSize.value,
                                 keyword: keyword.value,
                                 memberTab: memberTab.value })
    rows.value = (d.rows || []).map(r => ({ ...r, _level: r.member_level || 0 }))
    total.value = d.total || 0
    page.value = d.page || 1
    stats.value = d.stats || {}
  } catch (e) {
    if (e.status === 403) denied.value = true
    else toast(e.message || '加载失败', 'error')
  }
}

const stats = ref({})
const rows = ref([])
const page = ref(1)
const total = ref(0)
const pageSize = ref(10)
const keyword = ref('')
const denied = ref(false)
// 批量设置到期(2026-08-17): 多选 checkbox
const selectedIds = ref([])
const batchExpireOpen = ref(false)
const batchExpireDate = ref('')
const batchSaving = ref(false)
const pageAllChecked = computed(() => {
  const list = userSort.sorted(rows.value, userVal).filter(u => !u.is_admin)
  return list.length > 0 && list.every(u => selectedIds.value.includes(u.id))
})
function togglePageAll(e) {
  const list = userSort.sorted(rows.value, userVal).filter(u => !u.is_admin)
  if (e.target.checked) {
    list.forEach(u => { if (!selectedIds.value.includes(u.id)) selectedIds.value.push(u.id) })
  } else {
    const ids = list.map(u => u.id)
    selectedIds.value = selectedIds.value.filter(id => !ids.includes(id))
  }
}
function openBatchExpire() {
  if (!selectedIds.value.length) { toast('请先勾选用户', 'error'); return }
  batchExpireDate.value = ''
  batchExpireOpen.value = true
}
function closeBatchExpire() { batchExpireOpen.value = false }
async function batchExtend(duration) {
  const label = { week: '+1周', month: '+1月', quarter: '+1季', year: '+1年' }[duration]
  await doBatchExpire({ duration }, label)
}
async function batchSetDate() {
  if (!batchExpireDate.value) { toast('请先选择日期', 'error'); return }
  await doBatchExpire({ expire_at: batchExpireDate.value }, '设日期')
}
async function batchForever() { await doBatchExpire({ days: 0 }, '永久') }
async function doBatchExpire(payload, label) {
  const uids = selectedIds.value
  if (!uids.length) { toast('未勾选用户', 'error'); return }
  batchSaving.value = true
  try {
    const d = await setUsersExpire(uids, payload)
    toast(`${label}批量设置完成: 成功 ${d.success}/${d.total}`, d.failed && d.failed.length ? 'warning' : 'success')
    batchExpireOpen.value = false
    selectedIds.value = []
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '批量设置失败', 'error')
  } finally {
    batchSaving.value = false
  }
}

const scoring = reactive({})        // 竞价评分配置(盘中已合并为同一套)
const wKeys = ref([])
const confKeys = ref([])
const activeFactor = ref('bid')     // 打分明细当前激活的因子Tab
const saving = ref(false)
const saveMsg = ref('')
const saveErr = ref(false)

const weightSum = computed(() => {
  let s = 0
  wKeys.value.forEach(([k]) => { const v = Number(scoring[k]); if (!isNaN(v)) s += v })
  return s
})

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

const factors = reactive({})        // 竞价打分明细
const factorOrder = ['bid', 'activity', 'warn', 'market', 'yesterday']
const scoringLoaded = ref(false)

async function extendUser(u, action) {
  const label = { week: '+1周', month: '+1月', quarter: '+1季', year: '+1年', forever: '永久', date: '设日期' }[action]
  try {
    let payload
    if (action === 'forever') payload = { days: 0 }
    else if (action === 'date') {
      if (!u._expireDate) { toast('请先选择日期', 'error'); return }
      payload = { expire_at: u._expireDate }
    } else {
      payload = { duration: action }
    }
    const d = await setUserExpire(u.id, payload)
    toast(`${u.username} ${label}设置成功，到期 ${fmtBjDay(d.expire_at)}`, 'success')
    u._expireDate = ''
    closeMenu()
    loadUsers(page.value)
  } catch (e) {
    toast(e.message || '设置失败', 'error')
  }
}

// 每页条数变更: 回第 1 页重载
function changePageSize() {
  loadUsers(1)
}

// 会员等级: 0=免费试用 1=付费会员 2=VIP (永久)
function levelLabel(lv) {
  return lv === 2 ? 'VIP' : lv === 1 ? '付费会员' : '免费试用'
}
async function setLevel(u) {
  try {
    const d = await adminSetMemberLevel(u.id, u._level)
    toast(`${u.username} 已设为「${d.label}」`, 'success')
    u.member_level = u._level
  } catch (e) {
    toast(e.message || '设置失败', 'error')
    u._level = u.member_level || 0
  }
}

async function loadScoring() {
  try {
    const d = await adminScoring()
    Object.assign(scoring, d.scoring || {})
    wKeys.value = d.w_keys || []
    confKeys.value = d.conf_keys || []
    const fac = (d.scoring && d.scoring.factors) || {}
    factorOrder.forEach((fk) => {
      factors[fk] = fac[fk] || { label: fk, unit: '', buckets: [], default: 0.1 }
      // buckets 行转数组, 便于 v-model.number 双向绑定
      factors[fk].buckets = (factors[fk].buckets || []).map((b) => [Number(b[0]), Number(b[1]), Number(b[2])])
    })
    scoringLoaded.value = true
  } catch (e) {
    if (e.status === 403) denied.value = true
    else toast(e.message || '加载失败', 'error')
  }
}

function addBucket(fk) {
  factors[fk].buckets.push([0, 1, 0.5])
}

function delBucket(fk, idx) {
  factors[fk].buckets.splice(idx, 1)
}

async function saveScoring() {
  saveMsg.value = ''
  const payload = {}
  Object.keys(scoring).forEach((k) => {
    if (!k.startsWith('factors')) payload[k] = Number(scoring[k])
  })
  payload.factors = {}
  factorOrder.forEach((fk) => {
    const f = factors[fk]
    payload.factors[fk] = {
      label: f.label, unit: f.unit, default: Number(f.default),
      buckets: f.buckets.map((b) => [String(b[0]), String(b[1]), Number(b[2])])
    }
  })
  saving.value = true
  try {
    const d = await apiSaveScoring(payload)
    saveMsg.value = d.msg || '已保存'
    saveErr.value = false
    toast('竞价权重与打分明细已保存并生效', 'success')
  } catch (e) {
    saveMsg.value = e.message || '保存失败'
    saveErr.value = true
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadUsers(1)
  loadScoring()
  loadDefaults()
})
</script>

<style scoped>
.admin-wrap { max-width: 1500px; margin: 0 auto; padding: 14px 16px; }
.admin-head { display: flex; align-items: center; margin-bottom: 14px; }
.admin-card { background: var(--bg-hover); border: 1px solid var(--border-soft); border-radius: 10px; padding: 16px; margin-bottom: 14px; }
.field-label { color: #bbb; font-size: 12px; }
.admin-tip { color: #888; font-size: 12px; margin-left: 8px; }
.admin-save-btn {
  background: rgba(120,200,80,0.2);
  border: 1px solid #78c850;
  color: #c0e8a0;
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  transition: opacity 0.15s;
}
.admin-save-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.admin-save-btn:hover:not(:disabled) { background: rgba(120,200,80,0.32); }
/* 「保存并强制生效」按钮: 深色主题用深红警示色 */
.save-force-btn { background: #A32D2D; border: 1px solid #A32D2D; color: #fff; }
.save-force-btn:hover:not(:disabled) { background: #c93838; border-color: #c93838; }
.admin-msg-ok { color: #7ce8a0; }
.admin-msg-err { color: #ff6a6a; }
.weight-desc { color: #999; font-size: 12px; }
.weight-total { color: var(--accent-text); font-size: 13px; }
.weight-warn { color: #ff6a6a; }
.admin-warn { color: #ff6a6a; }
.admin-ok-text { color: #7ce8a0; }
.card-title { display: flex; align-items: center; font-size: 15px; color: #ffe0a0; margin-bottom: 12px; }
.admin-input { background: var(--bg-input); border: 1px solid var(--border-soft); border-radius: 6px; color: var(--text-main); padding: 6px 10px; font-size: 13px; }
.admin-input:focus { outline: none; border-color: #ffb400; }
/* 2026-08-17: 弹窗内输入框放大(主人反馈: 设置资料/时间的框太小) */
.admin-input-lg { padding: 9px 12px; font-size: 14px; border-radius: 7px; }
.batch-expire-box { margin-top: 4px; }
.batch-exp-btn { padding: 8px 16px; font-size: 13px; border-radius: 6px; }
.batch-forever { background: rgba(255,90,90,0.15); color: #ff6a6a; border-color: rgba(255,90,90,0.5); }
.admin-search-btn {
  background: rgba(0,180,255,0.15);
  border: 1px solid #00b4ff;
  color: #a0e0ff;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}
.factor-tabs {
  display: flex;
  gap: 8px;
  margin: 4px 0 14px;
  flex-wrap: wrap;
}
.factor-tab {
  background: var(--bg-hover);
  border: 1px solid var(--border-soft);
  color: var(--text-secondary);
  border-radius: 8px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}
.factor-tab:hover { border-color: #ffb400; color: #ffe0a0; }
.factor-tab.active {
  background: rgba(120,200,80,0.14);
  border-color: #78c850;
  color: #c8f0a8;
}
.table-scroll { overflow-x: auto; }
.admin-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.admin-table th, .admin-table td { border-bottom: 1px solid var(--border-soft); padding: 8px 10px; text-align: center; color: var(--text-secondary); }
.admin-table th { color: var(--text-muted); font-weight: 500; }
.admin-tag { color: #ffd700; border: 1px solid #ffd700; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
.user-tag { color: var(--text-muted); border: 1px solid #666; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
/* 角色徽标 (合并 is_admin + member_level 显示, 紧贴用户名) */
.user-name-row { display: inline-flex; align-items: center; gap: 6px; }
.role-badge { display: inline-block; border-radius: 4px; padding: 2px 10px; font-size: 11px; font-weight: 600; }
.role-admin { color: #4a9eff; border: 1px solid #4a9eff; background: rgba(74,158,255,0.12); }
.role-vip   { color: #ffb347; border: 1px solid #ffb347; background: rgba(255,179,71,0.15); }
.role-paid  { color: #ff6a6a; border: 1px solid #ff6a6a; background: rgba(255,106,106,0.12); }
.role-trial { color: var(--text-muted); border: 1px solid #666; }
/* 备注/付款备注单元格 */
.cell-note { display: block; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary); font-size: 12px; }
.cell-pay { display: block; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #6ab0ff; font-size: 11px; margin-top: 2px; }
/* ⋮ 操作下拉 (Teleport 到 body, 定位由内联 style position:fixed 控制) */
.row-actions { display: inline-block; }
.row-menu { z-index: 100000; min-width: 168px; max-width: 220px;
  background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.35); padding: 4px; }
.row-menu-item { display: block; width: 100%; text-align: left; padding: 6px 8px; font-size: 13px;
  background: transparent; border: 0; color: var(--text-main); cursor: pointer; border-radius: 4px; }
.row-menu-item:hover { background: rgba(0,180,255,0.12); }
.row-menu-danger { color: #ff6a6a; }
.row-menu-danger:hover { background: rgba(255,80,80,0.12); }
.row-menu-expire { margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--border-soft); }
.expired-tag { color: #ff6a6a; border: 1px solid #ff5050; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
.ok-tag { color: #7ce8a0; border: 1px solid #4caf70; border-radius: 4px; padding: 1px 8px; font-size: 12px; }
.expire-cell { position: relative; display: inline-block; }

/* 会员等级 */
.level-cell { display: inline-flex; align-items: center; gap: 6px; }
.level-tag { border-radius: 4px; padding: 1px 8px; font-size: 12px; white-space: nowrap; }
.level-0 { color: var(--text-muted); border: 1px solid #666; }
.level-1 { color: #ff6a6a; border: 1px solid rgba(255, 90, 90, 0.55); }
.level-2 { color: #ffd700; border: 1px solid #ffd700; }
.level-select {
  background: var(--bg-input); color: var(--text-secondary);
  border: 1px solid var(--border-soft); border-radius: 4px;
  font-size: 12px; padding: 3px 6px; cursor: pointer; min-width: 100px;
}
.expire-popover {
  position: absolute;
  top: 28px;
  right: 0;
  z-index: 10;
  background: var(--bg-panel-solid);
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  padding: 10px 12px;
  min-width: 220px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.6);
}
.pop-label { font-size: 11px; color: var(--text-muted); margin: 6px 0 4px; }
.pop-label:first-child { margin-top: 0; }
.pop-row { display: flex; gap: 4px; margin-bottom: 6px; align-items: center; flex-wrap: wrap; }
.mini-btn { background: rgba(0,180,255,0.12); border: 1px solid #00b4ff; color: #a0e0ff; border-radius: 4px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.mini-btn:hover { background: rgba(0,180,255,0.25); }
.mini-btn.danger { background: rgba(255,80,80,0.12); border-color: #ff5050; color: #ff9a9a; }
.mini-btn.danger:hover { background: rgba(255,80,80,0.25); }
.pwd-btn { background: rgba(255,180,0,0.12); border: 1px solid #ffb400; color: #ffe0a0; }
.pwd-btn:hover { background: rgba(255,180,0,0.25); }
.pwd-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.55); display: flex; align-items: center; justify-content: center; z-index: 100; }
.pwd-pop { background: var(--bg-panel-solid); border: 1px solid var(--border-soft); border-radius: 10px; padding: 18px 20px; min-width: 320px; box-shadow: 0 6px 24px rgba(0,0,0,0.35); }
.pwd-title { font-size: 14px; color: #ffe0a0; margin-bottom: 12px; }
/* 编辑资料弹窗: 2 列网格 */
.profile-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 12px; }
.profile-label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-muted); text-align: left; }
.profile-label .admin-input { width: 100%; box-sizing: border-box; }
.profile-label textarea.admin-input { resize: vertical; min-height: 36px; font-family: inherit; }
.profile-label.profile-pay { color: #6ab0ff; }
.profile-tip { font-size: 11px; color: #888; margin-top: 10px; text-align: left; }
/* 用户列表: 微信名/备注小字 */
.user-sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.user-remark { color: #b8965a; }
.user-pay { color: #6ab0ff; font-weight: 500; }
/* 会员筛选 tab */
.member-tabs { display: flex; flex-wrap: wrap; gap: 2px; background: rgba(255,255,255,0.04); padding: 2px; border-radius: 6px; }
.member-tab { background: transparent; border: 0; color: var(--text-muted); padding: 4px 10px; font-size: 12px; cursor: pointer; border-radius: 4px; white-space: nowrap; }
.member-tab:hover { color: var(--text-main); }
.member-tab.active { background: rgba(0,180,255,0.18); color: #a0e0ff; }
/* 创建结果展示 */
.create-result { margin-top: 14px; padding: 12px; background: rgba(0,200,120,0.10); border: 1px solid rgba(0,200,120,0.35); border-radius: 6px; font-size: 13px; }
.mini-date { background: var(--bg-card); border: 1px solid var(--border-soft); border-radius: 4px; color: var(--text-main); padding: 3px 6px; font-size: 12px; color-scheme: light; }
/* 2026-08-17: 设置期限面板日期框放大(主人反馈) */
.row-menu-expire .mini-date { padding: 7px 10px; font-size: 14px; border-radius: 6px; width: 100%; box-sizing: border-box; }
.row-menu-expire .pop-row { gap: 6px; }
.row-menu-expire .mini-btn { padding: 7px 12px; font-size: 13px; }
.pager { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 12px; flex-wrap: wrap; }
.pager-left { display: flex; align-items: center; gap: 6px; }
.pager-right { display: flex; align-items: center; gap: 12px; }
.page-btn { background: rgba(0,180,255,0.12); border: 1px solid #00b4ff; color: #a0e0ff; border-radius: 6px; padding: 4px 14px; cursor: pointer; }
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.weight-table input { color: #ffd700; }
.factor-box { border: 1px solid var(--border-soft); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.factor-title { display: flex; align-items: center; color: #ffd700; font-size: 14px; margin-bottom: 8px; }
.bucket-table input { color: #a0e0ff; }
.add-btn { background: rgba(0,180,255,0.12); border: 1px dashed #00b4ff; color: #a0e0ff; border-radius: 6px; padding: 3px 14px; cursor: pointer; font-size: 12px; }
.del-btn { background: rgba(255,80,80,0.15); border: 1px solid #ff5050; color: #ff9a9a; border-radius: 6px; padding: 2px 8px; cursor: pointer; }

/* 浅色主题覆盖 */
body[data-bg="light"] .card-title {  color: #5a4a3a;  }
body[data-bg="light"] .mini-btn.danger {  color: #b83010; background: rgba(255,80,80,0.12); border-color: rgba(220,50,50,0.5);  }
body[data-bg="light"] .mini-btn.danger:hover {  background: #b83010; color: white;  }
body[data-bg="light"] .del-btn {  color: #b83010; background: rgba(255,80,80,0.1); border-color: rgba(220,50,50,0.5);  }
body[data-bg="light"] .del-btn:hover {  background: #b83010; color: white;  }
body[data-bg="light"] .pwd-pop {  background: rgba(255,255,255,0.98); border-color: var(--border-soft);  }
body[data-bg="light"] .pwd-mask {  background: rgba(0,0,0,0.45);  }
body[data-bg="light"] .pwd-btn {  color: #8a5500; background: rgba(255,180,0,0.15); border-color: #c79100;  }
body[data-bg="light"] .pwd-title {  color: #5a4a3a;  }
body[data-bg="light"] .weight-table input {  color: #1a1d26; background: rgba(255,255,255,0.95);  }
body[data-bg="light"] .bucket-table input {  color: #1a1d26; background: rgba(255,255,255,0.95);  }
body[data-bg="light"] .factor-title {  color: #8a5500;  }
body[data-bg="light"] .factor-tab {  color: #5a6b85; border-color: var(--border-soft);  }
body[data-bg="light"] .factor-tab:hover {  color: #5a4a3a; border-color: #c79100;  }
body[data-bg="light"] .factor-tab.active {  color: #5a4a3a; border-color: #c79100; background: rgba(255,180,0,0.15);  }
body[data-bg="light"] .page-btn {  color: #005c5a; background: rgba(0,180,180,0.12); border-color: #0080a0;  }
body[data-bg="light"] .mini-date {  color: #1a1d26; background: rgba(255,255,255,0.95); border-color: var(--border-soft);  }
body[data-bg="light"] .mini-btn {  color: #1a1d26; background: rgba(240,245,250,0.9); border-color: var(--border-soft);  }
body[data-bg="light"] .admin-tag {  color: #8a5500; border-color: #c79100;  }
body[data-bg="light"] .expired-tag {  color: #b83010; border-color: #b83010;  }
body[data-bg="light"] .level-2 { color: #8a5500; border-color: #c79100; }
body[data-bg="light"] .level-1 { color: #c03030; border-color: #e06060; }
body[data-bg="light"] .ok-tag {  color: #2d7020; border-color: #4caf70;  }
body[data-bg="light"] .weight-table input { color: #1a1d26; background: rgba(255,255,255,0.95); }
body[data-bg="light"] .bucket-table input { color: #1a1d26; background: rgba(255,255,255,0.95); }
body[data-bg="light"] .factor-title { color: #8a5500; }
body[data-bg="light"] .expire-popover { background: rgba(255,255,255,0.98); border-color: var(--border-soft); }
body[data-bg="light"] .pwd-pop { background: rgba(255,255,255,0.98); border-color: var(--border-soft); }
body[data-bg="light"] .field-label { color: #5a4a3a; }
body[data-bg="light"] .admin-tip { color: #5a6b85; }
body[data-bg="light"] .admin-save-btn { background: rgba(34,139,34,0.1); border: 1px solid #228722; color: #1a6b1a; }
body[data-bg="light"] .admin-save-btn:hover { background: #228722; color: #fff; }
/* 浅色主题: 「保存并强制生效」改用浅红底 + 深红字(对应「保存默认值」浅绿底 + 深绿字) */
body[data-bg="light"] .save-force-btn { background: rgba(163,45,45,0.12); border-color: #A32D2D; color: #A32D2D; }
body[data-bg="light"] .save-force-btn:hover:not(:disabled) { background: #A32D2D; color: #fff; }
body[data-bg="light"] .admin-msg-ok { color: #1a6b1a; }
body[data-bg="light"] .weight-desc { color: #5a6b85; }
body[data-bg="light"] .weight-total { color: #8a5500; }
body[data-bg="light"] .weight-warn { color: #b83010; }

/* ===================== 移动端适配 (<=768px) ===================== */
@media (max-width: 768px) {
  /* 页面留白压缩 */
  .admin-wrap { padding: 8px 4px; }
  .admin-card { padding: 10px 8px; margin-bottom: 10px; }
  /* 头部紧凑: 返回按钮/标题 */
  .admin-head { gap: 6px; }
  .admin-head h2 { font-size: 17px; }
  /* 宽表格横向滚动(用户列表 11 列 / 权重表 / 打分明细表) */
  .table-scroll { -webkit-overflow-scrolling: touch; }
  .table-scroll .admin-table { min-width: 860px; }
  .weight-table { min-width: 640px; }
  .bucket-table { min-width: 560px; }
  /* 表格字号压缩 */
  .admin-table th, .admin-table td { padding: 6px 6px; font-size: 12px; }
  /* 输入框触控加大(手机点不中小输入框) */
  .admin-input { min-height: 32px; padding: 6px 8px; font-size: 13px; }
  /* 因子 Tab 紧凑 */
  .factor-tab { padding: 5px 10px; font-size: 12px; }
  /* 搜索栏/操作行换行 */
  .admin-card > div[style*="flex"] { flex-wrap: wrap; }
  /* 保存按钮触控加大 */
  .admin-save-btn { padding: 8px 14px; }
}
</style>
