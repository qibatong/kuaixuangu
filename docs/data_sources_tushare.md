# Tushare Replay 接口文档（全量）

> 本页根据代理网关接口目录（`catalog.json`）自动整理，覆盖全部分类的 137 个接口（125 个唯一接口名）。
> 供 kuaixuan（快选 · 竞价 AI 选股系统）后续接入 Tushare 数据时参考。

## 一、接入方式（代理网关）

系统不直连 tushare 官方，而是通过自建/第三方代理网关访问 Tushare Pro 的 replay 数据。开发时无需积分校验与积分判断。

| 项 | 说明 |
|---|---|
| 网关 BaseURL | `https://ai-tool.indevs.in` |
| 认证 | 请求头携带 `X-API-Key: <api_key>` |
| 完整请求路径 | `{BASE_URL}/tushare{接口path}`，例如 `{BASE_URL}/tushare/pro/daily_basic` |
| 数据模式 | `non-minute-only`（分钟/实时 tick 类接口被网关屏蔽） |
| 返回结构 | `{ api_name, count, 字段... }`，字段按请求的 `fields` 返回 |
| 请求方式 | HTTP GET，参数走 query string |

### 通用 Python 请求示例

```python
import requests

API_KEY = '<你的-api-key>'
BASE_URL = 'https://ai-tool.indevs.in'

session = requests.Session()
session.trust_env = False          # 关闭环境代理，避免该网关被代理拦截
headers = {'X-API-Key': API_KEY}

def get_json(path: str, params: dict) -> dict:
    resp = session.get(BASE_URL + path, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

data = get_json('/tushare/pro/daily_basic', {'ts_code': '000002.SZ', 'trade_date': '20260424'})
print('api_name=', data.get('api_name'), 'count=', data.get('count'))
print(data)
```

> 生产环境建议：多 `BASE_URL` 故障切换 + `urllib3` 重试（429/5xx 自动重试），示例可参考各接口内置的 `python_example`。

## 二、被网关屏蔽的接口（不可用）

以下分钟线/实时 tick 类接口在 replay 网关中不可用：

`fund_min`, `index_min`, `realtime_tick`, `rt_min`, `rt_tick`, `stk_mins`

> 注意：分类中出现的 `hk_mins`、`us_mins`、`fut_min`、`opt_mins` 等为日/周级别外的低频接口，未被屏蔽，仍可调用。

## 三、缓存时效策略

网关按下表缓存数据，请求相同参数会命中缓存，避免重复拉取：

| 缓存桶 | 时效 |
|---|---|
| basic_and_mapping（基础/映射） | 通常 7 天 |
| calendar（交易日历） | 通常 30 天 |
| daily_family（日线族） | 通常到北京时间当日 24:00 |
| historical_date_queries（历史区间查询） | 通常 3 到 7 天 |
| news（新闻） | 约 15 分钟 |
| taxonomy（板块/成分等分类） | 通常 7 天 |
| fallback（兜底） | 默认 6 小时 |

缓存建议（对接 kuaixuan 时）：

1. 日线族数据当日收盘后重复值高，**收盘后到晚间补齐阶段最值得刷新**；跨日务必重新拉取（不要让本地缓存过期后继续用）。
2. 新闻类时效约 15 分钟，用于盘中决策时按需拉取。
3. 基础/映射/交易日历稳定，可本地缓存 7–30 天，减少网关压力。

## 四、接口明细（按分类）

各接口小节包含：**完整调用路径、示例参数、返回字段、缓存时效**。这里用简化 `get_json(path, params)` 演示调用。

### 1. ETF数据

| 接口名 | 调用路径 |
|---|---|
| `fund_basic` | `/tushare/pro/fund_basic` |
| `fund_daily` | `/tushare/pro/fund_daily` |
| `fund_nav` | `/tushare/pro/fund_nav` |

#### `fund_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/fund_basic?market=E`
- **调用路径**：`GET /tushare/pro/fund_basic`

| 参数 | 示例值 |
|---|---|
| market | 'E' |

- **返回字段**：`ts_code` `name` `management` `custodian` `fund_type` `found_date` `list_date`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：合约、基金、指数与标的基础资料变化相对低频。
  - 刷新建议：基础资料建议按天或按周刷新

```python
data = get_json('/tushare/pro/fund_basic', {"market": "E"})
print(data)
```

#### `fund_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/fund_daily?ts_code=510300.SH`
- **调用路径**：`GET /tushare/pro/fund_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | '510300.SH' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `vol` `amount`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/fund_daily', {"ts_code": "510300.SH"})
print(data)
```

#### `fund_nav`

- **示例URL**：`https://ai-tool.indevs.in/pro/fund_nav?ts_code=510300.SH&end_date=20260320`
- **调用路径**：`GET /tushare/pro/fund_nav`

| 参数 | 示例值 |
|---|---|
| end_date | '20260320' |
| ts_code | '510300.SH' |

- **返回字段**：`ts_code` `end_date` `unit_nav` `accum_nav` `adj_nav`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：历史快照已基本稳定，但保留一周有利于吸收源端补录或小幅修订。
  - 刷新建议：历史日期快照一般按周刷新即可

```python
data = get_json('/tushare/pro/fund_nav', {"end_date": "20260320", "ts_code": "510300.SH"})
print(data)
```

### 2. 主连/连续合约数据

| 接口名 | 调用路径 |
|---|---|
| `fut_mapping` | `/tushare/pro/fut_mapping` |

#### `fut_mapping`

- **示例URL**：`https://ai-tool.indevs.in/pro/fut_mapping?ts_code=IF.CFX`
- **调用路径**：`GET /tushare/pro/fut_mapping`

| 参数 | 示例值 |
|---|---|
| ts_code | 'IF.CFX' |

- **返回字段**：`ts_code` `trade_date` `mapping_ts_code`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：基础资料与主连映射变化相对低频，但不能无限期缓存。
  - 刷新建议：基础资料与映射关系建议按天或按周刷新

```python
data = get_json('/tushare/pro/fut_mapping', {"ts_code": "IF.CFX"})
print(data)
```

### 3. 互动易问答（沪深）

| 接口名 | 调用路径 |
|---|---|
| `irm_qa_sh` | `/tushare/pro/irm_qa_sh` |
| `irm_qa_sz` | `/tushare/pro/irm_qa_sz` |

#### `irm_qa_sh`

- **示例URL**：`https://ai-tool.indevs.in/pro/irm_qa_sh?ts_code=600000.SH`
- **调用路径**：`GET /tushare/pro/irm_qa_sh`

| 参数 | 示例值 |
|---|---|
| ts_code | '600000.SH' |

- **返回字段**：`ts_code` `ann_date` `q` `a`

- **缓存时效**：默认 6 小时（TTL≈21600s）
  - 说明：未显式分类的接口先采用保守策略，后续再按实际稳定性细化。
  - 刷新建议：未知类型先按 6 小时观察

```python
data = get_json('/tushare/pro/irm_qa_sh', {"ts_code": "600000.SH"})
print(data)
```

#### `irm_qa_sz`

- **示例URL**：`https://ai-tool.indevs.in/pro/irm_qa_sz?ts_code=000001.SZ`
- **调用路径**：`GET /tushare/pro/irm_qa_sz`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |

- **返回字段**：`ts_code` `ann_date` `q` `a`

- **缓存时效**：默认 6 小时（TTL≈21600s）
  - 说明：未显式分类的接口先采用保守策略，后续再按实际稳定性细化。
  - 刷新建议：未知类型先按 6 小时观察

```python
data = get_json('/tushare/pro/irm_qa_sz', {"ts_code": "000001.SZ"})
print(data)
```

### 4. 全球指数数据

| 接口名 | 调用路径 |
|---|---|
| `index_global` | `/tushare/pro/index_global` |

#### `index_global`

- **示例URL**：`https://ai-tool.indevs.in/pro/index_global?ts_code=HSI&start_date=20260301&end_date=20260322`
- **调用路径**：`GET /tushare/pro/index_global`

| 参数 | 示例值 |
|---|---|
| ts_code | 'HSI' |
| start_date | '20260301' |
| end_date | '20260322' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/index_global', {"ts_code": "HSI", "start_date": "20260301", "end_date": "20260322"})
print(data)
```

### 5. 外汇数据

| 接口名 | 调用路径 |
|---|---|
| `fx_obasic` | `/tushare/pro/fx_obasic` |
| `fx_daily` | `/tushare/pro/fx_daily` |

#### `fx_obasic`

- **示例URL**：`https://ai-tool.indevs.in/pro/fx_obasic?ts_code=USDCNH.FXCM`
- **调用路径**：`GET /tushare/pro/fx_obasic`

| 参数 | 示例值 |
|---|---|
| ts_code | 'USDCNH.FXCM' |

- **返回字段**：`ts_code` `symbol` `name` `fullname` `exchange` `market` `classify` `base_currency` `quote_currency` `list_status` `list_date` `delist_date` `min_unit` `pip` `pip_cost`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：合约、基金、指数与标的基础资料变化相对低频。
  - 刷新建议：基础资料建议按天或按周刷新

```python
data = get_json('/tushare/pro/fx_obasic', {"ts_code": "USDCNH.FXCM"})
print(data)
```

#### `fx_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/fx_daily?ts_code=USDCNH.FXCM`
- **调用路径**：`GET /tushare/pro/fx_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | 'USDCNH.FXCM' |

- **返回字段**：`ts_code` `trade_date` `bid_open` `bid_close` `bid_high` `bid_low` `tick_qty`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/fx_daily', {"ts_code": "USDCNH.FXCM"})
print(data)
```

### 6. 指数数据

| 接口名 | 调用路径 |
|---|---|
| `index_basic` | `/tushare/pro/index_basic` |
| `index_daily` | `/tushare/pro/index_daily` |
| `index_weight` | `/tushare/pro/index_weight` |
| `index_weekly` | `/tushare/pro/index_weekly` |
| `index_monthly` | `/tushare/pro/index_monthly` |

#### `index_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/index_basic?market=SSE&limit=200`
- **调用路径**：`GET /tushare/pro/index_basic`

| 参数 | 示例值 |
|---|---|
| market | 'SSE' |
| limit | '200' |

- **返回字段**：`ts_code` `name` `market` `category` `list_date`

- **缓存时效**：当前缓存 1 天，陈旧缓存 7 天内清理
  - 说明：指数列表和基础元数据更新频率低，优先复用站内整理结果可以减少上游消耗。
  - 刷新建议：基础资料通常日内不会频繁变化，隔天刷新即可

```python
data = get_json('/tushare/pro/index_basic', {"market": "SSE", "limit": "200"})
print(data)
```

#### `index_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/index_daily?ts_code=000001.SH&start_date=2026-03-01&end_date=2026-03-27`
- **调用路径**：`GET /tushare/pro/index_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SH' |
| start_date | '2026-03-01' |
| end_date | '2026-03-27' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：历史查询通常保留 7 天，陈旧缓存 30 天内清理
  - 说明：指数历史序列天然适合缓存，站内结果可覆盖大部分常见指数，剩余缺口保留原生上游兜底。
  - 刷新建议：历史区间按需调用即可；需要补齐最近交易日时可主动刷新

```python
data = get_json('/tushare/pro/index_daily', {"ts_code": "000001.SH", "start_date": "2026-03-01", "end_date": "2026-03-27"})
print(data)
```

#### `index_weight`

- **示例URL**：`https://ai-tool.indevs.in/pro/index_weight?index_code=000001.SH`
- **调用路径**：`GET /tushare/pro/index_weight`

| 参数 | 示例值 |
|---|---|
| index_code | '000001.SH' |

- **返回字段**：`index_code` `trade_date` `con_code` `weight`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：历史快照已基本稳定，但保留一周有利于吸收源端补录或小幅修订。
  - 刷新建议：历史日期快照一般按周刷新即可

```python
data = get_json('/tushare/pro/index_weight', {"index_code": "000001.SH"})
print(data)
```

#### `index_weekly`

- **示例URL**：`https://ai-tool.indevs.in/pro/index_weekly?ts_code=000001.SH&start_date=2024-01-01&end_date=2026-03-27`
- **调用路径**：`GET /tushare/pro/index_weekly`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SH' |
| start_date | '2024-01-01' |
| end_date | '2026-03-27' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：历史查询通常保留 7 天，陈旧缓存 30 天内清理
  - 说明：指数历史序列天然适合缓存，站内结果可覆盖大部分常见指数，剩余缺口保留原生上游兜底。
  - 刷新建议：历史区间按需调用即可；需要补齐最近交易日时可主动刷新

```python
data = get_json('/tushare/pro/index_weekly', {"ts_code": "000001.SH", "start_date": "2024-01-01", "end_date": "2026-03-27"})
print(data)
```

#### `index_monthly`

- **示例URL**：`https://ai-tool.indevs.in/pro/index_monthly?ts_code=000001.SH&start_date=2018-01-01&end_date=2026-03-27`
- **调用路径**：`GET /tushare/pro/index_monthly`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SH' |
| start_date | '2018-01-01' |
| end_date | '2026-03-27' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：历史查询通常保留 7 天，陈旧缓存 30 天内清理
  - 说明：指数历史序列天然适合缓存，站内结果可覆盖大部分常见指数，剩余缺口保留原生上游兜底。
  - 刷新建议：历史区间按需调用即可；需要补齐最近交易日时可主动刷新

