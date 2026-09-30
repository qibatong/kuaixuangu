# -*- coding: utf-8 -*-
"""轮询间隔 × 缓存 TTL 对齐闸门（2026-10-01 竞价链路 P2-6 / 清单 3.3）

**判据（本闸门唯一的一条不变量）**：判断"是否白打上游"只看一个方向 ——
  · 前端轮询间隔 **>** 后端 TTL ⇒ 每次轮询都**必然穿透**到上游（缓存 100% 未命中）= 浪费;
  · 前端轮询间隔 **≤** 后端 TTL ⇒ 只是拿到同一份缓存, 廉价（允许, 不必处理）。
⇒ 故只断言 `interval >= ttl`：谁把 TTL 调短、或把轮询调快, 导致"必然穿透", 这里就红。

**为什么放 backend/tests**：TTL 是后端常量（`core.config` / `services.kpl`），而间隔写在前端
源码里。一个用例同时钉住两侧, 单边改动不一致就失败 —— 比两侧各写半句注释可靠。
（前端源码不在本机时 `skip`, 便于 CI 只跑 backend 的场景。）

**为什么必须配 TTL 下限**：否则"把 TTL 调成 1s 让闸门变绿"也能过 —— 那是**假对齐**
（等于关掉缓存、把上游打爆）。低位必须守住盘中新鲜度。

来源依据：`docs/竞价链路-按优先级优化清单-20260929.md` 第 3.3 条 + 生产实测（2026-10-01）。
"""
import os
import re

import pytest

from app.core import config
from app.services import kpl

_FE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                  "frontend", "src"))

# (说明, 前端文件(相对 frontend/src), 抓间隔的正则(毫秒), 后端 TTL 取值函数)
_CASES = [
    ("异动重点监控 yidong-monitor", "views/YidongView.vue",
     r"loadWarn\(\); loadMonitor\(\) \}, (\d+)", lambda: config.KPL_YIDONG_TTL),
    ("市场概览 market-brief", "views/MarketView.vue",
     r"isIntradayNow\(\)\) tick\(\) \}, (\d+)", lambda: kpl.MARKET_BRIEF_TTL),
    ("板块强度 board-rank", "views/MarketView.vue",
     r"isIntradayNow\(\)\) tick\(\) \}, (\d+)", lambda: config.KPL_BOARD_TTL),
    ("板块成分股 board-stocks", "components/MarketBoardPanel.vue",
     r"\}, (\d+), \{ immediate: false \}\)", lambda: config.KPL_BOARD_STOCKS_TTL),
]


def _poll_seconds(fname, pattern):
    """从前端源码抓轮询间隔(秒); 抓不到 => **显式失败**(而不是静默跳过)"""
    # 🔴 适用范围: 本闸门是**仓库级不变量**(同时钉前后端两侧), 只在 git 工作树里有意义。
    #   部署机(如测试机 /opt/kuaixuan)只有一份**代码快照**(含 frontend/src 但不含 .git),
    #   而且那份快照可能落后于本仓库 ⇒ 在那里断言 pattern 会假红(2026-10-01 实测踩到)。
    _root = os.path.abspath(os.path.join(_FE, "..", ".."))
    if not os.path.exists(os.path.join(_root, ".git")):
        pytest.skip("非仓库副本(部署机只有代码快照, 无 .git) —— 本闸门只在仓库内生效: %s" % _root)
    path = os.path.join(_FE, fname)
    if not os.path.exists(path):
        pytest.skip("前端源码不在本机(CI 只跑 backend 时): %s" % fname)
    src = open(path, encoding="utf-8").read()
    m = re.search(pattern, src)
    assert m, ("%s 里找不到该轮询写法 —— 说明它被重构过。请同步更新本闸门的 pattern, "
               "并复核 docs/竞价链路-按优先级优化清单-20260929.md 第 3.3 条" % fname)
    return int(m.group(1)) // 1000


@pytest.mark.parametrize("name,fname,pattern,ttl", _CASES, ids=[c[0] for c in _CASES])
def test_poll_interval_is_not_shorter_than_ttl(name, fname, pattern, ttl):
    """轮询间隔必须 >= TTL：否则每次轮询都穿透上游（缓存白做）"""
    sec = _poll_seconds(fname, pattern)
    t = int(ttl())
    assert sec >= t, (
        "%s: 前端轮询 %ds < 后端 TTL %ds ⇒ 每次轮询必然穿透上游(缓存 100% 未命中)。"
        "二选一: 抬 TTL 到 %ds, 或把轮询降到 %ds。改完请同步本闸门与清单 §3.3 的表格。"
        % (name, sec, t, sec, t))


def test_ttl_still_keeps_intraday_freshness():
    """🔴 TTL 下限: 不许"把 TTL 调成 1s"来让上面的断言变绿（那是假对齐 = 关掉缓存）

    盘中这几个面板的可见新鲜度不能超过 ~1 分钟, 且都不该退化成"无缓存"。
    """
    for name, t in (("异动 yidong", config.KPL_YIDONG_TTL),
                    ("板块强度 board", config.KPL_BOARD_TTL),
                    ("板块成分股 board_stocks", config.KPL_BOARD_STOCKS_TTL),
                    ("市场概览 market_brief", kpl.MARKET_BRIEF_TTL)):
        assert 15 <= t <= 120, "%s TTL=%ss 超出合理区间 [15,120]" % (name, t)


def test_known_exemption_spot_is_documented():
    """**允许方向**的豁免项（记录在案, 不是漏网）: spot 页 30s 轮询 vs SPOT_CACHE_TTL 60s

    这是 interval < ttl（轮询比缓存快）⇒ 只会"拿到同一份行情再重算一次评分", 不打上游;
    且 spot 评分本身依赖实时涨幅/量比, 30s 重算是**有意**的。故这里只钉住"仍是有意为之",
    防止有人把它当错配顺手改坏(例如把 TTL 压到 30 ⇒ 东财全市场预热翻倍出网)。
    """
    assert config.SPOT_CACHE_TTL == 60, "改了 SPOT_CACHE_TTL 请复核 spot 页 30s 轮询的取舍"
