# -*- coding: utf-8 -*-
"""
WebPush 发送（2026-10-04，手机端「真推送」）
==========================================

★ 为什么自己写而不用 pywebpush：
  两机 venv 都**已有 `cryptography`**（实测确认），装 pywebpush 会往生产 venv 引新包
  （http_ece / py_vapid / six …），不符合"能不动线上环境就不动"的纪律。
  RFC 8291 的加密本身不复杂：**ECDH(P-256) + HKDF-SHA256 + AES-128-GCM**，
  用 cryptography  primitives 拼起来约 80 行，可单测（见文件末尾 `_selftest`）。

流程（标准 WebPush）：
  1. VAPID：服务端持有一把 EC P-256 密钥，给每个请求签 ES256 JWT（aud = 推送服务 origin）
  2. 载荷加密：不能用明文（推送服务能看到），按 RFC 8291 做 aes128gcm 加密
  3. POST 到浏览器给的 endpoint，Header 带 Authorization: vapid t=<jwt>,k=<pubkey>

🔴 前提：Service Worker / Push 只在**安全上下文**可用 ⇒ 必须 https。
   现网 `https://www.kuaixuangu.cn` 是可信证书（CN=www.kuaixuangu.cn，有效期至 2027-03-01），已满足。
"""

from __future__ import annotations

import base64
import json
import os
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

# ---------------------------------------------------------------- 基础工具


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _raw_pubkey(priv: ec.EllipticCurvePrivateKey) -> bytes:
    """未压缩点 0x04 || X(32) || Y(32) —— 浏览器 applicationServerKey / 加密都要这个格式"""
    nums = priv.public_key().public_numbers()
    return b"\x04" + nums.x.to_bytes(32, "big") + nums.y.to_bytes(32, "big")


def _load_pubkey_from_raw(raw: bytes) -> ec.EllipticCurvePublicKey:
    if len(raw) != 65 or raw[0] != 0x04:
        raise ValueError("不是合法的未压缩 EC 点: len=%d head=%s" % (len(raw), raw[:1]))
    x = int.from_bytes(raw[1:33], "big")
    y = int.from_bytes(raw[33:65], "big")
    # raw 本身就是完整的 0x04||X||Y，可直接交给 from_encoded_point
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)


# ---------------------------------------------------------------- VAPID 密钥


def _keys_path() -> str:
    """密钥落盘位置：与数据库同目录（/opt/kuaixuan/vapid_keys.json）。

    🔴 为什么要落盘：VAPID 公钥**必须与浏览器订阅时的 applicationServerKey 一致**，
       换了密钥 ⇒ 之前所有订阅全部作废（endpoint 还活着但解密/鉴权失败）。
       所以绝不能每次启动重新生成。
    """
    from app.core import config

    db = getattr(config, "DB_FILE", "") or ""
    d = os.path.dirname(os.path.abspath(db)) if db else os.getcwd()
    return os.path.join(d, "vapid_keys.json")


_VAPID_CACHE = {"priv": None, "pub_b64": None}


def get_vapid_keypair():
    """返回 (private_key, public_key_b64url)。首次调用会生成并落盘。"""
    if _VAPID_CACHE["priv"] is not None:
        return _VAPID_CACHE["priv"], _VAPID_CACHE["pub_b64"]

    path = _keys_path()
    priv = None
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f)
            priv = serialization.load_pem_private_key(j["private_pem"].encode(), password=None)
    except Exception as e:
        print("[webpush] VAPID 密钥读取失败(%s)，将重新生成 ⇒ 旧订阅会失效" % e)
        priv = None

    if priv is None:
        priv = ec.generate_private_key(ec.SECP256R1())
        pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"private_pem": pem, "created_at": int(time.time())}, f)
            os.chmod(path, 0o600)   # 私钥只给 owner
        except Exception as e:
            print("[webpush] VAPID 密钥落盘失败: %s" % e)

    _VAPID_CACHE["priv"] = priv
    _VAPID_CACHE["pub_b64"] = b64url_encode(_raw_pubkey(priv))
    return _VAPID_CACHE["priv"], _VAPID_CACHE["pub_b64"]


def vapid_public_key() -> str:
    return get_vapid_keypair()[1]


def _vapid_jwt(endpoint: str, ttl: int = 12 * 3600) -> str:
    """ES256 JWT：头 {typ,alg} + 载荷 {aud, exp, sub}"""
    from app.core import config

    priv, pub_b64 = get_vapid_keypair()
    origin = "https://%s" % endpoint.split("/")[2]
    header = {"typ": "JWT", "alg": "ES256"}
    claims = {
        "aud": origin,
        "exp": int(time.time()) + ttl,
        "sub": getattr(config, "PUSH_VAPID_SUBJECT", "mailto:admin@kuaixuangu.cn"),
    }
    signing_input = (
        b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    )
    sig = priv.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    return signing_input + "." + b64url_encode(sig)


