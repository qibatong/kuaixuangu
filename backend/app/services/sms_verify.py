# -*- coding: utf-8 -*-
"""
阿里云短信验证码服务 (号码认证·短信认证, 2026-08-30 个人开发者免资质接入)
=====================================================================================
使用阿里云「短信认证」: 个人实名即可, 系统赠送签名+标准验证码模板(免申请签名/模板)。
  - SendSmsVerifyCode  发送: TemplateParam 用 "##code##" 占位, 系统自动生成验证码,
                           服务端闭环校验(不本地存码)
  - CheckSmsVerifyCode 校验: Model.VerifyResult == "PASS" 即通过(有效期接口内置)

配置(走 systemd drop-in / 环境变量, AK 不进 git):
  ALIYUN_AK_ID / ALIYUN_AK_SECRET   RAM 子账号 AccessKey(需授权 AliyunDypnsFullAccess)
  SMS_SIGN_NAME / SMS_TEMPLATE_CODE  控制台赠送签名名(恒创联众) / 赠送模板编号(100001)

依赖: pip install alibabacloud_dypnsapi20170525 (未安装时 send/check 返回配置缺失提示)
限制: 仅中国大陆手机号(86)
"""
import json
import os

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


def _runtime():
    """SDK 2.0 需要显式 RuntimeOptions(传 None 会报错)"""
    from alibabacloud_tea_util import models as tea_util_models
    return tea_util_models.RuntimeOptions()


def send_code(phone, scene="", code=None, interval=60, valid_time=5, out_id=""):
    """发送验证码到手机号.
    - code: None → 模板参数用 "##code##" 占位, 系统自动生成(推荐, 阿里云可闭环校验);
            传自定义 4-6 位数字 → 直接下发该值, 阿里云无法校验, 需自行存库比对
    - interval: 同号码重发最小间隔(秒, 默认 60)
    - valid_time: 验证码有效期(分钟, 默认 5; SDK 需转成秒)
    返回 (ok, msg); 失败不计费"""
    client = _client()
    from alibabacloud_dypnsapi20170525 import models as m
    # TemplateParam 必填: 系统生成用 ##code## 占位; 自定义 code 直接传值
    tpl_param = {"code": "##code##" if code is None else str(code),
                 "min": str(valid_time)}
    req = m.SendSmsVerifyCodeRequest(
        phone_number=phone,
        sign_name=config.SMS_SIGN_NAME,
        template_code=config.SMS_TEMPLATE_CODE,
        template_param=json.dumps(tpl_param, ensure_ascii=False),
        country_code="86",
        interval=interval,
        valid_time=valid_time * 60,      # 单位: 秒(接口默认 300)
        code_type=1,                      # 1=纯数字验证码
        duplicate_policy=1,               # 同场景同号重复发送时: 1=覆盖旧码(默认)
        scheme_name=scene or None,        # 空则用「默认方案」
        out_id=out_id or scene or phone,
        return_verify_code=(code is not None),  # 自定义 code 时回显以便自查
    )
    resp = client.send_sms_verify_code_with_options(req, _runtime())
    body = getattr(resp, "body", None)
    ok = bool(body and body.code == "OK")
    msg = (body and body.message) or "未知"
    if not ok:
        log.warning("短信发送失败 phone=%s scene=%s msg=%s", phone, scene, msg)
    return ok, msg


def check_code(phone, code, scene=""):
    """校验验证码(系统生成码, 服务端闭环). 返回 (ok, msg)
    注意: scene 必须与 send 时一致, 否则阿里云返回 isv.ValidateFail"""
    client = _client()
    from alibabacloud_dypnsapi20170525 import models as m
    req = m.CheckSmsVerifyCodeRequest(
        phone_number=phone,
        verify_code=str(code),
        country_code="86",
        case_auth_policy=1,               # 1=不区分大小写(数字码无影响)
        scheme_name=scene or None,        # 必须与 send 一致(空则默认方案)
    )
    resp = client.check_sms_verify_code_with_options(req, _runtime())
    body = getattr(resp, "body", None)
    if not body:
        return False, "校验失败(空响应)"
    model = getattr(body, "model", None)
    passed = bool(body.code == "OK" and model and model.verify_result == "PASS")
    return passed, body.message or ""


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
