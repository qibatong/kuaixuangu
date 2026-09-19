# -*- coding: utf-8 -*-
"""只读实测(用本机已部署的 fetcher): 老文件路径 vs 我们的补丁源, 谁能拿到 f630

对应主人的疑问: 「原始的选股文件不存库、每次实时拉取, 为啥就能拿到异动值?」
"""
import sys
import json
import time

sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services import fetcher          # noqa: E402

FS = "m:1+t:2,m:0+t:6"     # 沪深主板(服务实际用的 hs 分区)
CODES = ["600000", "600519", "000001", "300434", "688981"]


def stat(tag, diff, label="f630"):
    if diff is None:
        return
    vals = [int(it.get(label) or 0) for it in diff]
    nz = sum(1 for v in vals if v)
    buckets = {}
    for v in vals:
        buckets[v] = buckets.get(v, 0) + 1
    print("       %s 非0 = %d/%d   分布 = %s" % (label, nz, len(vals),
                                              json.dumps(dict(sorted(buckets.items())), ensure_ascii=False)))
    if diff:
        s = diff[0]
        print("       样例: %s %s  %s=%s f615=%s" % (s.get("f12"), s.get("f14"),
                                                   label, s.get(label), s.get("f615")))


def main():
    print("=" * 84)
    print("① 老文件的等价路径: push2dycalc · clist · 只第 1 页 200 只")
    print("=" * 84)
    try:
        t0 = time.time()
        page = fetcher._fetch_clist_page(FS, 1, "f3")
        ms = (time.time() - t0) * 1000
        print("   [ OK ] %5.0fms  返回 %d 只  total=%s" % (ms, len(page), getattr(page, "total", "?")))
        stat("page1", page)
    except Exception as e:                                    # noqa: BLE001
        print("   [FAIL] %s: %s" % (type(e).__name__, str(e)[:140]))

    print()
    print("=" * 84)
    print("② 我们的补丁源: push2.eastmoney · ulist.np/get · 按 code 点查")
    print("=" * 84)
    try:
        t0 = time.time()
        rows = fetcher.fetch_raw_by_codes(CODES)
        ms = (time.time() - t0) * 1000
        print("   [ OK ] %5.0fms  返回 %d 只" % (ms, len(rows)))
        stat("ulist", rows)
    except Exception as e:                                    # noqa: BLE001
        print("   [FAIL] %s: %s" % (type(e).__name__, str(e)[:140]))

    print()
    print("=" * 84)
    print("③ 采集侧全量: push2dycalc · clist · 按 total 动态页数(v4.11.32)")
    print("=" * 84)
    try:
        t0 = time.time()
        rows = fetcher.fetch_eastmoney_all(FS)
        ms = (time.time() - t0) * 1000
        print("   [ OK ] %5.0fms  返回 %d 只" % (ms, len(rows)))
        stat("clist_all", rows)
    except Exception as e:                                    # noqa: BLE001
        print("   [FAIL] %s: %s" % (type(e).__name__, str(e)[:140]))

    print()
    print("=" * 84)
    print("④ 熔断状态")
    print("=" * 84)
    try:
        h = fetcher._HEALTH.get("eastmoney_clist", {})
        print("   eastmoney_clist: ok=%s fail=%s down_since=%s"
              % (h.get("ok"), h.get("fail"), h.get("down_since")))
        print("   _check_circuit = %s" % fetcher._check_circuit("eastmoney_clist"))
    except Exception as e:                                    # noqa: BLE001
        print("   读取失败: %r" % (e,))
    print()
    print("注: 周六非竞价时段, f630 真值可能为 0; 本探针重点看【接口能否连通】。")


if __name__ == "__main__":
    main()