# ---------------------------------------------------------------- 载荷加密(RFC 8291)


def encrypt_payload(p256dh_b64: str, auth_b64: str, plaintext: bytes, salt: bytes = None) -> bytes:
    """把明文加密成 aes128gcm 正文。

    :param p256dh_b64: 浏览器订阅里的 keys.p256dh（base64url 未压缩点）
    :param auth_b64:   浏览器订阅里的 keys.auth（base64url，16 字节）
    """
    client_pub_raw = b64url_decode(p256dh_b64)
    auth_secret = b64url_decode(auth_b64)

    server_priv = ec.generate_private_key(ec.SECP256R1())
    server_pub_raw = _raw_pubkey(server_priv)

    shared = server_priv.exchange(ec.ECDH(), _load_pubkey_from_raw(client_pub_raw))

    # 1) 派生共享密钥材料
    info_prefix = b"WebPush: info\x00" + client_pub_raw + server_pub_raw
    prk = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=info_prefix).derive(shared)

    if salt is None:
        salt = os.urandom(16)

    # 2) 内容密钥与 nonce
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(prk)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(prk)

    # 3) 加密。RFC 要求明文尾部补 1 字节 0x02 作为填充分隔符（aes128gcm 模式）
    padded = plaintext + b"\x02"
    ct = AESGCM(cek).encrypt(nonce, padded, None)

    # 4) aes128gcm 头：salt(16) || rs(4, BE) || idlen(1) || keyid(65) || ciphertext
    header = salt + (4096).to_bytes(4, "big") + bytes([65]) + server_pub_raw
    return header + ct


# ---------------------------------------------------------------- 发送


def send_one(sub: dict, title: str, body: str, url: str = "/", ttl: int = 86400) -> tuple:
    """给**一个订阅**推送。返回 (ok: bool, 说明: str)。

    :param sub: {"endpoint":..., "p256dh":..., "auth":...}
    """
    if requests is None:
        return False, "requests 未安装"
    try:
        payload = json.dumps(
            {"title": title, "body": body, "url": url}, ensure_ascii=False
        ).encode("utf-8")
        cipher = encrypt_payload(sub["p256dh"], sub["auth"], payload)
        _, pub_b64 = get_vapid_keypair()
        headers = {
            "Authorization": "vapid t=%s, k=%s" % (_vapid_jwt(sub["endpoint"]), pub_b64),
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "aes128gcm",
            "TTL": str(ttl),
            "Urgency": "normal",
        }
        r = requests.post(sub["endpoint"], data=cipher, headers=headers, timeout=15)
        # 404/410 = 订阅已失效（用户清了浏览器数据 / 退订）⇒ 调用方应删除该行
        if r.status_code in (404, 410):
            return False, "订阅已失效(%d)" % r.status_code
        if r.status_code >= 400:
            return False, "推送服务返回 %d: %s" % (r.status_code, r.text[:120])
        return True, str(r.status_code)
    except Exception as e:
        return False, "异常: %s" % e


# ---------------------------------------------------------------- 自测（加密可逆性）


def _selftest() -> bool:
    """不依赖网络的自检：验证我们加密出来的东西，用**客户端私钥**能解开。"""
    client_priv = ec.generate_private_key(ec.SECP256R1())
    p256dh = b64url_encode(_raw_pubkey(client_priv))
    auth = b64url_encode(os.urandom(16))
    plain = json.dumps({"title": "测试", "body": "你好"}, ensure_ascii=False).encode()

    blob = encrypt_payload(p256dh, auth, plain)
    salt = blob[:16]
    rs = int.from_bytes(blob[16:20], "big")
    idlen = blob[20]
    server_pub_raw = blob[21 : 21 + idlen]
    ct = blob[21 + idlen :]

    assert rs == 4096, "记录大小应为 4096"
    shared = client_priv.exchange(ec.ECDH(), _load_pubkey_from_raw(server_pub_raw))
    auth_secret = b64url_decode(auth)
    prk = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=auth_secret,
        info=b"WebPush: info\x00" + b64url_decode(p256dh) + server_pub_raw,
    ).derive(shared)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(prk)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(prk)
    got = AESGCM(cek).decrypt(nonce, ct, None)
    assert got.endswith(b"\x02"), "缺少 RFC 8291 填充分隔符"
    assert got[:-1] == plain, "解密结果与原文不一致"
    return True


if __name__ == "__main__":
    print("selftest:", "PASS" if _selftest() else "FAIL")
    print("vapid pub:", vapid_public_key()[:24], "...")
