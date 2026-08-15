# 开盘啦文档接口 vs 本地封装 全量核对报告

> 文档来源: http://110.42.192.180:4096/docs/102 (104 个接口页)
> 核对时间: 2026-08-15, 本地代码: backend/app/services/kpl.py

## 结论: 104 个接口 100% 已封装(88 个编号封装 + 16 个具名覆盖)

## 一、文档有、无 fetch_kpl_docXX 但具名接口已覆盖(15 个)

| doc 编号 | 文档功能 | 具名封装 |
|----------|----------|----------|
| doc10 | 涨停实时 | `fetch_zt_pool(涨停实时)` |
| doc11 | 炸板实时 | `fetch_broken_zt(炸板实时)` |
| doc12 | 跌停实时 | `fetch_dt_pool(跌停实时)` |
| doc25 | 涨停历史 | `fetch_zt_pool(涨停历史)` |
| doc26 | 炸板历史 | `fetch_broken_zt(炸板历史)` |
| doc27 | 跌停历史 | `fetch_dt_pool(跌停历史)` |
| doc28 | 昨日涨停 | `fetch_yest_zt_pool(昨日涨停)` |
| doc32 | 实时 | `fetch_live_room(实时直播)` |
| doc34 | 上涨与下跌数量 - 曲线 | `fetch_updown_line` |
| doc35 | 涨停数与跌停数 - 曲线 | `fetch_zt_dt_line` |
| doc36 | 炸板数量 - 曲线 | `fetch_broken_line` |
| doc37 | 昨日涨停今日表现 - 曲线 | `fetch_yest_zt_perf_line` |
| doc38 | 市场温度 - 曲线 | `fetch_market_temp_line` |
| doc39 | 热点解读 | `fetch_hot_stocks/hot_plates` |
| doc40 | 板块名称与对应题材 | `fetch_hot_plates` |

## 二、本轮新补封装

| 接口 | 说明 |
|------|------|
| `fetch_kpl_doc116` | 大面股-实时 (apphwshhq, a=GetPMSL_KQXY) — 与 doc77 历史同参, host 换实时, 实测返回 9 条 |
| `fetch_kpl_doc75`(补) | 指定个股-大单净额 — 实际已有具名 `fetch_dadan_net` 覆盖 |
