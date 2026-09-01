#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
线程守护 (thread_guard) —— 2026-09-01 生产线程爆炸事故的兜底监控
==============================================================
每分钟(systemd timer)检查 kuaixuan / kx-worker 全部相关进程线程数
(uvicorn 主进程+worker 子进程), 超过阈值时告警到飞书
(复用 NOTIFY_FEISHU_WEBHOOK / NOTIFY_FEISHU_SECRET 配置,
支持加签, 与 app/services/notify.py 同一实现)。

短信告警(2026-09-01 追加): 告警时同时发短信到 ALERT_PHONE
(默认 18883856602, 可用环境变量 THREAD_GUARD_PHONE 覆盖)。
通道复用阿里云「号码认证·短信认证」(dypnsapi20170525), 与 app/services/
sms_verify.py 相同实现——注意该产品模板固定为「验证码」格式, 短信内容
不可自定义, 仅作为「服务器异常」的强提醒(详情看飞书消息)。若需自定义
内容的正式告警短信, 需另接入云短信(dysmsapi)申请签名+模板。

- 去抖: 连续 CONSECUTIVE 次超阈值才告警, 避免瞬时抖动误报
- 冷却: 告警后 COOLDOWN 秒内不重复告警
- 服务 down 也会告警(pgrep 无匹配进程)
- 状态/日志: /var/log/kuaixuan/

用法:
    python3 thread_guard.py            # 检查一次(正常打印 ok 行)
    python3 thread_guard.py --force    # 强制告警一次(验证飞书+短信通道用)

systemd 部署(两端服务器):
    /etc/systemd/system/kx-thread-guard.service
    /etc/systemd/system/kx-thread-guard.timer  (OnCalendar=*:0/1 每分钟)
