# v4.11.84 生产机 + 测试机 前端部署记录（前端 P1/P2 四批）

- **日期**：2026-09-30 23:4x ~ 2026-10-01 00:2x（北京时间）
- **触发指令**：「都可以做」→「继续做」×2 →「这波改动上生产」
- **目标机**：**生产机**（`/opt/kuaixuan`，venv `/opt/kuaixuan-venv`）＋ **测试机**（同根，venv `/opt/bid-venv`）
- **部署内容**：**仅前端 `dist`** —— 后端 / `/etc/kuaixuan/env.conf` / settings / DB **一个字节未动**
- **前一版**：`v4.11.83`（入口 `index-DULQIVD_.js`）

---

## 一、四批改动（详见 `docs/前端P1P2-施工计划-20260930.md`）

| 批 | commit | 内容 |
|---|---|---|
| 1 工程项 | `b85779d` | 登录页定时器泄漏修复；`实体/可信/评分` 表头解释；水印防删除守卫收窄；水印 `opacity .18→.10`；FA 改非阻塞（过渡态）；**echarts 按需注册收口**（1.13MB → 669KB，gzip 378→225KB）；可选字体只留 regular（部署字体 21MB→13MB） |
| 2 格式/配色/a11y | `01927b3` | `fmtNum/fmtVol` 统一「\|v\|≥1000 上千分位」；语义色 `:root` token + `dim-25` 提亮；筛选区**回车=应用** + 8 个 `aria-label`；**color_guard 棘轮**；清 2 个既有 eslint error |
| 3 布局 | `c27d977` | 双栏 **≥1100**（整块配套行为下移）；≤430 **每日一卡**；**breakpoint_guard 棘轮**；搜索高亮去全局 `querySelector` |
| 4 Font Awesome | `12a3eb8` | **完全自托管**：子集 CSS 5KB + 本地 woff2 77KB（走 Vite 资产管线）⇒ 零外链/零阻塞/离线可用；修 **4 处"永不渲染"图标**；**fa_guard** |

**校验**：`npm run verify` = eslint **0 error** + 4 个静态闸门（css 滚动 / 色值 / 断点 / 图标字形）+ 4 个 SSR 用例与基线逐项一致（nav 230/20、spot 61/6、adm 23/0、board 21/0）。

---

## 二、部署流程

```bash
# 1) 构建（与测试机同源：entry index-6MuW3Ytz.js、index.html md5 一致后才推）
cd frontend && npx vite build --outDir /tmp/kx_dist_p9
# 2) 备份
cp -a /opt/kuaixuan/dist /opt/kuaixuan/dist.bak_v41184_20260930-235553   # 旧入口 index-DULQIVD_.js、1096 文件
# 3) 增量上传（按内容 md5 比对，只增不删；**上传后自动 chmod 644/755**）
python3 scripts/_kx_direct.py putdir <生产机> '<pass>' /tmp/kx_dist_p9 /opt/kuaixuan/dist   # 上传 42 个 / 0.88MB
```

---

## 三、🔴 事故：`public/fonts/` 被 SPA 回退吞掉 ⇒ 图标全空（已修复）

**现象**：批 4 首次上线后，生产上 `document.fonts` 里 `FontAwesome: **error**`、全站图标空白。

**排查**（三点定位）：
1. 文件本体正确：`/opt/kuaixuan/dist/fonts/fontawesome-webfont.woff2` 77,160B、md5 与本地一致、权限 644；
2. 但**经 nginx 取回的是 `index.html`**：`HTTP 200` + `content-length 3565` + `content-type text/html`（md5 = index.html）——
   即请求被 **SPA 回退**（`location / { try_files $uri $uri/ /index.html; }`）接走；
3. 对比：`/assets/*.woff2`（391 个 fontsource 字体）一直是 **200 + `font/woff2`** ✓。

**根因**：新增的静态资源目录 `/fonts/` 未落在 nginx 实际服务的静态路径下（`/assets/` 才是验证过可用的），
**且失败方式具有欺骗性 —— 状态码是 200**。

**修复**：把字体改走 **Vite 资产管线**（`src/assets/fonts/` ⇒ 打包为 `/assets/fontawesome-webfont-<hash>.woff2`，CSS 里相对路径由 Vite 重写）
⇒ 实测 `200 / 77160B / font/woff2`、`document.fonts.check('14px FontAwesome')` **true**、CDN 外链 **0**。

**教训（已写进 AGENTS.md）**：**凡新增静态资源，验证必须看 `content-type` 而不是只看状态码** —— SPA 回退会把
"文件不存在"伪装成 200 + index.html。同类前车之鉴：v4.11.83 的 **SFTP 新文件 640 权限**事故（表现为 403）。

---

## 四、上线后生产真机回归（本机 Chrome 经 SSH 隧道 + 整站透传代理）

| 项 | 结果 |
|---|---|
| 入口 / echarts / 字体块 | `index-6MuW3Ytz.js`、`echarts-*.js`、`fontawesome-webfont-*.woff2` **均 200** |
| 无世界读权限文件 | **0**（`putdir` 自动 chmod 生效，未重演 v4.11.83 的 403） |
| Font Awesome | 本地加载 `true`；字形 `\uf0e7` 等齐全；**CDN 请求 0**；`index.html` 外链 0 |
| 主表（1440） | 宽 693 / 34 行 / `.tick`+箭头 **102** / 水印 `opacity .10` |
| 表头解释 | `可信` 的 `title` 已生效 |
| 紧凑双栏（1152） | `602.266px 523.719px`（1.15:1），右栏可见；1099 以下仍是单列 + 切换栏 |
| ≤430 每日一卡 | 单列 `398px`、4 张卡、**无横滑** |
| 图标修复 | `fa-crown`（会员）/`fa-chart-line`/`fa-newspaper` 由"空白"变为可见 |
| 服务 | `nginx` / `kuaixuan` / `kx-worker` 全 `active` |

---

## 五、回滚

```bash
cp -a /opt/kuaixuan/dist.bak_v41184_20260930-235553/. /opt/kuaixuan/dist/   # 静态资源, 秒级, 无需重启
```
> 注：回滚会退回 `v4.11.83`，其图标走 cdnjs（可用但有外链/阻塞）；如需保留图标自托管而只回滚其它项，需重新构建中间版本。

## 六、遗留
1. **FA 字体文件位置**已从 `public/fonts/` 迁到 `src/assets/fonts/`（旧路径文件已在本地删除，服务端 `dist/fonts/` 为无人引用的残留，可清理）。
2. **P2-1 / P2-9 / P2-4** 的结论与建议见计划文档「五点五」节。
3. 10-08 开盘前建议复看一次「取数日=上一交易日」场景（国庆 10-01~10-07 休市）。
