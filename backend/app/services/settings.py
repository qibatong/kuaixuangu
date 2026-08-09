# -*- coding: utf-8 -*-
"""
系统设置服务: settings 表 key-value 存储(值为 JSON 字符串)
==========================================================
用于管理员可配置项: 评分权重等。读写均为 JSON 对象。
"""
import json
import sqlite3
import time

from ..core import config, logger
from ..db import database

log = logger.get_logger(__name__)


def get(key, default=None):
    """读取设置项, 返回解析后的 JSON 值; 不存在或解析失败返回 default"""
    try:
        conn = database.get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
    except Exception:
        return default
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return default


def set(key, value):
    """写入设置项(JSON 序列化), 返回是否成功。
    注意: 用 INSERT OR REPLACE(老 SQLite 3.7 不支持 ON CONFLICT UPSERT)
    """
    try:
        conn = database.get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?,?,?)",
            (key, json.dumps(value, ensure_ascii=False), int(time.time())))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        log.error("设置写入失败 key=%s err=%s", key, e)
        return False