"""
import base64
import hashlib
import hmac
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request

THREAD_LIMIT = 150      # 单进程线程数阈值(正常 ~13, 事故 2000+)
TOTAL_LIMIT = 300       # 全部服务线程总数阈值
CONSECUTIVE = 2         # 连续 N 次超阈值才告警(去抖)
COOLDOWN = 600          # 告警冷却(秒)
STATE_FILE = "/var/log/kuaixuan/thread_guard_state.json"
ENV_CONF = "/etc/kuaixuan/env.conf"
CA_FILE = "/etc/pki/tls/certs/ca-bundle.crt"   # CentOS 7 标准 CA bundle

# ---------- 短信告警配置 ----------
ALERT_PHONE = os.environ.get("THREAD_GUARD_PHONE", "18883856602")  # 告警接收手机号
SMS_CONF = "/etc/systemd/system/kuaixuan.service.d/sms.conf"       # AK/签名/模板(drop-in)
SMS_ENDPOINT = "dypnsapi.aliyuncs.com"


def make_ctx():
    """自编译 Python 缺 CA bundle(SSL_CERT_FILE 未配置), 用系统 CA 文件建 context"""
    cafile = os.environ.get("SSL_CERT_FILE") or CA_FILE
    if cafile and os.path.exists(cafile):
        try:
            return ssl.create_default_context(cafile=cafile)
        except Exception:
            pass
    return ssl._create_unverified_context()


def read_env_conf():
    """从 env.conf 读取飞书 webhook / secret"""
    webhook = secret = ""
    try:
        with open(ENV_CONF, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("NOTIFY_FEISHU_WEBHOOK="):
                    webhook = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("NOTIFY_FEISHU_SECRET="):
                    secret = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return webhook, secret


def read_sms_conf():
    """从 kuaixuan.service.d/sms.conf 读取阿里云 AK/签名/模板(drop-in 环境变量)"""
    env = {}
    try:
        with open(SMS_CONF, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("Environment="):
                    continue
                kv = line[len("Environment="):]
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def send_sms_alert(phone):
    """短信告警: 复用阿里云号码认证·短信认证(dypnsapi20170525)发验证码短信.

    注意: 该产品模板固定为验证码格式, 内容不可自定义; 仅作强提醒,
    异常详情以飞书消息为准。返回 (ok, msg); 失败不影响飞书告警。
    """
    env = read_sms_conf()
    ak_id = env.get("ALIYUN_AK_ID", "")
    ak_secret = env.get("ALIYUN_AK_SECRET", "")
    sign = env.get("SMS_SIGN_NAME", "恒创联众")
    tpl = env.get("SMS_TEMPLATE_CODE", "100001")
    if not (ak_id and ak_secret):
        return False, "sms.conf 缺 ALIYUN_AK_ID/SECRET"
    try:
        from alibabacloud_dypnsapi20170525.client import Client
        from alibabacloud_dypnsapi20170525 import models as m
        from alibabacloud_tea_openapi import models as open_api_models
        from alibabacloud_tea_util import models as tea_util_models
    except ImportError:
        return False, "未安装 alibabacloud_dypnsapi20170525"

    cfg = open_api_models.Config(access_key_id=ak_id, access_key_secret=ak_secret)
    cfg.endpoint = SMS_ENDPOINT
    client = Client(cfg)
    req = m.SendSmsVerifyCodeRequest(
        phone_number=phone,
        sign_name=sign,
        template_code=tpl,
        template_param=json.dumps({"code": "##code##", "min": "5"},
                                  ensure_ascii=False),
        country_code="86",
        interval=60,
        valid_time=300,
        code_type=1,
        duplicate_policy=1,
        scheme_name="thread_guard",
        out_id="thread_guard",
    )
    resp = client.send_sms_verify_code_with_options(req, tea_util_models.RuntimeOptions())
    body = getattr(resp, "body", None)
    ok = bool(body and body.code == "OK")
    msg = (body and body.message) or "未知"
    return ok, msg


def service_threads():
    """返回 ({服务名: 线程信息}, 总线程, 挂掉的服务列表)

    注意: uvicorn 主进程只有 1 线程, 真正干活的 worker 是子进程(事故时
    2 worker × 2082 线程), 且 worker 子进程 cmdline 是 spawn_main(不含
    "uvicorn app.main"), pgrep 匹配不到 → 必须用 systemd cgroup.procs
    拿该服务的全部进程 PID 再汇总。这是唯一不漏的精确方式。
    线程信息格式: "总线程(worst单进程)"
    """
    out, down = {}, []

    def _sum(svc):
        cg = "/sys/fs/cgroup/systemd/system.slice/%s.service/cgroup.procs" % svc
        total, worst, nproc = 0, 0, 0
        try:
            with open(cg, "r") as f:
                pids = [l.strip() for l in f if l.strip().isdigit()]
            for pid in pids:
                rr = subprocess.run(["grep", "Threads", "/proc/%s/status" % pid],
                                    capture_output=True, text=True, timeout=15)
                parts = rr.stdout.strip().split()
                if len(parts) >= 2 and parts[1].isdigit():
                    n = int(parts[1])
                    total += n
                    worst = max(worst, n)
                    nproc += 1
        except Exception:
            pass
        return total, worst, nproc

    for svc in ("kuaixuan", "kx-worker"):
        total, worst, nproc = _sum(svc)
        if nproc == 0:
            down.append(svc)
        else:
            out[svc] = "%d(worst%d)" % (total, worst) if nproc > 1 else str(total)
    return out, sum(int(v.split("(")[0]) for v in out.values()), down


def feishu_sign(timestamp, secret):
    """飞书加签: string_to_sign = '{ts}\\n{secret}', hmac key 为拼接串、msg 为空, base64"""
    string_to_sign = "%s\n%s" % (timestamp, secret)
    hmac_code = hmac.new(string_to_sign.encode("utf-8"),
                         digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_feishu(text, webhook, secret):
    payload = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(round(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = feishu_sign(ts, secret)
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=make_ctx()) as resp:
            return resp.status
    except Exception:
        return -1


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"streak": 0, "last_alert": 0}


def save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def main():
    force = "--force" in sys.argv
    st = load_state()
    per_svc, total, down = service_threads()

    def _svc_num(v):
        return int(str(v).split("(")[0])

    over = [s for s, v in per_svc.items() if _svc_num(v) > THREAD_LIMIT]
    abnormal = bool(over) or total > TOTAL_LIMIT or bool(down)

    st["streak"] = st.get("streak", 0) + 1 if abnormal else 0
    save_state(st)

    now = time.time()
    cooldown_ok = (now - st.get("last_alert", 0)) > COOLDOWN
    should_alert = force or (abnormal and st["streak"] >= CONSECUTIVE and cooldown_ok)

    detail = "  ".join("%s=%s" % (k, v) for k, v in sorted(per_svc.items()))
    if down:
        detail += "  服务挂了:%s" % ",".join(down)

    if should_alert:
        webhook, secret = read_env_conf()
        lines = [
            "🚨【快选线程守护】服务异常",
            "状态: %s (连续第%d次)" % ("超阈值" if abnormal else "手动测试", st["streak"]),
            "线程: %s  总=%d" % (detail, total),
            "阈值: 单进程>%d 或 总>%d" % (THREAD_LIMIT, TOTAL_LIMIT),
            "时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.gmtime(time.time() + 8 * 3600)),
        ]
        if webhook:
            code = send_feishu("\n".join(lines), webhook, secret)
            print("ALERT sent code=%s %s" % (code, detail))
            st["last_alert"] = now
            save_state(st)
        else:
            print("WARN: 未配置 NOTIFY_FEISHU_WEBHOOK, 告警未发送 %s" % detail)

        # 短信强提醒(验证码通道, 详情看飞书)
        ok, msg = send_sms_alert(ALERT_PHONE)
        print("SMS sent ok=%s %s" % (ok, msg))
    else:
        print("ok threads=%d %s" % (total, detail))


if __name__ == "__main__":
    main()
