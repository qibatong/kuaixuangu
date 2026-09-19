# -*- coding: utf-8 -*-
"""关键验证: 把补丁源的域名从 push2 换成 push2dycalc, 能否恢复按 code 点查(且带 f630)?

背景: fetcher._ULIST_URL 写死 https://push2.eastmoney.com/api/qt/ulist.np/get
      实测该域名 RemoteDisconnected(整站 RST); 而 push2dycalc.eastmoney.com 畅通。
本探针分别打两个域名, 比较连通性与 f630 可得性。只读。
"""
import sys
import json
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/opt/kuaixuan/backend")

from app.services import fetcher        # noqa: E402
from app.core import config             # noqa: E402

CODES = ["600000", "600519", "000001", "300434", "688981", "002584"]
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
       "Referer": "https://quote.eastmoney.com/"}


def try_host(host, path, extra=None):
    secids = ",".join(fetcher._secid(c) for c in CODES)
    q = {"fltt": 2, "invt": 2, "fields": config.FIELDS,
         "secids": secids, "ut": config.EASTMONEY_UT}
    if extra:
        q.update(extra)
    url = host + path + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers=HDR)
    t0 = time.time()
    try:
        with fetcher._http_get(req, timeout=10, context=fetcher._NO_VERIFY_CTX) as resp:
            body = resp.read().decode("utf-8", "replace")
        ms = (time.time() - t0) * 1000
        d = json.loads(body)
        diff = (d.get("data") or {}).get("diff") or []
        nz = sum(1 for it in diff if int(it.get("f630") or 0))
        print("   [ OK ] %-38s %5.0fms  rc=%-3s 返回=%-3d f630非0=%d"
              % (host.split("//")[1], ms, d.get("rc"), len(diff), nz))
        for it in diff[:6]:
            print("           %-7s %-8s f630=%-3s f615=%-7s f2=%-8s f10=%s"
                  % (it.get("f12"), it.get("f14"), it.get("f630"),
                     it.get("f615"), it.get("f2"), it.get("f10")))
        return diff
    except Exception as e:                                    # noqa: BLE001
        ms = (time.time() - t0) * 1000
        print("   [FAIL] %-38s %5.0fms  %s: %s"
              % (host.split("//")[1], ms, type(e).__name__, str(e)[:70]))
        return None


def main():
    print("=" * 92)
    print("接口 ulist.np/get (按 code 批量点查) — 换域名对照")
    print("=" * 92)
    try_host("https://push2.eastmoney.com", "/api/qt/ulist.np/get")
    try_host("https://push2dycalc.eastmoney.com", "/api/qt/ulist.np/get")

    print()
    print("=" * 92)
    print("接口 stock/get (单只点查)")
    print("=" * 92)
    try_host("https://push2.eastmoney.com", "/api/qt/stock/get",
             {"secid": "1.600000", "fields": "f43,f57,f58,f60,f107,f630"})
    try_host("https://push2dycalc.eastmoney.com", "/api/qt/stock/get",
             {"secid": "1.600000", "fields": "f43,f57,f58,f60,f107,f630"})

    print()
    print("当前 fetcher._ULIST_URL =", fetcher._ULIST_URL)


if __name__ == "__main__":
    main()
