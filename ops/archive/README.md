# ops/archive —— 生产机运维脚本归档（**入库**，非代码路径）

## 这个目录为什么存在

生产机 `/opt/kuaixuan` **没有 `.git`**，靠 scp / rsync 覆盖部署。因此存在一类文件：

> **只在生产机（和被忽略的本地工作区）上活着，版本库里没有。**

它们的风险是**同一个失败模式会同时删掉两侧**：一次「整目录替换式」发版
（生产上有 `scripts.bak_prodsync_20260921-145806` 这类痕迹，说明确实发生过）会把这些文件
从生产机抹掉；而本机那侧又是 `.gitignore` 忽略的 —— 于是**永久丢失**。

本仓库 `.gitignore` 的既定策略（L81-95、L106-123）是：
**「探测/调试与一次性补数修复脚本不入库」**，理由是保持 `scripts/` 目录语义干净
（`scripts/` 里应该是生产在跑的工具链，不是历史探针堆）。

同一份 `.gitignore` 的 L166-172 又记着这条策略曾经付出的代价：

> 例外: 可复用的部署/核对工具(2026-09-22 起入库)
> 背景: **沙箱曾误删 scripts/ 下 87 个文件, 未入库的运维脚本全部无法恢复(只能重写)**
> 只放行**通用工具**; 一次性探针/版本绑定的部署脚本仍按 `scripts/_kx_*.py` 忽略

⇒ **本目录就是来解决这个矛盾的**：既不破坏 `scripts/` 的语义（这里不是生产代码，
不会被误当工具链引用），又让这些文件**进 git、有远程备份、可 diff、可一键还原**。

---

## 权威性

- **本目录是这 13 个文件的权威副本**（`md5` 已与生产机原件逐位核对，见下表）。
- 工作区里 `scripts/aipick/_*.py`、`backend/scripts/*.py` 的同名文件是**同一份内容的镜像**，
  仅为「按原路径落盘」而存在（因为生产机上的路径就是那样）。**两者如有分歧，以本目录为准。**
- 本目录**不是生产代码路径**。不会被打包、不会被 `app/` 导入、不参与后端/前端构建。

---

## 一键还原到生产机

目录结构**按生产机原路径镜像**，所以还原就是一条同步：

```bash
# 只同步归档里这 13 个文件（不会碰生产机其它任何内容）
rsync -av --relative ops/archive/prod/./ root@121.196.230.80:/opt/kuaixuan/

# 或只还原某一个
scp ops/archive/prod/backend/scripts/backfill_kpl_seal.py \
    root@121.196.230.80:/opt/kuaixuan/backend/scripts/
```

> ⚠️ 归档里的 `.py` 全部把 `/opt/kuaixuan/...` **绝对路径和具体日期/标的写死在源码里**
> （`date='2026-08-27'`、`time_point='9_24'`、`康盛股份 002418`、`8/18-8/28` …），
> 还原是**恢复现场**，不是「拿来就能再跑一遍」—— 要复用请先改参数。

---

## 清单（13 个，md5 已与生产机原件逐位核对；行尾归一化后计算）

归档路径已去掉 `ops/archive/prod/` 前缀。

### `aipick/scripts/` —— 生产机 `/opt/kuaixuan/aipick/scripts/`（11 个）

| 文件 | md5 | 字节 | 行 | 用途 | 已跑过 |
|---|---|---|---|---|---|
| `_924check.py` | `70b21f78fc2a39de044eff5fd2cea1f7` | 1910 | 48 | 强制 `time_point='9_24'`，读 `snapshot_bid` 9_24 构造 5554 只特征 | 是 |
| `_alltp.py` | `db8e3e8050332fc09f1a455b85e08f33` | 2857 | 64 | 拉 2026-08-27 基准 stocks，比对各 time_point 的模型打分 | 是 |
| `_audit_amt.py` | `97a65aca7e3f911394ed521211c4fba5` | 5775 | 130 | **竞价额阈值体检**（方法论级，已另行收编为 `scripts/aipick/audit_amt.py`） | 是 |
| `_f.py` | `0a8e1285a3cf900cfd636e532617de69` | 934 | 21 | 单次跑 `predict(2026-08-28, force=True)` 并打印产物统计 | 是 |
| `_kx_attach_concepts.py` | `fe2909d613960e2f6bd0ea53368820b8` | 1665 | 46 | 给历史 `predictions_*.json` 批量回填 `concepts` 字段（不重算预测） | 是 |
| `_kx_verify_payload.py` | `d7852c50fe8d35f7053459f3daf33bd2` | 605 | 13 | 校验最新 `predictions_*.json` 里 `concepts` 是否落上 | 是 |
| `_probe_fetch.py` | `d67dcecdeab9a1def09cd392f227c098` | 400 | 11 | 探 `fetch_from_kuaixuan` 是否含 002418 康盛股份 | 是 |
| `_probe_prod.py` | `0f8b42d106f9559f611509362b48606e` | 1030 | 25 | 遍历全部 `predictions_*.json`，追康盛股份的 `ai_prob` | 是 |
| `_scan639.py` | `28094ca7f1ed09d22dcd15d0449b38b0` | 2275 | 42 | 用康盛 9_25 特征做模型敏感性扫描（单特征扰动看输出） | 是 |
| `backfill_100.py` | `7506c5df1dfc2dcc2d627cb187f1f941` | 4246 | 123 | 补 8/25-8/28 缺失标签；数据源 = 腾讯不复权日K（覆盖东财被墙） | 是 |
| `backfill_labels.py` | `0590dca9c105dc66b091ba7bf7529c8f` | 1956 | 58 | 补 8/18-8/28 缺失 `is_limit_up` / `close_chg`（源：快选 `close_change_history`） | 是 |

