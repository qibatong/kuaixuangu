#!/bin/bash
# v4.11.27 真实时刻闸门探针 —— **只读观察 + 一次 filter 请求**(会走选股链路, 测试机专用)
#
# 用途: 在**具体时刻**主动验一次闸门是否按新口径放行/拦截。
#       预检 P3 是用"注入时刻"验纯函数; 本脚本是"真实时刻 + 真实 HTTP", 补最后一环。
#
# 建议时刻:
#   09:05  期望  is_pick_open=False + HTTP blocked=true msg='9:15 后开放 …'
#   09:18  期望  is_pick_open=True  + HTTP ok=true 且**无 blocked**(← 本次核心)
#   09:25:20 期望 is_pick_open=False + HTTP blocked=true(当日 9_25 未落库)
#   09:28  期望  is_pick_open=True  + HTTP ok=true(名单应为当日)
set -u
APP="${KX_APP:-/opt/kuaixuan}"
BE="$APP/backend"
if [ -x "${KX_VENV:-/opt/kuaixuan-venv/bin/python}" ]; then PY="${KX_VENV:-/opt/kuaixuan-venv/bin/python}"
elif [ -x /opt/bid-venv/bin/python ]; then PY=/opt/bid-venv/bin/python
else PY=$(command -v python3); fi

date '+现在: %Y-%m-%d %H:%M:%S %A'
echo "--- 口径(真实时刻) ---"
"$PY" -c "
import sys
sys.path.insert(0, '$BE')
from app.services.picker import mode as pm
from app.services import settings as st
print('  is_pick_open()   =', pm.is_pick_open())
print('  pick_resume_at() =', repr(pm.pick_resume_at()))
print('  pick_window_guard=', repr(st.get('pick_window_guard')))
"

echo "--- ping(开关透出) ---"
"$PY" /tmp/_gen_test_token.py > /tmp/_tok_live.txt 2>&1
T=$(sed -n 's/^TOKEN=//p' /tmp/_tok_live.txt | tail -1)
if [ -z "$T" ]; then echo "  FAIL 未能签发 token"; sed 's/^/    /' /tmp/_tok_live.txt; rm -f /tmp/_tok_live.txt; exit 1; fi
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:8010/api/stocks?action=ping"
echo

echo "--- filter(真实闸门结果, 只看前 400 字节) ---"
curl -s -H "Authorization: Bearer $T" \
     "http://127.0.0.1:8010/api/stocks?action=filter&markets=sh_sz" | head -c 400
echo

echo "--- 服务/日志 ---"
for s in kuaixuan kx-worker; do printf '  %-12s %s\n' "$s" "$(systemctl is-active $s 2>&1)"; done
printf '  app.log Traceback 计数: %s\n' "$(grep -c Traceback "$APP/logs/app.log" 2>/dev/null)"
rm -f /tmp/_tok_live.txt
echo "--- 探针结束 ---"