```python
data = get_json('/tushare/pro/index_monthly', {"ts_code": "000001.SH", "start_date": "2018-01-01", "end_date": "2026-03-27"})
print(data)
```

### 7. 新闻数据

| 接口名 | 调用路径 |
|---|---|
| `news` | `/tushare/pro/news` |
| `major_news` | `/tushare/pro/major_news` |
| `express_news` | `/tushare/pro/express_news` |
| `gdelt_industry_daily_timeline` | `/tushare/pro/gdelt_industry_daily_timeline` |
| `cjzc` | `/tushare/pro/cjzc` |
| `cjzc` | `/tushare/pro/cjzc` |
| `news_cctv` | `/tushare/pro/news_cctv` |
| `news_economic_baidu` | `/tushare/pro/news_economic_baidu` |
| `news_report_time_baidu` | `/tushare/pro/news_report_time_baidu` |
| `news_trade_notify_dividend_baidu` | `/tushare/pro/news_trade_notify_dividend_baidu` |
| `news_trade_notify_suspend_baidu` | `/tushare/pro/news_trade_notify_suspend_baidu` |
| `sge_daily` | `/tushare/pro/sge_daily` |

#### `news`

- **示例URL**：`https://ai-tool.indevs.in/pro/news?src=cls&start_date=2026-04-14+00:00:00&end_date=2026-04-14+23:59:59`
- **调用路径**：`GET /tushare/pro/news`

| 参数 | 示例值 |
|---|---|
| src | 'cls' |
| start_date | '2026-04-14 00:00:00' |
| end_date | '2026-04-14 23:59:59' |

- **返回字段**：`datetime` `content` `title` `channels`

- **缓存时效**：约 15 分钟（TTL≈900s）
  - 说明：新闻流更新快，但短时重复请求复用同一批抓取结果最划算。
  - 刷新建议：新闻监控建议 5 到 15 分钟刷新一次

```python
data = get_json('/tushare/pro/news', {"src": "cls", "start_date": "2026-04-14 00:00:00", "end_date": "2026-04-14 23:59:59"})
print(data)
```

#### `major_news`

- **示例URL**：`https://ai-tool.indevs.in/pro/major_news?src=%E8%B4%A2%E8%81%94%E7%A4%BE&start_date=2026-04-14+00:00:00&end_date=2026-04-14+23:59:59`
- **调用路径**：`GET /tushare/pro/major_news`

| 参数 | 示例值 |
|---|---|
| src | '财联社' |
| start_date | '2026-04-14 00:00:00' |
| end_date | '2026-04-14 23:59:59' |

- **返回字段**：`pub_time` `src` `title` `content`

- **缓存时效**：约 15 分钟（TTL≈900s）
  - 说明：新闻流更新快，但短时重复请求复用同一批抓取结果最划算。
  - 刷新建议：新闻监控建议 5 到 15 分钟刷新一次

```python
data = get_json('/tushare/pro/major_news', {"src": "财联社", "start_date": "2026-04-14 00:00:00", "end_date": "2026-04-14 23:59:59"})
print(data)
```

#### `express_news`

- **示例URL**：`https://ai-tool.indevs.in/pro/express_news?scope=all&limit=50`
- **调用路径**：`GET /tushare/pro/express_news`

| 参数 | 示例值 |
|---|---|
| scope | 'all' |
| limit | '50' |

- **返回字段**：`title` `content` `datetime` `src`

- **缓存时效**：当前缓存 15 分钟，陈旧缓存 2 天内清理
  - 说明：快讯流更新速度快，短缓存能抑制重复请求，但不适合像 major_news 一样跨天长保留。
  - 刷新建议：高频盯盘时每 10 到 15 分钟刷新一次；重大行情时可主动回源

```python
data = get_json('/tushare/pro/express_news', {"scope": "all", "limit": "50"})
print(data)
```

#### `gdelt_industry_daily_timeline`

- **示例URL**：`https://ai-tool.indevs.in/pro/gdelt_industry_daily_timeline?query=%22artificial+intelligence%22&start_date=2026-04-01&end_date=2026-04-10&limit=30`
- **调用路径**：`GET /tushare/pro/gdelt_industry_daily_timeline`

| 参数 | 示例值 |
|---|---|
| query | '"artificial intelligence"' |
| start_date | '2026-04-01' |
| end_date | '2026-04-10' |
| limit | '30' |

- **返回字段**：`date` `article_count` `total_monitored_articles` `coverage_ratio`

- **缓存时效**：历史查询通常保留 7 天，陈旧缓存 30 天内清理
  - 说明：这是低频日度时间线，适合做历史回放与主题热度监控，不需要像快讯一样高频回源。
  - 刷新建议：同一 query 的历史区间通常按需拉一次即可；需要补最近一天时再主动刷新

```python
data = get_json('/tushare/pro/gdelt_industry_daily_timeline', {"query": "\"artificial intelligence\"", "start_date": "2026-04-01", "end_date": "2026-04-10", "limit": "30"})
print(data)
```

#### `cjzc`

- **示例URL**：`https://ai-tool.indevs.in/pro/cjzc?limit=20`
- **调用路径**：`GET /tushare/pro/cjzc`

| 参数 | 示例值 |
|---|---|
| limit | '20' |

- **返回字段**：`title` `summary` `pub_time` `url` `src`

- **缓存时效**：当前缓存 12 小时，陈旧缓存 14 天内清理
  - 说明：财经早餐以日更为主，没有必要像快讯那样高频刷新，但仍需要保留当天最新版本。
  - 刷新建议：早盘前后或晨会前拉一次通常就够；需要确认是否更新时可主动回源

```python
data = get_json('/tushare/pro/cjzc', {"limit": "20"})
print(data)
```

#### `cjzc`

- **示例URL**：`https://ai-tool.indevs.in/pro/cjzc?start_date=2026-03-20&end_date=2026-03-27`
- **调用路径**：`GET /tushare/pro/cjzc`

| 参数 | 示例值 |
|---|---|
| start_date | '2026-03-20' |
| end_date | '2026-03-27' |

- **返回字段**：`title` `summary` `pub_time` `url` `src`

- **缓存时效**：历史查询按需缓存，通常保留 7 天，适合做回放与补抓
  - 说明：财经早餐单次即可拉回完整历史列表，站内按发布时间切片后再缓存更稳妥。
  - 刷新建议：需要历史晨报时按日期区间调用即可，无需高频刷新

```python
data = get_json('/tushare/pro/cjzc', {"start_date": "2026-03-20", "end_date": "2026-03-27"})
print(data)
```

#### `news_cctv`

- **示例URL**：`https://ai-tool.indevs.in/pro/news_cctv?date=2026-03-26&limit=20`
- **调用路径**：`GET /tushare/pro/news_cctv`

| 参数 | 示例值 |
|---|---|
| date | '2026-03-26' |
| limit | '20' |

- **返回字段**：`date` `title` `content`

- **缓存时效**：带日期请求走历史缓存；未传日期时按当前数据缓存
  - 说明：这些接口天然是单日视图，缓存策略按是否显式指定日期切分更符合 replay 语义。
  - 刷新建议：回放历史时按需查询；查询当日数据时可在事件更新后再次刷新

```python
data = get_json('/tushare/pro/news_cctv', {"date": "2026-03-26", "limit": "20"})
print(data)
```

#### `news_economic_baidu`

- **示例URL**：`https://ai-tool.indevs.in/pro/news_economic_baidu?date=2026-03-26&limit=50`
- **调用路径**：`GET /tushare/pro/news_economic_baidu`

| 参数 | 示例值 |
|---|---|
| date | '2026-03-26' |
| limit | '50' |

- **返回字段**：`日期` `时间` `地区` `事件` `公布` `预期` `前值` `重要性`

- **缓存时效**：带日期请求走历史缓存；未传日期时按当前数据缓存
  - 说明：这些接口天然是单日视图，缓存策略按是否显式指定日期切分更符合 replay 语义。
  - 刷新建议：回放历史时按需查询；查询当日数据时可在事件更新后再次刷新

```python
data = get_json('/tushare/pro/news_economic_baidu', {"date": "2026-03-26", "limit": "50"})
print(data)
```

#### `news_report_time_baidu`

- **示例URL**：`https://ai-tool.indevs.in/pro/news_report_time_baidu?date=2026-03-26&limit=50`
- **调用路径**：`GET /tushare/pro/news_report_time_baidu`

| 参数 | 示例值 |
|---|---|
| date | '2026-03-26' |
| limit | '50' |

- **返回字段**：`股票代码` `股票简称` `交易所` `财报类型` `发布时间` `市值` `发布日期`

- **缓存时效**：带日期请求走历史缓存；未传日期时按当前数据缓存
  - 说明：这些接口天然是单日视图，缓存策略按是否显式指定日期切分更符合 replay 语义。
  - 刷新建议：回放历史时按需查询；查询当日数据时可在事件更新后再次刷新

```python
data = get_json('/tushare/pro/news_report_time_baidu', {"date": "2026-03-26", "limit": "50"})
print(data)
```

#### `news_trade_notify_dividend_baidu`

- **示例URL**：`https://ai-tool.indevs.in/pro/news_trade_notify_dividend_baidu?date=2026-03-26&limit=50`
- **调用路径**：`GET /tushare/pro/news_trade_notify_dividend_baidu`

| 参数 | 示例值 |
|---|---|
| date | '2026-03-26' |
| limit | '50' |

- **返回字段**：`股票代码` `股票简称` `交易所` `除权日` `分红` `送股` `转增` `实物` `报告期`

- **缓存时效**：带日期请求走历史缓存；未传日期时按当前数据缓存
  - 说明：这些接口天然是单日视图，缓存策略按是否显式指定日期切分更符合 replay 语义。
  - 刷新建议：回放历史时按需查询；查询当日数据时可在事件更新后再次刷新

```python
data = get_json('/tushare/pro/news_trade_notify_dividend_baidu', {"date": "2026-03-26", "limit": "50"})
print(data)
```

#### `news_trade_notify_suspend_baidu`

- **示例URL**：`https://ai-tool.indevs.in/pro/news_trade_notify_suspend_baidu?date=2026-03-26&limit=50`
- **调用路径**：`GET /tushare/pro/news_trade_notify_suspend_baidu`

| 参数 | 示例值 |
|---|---|
| date | '2026-03-26' |
| limit | '50' |

- **返回字段**：`股票代码` `股票简称` `交易所代码` `停牌时间` `复牌时间` `停牌事项说明` `市值` `公告日期` `公告时间` `证券类型` `市场类型` `是否跳过`

- **缓存时效**：带日期请求走历史缓存；未传日期时按当前数据缓存
  - 说明：这些接口天然是单日视图，缓存策略按是否显式指定日期切分更符合 replay 语义。
  - 刷新建议：回放历史时按需查询；查询当日数据时可在事件更新后再次刷新

```python
data = get_json('/tushare/pro/news_trade_notify_suspend_baidu', {"date": "2026-03-26", "limit": "50"})
print(data)
```

#### `sge_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/sge_daily?ts_code=Au99.99.SGE&start_date=20260401&end_date=20260420`
- **调用路径**：`GET /tushare/pro/sge_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | 'Au99.99.SGE' |
| start_date | '20260401' |
| end_date | '20260420' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：历史查询通常保留 7 天，陈旧缓存 30 天内清理
  - 说明：sge_daily 是标准历史日行情接口，历史区间请求天然适合和 current 查询分桶缓存。
  - 刷新建议：历史区间按需拉取即可；最近交易日补数可在收盘后刷新

```python
data = get_json('/tushare/pro/sge_daily', {"ts_code": "Au99.99.SGE", "start_date": "20260401", "end_date": "20260420"})
print(data)
```

### 8. 期权数据

| 接口名 | 调用路径 |
|---|---|
| `opt_basic` | `/tushare/pro/opt_basic` |
| `opt_daily` | `/tushare/pro/opt_daily` |

#### `opt_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/opt_basic?exchange=SSE`
- **调用路径**：`GET /tushare/pro/opt_basic`

| 参数 | 示例值 |
|---|---|
| exchange | 'SSE' |

- **返回字段**：`ts_code` `name` `call_put` `exercise_price` `list_date` `delist_date`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：合约、基金、指数与标的基础资料变化相对低频。
  - 刷新建议：基础资料建议按天或按周刷新

```python
data = get_json('/tushare/pro/opt_basic', {"exchange": "SSE"})
print(data)
```

#### `opt_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/opt_daily?ts_code=10008055.SH`
- **调用路径**：`GET /tushare/pro/opt_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | '10008055.SH' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `vol` `amount` `oi`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/opt_daily', {"ts_code": "10008055.SH"})
print(data)
```

### 9. 期货数据

| 接口名 | 调用路径 |
|---|---|
| `fut_basic` | `/tushare/pro/fut_basic` |
| `fut_daily` | `/tushare/pro/fut_daily` |
| `fut_mapping` | `/tushare/pro/fut_mapping` |
| `fut_settle` | `/tushare/pro/fut_settle` |

#### `fut_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/fut_basic?exchange=CFFEX`
- **调用路径**：`GET /tushare/pro/fut_basic`

| 参数 | 示例值 |
|---|---|
| exchange | 'CFFEX' |

- **返回字段**：`ts_code` `symbol` `exchange` `name` `fut_code` `multiplier` `trade_unit`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：合约、基金、指数与标的基础资料变化相对低频。
  - 刷新建议：基础资料建议按天或按周刷新

```python
data = get_json('/tushare/pro/fut_basic', {"exchange": "CFFEX"})
print(data)
```

#### `fut_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/fut_daily?ts_code=RB0&limit=20`
- **调用路径**：`GET /tushare/pro/fut_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | 'RB0' |
| limit | '20' |

