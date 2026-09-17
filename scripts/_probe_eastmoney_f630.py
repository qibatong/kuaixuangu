# -*- coding: utf-8 -*-
"""只读探针: 实测东财 f630(异动等级) 的真实取值分布。

背景: 选股评分 17% 因子现由 bid_strength(竞价强度) 替代 f630 异动等级。
若要把该因子「改回东财」, 必须先确认:
  1) f630 到底返回什么取值域 —— contract.py 注释称"实测取值 0/1/2",
     而评分配置的分档表是 [["5","6",1],["4","5",0.85],["3","4",0.6]] (3~6 级)。
     若真为 0/1/2, 则改回东财后该因子**无一命中分档 → 全员 default 0.18 → 零区分度**。
  2) 盘前/竞价时段 f630 是否有值 —— 评分只在 9:25 定格后算, 若那时 f630 还没值,
     改回东财等于自废 17% 权重。

纯只读: 只发 GET, 不写库/不改文件/不重启。
"""
import collections
import json
import sys
import time

sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services import fetcher            # noqa: E402
from app.core import config                 # noqa: E402

# 板块分区(与生产使用一致)
SECTORS = [
    ("深主板+中小", "m:0+t:6,m:0+t:80"),
    ("沪主板+科创", "m:1+t:2,m:1+t:23"),
    ("北交所",      "m:0+t:81+s:2048"),
]


def main():
    print("=" * 72)
    print("东财 f630(异动等级) 实测探针   本地时刻=%s" % time.strftime("%F %T"))
    print("接口 = %s" % config.EASTMONEY_URL)
    print("FIELDS 含 f630 = %s" % ("f630" in config.FIELDS))
    print("=" * 72)

    allf630 = collections.Counter()
    for name, fs in SECTORS:
        try:
            diff = fetcher._fetch_clist_page(fs, 1, "f3")
        except Exception as e:                                    # noqa: BLE001
            print("\n[%s] 抓取失败: %s: %s" % (name, type(e).__name__, e))
            continue
        c630 = collections.Counter()
        c615 = collections.Counter()
        for x in diff:
            c630[str(x.get("f630"))] += 1
            v = x.get("f615")
            c615["有值" if v not in (None, "-", "") else "空"] += 1
            allf630[str(x.get("f630"))] += 1
        print("\n[%s]  样本 n=%d" % (name, len(diff)))
        print("   f630 取值分布 : %s" % dict(c630))
        print("   f615 竞价涨幅 : %s" % dict(c615))
        # 抽 3 只看原始字段
        for x in diff[:3]:
            print("     样例 %s %s  f630=%r f615=%r f2=%r f3=%r f12=%r" % (
                x.get("f12"), x.get("f14"), x.get("f630"),
                x.get("f615"), x.get("f2"), x.get("f3"), x.get("f12")))

    print("\n" + "=" * 72)
    print("全样本 f630 汇总: %s" % dict(allf630))
    print("=" * 72)

    # 关键判定
    cfg_buckets = [["5", "6"], ["4", "5"], ["3", "4"]]
    hit = 0
    tot = 0
    for k, n in allf630.items():
        tot += n
        try:
            v = float(k)
        except (TypeError, ValueError):
            continue
        for lo, hi in cfg_buckets:
            if float(lo) <= v < float(hi):
                hit += n
                break
    print("判定: 分档表(5-6/4-5/3-4) 命中率 = %d/%d" % (hit, tot))
    if tot and hit == 0:
        print("  >>> 🔴 警告: f630 实测取值**无一命中分档表** → 改回东财后 17% 因子恒为")
        print("          default 0.18, 完全失去区分度。改回前必须重校分档表。")
    elif tot:
        print("  >>> ✅ 有命中, 分档表与实测取值域兼容。")


if __name__ == "__main__":
    main()
