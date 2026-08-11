# -*- coding: utf-8 -*-
"""
推送提醒服务: 竞价选股结果 → 微信(Server酱/企业微信) / 飞书
===========================================================
- 纯标准库 urllib 实现, 零第三方依赖
- 任一渠道失败不影响其他渠道与选股主流程(仅记日志)
- 相同内容在去重窗口内不重复推送(防手动多次 lock 刷屏)
- 全部渠道未配置时推送自动跳过, 返回 {}

环境变量(见 core/config.py):
    NOTIFY_FEISHU_WEBHOOK    飞书群机器人 webhook
    NOTIFY_SERVERCHAN_KEY    Server酱 SendKey(推个人微信)
    NOTIFY_WECHAT_WEBHOOK    企业微信群机器人 webhook
"""
import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
import urllib.request

from ..core import config, logger

log = logger.get_logger(__name__)

# content md5 -> 最近推送时间戳, 用于去重
_last_push = {}
_dedup_lock = threading.Lock()

_ICONS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def bj_date_str():
    """北京时间 'MM-DD HH:MM' 字符串(消息头用)"""
    g = time.gmtime(time.time() + 8 * 3600)
    return "%02d-%02d %02d:%02d" % (g.tm_mon, g.tm_mday, g.tm_hour, g.tm_min)


def build_message(result, filters=None, limit=None):
    """把选股结果构造成统一推送文本(展示 Top N, 默认 NOTIFY_TOP_N)"""
    limit = limit or config.NOTIFY_TOP_N
    top = (result or [])[:limit]
    markets = "、".join((filters or {}).get("markets", [])) or "沪深A股"
    head = "【快选 · 竞价选股 %s】" % bj_date_str()
    sub = "%s · 入选 %d 只" % (markets, len(result or []))
    if result and len(result) > len(top):
        sub += "（展示 Top%d）" % len(top)
    lines = [head, sub]
    if not top:
        lines.append("今日无符合策略的标的。")
        return "\n".join(lines)

    for i, s in enumerate(top):
        icon = _ICONS[i] if i < len(_ICONS) else "%d." % (i + 1)
        name = "%s %s" % (s.get("code", ""), s.get("name", ""))
        lines.append("%s %s  胜率%.1f%%" % (icon, name.strip(), s.get("probability", 0) or 0))
        extra = []
        bc = s.get("bidChange")
        if bc is not None:
            extra.append("竞价%+.2f%%" % bc)
        if s.get("bidAmt"):
            extra.append("竞价额%.0f万" % s["bidAmt"])
        if s.get("circulationMV"):
            extra.append("流通%.0f亿" % s["circulationMV"])
        if s.get("industry") and s["industry"] != "-":
            extra.append(s["industry"])
        if extra:
            lines.append("   " + "  ".join(extra))
    return "\n".join(lines)


def _dedup(content, now=None):
    """相同内容在去重窗口内返回 True(应跳过)"""
    now = now or time.time()
    h = hashlib.md5(content.encode("utf-8")).hexdigest()
    with _dedup_lock:
        last = _last_push.get(h, 0)
        if now - last < _dedup_window(now):
            return True
        _last_push[h] = now
    return False


def _dedup_window(now=None):
    """去重窗口(秒): 竞价时段(9:25-9:31 北京时间)自动放大到覆盖整个竞价时段,
    防止 9:25-9:30 多用户反复 lock 刷屏; 其他时段用配置默认窗口。"""
    now = now if now is not None else time.time()
    g = time.gmtime(now + 8 * 3600)
    hm = g.tm_hour * 60 + g.tm_min
    if 9 * 60 + 25 <= hm <= 9 * 60 + 31:
        remain = (9 * 60 + 31 - hm) * 60   # 距 9:31 剩余秒数
        return max(remain, config.NOTIFY_DEDUP_SECONDS)
    return config.NOTIFY_DEDUP_SECONDS