- **返回字段**：`ts_code` `trade_date` `pre_close` `pre_settle` `open` `high` `low` `close` `settle` `change1` `change2` `vol` `amount` `oi` `oi_chg` `delv_settle`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/fut_daily', {"ts_code": "RB0", "limit": "20"})
print(data)
```

#### `fut_mapping`

- **示例URL**：`https://ai-tool.indevs.in/pro/fut_mapping?ts_code=IF.CFX`
- **调用路径**：`GET /tushare/pro/fut_mapping`

| 参数 | 示例值 |
|---|---|
| ts_code | 'IF.CFX' |

- **返回字段**：`ts_code` `trade_date` `mapping_ts_code`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：基础资料与主连映射变化相对低频，但不能无限期缓存。
  - 刷新建议：基础资料与映射关系建议按天或按周刷新

```python
data = get_json('/tushare/pro/fut_mapping', {"ts_code": "IF.CFX"})
print(data)
```

#### `fut_settle`

- **示例URL**：`https://ai-tool.indevs.in/pro/fut_settle?trade_date=20250115`
- **调用路径**：`GET /tushare/pro/fut_settle`

| 参数 | 示例值 |
|---|---|
| trade_date | '20250115' |

- **返回字段**：`ts_code` `trade_date` `settle` `trading_fee_rate` `trading_fee` `delivery_fee` `b_hedging_margin_rate` `s_hedging_margin_rate` `long_margin_rate` `short_margin_rate`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：历史快照已基本稳定，但保留一周有利于吸收源端补录或小幅修订。
  - 刷新建议：历史日期快照一般按周刷新即可

```python
data = get_json('/tushare/pro/fut_settle', {"trade_date": "20250115"})
print(data)
```

### 10. 板块数据

| 接口名 | 调用路径 |
|---|---|
| `concept` | `/tushare/pro/concept` |
| `concept_detail` | `/tushare/pro/concept_detail` |
| `index_classify` | `/tushare/pro/index_classify` |
| `index_member` | `/tushare/pro/index_member` |
| `ths_index` | `/tushare/pro/ths_index` |
| `ths_member` | `/tushare/pro/ths_member` |
| `the_member` | `/tushare/pro/the_member` |
| `block_moneyflow` | `/tushare/pro/block_moneyflow` |
| `dc_index_prev` | `/tushare/pro/dc_index_prev` |

#### `concept`

- **示例URL**：`https://ai-tool.indevs.in/pro/concept?src=ts`
- **调用路径**：`GET /tushare/pro/concept`

| 参数 | 示例值 |
|---|---|
| src | 'ts' |

- **返回字段**：`code` `name` `src`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：概念主题与成分关系变化低频，但市场热点变化会推动偶发调整。
  - 刷新建议：概念分类和成分关系建议每天到每周刷新一次

```python
data = get_json('/tushare/pro/concept', {"src": "ts"})
print(data)
```

#### `concept_detail`

- **示例URL**：`https://ai-tool.indevs.in/pro/concept_detail?limit=3`
- **调用路径**：`GET /tushare/pro/concept_detail`

| 参数 | 示例值 |
|---|---|
| limit | '3' |

- **返回字段**：`id` `concept_name` `ts_code` `name` `in_date` `out_date`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：概念主题与成分关系变化低频，但市场热点变化会推动偶发调整。
  - 刷新建议：概念分类和成分关系建议每天到每周刷新一次

```python
data = get_json('/tushare/pro/concept_detail', {"limit": "3"})
print(data)
```

#### `index_classify`

- **示例URL**：`https://ai-tool.indevs.in/pro/index_classify?level=L1&src=SW2021`
- **调用路径**：`GET /tushare/pro/index_classify`

| 参数 | 示例值 |
|---|---|
| level | 'L1' |
| src | 'SW2021' |

- **返回字段**：`index_code` `industry_name` `level` `industry_code` `is_pub` `parent_code` `src`

- **缓存时效**：默认 6 小时（TTL≈21600s）
  - 说明：未显式分类的接口先采用保守策略，后续再按实际稳定性细化。
  - 刷新建议：未知类型先按 6 小时观察

```python
data = get_json('/tushare/pro/index_classify', {"level": "L1", "src": "SW2021"})
print(data)
```

#### `index_member`

- **示例URL**：`https://ai-tool.indevs.in/pro/index_member?index_code=801010.SI`
- **调用路径**：`GET /tushare/pro/index_member`

| 参数 | 示例值 |
|---|---|
| index_code | '801010.SI' |

- **返回字段**：`index_code` `con_code` `in_date` `out_date` `is_new`

- **缓存时效**：默认 6 小时（TTL≈21600s）
  - 说明：未显式分类的接口先采用保守策略，后续再按实际稳定性细化。
  - 刷新建议：未知类型先按 6 小时观察

```python
data = get_json('/tushare/pro/index_member', {"index_code": "801010.SI"})
print(data)
```

#### `ths_index`

- **示例URL**：`https://ai-tool.indevs.in/pro/ths_index?exchange=A&type=N`
- **调用路径**：`GET /tushare/pro/ths_index`

| 参数 | 示例值 |
|---|---|
| exchange | 'A' |
| type | 'N' |

- **返回字段**：`ts_code` `name` `count` `exchange` `list_date` `type`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：概念主题与成分关系变化低频，但市场热点变化会推动偶发调整。
  - 刷新建议：概念分类和成分关系建议每天到每周刷新一次

```python
data = get_json('/tushare/pro/ths_index', {"exchange": "A", "type": "N"})
print(data)
```

#### `ths_member`

- **示例URL**：`https://ai-tool.indevs.in/pro/ths_member?ts_code=885573.TI`
- **调用路径**：`GET /tushare/pro/ths_member`

| 参数 | 示例值 |
|---|---|
| ts_code | '885573.TI' |

- **返回字段**：`ts_code` `con_code` `name` `weight` `in_date` `out_date` `is_new`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：概念主题与成分关系变化低频，但市场热点变化会推动偶发调整。
  - 刷新建议：概念分类和成分关系建议每天到每周刷新一次

```python
data = get_json('/tushare/pro/ths_member', {"ts_code": "885573.TI"})
print(data)
```

#### `the_member`

- **示例URL**：`https://ai-tool.indevs.in/pro/the_member?ts_code=885573.TI`
- **调用路径**：`GET /tushare/pro/the_member`

| 参数 | 示例值 |
|---|---|
| ts_code | '885573.TI' |

- **返回字段**：`ts_code` `con_code` `name` `weight` `in_date` `out_date` `is_new`

- **缓存时效**：当前按 fallback 策略缓存，通常 6 小时
  - 说明：成分关系属于低频变更数据，重心在于权限可用性与透传稳定性，不在分钟级刷新。
  - 刷新建议：板块成分变化通常不需要高频轮询，按需查询或盘后刷新更合适

```python
data = get_json('/tushare/pro/the_member', {"ts_code": "885573.TI"})
print(data)
```

#### `block_moneyflow`

- **示例URL**：`https://ai-tool.indevs.in/pro/block_moneyflow?limit=100`
- **调用路径**：`GET /tushare/pro/block_moneyflow`

| 参数 | 示例值 |
|---|---|
| limit | '100' |

- **返回字段**：`trade_date` `ts_code` `name` `lead_stock` `close_price` `pct_change` `industry_index` `company_num` `pct_change_stock` `net_buy_amount` `net_sell_amount` `net_amount`

```python
data = get_json('/tushare/pro/block_moneyflow', {"limit": "100"})
print(data)
```

#### `dc_index_prev`

- **示例URL**：`https://ai-tool.indevs.in/pro/dc_index_prev?date_str=20260327`
- **调用路径**：`GET /tushare/pro/dc_index_prev`

| 参数 | 示例值 |
|---|---|
| date_str | '20260327' |

- **返回字段**：`ts_code` `trade_date` `name` `leading` `pct_change` `turnover_rate` `up_num` `down_num`

```python
data = get_json('/tushare/pro/dc_index_prev', {"date_str": "20260327"})
print(data)
```

### 11. 港股数据

| 接口名 | 调用路径 |
|---|---|
| `hk_basic` | `/tushare/pro/hk_basic` |
| `hk_daily` | `/tushare/pro/hk_daily` |
| `hk_hold` | `/tushare/pro/hk_hold` |

#### `hk_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_basic?ts_code=00700.HK`
- **调用路径**：`GET /tushare/pro/hk_basic`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |

- **返回字段**：`ts_code` `symbol` `name` `fullname` `enname` `exchange` `market` `industry` `curr_type` `list_status` `list_date` `delist_date` `trade_unit` `isin`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：合约、基金、指数与标的基础资料变化相对低频。
  - 刷新建议：基础资料建议按天或按周刷新

```python
data = get_json('/tushare/pro/hk_basic', {"ts_code": "00700.HK"})
print(data)
```

#### `hk_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_daily?ts_code=00700.HK`
- **调用路径**：`GET /tushare/pro/hk_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/hk_daily', {"ts_code": "00700.HK"})
print(data)
```

#### `hk_hold`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_hold?trade_date=20260320&ts_code=00700.HK`
- **调用路径**：`GET /tushare/pro/hk_hold`

| 参数 | 示例值 |
|---|---|
| trade_date | '20260320' |
| ts_code | '00700.HK' |

- **返回字段**：`code` `trade_date` `ts_code` `name` `vol` `ratio` `exchange`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：历史快照已基本稳定，但保留一周有利于吸收源端补录或小幅修订。
  - 刷新建议：历史日期快照一般按周刷新即可

```python
data = get_json('/tushare/pro/hk_hold', {"trade_date": "20260320", "ts_code": "00700.HK"})
print(data)
```

### 12. 筹码分布

| 接口名 | 调用路径 |
|---|---|
| `cyq_chips` | `/tushare/pro/cyq_chips` |
| `cyq_perf` | `/tushare/pro/cyq_perf` |

#### `cyq_chips`

- **示例URL**：`https://ai-tool.indevs.in/pro/cyq_chips?ts_code=000002.SZ`
- **调用路径**：`GET /tushare/pro/cyq_chips`

| 参数 | 示例值 |
|---|---|
| ts_code | '000002.SZ' |

- **返回字段**：`ts_code` `trade_date` `price` `percent`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/cyq_chips', {"ts_code": "000002.SZ"})
print(data)
```

#### `cyq_perf`

- **示例URL**：`https://ai-tool.indevs.in/pro/cyq_perf?ts_code=000002.SZ`
- **调用路径**：`GET /tushare/pro/cyq_perf`

| 参数 | 示例值 |
|---|---|
| ts_code | '000002.SZ' |

- **返回字段**：`ts_code` `trade_date` `weight_avg` `cost_5pct` `cost_15pct` `cost_50pct` `cost_85pct` `cost_95pct`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/cyq_perf', {"ts_code": "000002.SZ"})
print(data)
```

### 13. 美股数据

| 接口名 | 调用路径 |
|---|---|
| `us_basic` | `/tushare/pro/us_basic` |
| `us_daily` | `/tushare/pro/us_daily` |
| `us_tradecal` | `/tushare/pro/us_tradecal` |

#### `us_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_basic?ts_code=AAPL`
- **调用路径**：`GET /tushare/pro/us_basic`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |

- **返回字段**：`ts_code` `name` `enname` `classify` `list_date` `delist_date`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：合约、基金、指数与标的基础资料变化相对低频。
  - 刷新建议：基础资料建议按天或按周刷新

```python
data = get_json('/tushare/pro/us_basic', {"ts_code": "AAPL"})
print(data)
```

#### `us_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_daily?ts_code=AAPL`
- **调用路径**：`GET /tushare/pro/us_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `vol` `amount`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/us_daily', {"ts_code": "AAPL"})
print(data)
```

#### `us_tradecal`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_tradecal?exchange=NYSE&start_date=20250101&end_date=20250131`
- **调用路径**：`GET /tushare/pro/us_tradecal`

| 参数 | 示例值 |
|---|---|
| exchange | 'NYSE' |
| start_date | '20250101' |
| end_date | '20250131' |

- **返回字段**：`exchange` `cal_date` `is_open` `pretrade_date`

- **缓存时效**：通常 30 天（TTL≈2592000s）
  - 说明：交易日历和休市安排稳定，适合长保留。
  - 刷新建议：每周或节假日前后刷新一次即可

```python
data = get_json('/tushare/pro/us_tradecal', {"exchange": "NYSE", "start_date": "20250101", "end_date": "20250131"})
print(data)
```

### 14. 股票

