import sys, os
os.chdir('/opt/kuaixuan/backend')
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.api import kpl as kplapi
from app.services import fetcher

lst, d = kplapi._read_auction_fast("seal")
d2 = d
print("seal date:", d2, "count:", len(lst))

def real_close(code):
    try:
        k = fetcher.fetch_stock_chart_robust(code, "day")
        if k and k.get("time"):
            times, closes = k["time"], k["close"]
            for i, t in enumerate(times):
                if str(t)[:10] == d2:
                    if i > 0 and closes[i-1]:
                        return round((closes[i]-closes[i-1])/closes[i-1]*100, 2)
    except Exception:
        pass
    return None

print("--- 校验前15只: 后端realChange vs 东财真实收盘涨幅 vs bidChange ---")
mismatch = []
for it in lst[:15]:
    code = it['code']; rc = it.get('realChange'); bc = it.get('bidChange')
    real = real_close(code)
    flag = ""
    if rc is None or real is None or abs(rc - real) > 0.1:
        flag = "  <<<< MISMATCH"
        mismatch.append(code)
    print(f"  {code} {it.get('name')} | back_realChange={rc} | eastmoney_close={real} | bidChange={bc}{flag}")
print("前15只 mismatched:", mismatch)