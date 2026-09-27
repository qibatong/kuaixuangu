# v4.11.71 / v4.11.72 / v4.11.73 生产机部署记录

- **日期**：2026-09-28 01:12 ~ 01:35（北京时间）
- **目标机**：**生产机 `121.196.230.80`**（部署根 `/opt/kuaixuan`，venv `/opt/kuaixuan-venv`）
- **触发指令**：「部署到生产机，同时看一下竞价抢筹页面数据刷新的很慢…你评估一下」
- **前一版生产状态**：后端 = `v4.11.69`（缺 v4.11.71/72 的后端改动）；前端 dist 构建于 **09-27 20:59**
  （卡在 `9106aa3` 与 `11b73fc` 之间）⇒ **不含 v4.11.68/69/71**

---

## 一、差异定位（先测差异，再定清单）

| 对象 | 方法 | 结论 |
|---|---|---|
| 后端 `app/` | 逐文件归一化 md5（本地 ↔ 生产）对拍 | **只差 2 个文件**：`app/api/dev.py`、`app/services/dev_risk.py` |
| 前端 `dist/` | 入口 hash + 内容关键词断言 | 入口为旧件；`异动计算器` **0 命中**、`个股计算器` **1 命中** ⇒ 确为 v4.11.71 之前 |

⇒ 部署清单定为 **后端 2 文件 + 前端全量 dist**。生产 `scripts/` 未纳入（部署工具脚本非运行时不依赖）。

---

## 二、后端部署（两阶段 B1 / B2）

### B1 落盘 + 校验（不重启）

1. **暂存区按线上行尾逐文件对齐**（`_kx_stage_match.py`）——本地 `CRLF` → 生产 `LF`
   （生产 `CRLF` 集实测为空，即全 `LF`；**不硬编码名单**，逐文件读线上行尾决定）。
2. **备份**：`/opt/kuaixuan/backend_bak_v41173_20260928-011529`（落后于 `app/` 同级，
   **不在 `backups/` 子目录下**；内含 `app/`(243 文件) / `kuaixuan.db` / `requirements*.txt` / `scripts/`）
3. **落盘 + md5 三方比对**（本地 / 暂存区 / 生产落盘）：

   | 文件 | 归一化 md5 |
   |---|---|
   | `app/api/dev.py` | `5c812e33f9fc0d18c75d1588d6239b0e` |
   | `app/services/dev_risk.py` | `fdc36a324e2daad0b98418923e68e3db` |

   三方一致；`py_compile` 通过。
4. **行为预检**（`_kp_preflight.py`，只读）：**17/17 PASS**，含
   `i = last - off + 1` 的 off-by-one 判别断言、旧写法 `i = last - off` **必不存在**、
   以及**反空转守卫**（correct 与 shifted 结果必须有可观测差）。

   > ⚠️ 首轮 4 项 FAIL 系**断言写错**，非产品缺陷：① 轴是「价格比序列」，涨跌混合时本不单调，
   > 「单调」断言无意义；② n=10 夹具里 `r[1] == r[11]` 导致 correct ≡ shifted，反空转守卫
   > 自己被触发。修法：夹具改全互异值 + 把「单调」换成更强的**逐位对齐**
   > `out[d] == ∏(1+r_i)`（`i` 取窗口内全部）。

### B2 重启 + 验证

- 清 `__pycache__` → 重启 `kuaixuan` + `kx-worker`：**PID 3803962 → 3870176**
- 两服务 `active`；日志 **Traceback 0**；本机探测 `200`
- **端到端验收**（`_kx_verify_dev.py`，自签临时 token uid=6，结束即删该行）：**16/16 PASS**
  - `/api/dev/status` 200 + `ok=true`；5 个指数序列均非空
    （`000002:120 / 399107:120 / 399102:120 / 000688:120 / 899050:120`）
  - `/api/dev/risk?code=605058` 200 + `ok=true` + `elapsed_ms=65`
    - `dev.d3 / d10 / d30` 三项均在，且各含 `value / thresh / status`
    - `project10` 为 list、长度 10（未来十日实基倒推）
  - 缺参 / 非法参 → **400**
  - `dev_risk.py` 落盘含 `i = last - off + 1`、不含旧写法
  - `/api/dev/today`、`/api/dev/tomorrow`、`/api/kpl/yidong-realtime` 全 200 + `ok`