| 接口名 | 调用路径 |
|---|---|
| `daily_basic` | `/tushare/pro/daily_basic` |
| `adj_factor` | `/tushare/pro/adj_factor` |
| `moneyflow` | `/tushare/pro/moneyflow` |
| `stk_factor` | `/tushare/pro/stk_factor` |
| `stock_basic` | `/tushare/pro/stock_basic` |
| `etf_basic` | `/tushare/pro/etf_basic` |
| `stk_auction_replay` | `/tushare/pro/stk_auction_replay` |

#### `daily_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/daily_basic?ts_code=000002.SZ&trade_date=20260424`
- **调用路径**：`GET /tushare/pro/daily_basic`

| 参数 | 示例值 |
|---|---|
| ts_code | '000002.SZ' |
| trade_date | '20260424' |

- **返回字段**：`ts_code` `trade_date` `close` `turnover_rate` `volume_ratio` `pe` `pb` `total_mv` `circ_mv`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/daily_basic', {"ts_code": "000002.SZ", "trade_date": "20260424"})
print(data)
```

#### `adj_factor`

- **示例URL**：`https://ai-tool.indevs.in/pro/adj_factor?ts_code=000002.SZ`
- **调用路径**：`GET /tushare/pro/adj_factor`

| 参数 | 示例值 |
|---|---|
| ts_code | '000002.SZ' |

- **返回字段**：`ts_code` `trade_date` `adj_factor`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/adj_factor', {"ts_code": "000002.SZ"})
print(data)
```

#### `moneyflow`

- **示例URL**：`https://ai-tool.indevs.in/pro/moneyflow?ts_code=000002.SZ`
- **调用路径**：`GET /tushare/pro/moneyflow`

| 参数 | 示例值 |
|---|---|
| ts_code | '000002.SZ' |

- **返回字段**：`ts_code` `trade_date` `buy_sm_vol` `sell_sm_vol` `net_mf_vol`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/moneyflow', {"ts_code": "000002.SZ"})
print(data)
```

#### `stk_factor`

- **示例URL**：`https://ai-tool.indevs.in/pro/stk_factor?ts_code=000002.SZ`
- **调用路径**：`GET /tushare/pro/stk_factor`

| 参数 | 示例值 |
|---|---|
| ts_code | '000002.SZ' |

- **返回字段**：`ts_code` `trade_date` `close` `macd_kdj_rsi` `boll`

- **缓存时效**：通常到北京时间当日 24:00（TTL≈26662s）
  - 说明：这类数据按日收盘、日线生成或日终补齐，当日内重复值较高，跨日必须失效。
  - 刷新建议：交易日收盘后到晚间补齐阶段最值得刷新

```python
data = get_json('/tushare/pro/stk_factor', {"ts_code": "000002.SZ"})
print(data)
```

#### `stock_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/stock_basic?ts_code=000001.SZ&fields=ts_code,symbol,name,area,industry,list_date`
- **调用路径**：`GET /tushare/pro/stock_basic`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |
| fields | 'ts_code,symbol,name,area,industry,list_date' |

- **返回字段**：`ts_code` `symbol` `name` `area` `industry` `list_date`

- **缓存时效**：通常 7 天
  - 说明：股票基础资料属于低频变更元数据，天然适合长缓存，不值得高频直连上游。
  - 刷新建议：单只股票基础信息通常按需查询即可；批量代码表建议本地落盘后周期性更新

```python
data = get_json('/tushare/pro/stock_basic', {"ts_code": "000001.SZ", "fields": "ts_code,symbol,name,area,industry,list_date"})
print(data)
```

#### `etf_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/etf_basic?exchange=SH&limit=50&fields=ts_code,csname,cname,list_date,list_status,exchange,mgr_name`
- **调用路径**：`GET /tushare/pro/etf_basic`

| 参数 | 示例值 |
|---|---|
| exchange | 'SH' |
| limit | '50' |
| fields | 'ts_code,csname,cname,list_date,list_status,exchange,mgr_name' |

- **返回字段**：`ts_code` `csname` `cname` `list_date` `list_status` `exchange` `mgr_name`

```python
data = get_json('/tushare/pro/etf_basic', {"exchange": "SH", "limit": "50", "fields": "ts_code,csname,cname,list_date,list_status,exchange,mgr_name"})
print(data)
```

#### `stk_auction_replay`

- **示例URL**：`https://ai-tool.indevs.in/pro/stk_auction_replay?ts_code=000001.SZ&trade_date=20260820&mode=summary`
- **调用路径**：`GET /tushare/pro/stk_auction_replay`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |
| trade_date | '20260820' |
| mode | 'summary' |

- **返回字段**：`ts_code` `trade_date` `trade_time` `open` `high` `low` `close` `price` `vol` `amount` `vwap` `data_level` `is_complete`

- **缓存时效**：当天竞价短缓存，历史竞价通常按历史查询保留
  - 说明：集合竞价属于开盘前短窗口数据，当天口径会快速变化，历史查询则更适合稳定回放。
  - 刷新建议：盘前看竞价时按需刷新；盘后复盘则优先按 trade_date 取稳定结果

```python
data = get_json('/tushare/pro/stk_auction_replay', {"ts_code": "000001.SZ", "trade_date": "20260820", "mode": "summary"})
print(data)
```

### 15. 融资融券基础数据

| 接口名 | 调用路径 |
|---|---|
| `margin` | `/tushare/pro/margin` |
| `margin_detail` | `/tushare/pro/margin_detail` |

#### `margin`

- **示例URL**：`https://ai-tool.indevs.in/pro/margin?trade_date=20260320`
- **调用路径**：`GET /tushare/pro/margin`

| 参数 | 示例值 |
|---|---|
| trade_date | '20260320' |

- **返回字段**：`trade_date` `exchange_id` `rzye` `rzmre` `rzche` `rqye` `rqyl` `rqmcl` `rzrqye`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：历史快照已基本稳定，但保留一周有利于吸收源端补录或小幅修订。
  - 刷新建议：历史日期快照一般按周刷新即可

```python
data = get_json('/tushare/pro/margin', {"trade_date": "20260320"})
print(data)
```

#### `margin_detail`

- **示例URL**：`https://ai-tool.indevs.in/pro/margin_detail?trade_date=20260320&ts_code=000002.SZ`
- **调用路径**：`GET /tushare/pro/margin_detail`

| 参数 | 示例值 |
|---|---|
| trade_date | '20260320' |
| ts_code | '000002.SZ' |

- **返回字段**：`trade_date` `ts_code` `name` `rzye` `rzmre` `rzche` `rqye` `rqyl` `rqmcl`

- **缓存时效**：通常 7 天（TTL≈604800s）
  - 说明：历史快照已基本稳定，但保留一周有利于吸收源端补录或小幅修订。
  - 刷新建议：历史日期快照一般按周刷新即可

```python
data = get_json('/tushare/pro/margin_detail', {"trade_date": "20260320", "ts_code": "000002.SZ"})
print(data)
```

### 16. 部分其他类型数据

| 接口名 | 调用路径 |
|---|---|
| `trade_cal` | `/tushare/pro/trade_cal` |
| `namechange` | `/tushare/pro/namechange` |

#### `trade_cal`

- **示例URL**：`https://ai-tool.indevs.in/pro/trade_cal?exchange=SSE&start_date=20260301&end_date=20260331`
- **调用路径**：`GET /tushare/pro/trade_cal`

| 参数 | 示例值 |
|---|---|
| end_date | '20260331' |
| exchange | 'SSE' |
| start_date | '20260301' |

- **返回字段**：`exchange` `cal_date` `is_open` `pretrade_date`

- **缓存时效**：通常 30 天（TTL≈2592000s）
  - 说明：交易日历和休市安排稳定，适合长保留。
  - 刷新建议：每周或节假日前后刷新一次即可

```python
data = get_json('/tushare/pro/trade_cal', {"end_date": "20260331", "exchange": "SSE", "start_date": "20260301"})
print(data)
```

#### `namechange`

- **示例URL**：`https://ai-tool.indevs.in/pro/namechange?ts_code=000002.SZ`
- **调用路径**：`GET /tushare/pro/namechange`

| 参数 | 示例值 |
|---|---|
| ts_code | '000002.SZ' |

- **返回字段**：`ts_code` `name` `start_date` `end_date` `ann_date` `change_reason`

- **缓存时效**：通常 30 天（TTL≈2592000s）
  - 说明：证券简称与历史更名记录变化极低频，适合长保留。
  - 刷新建议：企业更名类数据建议按周或按月刷新

```python
data = get_json('/tushare/pro/namechange', {"ts_code": "000002.SZ"})
print(data)
```

### 17. 分钟数据

| 接口名 | 调用路径 |
|---|---|
| `rt_k` | `/tushare/pro/rt_k` |
| `rt_fut_min` | `/tushare/pro/rt_fut_min` |
| `rt_fut_min_daily` | `/tushare/pro/rt_fut_min_daily` |
| `rt_fut_ticks` | `/tushare/pro/rt_fut_ticks` |
| `rt_fut_level2` | `/tushare/pro/rt_fut_level2` |

#### `rt_k`

- **示例URL**：`https://ai-tool.indevs.in/pro/rt_k?ts_code=3*.SZ%2C6*.SH%2C0*.SZ%2C9*.BJ`
- **调用路径**：`GET /tushare/pro/rt_k`

| 参数 | 示例值 |
|---|---|
| ts_code | '3*.SZ,6*.SH,0*.SZ,9*.BJ' |

- **返回字段**：`ts_code` `name` `pre_close` `high` `open` `low` `close` `vol` `amount` `num` `trade_time`

- **缓存时效**：realtime snapshot
  - 说明：Official rt_k requires ts_code and returns current-day realtime daily K rows.
  - 刷新建议：Refresh during trading hours as needed; rt_k is a same-day realtime interface and is not a historical trade_date replay API.

```python
data = get_json('/tushare/pro/rt_k', {"ts_code": "3*.SZ,6*.SH,0*.SZ,9*.BJ"})
print(data)
```

#### `rt_fut_min`

- **示例URL**：`https://ai-tool.indevs.in/pro/rt_fut_min?ts_code=RB0&freq=10MIN`
- **调用路径**：`GET /tushare/pro/rt_fut_min`

| 参数 | 示例值 |
|---|---|
| ts_code | 'RB0' |
| freq | '10MIN' |

- **返回字段**：`ts_code` `freq` `time` `open` `high` `low` `close` `vol` `hold` `is_complete` `period_start` `period_end` `trade_date`

- **缓存时效**：当前按下一根 bar 闭合时间自动刷新，陈旧缓存 2 天内清理
  - 说明：实时分钟口径天然应该跟随下一根 bar 的闭合时间滚动，而不是固定长 TTL。
  - 刷新建议：盯盘时按所需频率拉取即可；同一周期内重复请求通常直接命中缓存

```python
data = get_json('/tushare/pro/rt_fut_min', {"ts_code": "RB0", "freq": "10MIN"})
print(data)
```

#### `rt_fut_min_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/rt_fut_min_daily?ts_code=RB0&freq=10MIN`
- **调用路径**：`GET /tushare/pro/rt_fut_min_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | 'RB0' |
| freq | '10MIN' |

- **返回字段**：`ts_code` `freq` `time` `open` `high` `low` `close` `vol` `hold` `is_complete` `period_start` `period_end` `trade_date`

- **缓存时效**：当前交易日数据按下一根 bar 闭合时间自动刷新，历史日查询保留 7 天，陈旧缓存 30 天内清理
  - 说明：交易日内回放仍会增长，历史日分钟序列一旦闭市后则天然适合更长缓存。
  - 刷新建议：当前交易日做盘中回放时可按分钟级刷新；历史日查询通常按需拉一次即可

```python
data = get_json('/tushare/pro/rt_fut_min_daily', {"ts_code": "RB0", "freq": "10MIN"})
print(data)
```

#### `rt_fut_ticks`

- **示例URL**：`https://ai-tool.indevs.in/pro/rt_fut_ticks?ts_code=RB0`
- **调用路径**：`GET /tushare/pro/rt_fut_ticks`

| 参数 | 示例值 |
|---|---|
| ts_code | 'RB0' |

- **返回字段**：`ts_code` `trade_date` `time` `price` `vol` `amount` `hold` `bid_price` `ask_price`

```python
data = get_json('/tushare/pro/rt_fut_ticks', {"ts_code": "RB0"})
print(data)
```

#### `rt_fut_level2`

- **示例URL**：`https://ai-tool.indevs.in/pro/rt_fut_level2?ts_code=RB0`
- **调用路径**：`GET /tushare/pro/rt_fut_level2`

| 参数 | 示例值 |
|---|---|
| ts_code | 'RB0' |

- **返回字段**：`ts_code` `trade_date` `time` `name` `open` `high` `low` `price` `volume` `amount` `hold` `bid_price` `ask_price` `buy_vol` `sell_vol` `avg_price` `last_close` `last_settle_price`

```python
data = get_json('/tushare/pro/rt_fut_level2', {"ts_code": "RB0"})
print(data)
```

### 18. 公告与研报

