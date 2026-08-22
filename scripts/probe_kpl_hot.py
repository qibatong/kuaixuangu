#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测开盘啦「热门股偏离值」接口: 在测试机上用真实 token 依次尝试候选 a= 参数"""
import json, urllib.parse, urllib.request, ssl

HOSTS = ["apphwshhq.longhuvip.com", "apphwhq.longhuvip.com"]

# 从测试机 systemd 环境读 token
import subprocess
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

TOKEN = get_env("KPL_TOKEN") or get_env("KPL_USERID")
UID = get_env("KPL_USERID") or get_env("KPL_USER_ID")

print("token_len =", len(TOKEN), "uid =", UID)

_ssl = ssl.create_default_context()
_ssl.check_hostname = False
_ssl.verify_mode = ssl.CERT_NONE

def call(host, params, timeout=10):
    common = {
        "PhoneOSNew": "1",
        "DeviceID": get_env("KPL_DEVICEID"),
        "VerSion": "5.20.0.2",
        "Token": TOKEN,
        "UserID": UID,
    }
    common.update(params)
    url = "https://" + host + "/w1/api/index.php?" + urllib.parse.urlencode(common)
    req = urllib.request.Request(url, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; V2178A Build/UP1A.231005.007)",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl) as r:
            body = r.read().decode("utf-8", "ignore")
        return json.loads(body)
    except Exception as e:
        return {"__err__": str(e)}

candidates = [
    ("GetPianLiZhi_Hot", "StockBidYiDong", "w44"),
    ("GetPianLiZhi_Index", "StockBidYiDong", "w44"),
    ("GetPianLiZhi_Hot", "StockBidYiDong", "w43"),
    ("GetHotPianLi", "StockBidYiDong", "w44"),
    ("GetPianLiZhi_New", "StockBidYiDong", "w44"),
    ("GetYiDongHot", "StockBidYiDong", "w44"),
]

for host in HOSTS:
    for a, c, apiv in candidates:
        d = call(host, {"a": a, "c": c, "apiv": apiv})
        if "__err__" in d:
            print(f"[{host}] a={a} c={c} ERR {d['__err__'][:60]}")
            continue
        lst = d.get("List") or d.get("info") or d.get("list") or []
        sample = ""
        if lst:
            it = lst[0]
            sample = json.dumps(it, ensure_ascii=False)[:300]
        print(f"[{host}] a={a} c={c} apiv={apiv} keys={list(d.keys())} n={len(lst) if isinstance(lst,list) else 'n/a'} first={sample}")
    print("---")