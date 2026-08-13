#!/bin/bash
# ============================================================
# 快选·测试机浏览器回归: 服务启动后自动执行, 失败推飞书告警
# 由 systemd ExecStartPost 触发; 账号密码走 KX_REG_USER/KX_REG_PWD 环境变量
# 日志: /opt/kuaixuan/logs/browser_reg.log  截图: /opt/kuaixuan/logs/browser_reg/
# ============================================================
LOG=/opt/kuaixuan/logs/browser_reg.log
SHOT=/opt/kuaixuan/logs/browser_reg
PY=/usr/local/python311/bin/python3

echo "[$(date '+%F %T')] ========== 浏览器回归开始 ==========" >> "$LOG"
# 等待服务完全就绪(uvicorn 启动 + 首次数据预热)
sleep 20

"$PY" /opt/kuaixuan/scripts/browser_reg.py \
  --user "${KX_REG_USER}" --pwd "${KX_REG_PWD}" --shot-dir "$SHOT" >> "$LOG" 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
  echo "[$(date '+%F %T')] OK 浏览器回归通过" >> "$LOG"
else
  echo "[$(date '+%F %T')] FAIL 浏览器回归失败 rc=$RC" >> "$LOG"
  TAIL=$(tail -n 10 "$LOG" | tr '\n' ';')
  "$PY" - "$TAIL" <<'PY' >> "$LOG" 2>&1
import sys
sys.path.insert(0, "/opt/kuaixuan")
try:
    from backend.app.services.notify import send_text
    tail = sys.argv[1] if len(sys.argv) > 1 else ""
    r = send_text("快选测试机浏览器回归失败, 请查看日志\n日志尾部: %s\n详见 /opt/kuaixuan/logs/browser_reg.log" % tail[:500])
    print("notify:", r)
except Exception as e:
    print("notify fail:", e)
PY
fi

echo "[$(date '+%F %T')] ========== 浏览器回归结束 ==========" >> "$LOG"
# 始终返回 0: 回归失败已告警, 不影响 systemd 服务状态
exit 0