| 接口名 | 调用路径 |
|---|---|
| `anns_d` | `/tushare/pro/anns_d` |
| `stock_notice_report` | `/tushare/pro/stock_notice_report` |
| `stock_zh_a_disclosure_report_cninfo` | `/tushare/pro/stock_zh_a_disclosure_report_cninfo` |
| `research_report` | `/tushare/pro/research_report` |
| `stock_research_report_em` | `/tushare/pro/stock_research_report_em` |
| `report_rc` | `/tushare/pro/report_rc` |
| `opt_basic` | `/tushare/pro/opt_basic` |
| `opt_daily` | `/tushare/pro/opt_daily` |
| `opt_mins` | `/tushare/pro/opt_mins` |
| `opt_mins_batch` | `/tushare/pro/opt_mins_batch` |

#### `anns_d`

- **示例URL**：`https://ai-tool.indevs.in/pro/anns_d?ts_code=000001.SZ&ann_date=20260326&limit=50`
- **调用路径**：`GET /tushare/pro/anns_d`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |
| ann_date | '20260326' |
| limit | '50' |

- **返回字段**：`ts_code` `symbol` `name` `title` `ann_type` `ann_date` `ann_time` `url`

- **缓存时效**：显式日期或区间查询走历史缓存
  - 说明：公告检索更适合按日期或区间回放，日级缓存可以抑制重复抓取，同时保留列表查询能力。
  - 刷新建议：盘后公告密集时可按日刷新；历史区间回放通常按需调用即可

```python
data = get_json('/tushare/pro/anns_d', {"ts_code": "000001.SZ", "ann_date": "20260326", "limit": "50"})
print(data)
```

#### `stock_notice_report`

- **示例URL**：`https://ai-tool.indevs.in/pro/stock_notice_report?symbol=%E5%85%A8%E9%83%A8&date=2026-03-26&limit=50`
- **调用路径**：`GET /tushare/pro/stock_notice_report`

| 参数 | 示例值 |
|---|---|
| symbol | '全部' |
| date | '2026-03-26' |
| limit | '50' |

- **返回字段**：`代码` `名称` `公告标题` `公告类型` `公告日期` `网址`

- **缓存时效**：显式日期或区间查询走历史缓存
  - 说明：公告数据以日级或区间查询为主，历史缓存可以显著减少重复抓取。
  - 刷新建议：盘后公告密集时按日期重拉；历史区间回放通常无需高频刷新

```python
data = get_json('/tushare/pro/stock_notice_report', {"symbol": "全部", "date": "2026-03-26", "limit": "50"})
print(data)
```

#### `stock_zh_a_disclosure_report_cninfo`

- **示例URL**：`https://ai-tool.indevs.in/pro/stock_zh_a_disclosure_report_cninfo?symbol=000001&market=%E6%B2%AA%E6%B7%B1%E4%BA%AC&start_date=2026-03-20&end_date=2026-03-26&limit=50`
- **调用路径**：`GET /tushare/pro/stock_zh_a_disclosure_report_cninfo`

| 参数 | 示例值 |
|---|---|
| symbol | '000001' |
| market | '沪深京' |
| start_date | '2026-03-20' |
| end_date | '2026-03-26' |
| limit | '50' |

- **返回字段**：`代码` `简称` `公告标题` `公告时间` `公告链接`

- **缓存时效**：显式日期或区间查询走历史缓存
  - 说明：公告数据以日级或区间查询为主，历史缓存可以显著减少重复抓取。
  - 刷新建议：盘后公告密集时按日期重拉；历史区间回放通常无需高频刷新

```python
data = get_json('/tushare/pro/stock_zh_a_disclosure_report_cninfo', {"symbol": "000001", "market": "沪深京", "start_date": "2026-03-20", "end_date": "2026-03-26", "limit": "50"})
print(data)
```

#### `research_report`

- **示例URL**：`https://ai-tool.indevs.in/pro/research_report?symbol=000001&limit=20`
- **调用路径**：`GET /tushare/pro/research_report`

| 参数 | 示例值 |
|---|---|
| symbol | '000001' |
| limit | '20' |

- **返回字段**：`股票代码` `股票简称` `报告名称` `东财评级` `机构` `日期` `报告PDF链接`

- **缓存时效**：当前缓存 12 小时，陈旧缓存 7 天内清理
  - 说明：研报披露频率通常是日级到小时级，12 小时缓存既能控频，也不会把新研报压太久。
  - 刷新建议：盘前、盘后或重大事件后刷新最有意义

```python
data = get_json('/tushare/pro/research_report', {"symbol": "000001", "limit": "20"})
print(data)
```

#### `stock_research_report_em`

- **示例URL**：`https://ai-tool.indevs.in/pro/stock_research_report_em?symbol=000001&limit=20`
- **调用路径**：`GET /tushare/pro/stock_research_report_em`

| 参数 | 示例值 |
|---|---|
| symbol | '000001' |
| limit | '20' |

- **返回字段**：`股票代码` `股票简称` `报告名称` `东财评级` `机构` `日期` `报告PDF链接`

- **缓存时效**：当前缓存 12 小时，陈旧缓存 7 天内清理
  - 说明：与 research_report 共用同一份东财研报数据与缓存策略。
  - 刷新建议：盘前、盘后或重大事件后刷新最有意义

```python
data = get_json('/tushare/pro/stock_research_report_em', {"symbol": "000001", "limit": "20"})
print(data)
```

#### `report_rc`

- **示例URL**：`https://ai-tool.indevs.in/pro/report_rc?ts_code=000001.SZ&start_date=20260301&end_date=20260331&limit=20`
- **调用路径**：`GET /tushare/pro/report_rc`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |
| start_date | '20260301' |
| end_date | '20260331' |
| limit | '20' |

- **返回字段**：`ts_code` `name` `report_date` `org_name` `title` `analyst` `target_price`

```python
data = get_json('/tushare/pro/report_rc', {"ts_code": "000001.SZ", "start_date": "20260301", "end_date": "20260331", "limit": "20"})
print(data)
```

#### `opt_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/opt_basic?exchange=SSE&limit=50`
- **调用路径**：`GET /tushare/pro/opt_basic`

| 参数 | 示例值 |
|---|---|
| exchange | 'SSE' |
| limit | '50' |

- **返回字段**：`ts_code` `name` `exchange` `call_put` `exercise_type` `opt_type` `exercise_price` `list_date` `delist_date`

```python
data = get_json('/tushare/pro/opt_basic', {"exchange": "SSE", "limit": "50"})
print(data)
```

#### `opt_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/opt_daily?exchange=SSE&trade_date=20181212&limit=20`
- **调用路径**：`GET /tushare/pro/opt_daily`

| 参数 | 示例值 |
|---|---|
| exchange | 'SSE' |
| trade_date | '20181212' |
| limit | '20' |

- **返回字段**：`ts_code` `trade_date` `exchange` `pre_settle` `pre_close` `open` `high` `low` `close` `settle` `vol` `amount` `oi`

```python
data = get_json('/tushare/pro/opt_daily', {"exchange": "SSE", "trade_date": "20181212", "limit": "20"})
print(data)
```

#### `opt_mins`

- **示例URL**：`https://ai-tool.indevs.in/pro/opt_mins?ts_code=PP2703-P-7800.DCE&freq=1min&start_date=2026-04-09%2009:00:00&end_date=2026-04-10%2015:00:00&limit=200`
- **调用路径**：`GET /tushare/pro/opt_mins`

| 参数 | 示例值 |
|---|---|
| ts_code | 'PP2703-P-7800.DCE' |
| freq | '1min' |
| start_date | '2026-04-09 09:00:00' |
| end_date | '2026-04-10 15:00:00' |
| limit | '200' |

- **返回字段**：`ts_code` `trade_time` `open` `close` `high` `low` `vol` `amount` `oi`

```python
data = get_json('/tushare/pro/opt_mins', {"ts_code": "PP2703-P-7800.DCE", "freq": "1min", "start_date": "2026-04-09 09:00:00", "end_date": "2026-04-10 15:00:00", "limit": "200"})
print(data)
```

#### `opt_mins_batch`

- **示例URL**：`https://ai-tool.indevs.in/pro/opt_mins_batch?exchange=COMMODITY&trade_date=20260410&freq=1min&contract_limit=50&per_contract_limit=200`
- **调用路径**：`GET /tushare/pro/opt_mins_batch`

| 参数 | 示例值 |
|---|---|
| exchange | 'COMMODITY' |
| trade_date | '20260410' |
| freq | '1min' |
| contract_limit | '50' |
| per_contract_limit | '200' |

- **返回字段**：`ts_code` `trade_time` `open` `close` `high` `low` `vol` `amount` `oi`

```python
data = get_json('/tushare/pro/opt_mins_batch', {"exchange": "COMMODITY", "trade_date": "20260410", "freq": "1min", "contract_limit": "50", "per_contract_limit": "200"})
print(data)
```

### 19. 基金数据

| 接口名 | 调用路径 |
|---|---|
| `fund_daily` | `/tushare/pro/fund_daily` |
| `fund_announcement_report_em` | `/tushare/pro/fund_announcement_report_em` |

#### `fund_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/fund_daily?ts_code=510050.SH&start_date=20180101&end_date=20180131&limit=50`
- **调用路径**：`GET /tushare/pro/fund_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | '510050.SH' |
| start_date | '20180101' |
| end_date | '20180131' |
| limit | '50' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：历史日线回放，优先命中本地历史包与增量缓存
  - 说明：fund_daily 用于 ETF/基金历史全补齐，daily 可覆盖部分 ETF 近期数据，但不能替代 2018-2026 的完整基金日线历史。
  - 刷新建议：做历史补齐时按月份或季度分块调用；近期补齐可缩短 end_date 并设置 limit

```python
data = get_json('/tushare/pro/fund_daily', {"ts_code": "510050.SH", "start_date": "20180101", "end_date": "20180131", "limit": "50"})
print(data)
```

#### `fund_announcement_report_em`

- **示例URL**：`https://ai-tool.indevs.in/pro/fund_announcement_report_em?symbol=000001&limit=50`
- **调用路径**：`GET /tushare/pro/fund_announcement_report_em`

| 参数 | 示例值 |
|---|---|
| symbol | '000001' |
| limit | '50' |

- **返回字段**：`基金代码` `基金名称` `公告标题` `公告日期` `报告ID`

- **缓存时效**：当前缓存 6 小时，陈旧缓存 30 天内清理
  - 说明：基金公告相对稳定，短期缓存足以降低重复抓取，同时保留较长的历史查询命中窗口。
  - 刷新建议：历史公告列表通常按需调用，日内重复查询可直接复用缓存

```python
data = get_json('/tushare/pro/fund_announcement_report_em', {"symbol": "000001", "limit": "50"})
print(data)
```

### 20. 分析师数据

| 接口名 | 调用路径 |
|---|---|
| `analyst_rank` | `/tushare/pro/analyst_rank` |
| `analyst_detail` | `/tushare/pro/analyst_detail` |
| `analyst_history` | `/tushare/pro/analyst_history` |
| `analyst_history` | `/tushare/pro/analyst_history` |

#### `analyst_rank`

- **示例URL**：`https://ai-tool.indevs.in/pro/analyst_rank?year=2024&limit=50`
- **调用路径**：`GET /tushare/pro/analyst_rank`

| 参数 | 示例值 |
|---|---|
| year | '2024' |
| limit | '50' |

- **返回字段**：`分析师名称` `分析师单位` `年度指数` `12个月收益率` `分析师ID` `行业` `更新日期` `年度`

- **缓存时效**：当前缓存 1 天，陈旧缓存 7 天内清理
  - 说明：分析师榜单不是逐分钟变化的数据，日级缓存足够稳妥，也能显著减少重复抓取。
  - 刷新建议：日内一般无需高频刷新；隔日或跨年度榜单切换时再更新即可

```python
data = get_json('/tushare/pro/analyst_rank', {"year": "2024", "limit": "50"})
print(data)
```

#### `analyst_detail`

- **示例URL**：`https://ai-tool.indevs.in/pro/analyst_detail?analyst_id=11000455635&indicator=%E6%9C%80%E6%96%B0%E8%B7%9F%E8%B8%AA%E6%88%90%E5%88%86%E8%82%A1&limit=50`
- **调用路径**：`GET /tushare/pro/analyst_detail`

| 参数 | 示例值 |
|---|---|
| analyst_id | '11000455635' |
| indicator | '最新跟踪成分股' |
| limit | '50' |

- **返回字段**：`股票代码` `股票名称` `调入日期` `最新评级日期` `当前评级名称` `最新价格` `阶段涨跌幅`

- **缓存时效**：当前缓存 12 小时，陈旧缓存 7 天内清理
  - 说明：分析师跟踪组合和评级变更不会像快讯一样秒级跳动，半天级缓存更匹配真实更新节奏。
  - 刷新建议：盘后或次日刷新更有价值；同一交易日内通常复用缓存即可

```python
data = get_json('/tushare/pro/analyst_detail', {"analyst_id": "11000455635", "indicator": "最新跟踪成分股", "limit": "50"})
print(data)
```

#### `analyst_history`

- **示例URL**：`https://ai-tool.indevs.in/pro/analyst_history?analyst_id=11000213851&indicator=%E5%8E%86%E5%8F%B2%E8%B7%9F%E8%B8%AA%E6%88%90%E5%88%86%E8%82%A1&limit=100`
- **调用路径**：`GET /tushare/pro/analyst_history`