def _dedup_key(result, filters=None):
    """稳定去重键: 市场 + TopN 标的(代码|竞价涨幅1位小数|概率取整)。
    不含时间戳/实时易变尾巴 → 同一竞价窗口内同批标的只推一次;
    筛选范围不同(市场不同)或结果显著变化(涨幅/概率明显变)才视为新内容。"""
    markets = "、".join((filters or {}).get("markets", [])) or "沪深A股"
    top = (result or [])[:config.NOTIFY_TOP_N]
    items = []
    for s in top:
        try:
            bc = round(float(s.get("bidChange") or 0), 1)
        except (TypeError, ValueError):
            bc = 0.0
        try:
            prob = int(round(float(s.get("probability") or 0)))
        except (TypeError, ValueError):
            prob = 0
        items.append("%s|%.1f|%d" % (s.get("code", ""), bc, prob))
    return "%s#%s" % (markets, ";".join(items))


# ---------- 底层发送(可被测试 monkeypatch) ----------
def _post_json(url, payload, timeout=None):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout or config.NOTIFY_TIMEOUT) as resp:
        return resp.status, resp.read().decode("utf-8", "ignore")


def _post_form(url, data, timeout=None):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout or config.NOTIFY_TIMEOUT) as resp:
        return resp.status, resp.read().decode("utf-8", "ignore")


# ---------- 渠道实现 ----------
def _resp_ok(ok_flag, body):
    """渠道返回体里带业务码时二次校验: 飞书 errcode / Server酱 code / 企微 errcode"""
    if not ok_flag:
        return False, body[:200]
    try:
        d = json.loads(body)
        code = d.get("errcode", d.get("code"))
        if code not in (0, None):
            return False, body[:200]
    except Exception:
        pass
    return True, body[:200]


def _feishu_sign(timestamp, secret):
    """飞书机器人加签(安全设置选"签名校验"时必需)。

    严格照抄飞书官方示例: string_to_sign = "{timestamp}\\n{secret}",
    hmac 的 key 是拼接串本身、msg 为空, 再 base64。
    """
    string_to_sign = "%s\n%s" % (timestamp, secret)
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def _send_feishu(text):
    url = config.NOTIFY_FEISHU_WEBHOOK
    payload = {"msg_type": "text", "content": {"text": text}}
    if config.NOTIFY_FEISHU_SECRET:
        ts = str(round(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _feishu_sign(ts, config.NOTIFY_FEISHU_SECRET)
    status, body = _post_json(url, payload)
    return _resp_ok(200 <= status < 300, body)


def _send_serverchan(text):
    url = "https://sctapi.ftqq.com/%s.send" % config.NOTIFY_SERVERCHAN_KEY
    data = {"title": text.splitlines()[0][:32], "desp": text}
    status, body = _post_form(url, data)
    return _resp_ok(200 <= status < 300, body)


def _send_wecom(text):
    url = config.NOTIFY_WECHAT_WEBHOOK
    payload = {"msgtype": "text", "text": {"content": text}}
    status, body = _post_json(url, payload)
    return _resp_ok(200 <= status < 300, body)


def push_result(result, filters=None):
    """同步推送选股结果到所有已配置渠道。

    返回 {channel: {"ok": bool, "msg": str}}; 全部未配置/内容去重时返回 {}。
    任何渠道异常都会被捕获, 不会向上抛。
    """
    text = build_message(result, filters)
    if _dedup(_dedup_key(result, filters)):
        log.info("推送去重: 同批标的窗口内跳过")
        return {}

    channels = []
    if config.NOTIFY_FEISHU_WEBHOOK:
        channels.append(("feishu", _send_feishu))
    if config.NOTIFY_SERVERCHAN_KEY:
        channels.append(("serverchan", _send_serverchan))
    if config.NOTIFY_WECHAT_WEBHOOK:
        channels.append(("wecom", _send_wecom))
    if not channels:
        return {}

    out = {}
    for name, fn in channels:
        try:
            ok, msg = fn(text)
            out[name] = {"ok": ok, "msg": msg}
            if ok:
                log.info("推送成功 channel=%s", name)
            else:
                log.warning("推送失败 channel=%s resp=%s", name, msg)
        except Exception as e:
            out[name] = {"ok": False, "msg": str(e)[:200]}
            log.error("推送异常 channel=%s err=%s", name, e)
    return out


def push_result_async(result, filters=None):
    """后台线程推送, 不阻塞选股接口响应"""
    t = threading.Thread(target=push_result, args=(result, filters), daemon=True)
    t.start()
