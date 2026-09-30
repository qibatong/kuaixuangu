# v4.11.83 生产机 + 测试机 前端部署记录

- **日期**：2026-09-30 23:05 ~ 23:25（北京时间）
- **目标机**：**生产机**（部署根 `/opt/kuaixuan`，venv `/opt/kuaixuan-venv`）＋ **测试机**（同根，venv `/opt/bid-venv`）
- **触发指令**：「好部署到生产机吧。然后入库 上推 更新版本及记忆」
- **部署内容**：**仅前端 `dist`**（后端 / `env.conf` / settings / DB **一个字节未动**）
- **前一版生产状态**：前端 `index-1N7B3gsW.js`（今天 12:39 构建），后端落后 2 个运行时模块（`core/config.py`、`services/meoz_client.py` —— 缺「竞价期静默窗口」，**这是故意的**，见下）

---

## 一、本次上线的改动（= 前端工作区全部未提交改动）

| 归属 | 文件 | 内容 |
|---|---|---|
| 体验修复 6 项 | `public/logo.jpg` | 1280×1280 / 57.7KB → 256×256 / **10.3KB（−82%）** |
| | `api/stats.js` | `auctionOverview` 在途去重 + 3s 记忆化（`date` 参与键）⇒ 首屏 3 次 → **1 次** |
| | `api/stocks.js` | `getPrefs` 未登录直接返回 `{}` ⇒ 消除登录页 **401** |
| | `components/StockTable.vue` | ≤768 名称列横滑冻结（表头/体格**分底色**）＋ 数字列 `nowrap` ＋ `.tick` 背景闪 + ▲▼ |
| | `views/AuctionView.vue` | 右栏多日表 `MAX_ROWS_MSD=300` 截断提示 ＋ `.msd-row{content-visibility:auto}` |
| 同期在飞改动（主人） | `views/StockView.vue`、`views/AdminView.vue`、`views/YijinerView.vue`、`components/AipickReport.vue`、`components/ZhPicksPanel.vue`、`components/FilterPanel.vue`、`stores/stocks.js`、新增 `utils/strategy.js`(+test) | 含**按主人指令删除 `freeze-notice` 定格标注条**、`displayDate` 日期框口径、空表提示、AdminView 等 |

> 说明：前端块 hash 会**级联**（entry 变 ⇒ 40 个 `import("./index-*.js")` 的路由块文件名全变，内容只是引用路径变），
> 因此"变化 40 对块"≠"40 个功能变化"；判断语义变化要看 **CSS 类名/keyframes 名/字面量**。

---

## 二、部署流程（前端 dist，静态资源，**无需重启服务**）

1. **备份**：`cp -a /opt/kuaixuan/dist /opt/kuaixuan/dist.bak_20260930-231500`（1054 文件，入口 `index-1N7B3gsW.js`）
2. **上传**：`python3 scripts/_kx_direct.py putdir <生产机> '<pass>' /tmp/kx_dist_new /opt/kuaixuan/dist`
   —— paramiko SFTP，**按内容 md5 比对**只传差异（首跑 41 个 / 0.54MB），**只增不删**（旧块保留，避免已打开的旧页面缺块白屏）
3. **权限归一**：`find … -type d -exec chmod 755` ＋ `-type f -exec chmod 644`（见第三节事故）
4. **核验**：入口 `index-DULQIVD_.js` ✓、`index.html` md5 与本地**逐字节一致** ✓、`logo.jpg` 10,513B ✓、
   nginx `:80` → **301**（跳 https，正常）、服务 `nginx/kuaixuan/kx-worker` 全 `active`
5. **测试机同步**：同一份产物再 putdir 一次（42 文件），两机入口/校验和一致

---

## 三、🔴 事故与根因：SFTP 上传不带权限 ⇒ nginx 403 ⇒ 白屏（真实用户已命中）

**现象**：换盘后 `index.html` 正常返回，但入口 chunk `index-DULQIVD_.js` **HTTP 403**，SPA 起不来（浏览器报
`Failed to load resource: 403`，页面只剩骨架）。`/var/log/nginx/error.log`：