> 首轮 13/15 的 2 项 FAIL 系**我猜错返回键名**（写成 `d3/d10/d30`、`project`；真实为
> `dev`（内层 d3/d10/d30）与 `project10`），已按真实契约修正断言后重跑全绿。

---

## 三、前端部署（两阶段 S1 / S2）

### S1 暂存 + 断言（不动线上 dist）

1. 本地 `dist/` 即 **v4.11.71 产物**：入口 `index-CN96o_CV.js`、`异动计算器` 1 命中、
   `个股计算器` **0 命中**、无 macOS `AppleDouble` 垃圾文件、1045 个 assets。
2. 打包上传，tar 包 md5 `d95993280698af5a0311d1fc4d6c65dc`，上传后回读一致。
3. **内容断言**：禁含「个股计算器」→ **0 命中** ✓；必备「异动计算器」「未来十日推演」→ 各 **1 命中** ✓。
4. 权限修正（dir 755 / file 644）。

### 换盘前的 chunk 改名审计（`_kx_strip_md5.py` + `_kx_strip_cmp.py`）

剥掉 hash 出规范名 + 内容 md5，两端比对：**共同 66 / 相同 61 / 不同 5**
（`MarketView.css|js`、`YidongView.css|js`、`index.js`），另有一项仅旧包存在 `YidongFlow.css`。

逐条解释（全部落在本次改动源文件范围内）：

| 变化 | 解释 |
|---|---|
| `YidongView.css / .js` | `DevRiskDetail` 与 `YidongFlow` 两个组件**被内联进 YidongView chunk** —— grep 证实 `DevRiskDetail` / `YidongFlow` 字符串均出现在 `YidongView-*.js`；7 个 `yf-*` 类（`yf-title/yf-item/yf-dev/yf-empty/yf-search/yf-day/yf-code`）全部命中 `YidongView-*.css` |
| `MarketView.css / .js` | 同步受本次视图层改动影响 |
| `index.js` | 入口 hash 改名（入口本身，预期变化） |
| `YidongFlow.css`（仅旧） | 「**无 hash 名字本体**」，**不是真实线上文件**（已不存在） |

### S2 原子换盘

- 备份 `/opt/kuaixuan/dist_bak_20260928-011838`（同上级目录；`index.html` 仍引用**旧入口
  `index-D0JQaPeY.js`** 且该文件在备份内、assets 1046 项 ⇒ **回滚点有效**）
- 新入口 `200`、**旧入口 `404`**、`nginx -t` **ok**
- **外网验收**：`https://www.kuaixuangu.cn/` 入口 = `index-CN96o_CV.js`，**200**

---

## 四、污染与 5xx 归因

- 生产误建目录检查：`/root/C:` → **CLEAN**（无 `MSYS_NO_PATHCONV` 漏网产物）
- **5xx 共 41 条，全部集中在 `09-27 17:31 ~ 17:42`**（+ 1 条 `21:22`），URL 全为 `502`
  （`/api/kpl/yidong-realtime`、`/api/kpl/yidong-monitor`、`/api/dev/tomorrow`、
  `/api/kpl/activity`、`/api/kpl/track` 等）
  ⇒ **本次部署发生在 `09-28 01:17`，时间窗完全不相交 ⇒ 与本次部署无关**（属上一次 09-27 部署前的历史 502）。

---

## 五、回滚点

| 对象 | 路径 |
|---|---|
| 后端 | `/opt/kuaixuan/backend_bak_v41173_20260928-011529`（`app/` 243 文件 + `kuaixuan.db`） |
| 前端 | `/opt/kuaixuan/dist_bak_20260928-011838`（入口 `index-D0JQaPeY.js`，assets 1046） |

> ⚠️ 两者都在 **`/opt/kuaixuan/` 下**（与 `app/`、`dist/` 同级），**不在 `/opt/kuaixuan/backups/` 里**
> —— `backups/` 只放 09-22 的一次性整库备份（`be_20260922-212600` + 552MB `kuaixuan_20260922-212600.db`）。

## 六、遗留

- 观测：竞价抢筹冷取数尾巴问题**本轮未改代码**，仅出评估（见
  `docs/diagnosis-20260928-auction-qiangcang-latency.md`）。
- `scripts/_prod_md5.txt`、`scripts/_prod_md5_b64.txt` 为对拍中间产物，未纳入版本控制。
