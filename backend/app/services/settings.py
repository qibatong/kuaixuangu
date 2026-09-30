# -*- coding: utf-8 -*-
"""
系统设置服务: settings 表 key-value 存储(值为 JSON 字符串)
==========================================================
用于管理员可配置项: 评分权重等。读写均为 JSON 对象。
"""
import json
import os
import sqlite3
import time

from ..core import config, logger
from ..db import database

log = logger.get_logger(__name__)

# 进程内配置缓存的最长陈旧时间(秒); 0 = 关闭进程内缓存(每次都读库, 最准最慢)
# 2026-09-30 主人问"后台改参数会不会同步到后端、要不要自动重启"时引入。
# 背景: 生产/测试均为 uvicorn --workers 2, 而评分配置存在**进程内存**里
# (scorer._scoring_cfg / score_spot._spot_cfg), admin 保存后的 reload_*()
# 只能清**处理该请求的那个进程** ⇒ 另一半 worker 一直用旧配置(实测 30 次请求
# 19 次旧值 / 11 次新值), 且运行时无任何地方会 force 刷新 ⇒ **只有重启才恢复**。
# 现在各 worker 每 CFG_TTL 秒比一次配置指纹(见 raw()), 变了就自行重建,
# 不重启不停机; 9:26 系统批次不再有"约 50% 概率沿用旧权重"的问题。
# 设 0 = 关闭进程内缓存(每次都读库, 最准但最慢 —— 一条选股请求会读 200 次)。
CFG_TTL = float(os.environ.get("KX_CFG_TTL", "3") or 0)


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


def raw(key):
    """配置原文(settings.value 的 JSON 文本) —— 供进程内缓存当"指纹"比对。

    返回值三态, 调用方必须区分:
      · str  : 该 key 的原文; 无记录时是 **""**(合法状态 = 用代码默认值)
      · None : **读库失败**(SQLite 抖动/锁) —— 调用方应"沿用现有缓存", 绝不可
               因为读不到就重建, 否则会回落到代码默认值把线上权重/额度打回原样。

    2026-09-30 引入, 替代"用 updated_at 当版本号"的初版方案: updated_at 是**秒级**
    的 —— 管理员在同一秒内保存两次, 或 worker 恰在同一秒内启动构建缓存,
    版本号都可能不变 ⇒ 会**永久漏掉**那次改动。直接比对原文精确、无分辨率问题,
    且成本相同(同一次主键查询, μs 级); 每 CFG_TTL 秒才查一次, 可忽略。
    """
    try:
        conn = database.get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
    except Exception:
        return None
    if row is None:
        return ""
    return row[0] if row[0] is not None else ""


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
