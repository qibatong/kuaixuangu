#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, urllib.request, ssl, time

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

def sina_kline(code, datalen=120):
    sym = "sh" + code if code[0] == "6" else ("bj" + code if code[0] in "48" else "sz" + code)
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketData.getKLineData"
           f"?symbol={sym}&scale=240&ma=no&datalen={datalen}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Referer": "https://finance.sina.com.cn/"})
    with urllib.request.urlopen(req, timeout=12, context=_CTX) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))

if __name__ == "__main__":
    for code in ["600519", "000001", "300750", "688981", "830799"]:
        try:
            d = sina_kline(code)
            print(code, "rows=", len(d), "last=", d[-1]["day"], "close=", d[-1]["close"])
        except Exception as e:
            print(code, "ERR", repr(e)[:120])
        time.sleep(0.1)