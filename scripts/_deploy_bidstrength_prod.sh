#!/bin/bash
# 生产部署: 17% 异动因子改回东财 f630 (2026-09-17)
# 改动 = picker/pipeline.py 的 _load_strength 补开关短路 + settings use_bid_strength=0
# 语义: 备份 -> 落盘 -> 校验 -> 只读预检 -> 重启 -> 设开关 -> 验证
set -u
STAMP=$(date +%Y%m%d-%H%M%S)
BK=/opt/kuaixuan/backend_bak_bidstrength_$STAMP
BE=/opt/kuaixuan/backend
F=app/services/picker/pipeline.py

echo "############ 0) 备份 ############"
mkdir -p $BK/app/services/picker
cp -p $BE/$F $BK/app/services/picker/pipeline.py
md5sum $BE/$F > $BK/MANIFEST.md5
echo "  备份 -> $BK"
echo "  旧 md5 = $(cut -d' ' -f1 $BK/MANIFEST.md5)"

echo "############ 1) 落盘 ############"
cp -f /tmp/bs_pipeline.py $BE/$F
NEW=$(md5sum $BE/$F | cut -d' ' -f1)
echo "  新 md5 = $NEW"

echo "############ 2) 语法检查 ############"
cd $BE
if ! /opt/kuaixuan-venv/bin/python -m py_compile $F; then
  echo "  ✗ py_compile 失败 -> 立即回滚"
  cp -f $BK/app/services/picker/pipeline.py $BE/$F
  exit 1
fi
echo "  ✓ py_compile OK"

echo "############ 3) 只读预检(不重启) ############"
PYTHONPATH=$BE /opt/kuaixuan-venv/bin/python - <<'PY'
import types
from app.services import bid_strength as bs
from app.services.picker import pipeline as pl

orig = bs.enabled
ctx = types.SimpleNamespace(strengths={}, date=None)

# (a) 开关关闭 -> _load_strength 必须短路返回 {}
bs.enabled = lambda: False
out = pl._load_strength(["600000", "000001"], ctx)
a = (out == {})
print("  (a) 开关关闭  -> _load_strength = %r  期望 {}  -> %s" % (out, "PASS" if a else "FAIL"))

# (b) 开关打开 -> 不短路(空输入, 不触网, 只验证未提前 return)
bs.enabled = lambda: True
try:
    pl._load_strength([], ctx)
    b = True
except Exception as e:
    b = False
    print("     开关打开时异常: %s" % e)
print("  (b) 开关打开  -> 未短路 -> %s" % ("PASS" if b else "FAIL"))

# (c) 注入优先
bs.enabled = lambda: False
inj = pl._load_strength(["600000"], types.SimpleNamespace(strengths={"600000": 0.9}, date=None))
c = (inj == {"600000": 0.9})
print("  (c) 显式注入优先 -> %r -> %s" % (inj, "PASS" if c else "FAIL"))

bs.enabled = orig
print("  PREFLIGHT = %s" % ("ALL PASS" if (a and b and c) else "FAIL"))
raise SystemExit(0 if (a and b and c) else 1)
PY
if [ $? -ne 0 ]; then
  echo "  ✗ 预检失败 -> 立即回滚(未重启, 零用户影响)"
  cp -f $BK/app/services/picker/pipeline.py $BE/$F
  exit 1
fi

echo "############ 4) 重启 ############"
systemctl restart kuaixuan kx-worker
sleep 6
for u in kuaixuan kx-worker; do
  printf "  %-12s %-8s MainPID=%s\n" "$u" "$(systemctl is-active $u)" "$(systemctl show $u -p MainPID --value)"
done

echo "############ 5) 设开关 use_bid_strength=0 ############"
PYTHONPATH=$BE /opt/kuaixuan-venv/bin/python - <<'PY'
from app.services import settings
from app.services import bid_strength as bs
settings.set('use_bid_strength', '0')
print("  写入后 settings.get('use_bid_strength') = %r" % (settings.get('use_bid_strength'),))
print("  bid_strength.enabled() = %r" % (bs.enabled(),))
print("  (期望: '0' / False)")
PY

echo "############ 6) 运行态验证 ############"
echo -n "  首页 http: "; curl -sL -k -o /dev/null -w '%{http_code}\n' --max-time 10 http://127.0.0.1/
echo "  --- 最近日志 ---"
tail -6 /opt/kuaixuan/logs/app.log
echo "############ DONE (bak=$BK) ############"
