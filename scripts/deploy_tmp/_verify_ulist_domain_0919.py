#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v4.11.33 只读验证: 东财 ulist 点查域名修复是否真的让补丁源活过来。

背景: `_ULIST_URL` 原写死 push2.eastmoney.com(整站 RST) ⇒ fetch_raw_by_codes 必然失败
⇒ picker「eastmoney_realtime」补丁源形同虚设 ⇒ 每轮降级腾讯点查(无 f630)。

本脚本**只读**、不改任何配置、不落库:
  1. 逐域名直测 `_fetch_ulist_batch` —— 证明 push2 挂、push2dycalc 通且带 f630;
  2. 走真实链路 `fetch_raw_by_codes`(按 _ULIST_HOSTS 顺序) —— 证明补丁源已恢复;
  3. 输出 f630 覆盖, 供与定格口径对照。

运行: cd /opt/kuaixuan/backend && PYTHONPATH=/opt/kuaixuan/backend /opt/bid-venv/bin/python <本文件>
"""
import logging
import os
import sys
import time
import urllib.parse

sys.path.insert(0, "/opt/kuaixuan/backend")
os.environ.setdefault("OUTBOUND_IPS", "")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

from app.core import config                                  # noqa: E402
from app.services import fetcher                             # noqa: E402

CODES = ["000001", "300434", "002584", "600519", "000920", "301689", "002339", "003026"]


def main():
    secids = ",".join(fetcher._secid(c) for c in CODES)
    qs = urllib.parse.urlencode({
        "fltt": 2, "invt": 2, "fields": config.FIELDS,
        "secids": secids, "ut": config.EASTMONEY_UT,
    })

    print("=" * 78)
    print("① 逐域名直测 _fetch_ulist_batch（同 path、同参数，只换域名）")
    print("=" * 78)
    saved = fetcher._ULIST_HOSTS
    for host in saved:
        fetcher._broken_hosts.clear()
        fetcher._ULIST_HOSTS = (host,)
        t0 = time.time()
        try:
            diff = fetcher._fetch_ulist_batch(qs)
            got = ["%s:f630=%s" % (d.get("f12"), d.get("f630")) for d in diff]
            print("  ✅ %-42s %2d只 %5.0fms  %s"
                  % (host, len(diff), (time.time() - t0) * 1000, " ".join(got)))
        except Exception as e:                               # noqa: BLE001
            print("  ❌ %-42s %5.0fms  %s: %s"
                  % (host, (time.time() - t0) * 1000, type(e).__name__, str(e)[:90]))
    fetcher._ULIST_HOSTS = saved

    print()
    print("=" * 78)
    print("② 真实链路 fetch_raw_by_codes（按 _ULIST_HOSTS 顺序重试）")
    print("=" * 78)
    fetcher._broken_hosts.clear()
    t0 = time.time()
    try:
        raw = fetcher.fetch_raw_by_codes(CODES)
    except Exception as e:                                   # noqa: BLE001
        print("  🔴 点查失败: %s: %s" % (type(e).__name__, e))
        print("  ⇒ 补丁源仍未恢复, 需继续排查")
        return 1
    dt = (time.time() - t0) * 1000

    print("  返回 %d 只, 耗时 %.0fms" % (len(raw), dt))
    nz = 0
    for d in raw:
        w = d.get("f630")
        nz += 1 if (w not in (0, None, "-", "")) else 0
        print("    %-8s f615=%-7s f630=%-4s f2=%s"
              % (d.get("f12"), d.get("f615"), w, d.get("f2")))
    print()
    if raw:
        print("  📊 f630 非 0: %d/%d (%.0f%%)" % (nz, len(raw), 100.0 * nz / len(raw)))
    print()
    print("=" * 78)
    print("结论: 若 ① 中 push2dycalc ✅ 且 ② 返回非空 ⇒ 补丁源(eastmoney_realtime)已恢复")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