| 参数 | 示例值 |
|---|---|
| analyst_id | '11000213851' |
| indicator | '历史跟踪成分股' |
| limit | '100' |

- **返回字段**：`股票代码` `股票名称` `调入日期` `调出日期` `调入时评级名称` `调出原因` `累计涨跌幅`

- **缓存时效**：当前缓存 1 天，陈旧缓存 14 天内清理
  - 说明：历史分析师口径天然偏静态，日级缓存足够抑制重复抓取，同时保留较长历史查询窗口。
  - 刷新建议：历史回放通常按需调用；同一 analyst_id 在日内可直接复用缓存

```python
data = get_json('/tushare/pro/analyst_history', {"analyst_id": "11000213851", "indicator": "历史跟踪成分股", "limit": "100"})
print(data)
```

#### `analyst_history`

- **示例URL**：`https://ai-tool.indevs.in/pro/analyst_history?analyst_id=11000213851&indicator=%E5%8E%86%E5%8F%B2%E6%8C%87%E6%95%B0&limit=240`
- **调用路径**：`GET /tushare/pro/analyst_history`

| 参数 | 示例值 |
|---|---|
| analyst_id | '11000213851' |
| indicator | '历史指数' |
| limit | '240' |

- **返回字段**：`date` `value`

- **缓存时效**：当前缓存 1 天，陈旧缓存 14 天内清理
  - 说明：历史分析师口径天然偏静态，日级缓存足够抑制重复抓取，同时保留较长历史查询窗口。
  - 刷新建议：历史回放通常按需调用；同一 analyst_id 在日内可直接复用缓存

```python
data = get_json('/tushare/pro/analyst_history', {"analyst_id": "11000213851", "indicator": "历史指数", "limit": "240"})
print(data)
```

### 21. 本地量化聚合接口

| 接口名 | 调用路径 |
|---|---|
| `get_trade_days` | `/tushare/pro/get_trade_days` |
| `get_all_securities` | `/tushare/pro/get_all_securities` |
| `get_stock_chinese_name` | `/tushare/pro/get_stock_chinese_name` |
| `get_realtime_prices` | `/tushare/pro/get_realtime_prices` |
| `get_index_stocks` | `/tushare/pro/get_index_stocks` |
| `get_index_weights` | `/tushare/pro/get_index_weights` |
| `get_industries` | `/tushare/pro/get_industries` |
| `get_industry_stocks` | `/tushare/pro/get_industry_stocks` |

#### `get_trade_days`

- **示例URL**：`https://ai-tool.indevs.in/pro/get_trade_days?until=20260331&count=5`
- **调用路径**：`GET /tushare/pro/get_trade_days`

| 参数 | 示例值 |
|---|---|
| until | '20260331' |
| count | '5' |

- **返回字段**：`trade_date`

```python
data = get_json('/tushare/pro/get_trade_days', {"until": "20260331", "count": "5"})
print(data)
```

#### `get_all_securities`

- **示例URL**：`https://ai-tool.indevs.in/pro/get_all_securities?limit=5`
- **调用路径**：`GET /tushare/pro/get_all_securities`

| 参数 | 示例值 |
|---|---|
| limit | '5' |

- **返回字段**：`ts_code` `symbol` `name` `market` `snapshot_date`

```python
data = get_json('/tushare/pro/get_all_securities', {"limit": "5"})
print(data)
```

#### `get_stock_chinese_name`

- **示例URL**：`https://ai-tool.indevs.in/pro/get_stock_chinese_name?stock_list=000001.SZ,000002.SZ`
- **调用路径**：`GET /tushare/pro/get_stock_chinese_name`

| 参数 | 示例值 |
|---|---|
| stock_list | '000001.SZ,000002.SZ' |

- **返回字段**：`ts_code` `symbol` `name`

```python
data = get_json('/tushare/pro/get_stock_chinese_name', {"stock_list": "000001.SZ,000002.SZ"})
print(data)
```

#### `get_realtime_prices`

- **示例URL**：`https://ai-tool.indevs.in/pro/get_realtime_prices?stock_list=000001.SZ,000002.SZ`
- **调用路径**：`GET /tushare/pro/get_realtime_prices`

| 参数 | 示例值 |
|---|---|
| stock_list | '000001.SZ,000002.SZ' |

- **返回字段**：`ts_code` `symbol` `name` `price` `change` `pct_chg` `bid` `ask` `pre_close` `open` `high` `low` `vol` `amount` `trade_time`

```python
data = get_json('/tushare/pro/get_realtime_prices', {"stock_list": "000001.SZ,000002.SZ"})
print(data)
```

#### `get_index_stocks`

- **示例URL**：`https://ai-tool.indevs.in/pro/get_index_stocks?index_symbol=000300.SH&limit=5`
- **调用路径**：`GET /tushare/pro/get_index_stocks`

| 参数 | 示例值 |
|---|---|
| index_symbol | '000300.SH' |
| limit | '5' |

- **返回字段**：`index_symbol` `con_code` `con_name` `in_date`

```python
data = get_json('/tushare/pro/get_index_stocks', {"index_symbol": "000300.SH", "limit": "5"})
print(data)
```

#### `get_index_weights`

- **示例URL**：`https://ai-tool.indevs.in/pro/get_index_weights?index_symbol=000300.SH&limit=5`
- **调用路径**：`GET /tushare/pro/get_index_weights`

| 参数 | 示例值 |
|---|---|
| index_symbol | '000300.SH' |
| limit | '5' |

- **返回字段**：`index_symbol` `trade_date` `index_name` `con_code` `con_name` `weight`

```python
data = get_json('/tushare/pro/get_index_weights', {"index_symbol": "000300.SH", "limit": "5"})
print(data)
```

#### `get_industries`

- **示例URL**：`https://ai-tool.indevs.in/pro/get_industries?level=L1&limit=5`
- **调用路径**：`GET /tushare/pro/get_industries`

| 参数 | 示例值 |
|---|---|
| level | 'L1' |
| limit | '5' |

- **返回字段**：`industry_code` `industry_name` `parent_industry` `level` `constituent_count` `pe` `pe_ttm` `pb` `dividend_yield`

```python
data = get_json('/tushare/pro/get_industries', {"level": "L1", "limit": "5"})
print(data)
```

#### `get_industry_stocks`

- **示例URL**：`https://ai-tool.indevs.in/pro/get_industry_stocks?industry_code=801780.SI&limit=5`
- **调用路径**：`GET /tushare/pro/get_industry_stocks`

| 参数 | 示例值 |
|---|---|
| industry_code | '801780.SI' |
| limit | '5' |

- **返回字段**：`industry_code` `con_code` `con_name` `weight` `in_date`

```python
data = get_json('/tushare/pro/get_industry_stocks', {"industry_code": "801780.SI", "limit": "5"})
print(data)
```

### 22. 15000积分补全接口

| 接口名 | 调用路径 |
|---|---|
| `new_share` | `/tushare/pro/new_share` |
| `top_inst` | `/tushare/pro/top_inst` |
| `fina_audit` | `/tushare/pro/fina_audit` |
| `fina_mainbz` | `/tushare/pro/fina_mainbz` |
| `fina_mainbz_vip` | `/tushare/pro/fina_mainbz_vip` |
| `fund_company` | `/tushare/pro/fund_company` |
| `fund_div` | `/tushare/pro/fund_div` |
| `fund_portfolio` | `/tushare/pro/fund_portfolio` |
| `fund_adj` | `/tushare/pro/fund_adj` |
| `fut_wsr` | `/tushare/pro/fut_wsr` |
| `cb_basic` | `/tushare/pro/cb_basic` |
| `cb_issue` | `/tushare/pro/cb_issue` |
| `cb_daily` | `/tushare/pro/cb_daily` |
| `index_dailybasic` | `/tushare/pro/index_dailybasic` |
| `index_member_all` | `/tushare/pro/index_member_all` |
| `shibor_quote` | `/tushare/pro/shibor_quote` |
| `shibor_lpr` | `/tushare/pro/shibor_lpr` |

#### `new_share`

- **示例URL**：`https://ai-tool.indevs.in/pro/new_share?start_date=20250101&end_date=20251231`
- **调用路径**：`GET /tushare/pro/new_share`

| 参数 | 示例值 |
|---|---|
| start_date | '20250101' |
| end_date | '20251231' |

- **返回字段**：`ts_code` `sub_code` `name` `ipo_date` `issue_date` `amount` `market_amount` `price` `pe` `limit_amount` `funds` `ballot`

```python
data = get_json('/tushare/pro/new_share', {"start_date": "20250101", "end_date": "20251231"})
print(data)
```

#### `top_inst`

- **示例URL**：`https://ai-tool.indevs.in/pro/top_inst?trade_date=20260415&limit=20`
- **调用路径**：`GET /tushare/pro/top_inst`

| 参数 | 示例值 |
|---|---|
| trade_date | '20260415' |
| limit | '20' |

- **返回字段**：`trade_date` `ts_code` `exalter` `side` `buy` `buy_rate` `sell` `sell_rate` `net_buy` `reason`

```python
data = get_json('/tushare/pro/top_inst', {"trade_date": "20260415", "limit": "20"})
print(data)
```

#### `fina_audit`

- **示例URL**：`https://ai-tool.indevs.in/pro/fina_audit?ts_code=000001.SZ&period=20251231&limit=20`
- **调用路径**：`GET /tushare/pro/fina_audit`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |
| period | '20251231' |
| limit | '20' |

- **返回字段**：`ts_code` `ann_date` `end_date` `audit_result` `audit_fees` `audit_agency` `audit_sign`

```python
data = get_json('/tushare/pro/fina_audit', {"ts_code": "000001.SZ", "period": "20251231", "limit": "20"})
print(data)
```

#### `fina_mainbz`

- **示例URL**：`https://ai-tool.indevs.in/pro/fina_mainbz?ts_code=000001.SZ&period=20251231&type=P&limit=20`
- **调用路径**：`GET /tushare/pro/fina_mainbz`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |
| period | '20251231' |
| type | 'P' |
| limit | '20' |

- **返回字段**：`ts_code` `end_date` `bz_item` `bz_code` `bz_sales` `bz_profit` `bz_cost` `curr_type` `update_flag`

```python
data = get_json('/tushare/pro/fina_mainbz', {"ts_code": "000001.SZ", "period": "20251231", "type": "P", "limit": "20"})
print(data)
```

#### `fina_mainbz_vip`

- **示例URL**：`https://ai-tool.indevs.in/pro/fina_mainbz_vip?period=20251231&type=P&limit=20`
- **调用路径**：`GET /tushare/pro/fina_mainbz_vip`

| 参数 | 示例值 |
|---|---|
| period | '20251231' |
| type | 'P' |
| limit | '20' |

- **返回字段**：`ts_code` `end_date` `bz_item` `bz_code` `bz_sales` `bz_profit` `bz_cost` `curr_type` `update_flag`

```python
data = get_json('/tushare/pro/fina_mainbz_vip', {"period": "20251231", "type": "P", "limit": "20"})
print(data)
```

#### `fund_company`

- **示例URL**：`https://ai-tool.indevs.in/pro/fund_company?limit=50`
- **调用路径**：`GET /tushare/pro/fund_company`

| 参数 | 示例值 |
|---|---|
| limit | '50' |

- **返回字段**：`name` `shortname` `province` `city` `address` `phone` `office` `website` `chairman` `manager` `reg_capital` `setup_date` `end_date` `employees` `main_business` `org_code`

```python
data = get_json('/tushare/pro/fund_company', {"limit": "50"})
print(data)
```

#### `fund_div`

- **示例URL**：`https://ai-tool.indevs.in/pro/fund_div?ts_code=000001.OF&limit=20`
- **调用路径**：`GET /tushare/pro/fund_div`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.OF' |
| limit | '20' |

- **返回字段**：`ts_code` `ann_date` `imp_anndate` `base_date` `div_proc` `record_date` `ex_date` `pay_date` `earpay_date` `net_ex_date` `div_cash` `base_unit` `ear_distr` `ear_amount` `account_date` `base_year`

```python
data = get_json('/tushare/pro/fund_div', {"ts_code": "000001.OF", "limit": "20"})
print(data)
```

#### `fund_portfolio`

- **示例URL**：`https://ai-tool.indevs.in/pro/fund_portfolio?ts_code=000001.OF&period=20251231&limit=20`
- **调用路径**：`GET /tushare/pro/fund_portfolio`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.OF' |
| period | '20251231' |
| limit | '20' |

- **返回字段**：`ts_code` `ann_date` `end_date` `symbol` `mkv` `amount` `stk_mkv_ratio` `stk_float_ratio`

```python
data = get_json('/tushare/pro/fund_portfolio', {"ts_code": "000001.OF", "period": "20251231", "limit": "20"})
print(data)
```

#### `fund_adj`

- **示例URL**：`https://ai-tool.indevs.in/pro/fund_adj?ts_code=510300.SH&limit=20`
- **调用路径**：`GET /tushare/pro/fund_adj`

| 参数 | 示例值 |
|---|---|
| ts_code | '510300.SH' |
| limit | '20' |

- **返回字段**：`ts_code` `trade_date` `adj_factor`

```python
data = get_json('/tushare/pro/fund_adj', {"ts_code": "510300.SH", "limit": "20"})
print(data)
```

#### `fut_wsr`

