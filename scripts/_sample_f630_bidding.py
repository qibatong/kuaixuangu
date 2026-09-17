# -*- coding: utf-8 -*-
"""9:14-9:27 采样东财 f630(异动等级) 在**竞价时段**的真实取值域。

为什么必须采:
  主人要求把评分 17% 权重的「异动」因子改回东财 f630。但盘前实测(08:35)取值域是
  0~14, 而评分配置的分档表 [["5","6",1],["4","5",0.85],["3","4",0.6]] 只覆盖 3/4/5
  → 命中率仅 36%, 其余全落 default 0.18。而 contract.py 注释记的是"竞价时段实测
  取值 0/1/2" —— 若真是 0/1/2, 命中率就是 0%, 那 17% 权重对排序完全失效。
  两种说法必须用竞价时段的实测来裁决, 时间点就是 9:25 定格那一刻。

纯只读: 只发 GET clist; 不写库、不改文件、不重启。每分钟 3 个请求(9:23 后每 10 秒),
对生产正常采集(数十请求/轮)而言是噪音级。
"""
import collections
import json
import sys
import time

sys.path.insert(0, "/opt/kuaixuan/backend")
from app.services import fetcher            # noqa: E402

SECTORS = [
    ("sz", "m:0+t:6,m:0+t:80"),
    ("sh", "m:1+t:2,m:1+t:23"),
    ("bj", "m:0+t:81+s:2048"),
]
OUT = "/tmp/_f630_samples.jsonl"
START = "09:14:00"       # 竞价开始前 1 分钟
DENSE_FROM = "09:23:00"  # 9:23 起加密(定格前最后 2 分钟)
STOP = "09:27:00"


def _sec(hhmmss):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return h * 3600 + m * 60 + s


def _now_sec():
    t = time.localtime()
    return t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec


def sample():
    agg = collections.Counter()
    per = {}
    hits = []                                  # f630 ∈ {3,4,5} 的样例(分档表能命中的)
    for name, fs in SECTORS:
        try:
            d = fetcher._fetch_clist_page(fs, 1, "f12")
        except Exception as e:                 # noqa: BLE001
            per[name] = "ERR:%s" % e
            continue
        c = collections.Counter(str(x.get("f630")) for x in d)
        per[name] = dict(c)
        agg.update(c)
        for x in d:
            if str(x.get("f630")) in ("3", "4", "5") and len(hits) < 5:
                hits.append("%s %s f630=%s f615=%s" % (
                    x.get("f12"), x.get("f14"), x.get("f630"), x.get("f615")))
    return {"ts": time.strftime("%H:%M:%S"), "agg": dict(agg), "per": per, "hits": hits}


def main():
    f = open(OUT, "a", buffering=1)
    f.write(json.dumps({"_start": time.strftime("%F %T")}, ensure_ascii=False) + "\n")
    n_samp = 0
    while True:
        n = _now_sec()
        if n >= _sec(STOP):
            break
        if n < _sec(START):
            time.sleep(5)
            continue
        gap = 10 if n >= _sec(DENSE_FROM) else 60
        rec = sample()
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(json.dumps(rec, ensure_ascii=False))
        n_samp += 1
        time.sleep(gap)
    f.write(json.dumps({"_end": time.strftime("%F %T"), "_samples": n_samp},
                       ensure_ascii=False) + "\n")
    f.close()
    print("SAMPLE DONE n=%d" % n_samp)


if __name__ == "__main__":
    main()
