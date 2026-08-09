# -*- coding: utf-8 -*-
"""
统一日志模块
============
- 输出到 logs/app.log (10MB 大小轮转, 保留 5 份) + 标准输出
- 所有业务模块用 get_logger(__name__) 获取 logger
- 日志级别: INFO(正常关键操作) / WARNING(可恢复异常) / ERROR(失败)
"""
import logging
import os
from logging.handlers import RotatingFileHandler

from ..core import config


def setup_logging():
    """应用启动时调用一次, 幂等"""
    root = logging.getLogger()
    if getattr(root, "_bid_configured", False):
        return root
    log_dir = config.LOG_DIR
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        log_dir = "/tmp"   # 目录不可写时降级
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)
    root.setLevel(logging.INFO)
    root._bid_configured = True
    root.info("日志系统初始化完成: %s/app.log" % log_dir)
    return root


def get_logger(name):
    return logging.getLogger(name)
