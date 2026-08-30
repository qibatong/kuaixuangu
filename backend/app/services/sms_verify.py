# -*- coding: utf-8 -*-
"""
阿里云短信验证码服务 (号码认证·短信认证, 2026-08-30 个人开发者免资质接入)
=====================================================================================
使用阿里云「短信认证」: 个人实名即可, 系统赠送签名+标准验证码模板(免申请签名/模板)。
  - SendSmsVerifyCode  发送: 系统自动生成 4-6 位验证码, 服务端闭环校验(不本地存码)
  - CheckSmsVerifyCode 校验: 返回 true/false(带 5 分钟有效期, 接口内置)

配置(走 systemd drop-in / 环境变量, 不进 git):
  ALIYUN_AK_ID / ALIYUN_AK_SECRET   RAM 子账号 AccessKey(建议只授权 dypns)
  SMS_SIGN_NAME / SMS_TEMPLATE_CODE  控制台系统赠送签名名 / 验证码模板编号

依赖: pip install alibabacloud_dypnsapi20170525 (未安装时 send/check 返回配置缺失提示)
限制: 仅中国大陆手机号(86)
"""
import os
import time

from ..core import config, logger

log = logger.get_logger(__name__)


class SmsNotConfigured(Exception):
    """AccessKey / 签名 / 模板未配置"""


def _client():
    """懒加载 SDK client; 未安装 SDK 或未配置 AK 时抛异常"""
    ak_id = os.environ.get("ALIYUN_AK_ID", "").strip()
    ak_secret = os.environ.get("ALIYUN_AK_SECRET", "").strip()
    sign = config.SMS_SIGN_NAME
    tpl = config.SMS_TEMPLATE_CODE
    if not (ak_id and ak_secret and sign and tpl):
        raise SmsNotConfigured("短信配置缺失(ALIYUN_AK_ID/SECRET/SMS_SIGN_NAME/SMS_TEMPLATE_CODE)")
    try:
        from alibabacloud_dypnsapi20170525.client import Client
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError:
        raise SmsNotConfigured("未安装 alibabacloud_dypnsapi20170525, 请 pip install")
    cfg = open_api_models.Config(access_key_id=ak_id, access_key_secret=ak_secret)
    cfg.endpoint = "dypnsapi.aliyuncs.com"
    return Client(cfg)


def send_code(phone, scene="", code=None, interval=60, valid_time=5, out_id=""):
    """发送验证码到手机号.
    - code: None → 系统自动生成(推荐, CheckSmsVerifyCode 可校验闭环);
            传自定义 4-6 位数字 → CheckSmsVerifyCode 不校验, 需自行存库比对
    - interval: 同号码重发最小间隔(秒, 默认 60)
    - valid_time: 验证码有效期(分钟, 默认 5)
    返回 (ok, msg); 失败不计费"""
    client = _client()
    from alibabacloud_dypnsapi20170525 import models as m
    req = m.SendSmsVerifyCodeRequest(
        phone_number=phone,
        sign_name=config.SMS_SIGN_NAME,
        template_code=config.SMS_TEMPLATE_CODE,
        country_code="86",
        interval=interval,
        valid_time=valid_time,
        code_type=1,
        duplicate_check=True,          # 同号同码不重复发送
        early_warn=True,
        scheme_name=scene,
        out_id=out_id or scene or phone,
    )
    if code is not None:
        req.code = str(code)
        req.return_verify_code = True
    resp = client.send_sms_verify_code_with_options(req, None)
    body = getattr(resp, "body", None)
    ok = bool(body and body.code == "OK")
    msg = (body and body.message) or "未知"
    if not ok:
        log.warning("短信发送失败 phone=%s scene=%s msg=%s", phone, scene, msg)
    return ok, msg


def check_code(phone, code):
    """校验验证码(系统生成码, 服务端闭环). 返回 (ok, msg)"""
    client = _client()
    from alibabacloud_dypnsapi20170525 import models as m
    req = m.CheckSmsVerifyCodeRequest(
        phone_number=phone,
        verify_code=str(code),
        country_code="86",
        case_auth_policy=0,
    )
    resp = client.check_sms_verify_code_with_options(req, None)
    body = getattr(resp, "body", None)
    if not body:
        return False, "校验失败(空响应)"
    return bool(body.code == "OK" and body.verify_result), body.message or ""


# ---------- 限流辅助(复用 CacheStore 跨进程) ----------
def can_send(phone, ip, interval=60):
    """发送前限流: 同手机号 interval 秒 1 次 + 同 IP 60s 10 次.
    返回 (allowed, reason)"""
    from .cache_store import store
    p = store.incr("sms:phone:%s" % phone, ttl=interval)
    if p > 1:
        return False, "发送过于频繁, 请 %d 秒后再试" % interval
    ip_n = store.incr("sms:ip:%s" % ip, ttl=60)
    if ip_n > 10:
        return False, "当前网络发送次数过多, 请稍后再试"
    return True, ""