- **示例URL**：`https://ai-tool.indevs.in/pro/fut_wsr?trade_date=20250115&symbol=CU&limit=20`
- **调用路径**：`GET /tushare/pro/fut_wsr`

| 参数 | 示例值 |
|---|---|
| trade_date | '20250115' |
| symbol | 'CU' |
| limit | '20' |

- **返回字段**：`trade_date` `symbol` `fut_name` `warehouse` `pre_vol` `vol` `vol_chg` `area` `year`

```python
data = get_json('/tushare/pro/fut_wsr', {"trade_date": "20250115", "symbol": "CU", "limit": "20"})
print(data)
```

#### `cb_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/cb_basic?list_status=L&limit=50`
- **调用路径**：`GET /tushare/pro/cb_basic`

| 参数 | 示例值 |
|---|---|
| list_status | 'L' |
| limit | '50' |

- **返回字段**：`ts_code` `bond_full_name` `bond_short_name` `stk_code` `stk_short_name` `maturity` `par` `issue_price` `issue_size` `remain_size` `value_date` `maturity_date`

```python
data = get_json('/tushare/pro/cb_basic', {"list_status": "L", "limit": "50"})
print(data)
```

#### `cb_issue`

- **示例URL**：`https://ai-tool.indevs.in/pro/cb_issue?limit=50`
- **调用路径**：`GET /tushare/pro/cb_issue`

| 参数 | 示例值 |
|---|---|
| limit | '50' |

- **返回字段**：`ts_code` `ann_date` `res_ann_date` `plan_issue_size` `issue_size` `issue_price` `issue_type` `online_issue_size` `winning_rate`

```python
data = get_json('/tushare/pro/cb_issue', {"limit": "50"})
print(data)
```

#### `cb_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/cb_daily?ts_code=113001.SH&limit=20`
- **调用路径**：`GET /tushare/pro/cb_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | '113001.SH' |
| limit | '20' |

- **返回字段**：`ts_code` `trade_date` `pre_close` `open` `high` `low` `close` `change` `pct_chg` `vol` `amount`

```python
data = get_json('/tushare/pro/cb_daily', {"ts_code": "113001.SH", "limit": "20"})
print(data)
```

#### `index_dailybasic`

- **示例URL**：`https://ai-tool.indevs.in/pro/index_dailybasic?ts_code=000001.SH&trade_date=20260415`
- **调用路径**：`GET /tushare/pro/index_dailybasic`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SH' |
| trade_date | '20260415' |

- **返回字段**：`ts_code` `trade_date` `total_mv` `float_mv` `total_share` `float_share` `free_share` `turnover_rate` `turnover_rate_f` `pe` `pe_ttm` `pb`

```python
data = get_json('/tushare/pro/index_dailybasic', {"ts_code": "000001.SH", "trade_date": "20260415"})
print(data)
```

#### `index_member_all`

- **示例URL**：`https://ai-tool.indevs.in/pro/index_member_all?l1_code=801010.SI&limit=50`
- **调用路径**：`GET /tushare/pro/index_member_all`

| 参数 | 示例值 |
|---|---|
| l1_code | '801010.SI' |
| limit | '50' |

- **返回字段**：`l1_code` `l1_name` `l2_code` `l2_name` `l3_code` `l3_name` `ts_code` `name` `in_date` `out_date` `is_new`

```python
data = get_json('/tushare/pro/index_member_all', {"l1_code": "801010.SI", "limit": "50"})
print(data)
```

#### `shibor_quote`

- **示例URL**：`https://ai-tool.indevs.in/pro/shibor_quote?date=20260415`
- **调用路径**：`GET /tushare/pro/shibor_quote`

| 参数 | 示例值 |
|---|---|
| date | '20260415' |

- **返回字段**：`date` `bank` `on_b` `onew_b` `tw_b` `threem_b` `sixm_b` `ninem_b` `oney_b` `on_a` `onew_a` `tw_a` `threem_a` `sixm_a` `ninem_a` `oney_a`

```python
data = get_json('/tushare/pro/shibor_quote', {"date": "20260415"})
print(data)
```

#### `shibor_lpr`

- **示例URL**：`https://ai-tool.indevs.in/pro/shibor_lpr?start_date=20250101&end_date=20251231`
- **调用路径**：`GET /tushare/pro/shibor_lpr`

| 参数 | 示例值 |
|---|---|
| start_date | '20250101' |
| end_date | '20251231' |

- **返回字段**：`date` `1y` `5y`

```python
data = get_json('/tushare/pro/shibor_lpr', {"start_date": "20250101", "end_date": "20251231"})
print(data)
```

### 23. 港股

| 接口名 | 调用路径 |
|---|---|
| `hk_basic` | `/tushare/pro/hk_basic` |
| `hk_income` | `/tushare/pro/hk_income` |
| `hk_balancesheet` | `/tushare/pro/hk_balancesheet` |
| `hk_cashflow` | `/tushare/pro/hk_cashflow` |
| `hk_fina_indicator` | `/tushare/pro/hk_fina_indicator` |
| `hk_adj_factor` | `/tushare/pro/hk_adj_factor` |
| `hk_daily` | `/tushare/pro/hk_daily` |
| `hk_quote` | `/tushare/pro/hk_quote` |
| `hk_depth` | `/tushare/pro/hk_depth` |
| `hk_mins` | `/tushare/pro/hk_mins` |
| `hk_weekly` | `/tushare/pro/hk_weekly` |
| `hk_monthly` | `/tushare/pro/hk_monthly` |
| `hk_hold` | `/tushare/pro/hk_hold` |

#### `hk_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_basic?ts_code=00700.HK`
- **调用路径**：`GET /tushare/pro/hk_basic`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |

- **返回字段**：`ts_code` `symbol` `name` `fullname` `enname` `exchange` `market` `industry` `curr_type` `list_status` `list_date` `delist_date` `trade_unit` `isin`

- **缓存时效**：通常 1 天
  - 说明：港股基础信息属于低频变更元数据，适合长缓存并优先复用站内结果。
  - 刷新建议：基础资料日内通常无需高频刷新；隔天或公司资料变化后再拉取即可

```python
data = get_json('/tushare/pro/hk_basic', {"ts_code": "00700.HK"})
print(data)
```

#### `hk_income`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_income?ts_code=00700.HK&period=20241231&limit=20`
- **调用路径**：`GET /tushare/pro/hk_income`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |
| period | '20241231' |
| limit | '20' |

- **返回字段**：`ts_code` `end_date` `name` `ind_name` `ind_value`

```python
data = get_json('/tushare/pro/hk_income', {"ts_code": "00700.HK", "period": "20241231", "limit": "20"})
print(data)
```

#### `hk_balancesheet`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_balancesheet?ts_code=00700.HK&period=20241231&limit=20`
- **调用路径**：`GET /tushare/pro/hk_balancesheet`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |
| period | '20241231' |
| limit | '20' |

- **返回字段**：`ts_code` `name` `end_date` `ind_name` `ind_value`

```python
data = get_json('/tushare/pro/hk_balancesheet', {"ts_code": "00700.HK", "period": "20241231", "limit": "20"})
print(data)
```

#### `hk_cashflow`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_cashflow?ts_code=00700.HK&period=20241231&limit=20`
- **调用路径**：`GET /tushare/pro/hk_cashflow`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |
| period | '20241231' |
| limit | '20' |

- **返回字段**：`ts_code` `end_date` `name` `ind_name` `ind_value`

```python
data = get_json('/tushare/pro/hk_cashflow', {"ts_code": "00700.HK", "period": "20241231", "limit": "20"})
print(data)
```

#### `hk_fina_indicator`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_fina_indicator?ts_code=00700.HK&period=20241231&limit=5`
- **调用路径**：`GET /tushare/pro/hk_fina_indicator`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |
| period | '20241231' |
| limit | '5' |

- **返回字段**：`ts_code` `name` `end_date` `report_type` `std_report_date` `operate_income` `gross_profit` `holder_profit` `basic_eps` `bps`

```python
data = get_json('/tushare/pro/hk_fina_indicator', {"ts_code": "00700.HK", "period": "20241231", "limit": "5"})
print(data)
```

#### `hk_adj_factor`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_adj_factor?ts_code=00700.HK`
- **调用路径**：`GET /tushare/pro/hk_adj_factor`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |

- **返回字段**：`ts_code` `trade_date` `adj_factor` `split_ratio` `name`

- **缓存时效**：通常 7 天，陈旧缓存 30 天内清理
  - 说明：拆股与合股事件是低频公司行动，天然适合长缓存。
  - 刷新建议：公司行动发生后或回补历史复权事件时再刷新即可

```python
data = get_json('/tushare/pro/hk_adj_factor', {"ts_code": "00700.HK"})
print(data)
```

#### `hk_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_daily?ts_code=00700.HK`
- **调用路径**：`GET /tushare/pro/hk_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：当前交易日通常到北京时间当日 24:00；历史查询通常保留 7 天
  - 说明：港股日线是典型日频序列，最近交易日会在收盘后补齐，历史区间天然适合更长缓存。
  - 刷新建议：盘后补齐阶段或需要更新最近一个交易日时再刷新；历史区间按需查询即可

```python
data = get_json('/tushare/pro/hk_daily', {"ts_code": "00700.HK"})
print(data)
```

#### `hk_quote`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_quote?ts_code=00700.HK`
- **调用路径**：`GET /tushare/pro/hk_quote`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |

- **返回字段**：`ts_code` `trade_time` `open` `high` `low` `price` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：当前按分钟级短缓存，陈旧缓存 1 天内清理
  - 说明：实时快照更新快，但重复请求密度也高，短缓存更适合控频。
  - 刷新建议：盘中盯盘建议按秒级到分钟级主动刷新；非交易时段复用缓存即可

```python
data = get_json('/tushare/pro/hk_quote', {"ts_code": "00700.HK"})
print(data)
```

#### `hk_depth`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_depth?ts_code=00700.HK`
- **调用路径**：`GET /tushare/pro/hk_depth`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |

- **返回字段**：`ts_code` `bid_price1` `bid_vol1` `ask_price1` `ask_vol1` `bid_price2` `bid_vol2` `ask_price2` `ask_vol2`

- **缓存时效**：当前按秒级到分钟级短缓存，陈旧缓存 1 天内清理
  - 说明：盘口深度比成交快照更高频，缓存只能用于控频，不能替代实时拉取。
  - 刷新建议：盘口研究或盯盘时应按需主动刷新；不建议把这类接口当长缓存数据源

```python
data = get_json('/tushare/pro/hk_depth', {"ts_code": "00700.HK"})
print(data)
```

#### `hk_mins`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_mins?ts_code=00700.HK&freq=5MIN&limit=10`
- **调用路径**：`GET /tushare/pro/hk_mins`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |
| freq | '5MIN' |
| limit | '10' |

- **返回字段**：`ts_code` `freq` `time` `open` `high` `low` `close` `vol` `amount`

- **缓存时效**：当前交易日建议短缓存；历史分钟查询通常保留 2 天
  - 说明：分钟序列比日线更高频，但仍适合做短期缓存和受控回放。
  - 刷新建议：盘中最近几根分钟 bar 建议按需刷新；明确历史窗口时可直接带 start_date / end_date 回放

```python
data = get_json('/tushare/pro/hk_mins', {"ts_code": "00700.HK", "freq": "5MIN", "limit": "10"})
print(data)
```

#### `hk_weekly`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_weekly?ts_code=00700.HK&start_date=2025-01-01&end_date=2026-03-27`
- **调用路径**：`GET /tushare/pro/hk_weekly`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |
| start_date | '2025-01-01' |
| end_date | '2026-03-27' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：通常保留 7 天，陈旧缓存 30 天内清理
  - 说明：周频数据更新频率更低，缓存应明显长于日线。
  - 刷新建议：周线通常在周收盘后刷新即可；历史区间按需调用

```python
data = get_json('/tushare/pro/hk_weekly', {"ts_code": "00700.HK", "start_date": "2025-01-01", "end_date": "2026-03-27"})
print(data)
```

#### `hk_monthly`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_monthly?ts_code=00700.HK&start_date=2020-01-01&end_date=2026-03-27`
- **调用路径**：`GET /tushare/pro/hk_monthly`

| 参数 | 示例值 |
|---|---|
| ts_code | '00700.HK' |
| start_date | '2020-01-01' |
| end_date | '2026-03-27' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：通常保留 7 天，陈旧缓存 30 天内清理
  - 说明：月频序列更稳定，天然适合长缓存。
  - 刷新建议：月线通常在月末收盘后刷新即可；历史区间按需调用

```python
data = get_json('/tushare/pro/hk_monthly', {"ts_code": "00700.HK", "start_date": "2020-01-01", "end_date": "2026-03-27"})
print(data)
```

#### `hk_hold`

- **示例URL**：`https://ai-tool.indevs.in/pro/hk_hold?trade_date=20260320&ts_code=00700.HK`
- **调用路径**：`GET /tushare/pro/hk_hold`

| 参数 | 示例值 |
|---|---|
| trade_date | '20260320' |
| ts_code | '00700.HK' |

- **返回字段**：`code` `trade_date` `ts_code` `name` `vol` `ratio` `exchange`

