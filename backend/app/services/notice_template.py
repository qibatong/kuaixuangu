"""A6/M7 消息模板库(2026-10-07 v4.12.9)。

模板 = 一条**已经写好、验证过措辞**的消息内容(标题/正文/级别/分类/行动按钮)。
它回答的是"这条消息**怎么说**", 不回答"发给谁"和"什么时候发" —— 后者是每次发布现场的
决定(amount target/status/publish_at 都不进模板)。见 db/database.py 建表处的取舍说明。

三个刻意不为:
  ① **不做变量占位**(如 {nickname}) —— 替换失败会把 "{nickname} 你好" 原样发出去,
     而这种错误在发出去的那一秒就不可撤销。要个性化就得先埋点拿到可信字段, 那是另一个工单。
  ② **"使用量"由发布接口计数, 不由"载入"计数** —— 载入只是草稿行为, 可能被丢掉;
     只有真的 Publish 出去了才算用过一次, 这个数字才有资格用来排模板的常用度。
  ③ 删除是**物理删除**: 模板是文案草稿, 不是业务凭证, 没有审计价值 ⇒ 不留软删标志徒增复杂度。
     真怕误删靠的是下面的 use_count>0 提示(Django-like 保护), 而不是多一列 off_at。
"""
import logging
import sqlite3
import time

from ..core import config

log = logging.getLogger(__name__)

_FIELDS = ("name", "title", "body", "level", "category", "target", "days",
           "action_type", "action_value", "tag")


def _conn():
    c = sqlite3.connect(config.DB_FILE, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def rows():
    """模板列表: 常用优先(use_count), 同频按最近改过优先 —— 运营是照着这个下拉挑的。"""
    try:
        c = _conn()
        try:
            rs = c.execute("SELECT * FROM notice_templates ORDER BY use_count DESC, updated_at DESC").fetchall()
        finally:
            c.close()
    except Exception as e:
        log.warning("模板列表失败 err=%s", e)
        return []
    out = []
    for r in rs:
        d = dict(r)
        d["last_used_date"] = (_fmt(int(d.get("last_used_at") or 0)) if d.get("last_used_at") else "")
        out.append(d)
    return out


def save(tid, payload, who=""):
    """新建(tid=0)或整体覆盖更新。返回 {"ok", "id"|"msg"}。

    🔴 整体覆盖而不是按字段 patch: 模板一共 10 个字段、一次全传, patch 语义在这里只会
       让"某个字段到底有没有被改"变成悬案, 收益远小于复杂度。
    """
    name = str(payload.get("name") or "").strip()
    title = str(payload.get("title") or "").strip()
    btext = str(payload.get("body") or "").strip()
    if not name or not title or not btext:
        return {"ok": False, "msg": "模板名、标题、正文都不能为空"}
    vals = {k: str(payload.get(k) or "").strip() for k in ("level", "category", "target",
                                                           "action_type", "action_value", "tag")}
    try:
        days = max(0, int(payload.get("days") or 0))
    except (TypeError, ValueError):
        days = 0
    now = int(time.time())
    cols = dict(vals, name=name, title=title, body=btext, days=days, updated_at=now)
    try:
        c = _conn()
        try:
            if int(tid or 0) > 0:
                c.execute("UPDATE notice_templates SET %s WHERE id=?" %
                          ", ".join("%s=?" % k for k in cols), tuple(cols.values()) + (int(tid),))
            else:
                cols.update(created_by=who or "", created_at=now, use_count=0, last_used_at=0)
                cur = c.execute("INSERT INTO notice_templates (%s) VALUES (%s)" % (
                    ", ".join(cols), ", ".join("?" * len(cols))), tuple(cols.values()))
                tid = cur.lastrowid
            c.commit()
        finally:
            c.close()
    except sqlite3.IntegrityError:
        return {"ok": False, "msg": "已存在同名模板，换一个名字"}
    except Exception as e:
        log.warning("模板保存失败 err=%s", e)
        return {"ok": False, "msg": "保存失败"}
    return {"ok": True, "id": int(tid)}


def delete(tid):
    """删除模板。🔴 被发布过的(use_count>0)拒绝删除 —— 那些文案已经进过用户的信箱,
    删掉等于把历史口径抹掉; 真要停用, 改个名字加"已停用"前缀即可。"""
    try:
        c = _conn()
        try:
            r = c.execute("SELECT use_count FROM notice_templates WHERE id=?", (int(tid),)).fetchone()
            if not r:
                return {"ok": False, "msg": "模板不存在"}
            if int(r["use_count"] or 0) > 0:
                return {"ok": False, "msg": "该模板已发布过 %d 次，不支持删除（可改名标记停用）" % r["use_count"]}
            c.execute("DELETE FROM notice_templates WHERE id=?", (int(tid),))
            c.commit()
        finally:
            c.close()
    except Exception as e:
        log.warning("模板删除失败 err=%s", e)
        return {"ok": False, "msg": "删除失败"}
    return {"ok": True}


def touch(tid):
    """发布成功时计一次使用。失败无所谓(统计性质), 但必须**不抛**到发布主流程 ——
    模板计数不该有能力把一条已经发出去的公告变成失败。"""
    if not tid:
        return
    try:
        c = _conn()
        try:
            c.execute("UPDATE notice_templates SET use_count=use_count+1, last_used_at=? WHERE id=?",
                      (int(time.time()), int(tid)))
            c.commit()
        finally:
            c.close()
    except Exception as e:
        log.warning("模板用量计数失败 tid=%s err=%s", tid, e)


def _fmt(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts + 8 * 3600))
