# 开盘啦 (KPL) 已封装接口索引

> 自动生成: `python scripts/kpl_interface_index.py` — 请勿手改。

> 数据源服务: `backend/app/services/kpl.py`

> 调用方式: 统一走 `_call(host_key, params)` (POST form-urlencoded + Token/UserID/DeviceID 注入), 新增接口只需写 `fetch_kpl_docXX` + `_cached` 缓存。


## 一、编号接口 (fetch_kpl_docXX, 共 88 个)

| 编号 | 功能 | host | a= | c= | apiv | 已接入业务 |
|------|------|------|----|----|------|:---:|
| doc7 | **个股行情-日K** — k线-个股 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GetKLineDay_W14` | `StockLineData` | w40 | ⬜ 未接入 |
| doc8 | **个股行情-分时** — 分时与、实时涨幅 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `GetStockTrendIncremental` | `StockL2Data` | w41 | ⬜ 未接入 |
| doc9 | **个股行情-盘口** — 盘口五档 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `GetStockPanKou` | `StockL2Data` | w41 | ⬜ 未接入 |
| doc13 | **个股行情** — 大单成交 (apphq.longhuvip.com) -> dict | apphq.longhuvip.com | `GetMainMonitor_w30` | `StockYiDongKanPan` | w31 | ⬜ 未接入 |
| doc14 | **个股行情** — 大单委托 (apphq.longhuvip.com) -> dict | apphq.longhuvip.com | `GetWeiTuo_W14` | `StockL2Data` | w39 | ⬜ 未接入 |
| doc15 | **个股行情** — 涨停复盘 - 复盘啦 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetPlateInfo_w38` | `DailyLimitResumption` | w42 | ⬜ 未接入 |
| doc16 | **个股行情** — 涨停跌停-数量 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `RiseFallAnalysis` | `HomeDingPan` | w43 | ⬜ 未接入 |
| doc17 | **个股行情** — 涨停数量历史 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `RiseFallAnalysis` | `HisHomeDingPan` | w43 | ⬜ 未接入 |
| doc18 | **个股行情** — 上涨/下跌家数 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `MoodNumCount` | `MarketMood` | w43 | ⬜ 未接入 |
| doc19 | **个股行情** — 昨日涨停今表现 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetPlate_Info_QJ` | `ZhiShuRanking` | w42 | ⬜ 未接入 |
| doc20 | **个股行情** — 昨日连板今表现 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetPlate_Info_QJ` | `ZhiShuRanking` | w42 | ⬜ 未接入 |
| doc21 | **个股行情** — 昨日破板今日表现 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetPlate_Info_QJ` | `ZhiShuRanking` | w42 | ⬜ 未接入 |
| doc22 | **个股行情** — 今日破板率 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `RiseFallAnalysis` | `HomeDingPan` | w43 | ⬜ 未接入 |
| doc23 | **个股行情** — 情绪值指标/连板高度 (apphq.longhuvip.com) -> dict | apphq.longhuvip.com | `ChangeStatistics` | `HomeDingPan` | w41 | ⬜ 未接入 |
| doc24 | **个股行情** — 情绪-强度-历史 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `ChangeStatistics` | `HisHomeDingPan` | w44 | ⬜ 未接入 |
| doc30 | **竞价-涨停委买额(历史)** — 竞价涨停委买额-历史接口 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `MorningBiddingList` | `HisHomeDingPan` | w41 | ✅ |
| doc31 | **竞价** — 竞价-个股竞价分时 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `GetStockBid` | `StockL2Data` | w41 | ⬜ 未接入 |
| doc33 | **竞价** — 历史 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `ZhiBoContent` | `HisConceptionPoint` | w40 | ⬜ 未接入 |
| doc41 | **竞价** — 精选板块列表-实时： (apphq.longhuvip.com) -> dict | apphq.longhuvip.com | `RealRankingInfo` | `ZhiShuRanking` | w26 | ⬜ 未接入 |
| doc42 | **竞价** — 精选板块列表-历史 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `RealRankingInfo` | `ZhiShuRanking` | w41 | ⬜ 未接入 |
| doc43 | **竞价** — 精选板块-当天历史 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `RealRankingInfo` | `ZhiShuRanking` | w42 | ⬜ 未接入 |
| doc46 | **竞价** — 板块成分股 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `ZhiShuStockList_W8` | `ZhiShuRanking` | w41 | ⬜ 未接入 |
| doc47 | **竞价** — 当天涨停原因： (apphq.longhuvip.com) -> dict | apphq.longhuvip.com | `GetKLineZhangTing` | `StockLineData` | w24 | ⬜ 未接入 |
| doc48 | **竞价** — 历史涨停原因： (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GetKLineZhangTing` | `StockLineData` | w24 | ⬜ 未接入 |
| doc49 | **竞价** — 1，涨停的首板 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `DailyLimitPerformance` | `HomeDingPan` | w39 | ⬜ 未接入 |
| doc50 | **竞价** — 2，涨停的2板 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `DailyLimitPerformance` | `HomeDingPan` | w39 | ⬜ 未接入 |
| doc51 | **竞价** — 3，涨停的3板 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `DailyLimitPerformance` | `HomeDingPan` | w39 | ⬜ 未接入 |
| doc52 | **竞价** — 4，涨停的4板 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `DailyLimitPerformance` | `HomeDingPan` | w39 | ⬜ 未接入 |
| doc53 | **竞价** — 5，涨停的更高 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `DailyLimitPerformance` | `HomeDingPan` | w39 | ⬜ 未接入 |
| doc54 | **竞价** — 1，历史涨停的首板 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `DailyLimitPerformance` | `HisHomeDingPan` | w31 | ⬜ 未接入 |
| doc55 | **竞价** — 2，涨停的2板 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `DailyLimitPerformance` | `HisHomeDingPan` | w31 | ⬜ 未接入 |
| doc56 | **竞价** — 3，涨停的3板 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `DailyLimitPerformance` | `HisHomeDingPan` | w31 | ⬜ 未接入 |
| doc57 | **竞价** — 4，涨停的4板 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `DailyLimitPerformance` | `HisHomeDingPan` | w31 | ⬜ 未接入 |
| doc58 | **竞价** — 5，涨停的更高 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `DailyLimitPerformance` | `HisHomeDingPan` | w31 | ⬜ 未接入 |
| doc59 | **竞价** — 1，未涨停的首板 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `DailyLimitPerformance2` | `HomeDingPan` | w40 | ⬜ 未接入 |
| doc60 | **竞价** — 2，未涨停的2板 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `DailyLimitPerformance2` | `HomeDingPan` | w40 | ⬜ 未接入 |
| doc61 | **竞价** — 3，未涨停的3板 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `DailyLimitPerformance2` | `HomeDingPan` | w40 | ⬜ 未接入 |
| doc62 | **竞价** — 4，未涨停的4板 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `DailyLimitPerformance2` | `HomeDingPan` | w40 | ⬜ 未接入 |
| doc63 | **竞价** — 5，未涨停的更高 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `DailyLimitPerformance2` | `HomeDingPan` | w40 | ⬜ 未接入 |
| doc64 | **竞价** — 1，未涨停首板 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `DailyLimitPerformance2` | `HisHomeDingPan` | w42 | ⬜ 未接入 |
| doc65 | **竞价** — 2，未涨停2板 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `DailyLimitPerformance2` | `HisHomeDingPan` | w42 | ⬜ 未接入 |
| doc66 | **竞价** — 3，未涨停3板 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `DailyLimitPerformance2` | `HisHomeDingPan` | w42 | ⬜ 未接入 |
| doc67 | **竞价** — 4，未涨停4板 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `DailyLimitPerformance2` | `HisHomeDingPan` | w42 | ⬜ 未接入 |
| doc68 | **竞价** — 5，未涨停 更高 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `DailyLimitPerformance2` | `HisHomeDingPan` | w42 | ⬜ 未接入 |
| doc69 | **竞价** — 百日新高-板块排序 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GroupCount_w28` | `StockNewHigh` | w41 | ⬜ 未接入 |
| doc70 | **盘中-短线精灵** — 短线精灵 (apphq.longhuvip.com) -> dict | apphq.longhuvip.com | `Radar` | `HomeDingPan` | w33 | ⬜ 未接入 |
| doc71 | **盘中-人气热榜** — 盘中人气热榜 (apphq.longhuvip.com) -> dict | apphq.longhuvip.com | `GetHotPHB` | `StockBidYiDong` | w29 | ⬜ 未接入 |
| doc72 | **指数-全球指数** — 全球指数 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GlobalCommon` | `GlobalIndex` | w44 | ⬜ 未接入 |
| doc74 | **盘中** — 历史： (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GetStockChouMa_New` | `StockL2History` | w41 | ⬜ 未接入 |
| doc76 | **盘中** — 涨停基因 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `GetZhangTingGene` | `StockL2Data` | w42 | ⬜ 未接入 |
| doc77 | **盘中** — 大面股 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GetPMSL_KQXY` | `FuPanLa` | w35 | ⬜ 未接入 |
| doc78 | **盘中** — 板块内涨停数 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `GetPlate_Info_QJ` | `ZhiShuRanking` | w41 | ⬜ 未接入 |
| doc79 | **盘中** — 板块竞价异动 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `GetBKJJ_W36` | `StockBidYiDong` | w41 | ✅ |
| doc80 | **盘中** — 异动板块的个股 (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `GetBKJJBL` | `StockBidYiDong` | w41 | ⬜ 未接入 |
| doc81 | **盘中** — 板块列表（end为当日） (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetInterviewsByDateZS` | `StockLineData` | w41 | ⬜ 未接入 |
| doc82 | **盘中** — 全市场个股区间统计（end为当日） (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetInterviewsByDateStock` | `StockLineData` | w41 | ⬜ 未接入 |
| doc83 | **盘中** — 实时接口（最新季度）: (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GGList_JGCC` | `ZhuLiChiCang` | w41 | ⬜ 未接入 |
| doc84 | **盘中** — 历史 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GGList_JGCC` | `ZhuLiChiCang` | w41 | ⬜ 未接入 |
| doc85 | **盘中** — 实时接口（最新季度）、历史接口： (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GGList_JGCC_Plate_Stocks` | `ZhuLiChiCang` | w41 | ⬜ 未接入 |
| doc86 | **盘中** — 实时接口：（最新季度） (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GGList_BXZJ` | `ZhuLiChiCang` | w44 | ⬜ 未接入 |
| doc87 | **盘中** — 历史 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GGList_BXZJ` | `ZhuLiChiCang` | w44 | ⬜ 未接入 |
| doc88 | **盘中** — 实时接口：（最新季度） (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GGList_BXZJ_Stocks` | `ZhuLiChiCang` | w44 | ⬜ 未接入 |
| doc89 | **盘中** — 历史 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GGList_BXZJ_Stocks` | `ZhuLiChiCang` | w44 | ⬜ 未接入 |
| doc90 | **盘中** — 异动实时接口 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetPianLiZhi_Index` | `StockBidYiDong` | w44 | ⬜ 未接入 |
| doc91 | **盘中** — 股东变更 (applhb.longhuvip.com) -> dict | applhb.longhuvip.com | `GuDongRenShu` | `YiDianCangWei` | w44 | ⬜ 未接入 |
| doc92 | **盘中** — 股东追踪，追股东 (applhb.longhuvip.com) -> dict | applhb.longhuvip.com | `JGStockListox` | `JGTracking` | w41 | ⬜ 未接入 |
| doc93 | **盘中** — 股东追踪，追个股 (applhb.longhuvip.com) -> dict | applhb.longhuvip.com | `GetJGNameID` | `JGTracking` | w44 | ⬜ 未接入 |
| doc94 | **个股-全部相关概念板块** — \u4e2a\u80a1 - \u5168\u90e8\u76f8\u5173 \u6982\u5ff5\u677f\u5757 (apphwhq/apphwshhq.longhuvip.com) -> dict | apphwhq/apphwshhq.longhuvip.com | `GetStockIDPlate` | `StockL2Data` | w43 | ✅ |
| doc95 | **资讯-头条** — 头条 (apparticle.longhuvip.com) -> dict | apparticle.longhuvip.com | `GetTopList` | `PCNewsFlash` | w44 | ⬜ 未接入 |
| doc96 | **资讯-新闻** — 新闻 (apparticle.longhuvip.com) -> dict | apparticle.longhuvip.com | `GetList` | `PCNewsFlash` | w44 | ⬜ 未接入 |
| doc97 | **资讯** — 明天炒什么（列表） (applhb.longhuvip.com) -> dict | applhb.longhuvip.com | `InfoList` | `Topic` | w44 | ⬜ 未接入 |
| doc98 | **资讯** — 明天炒什么 利好个股 (applhb.longhuvip.com) -> dict | applhb.longhuvip.com | `InfoZS` | `Topic` | w44 | ⬜ 未接入 |
| doc99 | **资讯** — 明天炒什么 文章内容 (applhb.longhuvip.com) -> dict | applhb.longhuvip.com | `InfoGet` | `Topic` | w44 | ⬜ 未接入 |
| doc100 | **资讯** — 上榜股票 (applhb.longhuvip.com) -> dict | applhb.longhuvip.com | `GetStockList` | `LongHuBang` | w44 | ⬜ 未接入 |
| doc101 | **资讯** — 买入、卖出营业部详细数据 (applhb.longhuvip.com) -> dict | applhb.longhuvip.com | `GetNewOneStockInfo` | `Stock` | w41 | ⬜ 未接入 |
| doc103 | **个股** — 尾盘竞价抢筹 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetWPQC` | `StockBidYiDong` | w44 | ⬜ 未接入 |
| doc104 | **个股** — 竞价砸盘 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `MorningBiddingList` | `HomeDingPan` | w44 | ⬜ 未接入 |
| doc105 | **个股** — 竞价撮合大于2000万 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `MorningBiddingList` | `HomeDingPan` | w44 | ⬜ 未接入 |
| doc106 | **个股** — 历史分时 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GetStockTrend` | `StockL2History` | w41 | ⬜ 未接入 |
| doc107 | **个股** — 指数k线 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GetZhiShuKLine` | `ZhiShuKLine` | w44 | ⬜ 未接入 |
| doc108 | **个股** — 重点监控股票 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetYDTP_ZDJK_Today` | `StockBidYiDong` | w43 | ⬜ 未接入 |
| doc109 | **个股** — 多次异动个股 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetPianLiZhi_Many` | `StockBidYiDong` | w43 | ⬜ 未接入 |
| doc110 | **个股** — 实时接口 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `MarketSCLNKLine` | `HisHomeDingPan` | w44 | ⬜ 未接入 |
| doc111 | **个股** — 历史接口 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `MarketSCLNKLine` | `HisHomeDingPan` | w44 | ⬜ 未接入 |
| doc112 | **个股** — 竞价大于1000万 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `MorningBiddingList` | `HomeDingPan` | w44 | ⬜ 未接入 |
| doc113 | **个股** — 副图688523 (apphis.longhuvip.com) -> dict | apphis.longhuvip.com | `GetBidVolKLine` | `StockLineData` | w44 | ⬜ 未接入 |
| doc115 | **竞价-涨停委买额(实时)** — 竞价涨停委买额-实时接口： (apphwhq.longhuvip.com) -> dict | apphwhq.longhuvip.com | `MorningBiddingList` | `HomeDingPan` | w41 | ⬜ 未接入 |
| doc116 | **其他** — 大面股-实时 (apphwshhq.longhuvip.com) -> dict | apphwshhq.longhuvip.com | `GetPMSL_KQXY` | `FuPanLa` | w35 | ⬜ 未接入 |

## 二、具名业务封装 (fetch_xxx, 共 31 个)

| 函数 | 功能 | 已接入业务 |
|------|------|:---:|
| `fetch_bid_boom` | **竞价-爆量榜** — 竞价爆量/竞价成交额榜(实时): Type=10; 涨停委买额从 Type=4 榜单按代码合并补充 | ✅ |
| `fetch_bid_qiangcang` | **竞价抢筹** — 竞价抢筹(左右双表, 对标短线侠): | ✅ |
| `fetch_bid_seal` | **竞价-涨停委买额(实时)** — 竞价涨停委买额(实时, 9:15-9:30 有效) | ✅ |
| `fetch_board_map` | **概念合并(内部)** — 全市场个股概念 map: {code: "概念1、概念2"} | ✅ |
| `fetch_board_rank` | **板块强度** — 板块强度排行(实时) | ✅ |
| `fetch_board_rank_by_date` | **板块强度(历史)** — 精选板块列表-历史(doc42 apiv=w41, apphis host): | ✅ |
| `fetch_broken_line` | **炸板率曲线** — 炸板数量曲线(doc36): limit_up_broken_count + ratio | ✅ |
| `fetch_broken_zt` | **炸板股** — 炸板列表(东财 flash, 无需Token): day=None 今日; 'yesterday' 上一交易日; 'YYYY-MM-DD' 指定日 | ✅ |
| `fetch_dadan_net` | **大单净额** — 指定个股-大单净额分时(doc75): GetStockDaDanTrendIncremental | ✅ |
| `fetch_dt_pool` | **跌停池** — 跌停实时池(doc12): day=None 今日; YYYY-MM-DD 历史 | ✅ |
| `fetch_hot_plates` | **热点解读-板块** — 板块名称与对应题材(doc40): [{id,name,description}, ...] | ✅ |
| `fetch_hot_rank` | **人气榜** — 盘中人气热榜: List [[code,name,涨跌幅,?,排名,?,?], ...] | ✅ |
| `fetch_hot_stocks` | **热点解读-强势股** — 热点解读-强势股列表(doc39): items 是二维数组(fields 作列头) | ✅ |
| `fetch_ladder` | **连板梯队** — 连板梯队(实时), pid_type 1~5 | ✅ |
| `fetch_ladder_all` | **连板梯队** — 连板梯队全档(首板~五板+), 返回 {1:[...],2:[...],...} | ✅ |
| `fetch_lhb` | **龙虎榜** — 龙虎榜上榜股票(当天/指定历史日期): [{code,name,change,limitBoards,buyIn,amount,floatMv,turnover,amplitude,totalMv,joinNum}, ...] | ✅ |
| `fetch_lhb_detail` | **龙虎榜-明细** — 龙虎榜个股营业部明细: {name,time,change,limitBoards,buyTotal,sellTotal,upReason, | ✅ |
| `fetch_live_room` | **直播** — 涨停直播(doc32): fupanwang 实时涨停播报 | ✅ |
| `fetch_market_temp_line` | **市场温度曲线** — 市场温度曲线(doc38): market_temperature | ✅ |
| `fetch_sentiment` | **市场情绪** — 情绪值/连板高度: {ztjs 涨停家数, strong 情绪, lbgd 连板高度, df_num 大幅回撤} | ✅ |
| `fetch_stock_plate` | **个股-全部相关概念板块** — 个股全部相关概念板块(开盘啦 doc94 GetStockIDPlate): | ✅ |
| `fetch_updown_line` | **涨跌家数曲线** — 上涨/下跌家数曲线(doc34) | ✅ |
| `fetch_wpqc` | **尾盘抢筹** — 尾盘竞价抢筹(14:57 后): List [[code,name,资金标签,类型,概念,涨跌幅,抢筹委托,收盘金额, | ✅ |
| `fetch_yest_broken` | **昨日炸板(内部)** — 昨断板: 昨日涨停池中今日未涨停的股票(今日竞价表现从 snapshot_bid 9:25 全市场补) | ✅ |
| `fetch_yest_zt` | **昨日涨停今表现(内部)** — 昨日涨停股今日竞价表现: flash limit_up_pool&date=上一交易日(95只) + merge Type4 | ✅ |
| `fetch_yest_zt_perf_line` | **昨涨停今表现曲线** — 昨日涨停今日表现曲线(doc37): yesterday_limit_up_avg_pcp | ✅ |
| `fetch_yest_zt_pool` | **昨日涨停池** — 昨日涨停池(doc28): 默认今日的昨日; 可指定 YYYY-MM-DD | ✅ |
| `fetch_yesterday_perf` | **昨日涨停表现** — 昨日涨停/连板/破板今日平均表现: {zt:{change,net,date}, lb:{...}, pb:{...}} | ✅ |
| `fetch_zt_dt_line` | **涨停跌停曲线** — 涨停数与跌停数曲线(doc35) | ✅ |
| `fetch_zt_pool` | **涨停池** — 涨停实时池(doc10): day=None 今日; YYYY-MM-DD 历史. 复用 _flash_pool | ✅ |
| `fetch_zt_reason` | **涨停原因** — 个股当天/历史涨停原因: 返回 [{date, reason, sclt(龙一龙二), boom}, ...] | ✅ |

---

## 附: 其他数据源封装

- `backend/app/services/fetcher.py` — 东财行情/日K/竞价 (ensure_cache/ensure_spot_cache/fetch_yesterday_amounts 等)

- `backend/app/services/sector_rotation.py` — 板块轮动 (kpl/em 双源)

- `backend/app/services/hot_rank.py` — 人气榜 (kpl/em/ths 三源)