- **缓存时效**：通常到北京时间当日 24:00；历史查询通常保留 7 天
  - 说明：港股通持股属于日频统计口径，日内重复值较高，收盘后补齐后再缓存最稳。
  - 刷新建议：收盘后或晚间数据补齐阶段刷新最有意义；历史日期按需查询即可

```python
data = get_json('/tushare/pro/hk_hold', {"trade_date": "20260320", "ts_code": "00700.HK"})
print(data)
```

### 24. 美股

| 接口名 | 调用路径 |
|---|---|
| `us_basic` | `/tushare/pro/us_basic` |
| `us_income` | `/tushare/pro/us_income` |
| `us_balancesheet` | `/tushare/pro/us_balancesheet` |
| `us_cashflow` | `/tushare/pro/us_cashflow` |
| `us_fina_indicator` | `/tushare/pro/us_fina_indicator` |
| `us_adj_factor` | `/tushare/pro/us_adj_factor` |
| `us_daily` | `/tushare/pro/us_daily` |
| `us_daily_market_cap` | `/tushare/pro/us_daily_market_cap` |
| `us_quote` | `/tushare/pro/us_quote` |
| `us_depth` | `/tushare/pro/us_depth` |
| `us_mins` | `/tushare/pro/us_mins` |
| `us_weekly` | `/tushare/pro/us_weekly` |
| `us_monthly` | `/tushare/pro/us_monthly` |
| `us_tradecal` | `/tushare/pro/us_tradecal` |

#### `us_basic`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_basic?ts_code=AAPL`
- **调用路径**：`GET /tushare/pro/us_basic`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |

- **返回字段**：`ts_code` `name` `enname` `classify` `list_date` `delist_date`

- **缓存时效**：通常 1 天
  - 说明：美股基础资料变化频率低，适合长缓存，不必把查询压力打到上游。
  - 刷新建议：基础资料按需查询即可；批量代码表建议本地落盘后周期性更新

```python
data = get_json('/tushare/pro/us_basic', {"ts_code": "AAPL"})
print(data)
```

#### `us_income`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_income?ts_code=AAPL&limit=20`
- **调用路径**：`GET /tushare/pro/us_income`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |
| limit | '20' |

- **返回字段**：`ts_code` `end_date` `ind_type` `name` `ind_name` `ind_value` `report_type`

```python
data = get_json('/tushare/pro/us_income', {"ts_code": "AAPL", "limit": "20"})
print(data)
```

#### `us_balancesheet`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_balancesheet?ts_code=AAPL&limit=20`
- **调用路径**：`GET /tushare/pro/us_balancesheet`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |
| limit | '20' |

- **返回字段**：`ts_code` `end_date` `ind_type` `name` `ind_name` `ind_value` `report_type`

```python
data = get_json('/tushare/pro/us_balancesheet', {"ts_code": "AAPL", "limit": "20"})
print(data)
```

#### `us_cashflow`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_cashflow?ts_code=AAPL&limit=20`
- **调用路径**：`GET /tushare/pro/us_cashflow`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |
| limit | '20' |

- **返回字段**：`ts_code` `end_date` `ind_type` `name` `ind_name` `ind_value` `report_type`

```python
data = get_json('/tushare/pro/us_cashflow', {"ts_code": "AAPL", "limit": "20"})
print(data)
```

#### `us_fina_indicator`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_fina_indicator?ts_code=AAPL&period=20241231&limit=5`
- **调用路径**：`GET /tushare/pro/us_fina_indicator`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |
| period | '20241231' |
| limit | '5' |

- **返回字段**：`ts_code` `end_date` `ind_type` `security_name_abbr` `accounting_standards` `notice_date` `operate_income` `gross_profit` `parent_holder_netprofit` `basic_eps`

```python
data = get_json('/tushare/pro/us_fina_indicator', {"ts_code": "AAPL", "period": "20241231", "limit": "5"})
print(data)
```

#### `us_adj_factor`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_adj_factor?ts_code=AAPL`
- **调用路径**：`GET /tushare/pro/us_adj_factor`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |

- **返回字段**：`ts_code` `trade_date` `adj_factor` `split_ratio` `name`

- **缓存时效**：通常 7 天，陈旧缓存 30 天内清理
  - 说明：拆股与反向拆股属于低频公司行动，长缓存最合适。
  - 刷新建议：公司行动发生后或历史复权回补时按需刷新即可

```python
data = get_json('/tushare/pro/us_adj_factor', {"ts_code": "AAPL"})
print(data)
```

#### `us_daily`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_daily?ts_code=AAPL`
- **调用路径**：`GET /tushare/pro/us_daily`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `vol` `amount`

- **缓存时效**：当前交易日通常到北京时间次日收盘后窗口；历史查询通常保留 7 天
  - 说明：美股日线同样属于日频序列，最新一日在盘中并不稳定，历史部分则很适合长缓存。
  - 刷新建议：最近交易日建议在美股收盘后或盘后补齐阶段刷新；历史区间按需调用即可

```python
data = get_json('/tushare/pro/us_daily', {"ts_code": "AAPL"})
print(data)
```

#### `us_daily_market_cap`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_daily_market_cap?ts_code=AAPL`
- **调用路径**：`GET /tushare/pro/us_daily_market_cap`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |

- **返回字段**：`ts_code` `trade_date` `close` `total_share` `total_mv` `curr_type` `market_cap_method` `market_cap_precision`

- **缓存时效**：随美股日线缓存，当前交易日建议收盘后刷新
  - 说明：该接口依赖日线和当前总股本，频率不应高于日线刷新频率。
  - 刷新建议：需要最新市值时可在美股收盘后刷新；历史区间按需调用即可

```python
data = get_json('/tushare/pro/us_daily_market_cap', {"ts_code": "AAPL"})
print(data)
```

#### `us_quote`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_quote?ts_code=AAPL`
- **调用路径**：`GET /tushare/pro/us_quote`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |

- **返回字段**：`ts_code` `trade_time` `open` `high` `low` `price` `pre_close` `change` `pct_chg` `vol` `amount`

- **缓存时效**：当前按分钟级短缓存，陈旧缓存 1 天内清理
  - 说明：实时快照属于高频口径，短缓存足以抑制重复请求，同时保留新鲜度。
  - 刷新建议：盘中监控美股时按需主动刷新即可；非交易时段通常直接复用缓存

```python
data = get_json('/tushare/pro/us_quote', {"ts_code": "AAPL"})
print(data)
```

#### `us_depth`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_depth?ts_code=AAPL`
- **调用路径**：`GET /tushare/pro/us_depth`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |

- **返回字段**：`ts_code` `bid_price1` `bid_vol1` `ask_price1` `ask_vol1` `bid_price2` `bid_vol2` `ask_price2` `ask_vol2`

- **缓存时效**：当前按秒级到分钟级短缓存，陈旧缓存 1 天内清理
  - 说明：盘口深度属于更高频的实时口径，只适合极短缓存。
  - 刷新建议：盘口研究、监控委托簿或撮合观察时应按需主动刷新

```python
data = get_json('/tushare/pro/us_depth', {"ts_code": "AAPL"})
print(data)
```

#### `us_mins`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_mins?ts_code=AAPL&freq=5MIN&limit=10`
- **调用路径**：`GET /tushare/pro/us_mins`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |
| freq | '5MIN' |
| limit | '10' |

- **返回字段**：`ts_code` `freq` `time` `open` `high` `low` `close` `vol` `amount`

- **缓存时效**：当前交易日建议短缓存；历史分钟查询通常保留 2 天
  - 说明：美股分钟线属于高频序列，适合短缓存与回放并用。
  - 刷新建议：盘中监控时按需刷新最近分钟 bar；历史回放可直接带 start_date / end_date

```python
data = get_json('/tushare/pro/us_mins', {"ts_code": "AAPL", "freq": "5MIN", "limit": "10"})
print(data)
```

#### `us_weekly`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_weekly?ts_code=AAPL&start_date=2025-01-01&end_date=2026-03-27`
- **调用路径**：`GET /tushare/pro/us_weekly`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |
| start_date | '2025-01-01' |
| end_date | '2026-03-27' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `vol` `amount`

- **缓存时效**：通常保留 7 天，陈旧缓存 30 天内清理
  - 说明：周频数据变更慢，长缓存更合适。
  - 刷新建议：周线通常在周收盘后刷新即可；历史区间按需调用

```python
data = get_json('/tushare/pro/us_weekly', {"ts_code": "AAPL", "start_date": "2025-01-01", "end_date": "2026-03-27"})
print(data)
```

#### `us_monthly`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_monthly?ts_code=AAPL&start_date=2020-01-01&end_date=2026-03-27`
- **调用路径**：`GET /tushare/pro/us_monthly`

| 参数 | 示例值 |
|---|---|
| ts_code | 'AAPL' |
| start_date | '2020-01-01' |
| end_date | '2026-03-27' |

- **返回字段**：`ts_code` `trade_date` `open` `high` `low` `close` `vol` `amount`

- **缓存时效**：通常保留 7 天，陈旧缓存 30 天内清理
  - 说明：月频数据最稳定，天然适合长缓存和批量复用。
  - 刷新建议：月线通常在月末收盘后刷新即可；历史区间按需调用

```python
data = get_json('/tushare/pro/us_monthly', {"ts_code": "AAPL", "start_date": "2020-01-01", "end_date": "2026-03-27"})
print(data)
```

#### `us_tradecal`

- **示例URL**：`https://ai-tool.indevs.in/pro/us_tradecal?exchange=NYSE&start_date=20250101&end_date=20250131`
- **调用路径**：`GET /tushare/pro/us_tradecal`

| 参数 | 示例值 |
|---|---|
| exchange | 'NYSE' |
| start_date | '20250101' |
| end_date | '20250131' |

- **返回字段**：`exchange` `cal_date` `is_open` `pretrade_date`

- **缓存时效**：通常 7 天
  - 说明：交易日历变化非常低频，长缓存最合适，重点是保证接口统一语义。
  - 刷新建议：节假日表通常无需高频刷新；跨年、节假日附近或需要校验新安排时再刷新

```python
data = get_json('/tushare/pro/us_tradecal', {"exchange": "NYSE", "start_date": "20250101", "end_date": "20250131"})
print(data)
```

### 25. 财务报表数据

| 接口名 | 调用路径 |
|---|---|
| `income` | `/tushare/pro/income` |
| `balancesheet` | `/tushare/pro/balancesheet` |
| `cashflow` | `/tushare/pro/cashflow` |
| `dividend` | `/tushare/pro/dividend` |

#### `income`

- **示例URL**：`https://ai-tool.indevs.in/pro/income?ts_code=000001.SZ`
- **调用路径**：`GET /tushare/pro/income`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |

- **返回字段**：`ts_code` `ann_date` `f_ann_date` `end_date` `report_type` `basic_eps` `total_revenue` `revenue` `n_income` `n_income_attr_p`

```python
data = get_json('/tushare/pro/income', {"ts_code": "000001.SZ"})
print(data)
```

#### `balancesheet`

- **示例URL**：`https://ai-tool.indevs.in/pro/balancesheet?ts_code=000001.SZ`
- **调用路径**：`GET /tushare/pro/balancesheet`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |

- **返回字段**：`ts_code` `ann_date` `f_ann_date` `end_date` `total_assets` `total_liab` `total_hldr_eqy_exc_min_int` `total_hldr_eqy_inc_min_int` `money_cap`

```python
data = get_json('/tushare/pro/balancesheet', {"ts_code": "000001.SZ"})
print(data)
```

#### `cashflow`

- **示例URL**：`https://ai-tool.indevs.in/pro/cashflow?ts_code=000001.SZ`
- **调用路径**：`GET /tushare/pro/cashflow`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |

- **返回字段**：`ts_code` `ann_date` `f_ann_date` `end_date` `n_cashflow_act` `n_cashflow_inv_act` `n_cash_flows_fnc_act` `n_incr_cash_cash_equ` `c_cash_equ_end_period`

```python
data = get_json('/tushare/pro/cashflow', {"ts_code": "000001.SZ"})
print(data)
```

#### `dividend`

- **示例URL**：`https://ai-tool.indevs.in/pro/dividend?ts_code=000001.SZ`
- **调用路径**：`GET /tushare/pro/dividend`

| 参数 | 示例值 |
|---|---|
| ts_code | '000001.SZ' |

- **返回字段**：`ts_code` `ann_date` `end_date` `stk_div` `cash_div` `record_date` `ex_date` `imp_ann_date`

```python
data = get_json('/tushare/pro/dividend', {"ts_code": "000001.SZ"})
print(data)
```

## 五、字段说明（常用）

各接口返回字段为中文表头的拼音缩写，常用约定如下：

| 字段 | 含义 |
|---|---|
| ts_code | 证券代码（000002.SZ / 000001.SH） |
| trade_date | 交易日期 YYYYMMDD |
| close/open/high/low | 收盘/开/高/低 |
| vol/amount | 成交量（手）/ 成交额（千元） |
| turnover_rate | 换手率（%） |
| pe/pb | 市盈率 / 市净率 |
| total_mv/circ_mv | 总市值 / 流通市值（万元） |

> 以上为通用惯例，具体字段以各接口 `fields` 为准；网关对未知字段会忽略/报错，请求时建议显式指定 `fields`。
