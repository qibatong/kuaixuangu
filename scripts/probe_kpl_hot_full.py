#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓完整 GetPianLiZhi_Hot 数据"""
import json, urllib.parse, urllib.request, ssl, subprocess

def get_env(name):
    try:
        out = subprocess.check_output(
            f"systemctl show kuaixuan -p Environment 2>/dev/null | tr ' ' '\\n'", shell=True
        ).decode()
        for line in out.splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1]
    except Exception:
        pass
    return ""

TOKEN = get_env("KPL_TOKEN"); UID = get_env("KPL_USERID")

_ssl = ssl.create_default_context(); _ssl.check_hostname = False; _ssl.verify_mode = ssl.CERT_NONE

common = {
    "PhoneOSNew": "1", "DeviceID": get_env("KPL_DEVICEID"),
    "VerSion": "5.20.0.2", "Token": TOKEN, "UserID": UID,
    "a": "GetPianLiZhi_Hot", "c": "StockBidYiDong", "apiv": "w44",
}
url = "https://apphwshhq.longhuvip.com/w1/api/index.php?" + urllib.parse.urlencode(common)
req = urllib.request.Request(url, method="POST", headers={
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; V2178A Build/UP1A.231005.007)",
})
with urllib.request.urlopen(req, timeout=10, context=_ssl) as r:
    d = json.loads(r.read().decode("utf-8", "ignore"))
print("Day =", d.get("Day"), "Time =", d.get("Time"), "err =", d.get("errcode"))
for i, it in enumerate(d.get("List") or []):
    print(i, json.dumps(it, ensure_ascii=False))