### `backend/scripts/` —— 生产机 `/opt/kuaixuan/backend/scripts/`（2 个）

| 文件 | md5 | 字节 | 行 | 用途 | 已跑过 |
|---|---|---|---|---|---|
| `backfill_kpl_seal.py` | `2dc2b024fe1cd39f4e008d7ed664f8d2` | 4755 | 116 | 一次性回填：开盘啦 `doc30` 竞价涨停委买额 → `snapshot_bid.bid_buy_amt`（`row[4]=bidSealAmt`，单位元） | 是 |
| `recalc_boom_history.py` | `8bb6b568e1a61ce9417d5cc73b6daf01` | 6001 | 153 | 按最新逻辑重算某日「竞价爆量」并落 `auction_daily_history`；含 `free_mv` 回填（东财 f117/f21） | 是 |

> ★ `backend/scripts/` 在 git 里**整个目录都不存在**（两个文件都被 `.gitignore` 逐条点名），
> 所以 `git status --ignored backend/scripts/` 只会输出一行 `!! backend/scripts/`
> —— **数这类文件时不要靠 `git status` 数行数**，会漏。

### 本地版本绑定产物 —— **非**生产镜像（2 个，2026-09-26 v4.11.56 追加）

这两份**不是**生产机上的文件，而是本地发版过程产物里**带版本号、不可重生成**的部分
（打包台 `_pkg/`、校验清单 `pkg_expected_md5.txt` 已按 `.gitignore` 忽略，不进库）。

| 文件 | md5 | 字节 | 行 | 用途 |
|---|---|---|---|---|
| `v4.11.50_血缘台账.md` | `1981ae33946c9a5d4fd9c4b99dd80986` | 27919 | 171 | **v4.11.50 生产血缘对齐的逐文件四档判定台账**（✅/🔵/⚪/🔴），是「真缺口 0」自证的**原始证据**。原在打包台 `_pkg/` 里，2026-09-26 移出归档 |
| `scripts/deploy_v41153_prod.py` | `0f1228114c509c80794914b0cf965d6e` | 5509 | 165 | **v4.11.53 生产整批部署脚本**（paramiko + 26 文件清单）。密码走环境变量 `KX_PROD_PASS`、**无明文**；但因 `FILES` 里**写死了 26 个路径**（版本绑定）⇒ 按 L166-172 判据归档，**不进 `scripts/`** |

> 复用提示：`deploy_v41153_prod.py` 需 `pip install paramiko`，且其 `FILES` 与 v4.11.53 那一次
> 整批绑定；内置 `PROXY = ("127.0.0.1", 18080)` 是本机沙箱代理，换环境必须改。

---

## 依赖（还原后若还要跑，需先确认这些在位）

- 全部依赖生产机绝对路径 `/opt/kuaixuan/kuaixuan.db`、`/opt/kuaixuan/aipick/`、
  `/opt/kuaixuan/aipick/models/`、`/opt/kuaixuan/aipick/output/`。
- `backend/scripts/*` 依赖 `app.core.config`、`app.services.kpl`（在 `backend/` 下、
  需 service env 才含 `SSL_CERT_FILE`）。
- `scripts/aipick/_audit_amt.py` 依赖同目录 `train_v2.py` 的 `load_base` / `roc_auc`
  （`train_v2.py` **已入库**）；数据源是 `data/trainsetv2`（7 年基座，约 772 万行）。
- Python：`_audit_amt.py` / `_alltp.py` / `_scan639.py` 需 `pandas` + `numpy`（+`xgboost`）。
  本机用隔离 venv `/Users/batong/.workbuddy/binaries/python/envs/kuaixuan/bin/python`。

---

## 维护纪律

1. **新文件先判断「通用 vs 版本绑定」**（`.gitignore` L166-172 的判据）：
   - 通用工具 → 参数化后放进 `scripts/`，按先例在 `.gitignore` 加 `!` 例外**正式收编**。
   - 一次性探针 / 版本绑定脚本 → 归档到本目录，**不要**塞进 `scripts/`。
2. 往本目录加文件时，**必须**同步更新上表的 md5/字节/行数与「已跑过」列。
3. 本目录只增不删。确实要删某文件时，先在 `README.md` 里留一条说明（何时、为何），再删。
4. 从生产机取回文件归档后，**务必做一次 `tr -d '\r' | md5` 对拍**再写进表里 ——
   CRLF/LF 差异会制造假差异（本仓库文档记过这个坑）。
5. 🔴 **清理临时工作台（`_pkg/`、`scripts/deploy_tmp/`、`_research/` 等）之前，先扫一遍里面有没有
   带版本号的文档/脚本**，有就先移进本目录再清。这些目录都在 `.gitignore` 里 ⇒ 一旦删掉，
   **git 救不回来**（本目录存在的全部理由就是这个失败模式）。

---

## 相关记录

- `docs/history.md` 的 **v4.11.50** 条目：生产补丁反向回写本地仓 —— 血缘对齐
  （本目录的由来；含三方对拍取证方法与「真缺口 0」的自证）。
- **`ops/archive/v4.11.50_血缘台账.md`**（原 `_pkg/v4.11.50_血缘台账.md`，2026-09-26 移入）：
  逐文件四档判定（✅已入库且逐位相同 / 🔵已入库·仓里更新 / ⚪本机有副本·未入库 / 🔴真缺口）。
- 建立日期：**2026-09-26**（v4.11.51）。本地版本绑定产物一节于同日 **v4.11.56** 追加。