```
[error] open() "/opt/kuaixuan/dist/assets/index-DULQIVD_.js" failed (13: Permission denied),
        client: <外部用户IP>, request: "GET /assets/index-DULQIVD_.js HTTP/2.0",
        referrer: "https://www.kuaixuangu.cn/"      ← 🔴 外部真实用户
```

**根因**：
- SFTP `put` **新建**的文件权限由**远端 sshd 的 umask** 决定 —— 生产机 umask 027 ⇒ `-rw-r----- root:root`；
- nginx worker 以用户 `nginx` 运行 ⇒ **无读权限** ⇒ 403；
- **覆盖已有文件不会改权限** ⇒ 只有**本次新增的 42 个 chunk** 中招（`index.html`/`logo.jpg` 因原本存在而幸免）；
- 测试机 umask 022 ⇒ 落成 644，**侥幸未暴露** —— 所以"测试机通过"不代表生产通过。

**抢修**：`find /opt/kuaixuan/dist -type d -exec chmod 755 {} +` ＋ `-type f -exec chmod 644 {} +`（0 个文件缺世界读），
入口 chunk 复查 **200** 且 md5 与本地一致。**影响窗口约 2 分钟**（含至少 1 个外部真实用户）。

**根因修复（工具层，已入库）**：`scripts/_kx_direct.py` 新增 `_chmod_uploaded()`，`put`/`putm`/`putdir` **上传后一律
`chmod 644`（目录 755）**，并把事故写进代码注释。与既有约定一致（`AGENTS.md`：tar 打包阶段必须写死 dir 755 / file 644）。

---

## 四、上线后真机回归（生产站，390/1440；本机 Chrome 经 SSH 隧道）

| 指标 | 结果 |
|---|---|
| 入口 chunk | `index-DULQIVD_.js` **200**，md5 = 本地 ✓ |
| 「94分/83%」折行 | 30px/28px **单行（rects=1）** ✓（改前 28px / rects=2） |
| 名称列横滑冻结 | 390 横滑到最右后 left = **9**（表头/体格同步）✓（改前 static / −45） |
| 数字变化反馈 | `.tick` = 102、▲▼ = 102（`+7.88%▲`）✓（改前 0） |
| `prefs` 401 | **0** 条 ✓ |
| `auction-overview` | 不在重复请求列表（≤1 次）✓ |
| `logo.jpg` | transferSize **10,813 B** ✓ |
| 右栏多日表 | 308 行 / 2053 格渲染正常，`content-visibility:auto` ✓ |
| 1440 CLS / LCP | 0.0524 / 832ms（**位移全部来自 `FOOTER.app-footer` 在 7.0~8.3s 被右栏迟到数据顶下**，合计 0.0485/0.0519；改前基线 0.0083 是**只统计到 6s** 的窗口差，非本轮引入） |

---

## 五、回滚

```bash
# 前端（静态，秒级，无需重启）
cp -a /opt/kuaixuan/dist.bak_20260930-231500/. /opt/kuaixuan/dist/
```

- 后端/配置/DB 本次**未动**，无需回滚。
- 反向注意：**不要**把测试机的 `backend`（静默窗口）或 `settings`（`meoz_quiet_window` / `scoring` / `scoring_spot`）同步到生产。

---

## 六、遗留与后续

1. **`main` 合并**：本批仍在 `feature/scoring-v7-meoz`。
2. **下一个交易日 = 2026-10-08**（10-01~10-07 休市）⇒ 假期是「取数日=上一交易日」场景的天然验收场（本次删了
   `freeze-notice` 定格标注条，正是该场景的提示），建议假期内点一遍各页。
3. **CLS**：右栏数据到达时间（7~8s）是位移主因，属既有问题，可单独优化（骨架屏/预留高度）。
4. **测试机数据层小差异**（与本次无关）：今日 `9_25` 快照比生产少 2 只（`001246`/`301139`）、无 09-29 快照、
   `stock_score_daily` 今日 0 行、`scoring_spot` 为空配置。
5. **仓库敏感信息**：`AGENTS.md` 明文要求"仓库公开可见、严禁写入服务器地址/账号"，但 `docs/history.md` 等**既有**文档
   已含 IP（历史遗留）；本次新增文档已按该规则**脱敏**（IP/账号一律写「生产机/测试机」）。`.codebuddy/` 未入库。
