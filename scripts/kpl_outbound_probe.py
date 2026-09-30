# -*- coding: utf-8 -*-
"""P1-5（竞价链路清单 1.3）**量化探针** —— 数「三个 tab 各打几次开盘啦」+ 三 tab 重叠（合并机会）

清单原话：**先量化"每次请求出网几次"，再评估能否复用同一份榜单缓存**（避免盲改）。
本探针只统计、不改任何业务数据；缓存会被清（冷启动计数），服务会自行重取。

跑法（测试机）：
    set -a; . /etc/kuaixuan/env.conf; set +a
    timeout 300 /opt/bid-venv/bin/python /tmp/_kx_p15_probe.py

⚠️ 今天休市 ⇒ 强制把 `_is_auction_hours` 打成 True 走**竞价分支**（今天只数次数，
   数据本身是旧的/空的，计数不受影响）。实时调用**逐条打印**（便于随时 Ctrl-C）。
"""
import sqlite3
import sys
import time
from collections import Counter

from app.api import kpl as api_kpl
from app.services import kpl
from app.services.cache_store import store

CALLS = []
_orig = kpl._call


def _spy(host_key, params, timeout=12):
    # 🔴 必须带 StockID: 否则"同名接口不同票"会被误判成"可去重的重复调用"
    #    (2026-10-01 实测踩到: 三 tab 都出现 GetStockIDPlate ⇒ 误报"可省 70 次")
    tag = (host_key, str(params.get("a", "")), str(params.get("Type", "")),
           str(params.get("StockID", "") or params.get("c", ""))[:16])
    CALLS.append(tag)
    sys.stdout.write("        → [%d] host=%s a=%s Type=%s c=%s\n"
                     % (len(CALLS), tag[0], tag[1], tag[2], tag[3]))
    sys.stdout.flush()
    return _orig(host_key, params, timeout)


kpl._call = _spy
api_kpl._is_auction_hours = lambda: True          # 强制竞价分支


def _keys():
    conn = sqlite3.connect(store.db_file)
    out = set(r[0] for r in conn.execute(
        "SELECT key FROM kv_cache WHERE key LIKE 'kpl:%'"))
    conn.close()
    return out


def measure(name, fn):
    before = _keys()
    try:
        store.clear_prefix("kpl:")
    except Exception as e:                                     # noqa: BLE001
        print("   ⚠️ 清缓存失败 %s" % e)
    CALLS.clear()
    t0 = time.time()
    res = None
    try:
        res = fn()
    except Exception as e:                                     # noqa: BLE001
        print("   ⚠️ 调用异常: %s" % str(e)[:140])
    cold, dt = len(CALLS), time.time() - t0
    cold_calls = list(CALLS)
    CALLS.clear()
    t1 = time.time()
    try:
        fn()
    except Exception:                                          # noqa: BLE001
        pass
    warm, wdt = len(CALLS), time.time() - t1
    n = len(res.get("list") or []) if isinstance(res, dict) else -1
    print("\n=== %s ===" % name)
    print("  冷启动: 上游 **%d 次** / %.2fs / 返回 %d 条" % (cold, dt, n))
    print("  热(命中缓存): 上游 %d 次 / %.2fs" % (warm, wdt))
    print("  本 tab 新建缓存键: %s" % sorted(k for k in _keys() - before)[:8])
    if cold_calls:
        agg = Counter((c[1], c[2]) for c in cold_calls)
        print("  接口构成: %s" % ", ".join("%s(Type%s)×%d" % (a, t, n)
                                        for (a, t), n in agg.most_common(5)))
    return cold_calls


def main():
    res = {}
    res["封单 bid-seal"] = measure("竞价异动「封单」 /api/kpl/bid-seal",
                                   lambda: api_kpl.api_kpl_bid_seal(None, 1, ""))
    res["爆量 bid-boom"] = measure("竞价异动「爆量」 /api/kpl/bid-boom",
                                   lambda: api_kpl.api_kpl_bid_boom(None, 1, ""))
    res["净额 bid-net"] = measure("竞价异动「净额」 /api/kpl/bid-net",
                                  lambda: api_kpl.api_kpl_bid_net(None, 1, ""))

    print("\n########## 三 tab 重叠（同一上游调用被几个 tab 打到） ##########")
    cnt = Counter()
    for calls in res.values():
        for c in set(calls):
            cnt[c] += 1
    shared = [(c, k) for c, k in cnt.most_common() if k > 1]
    for c, k in shared:
        print("  ⚠️ %d 个 tab 都打了: host=%s a=%s Type=%s c=%s" % (k, c[0], c[1], c[2], c[3]))
    if not shared:
        print("  （无重叠：三 tab 打的是不同上游接口）")

    tot = sum(len(v) for v in res.values())
    uniq = len(set(c for v in res.values() for c in v))
    print("\n合计: 三 tab 冷启动共 %d 次开盘啦调用 / 去重 %d 次 ⇒ 重叠可省 %d 次"
          % (tot, uniq, tot - uniq))
    for name, calls in res.items():
        print("  · %s: %d 次/冷, 去重 %d" % (name, len(calls), len(set(calls))))

    # 2026-10-01: 顺带验证**出网埋点**自身的计数与汇总行格式（本探针跑的就是真 `_call`，
    #   故这一行就是 10-08 服务日志里会出现的那一行）。两条独立口径(本地 spy 计数 vs 埋点)
    #   对上，才算量化可信。
    # 🔴 不要预先老化窗口: 汇总已是**机会式**(计数时若到期会顺带汇总并清零),
    #   预老化会把刚累积的计数先清掉 ⇒ 这里打印出来的是空行(实测踩到)
    line = kpl.kpl_stat_flush(force=True)
    print("\n########## 埋点汇总行(10-08 服务日志里就是这个格式) ##########")
    print("  " + line)


main()
