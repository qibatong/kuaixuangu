# -*- coding: utf-8 -*-
"""
测试机真实浏览器回归脚本 (Chromium headless + CDP)
用途: 部署后自动验证页面渲染与数据, 无需本地浏览器
用法:
  /usr/local/python311/bin/python3 scripts/browser_reg.py
  # 自定义账号/截图目录(可选):
  /usr/local/python311/bin/python3 scripts/browser_reg.py --user xxx --pwd yyy --shot-dir /tmp/shots

环境准备(测试机一次性):
  yum install -y chromium          # EPEL 仓库自带, CentOS 7 glibc 2.17 兼容
  /usr/local/python311/bin/pip3 install -i https://pypi.tuna.tsinghua.edu.cn/simple websocket-client

注意: Chromium 111+ websocket 需 --remote-allow-origins=* (脚本已加)
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = "/usr/bin/chromium-browser"
BASE = "http://127.0.0.1"
PORT = 9222


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(shot_dir, user, pwd):
    os.makedirs(shot_dir, exist_ok=True)
    log("启动 headless chromium :%d" % PORT)
    proc = subprocess.Popen(
        [CHROME, "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
         "--remote-allow-origins=*",
         "--remote-debugging-port=%d" % PORT, "--window-size=1440,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    failed = []
    try:
        tabs = None
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=2))
                if tabs:
                    break
            except Exception:
                time.sleep(1)
        if not tabs:
            log("❌ chromium 调试端口未就绪")
            sys.exit(1)

        ws = websocket.create_connection(tabs[0]["webSocketDebuggerUrl"], timeout=30)
        mid = [0]

        def cdp(method, params=None, timeout=40):
            mid[0] += 1
            _id = mid[0]
            ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
            ws.settimeout(timeout)
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == _id:
                    return r

        def ev(expr, timeout=15):
            r = cdp("Runtime.evaluate",
                    {"expression": expr, "returnByValue": True, "awaitPromise": True}, timeout)
            res = r.get("result", {}).get("result", {})
            if res.get("exceptionDetails"):
                return "EXC:" + json.dumps(res["exceptionDetails"], ensure_ascii=False)[:200]
            return res.get("value")

        def shot(name):
            r = cdp("Page.captureScreenshot", {"format": "png"})
            data = r.get("result", {}).get("data")
            if data:
                with open(os.path.join(shot_dir, name), "wb") as f:
                    f.write(base64.b64decode(data))
                log("📷 截图 %s" % name)

        cdp("Page.enable")
        cdp("Runtime.enable")

        # ---- 登录 ----
        log("登录 %s" % user)
        cdp("Page.navigate", {"url": BASE + "/login"})
        time.sleep(3)
        r = ev("""
        (() => {
          const setVal = (el, v) => {
            const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            s.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true}));
          };
          const u = document.querySelector('input[placeholder*="用户名"]');
          const p = document.querySelector('input[placeholder*="密码"]');
          if (!u || !p) return 'NOFORM';
          setVal(u, '%s'); setVal(p, '%s');
          const b = [...document.querySelectorAll('button')].find(x => x.innerText.includes('登录'));
          if (b) { b.click(); return 'CLICKED'; }
          return 'NOBTN';
        })()
        """ % (user, pwd))
        log("  登录动作: %s" % r)
        time.sleep(3)
        url = ev("location.href")
        if "login" in (url or ""):
            log("❌ 登录失败 url=%s" % url)
            shot("00_login_fail.png")
            sys.exit(1)
        log("✅ 登录成功 -> %s" % url)

        # ---- /auction ----
        log("打开 /auction")
        cdp("Page.navigate", {"url": BASE + "/auction"})
        time.sleep(5)

        rows_js = """
        (() => {
          const t = document.querySelector('table.stock-table');
          if (!t) return JSON.stringify({err:'NO_TABLE'});
          const ths = [...t.querySelectorAll('thead th')].map(x => x.innerText.trim());
          const rows = [...t.querySelectorAll('tbody tr')].slice(0, 3).map(tr =>
            [...tr.querySelectorAll('td')].map(td => td.innerText.trim()));
          return JSON.stringify({ths, rows});
        })()
        """

        def click_tab(text):
            return ev("""
            (() => {
              const b = [...document.querySelectorAll('button')].find(x => x.innerText.includes('%s'));
              if (b) { b.click(); return true; } return false;
            })()
            """ % text)

        # Tab1 竞价委买(默认)
        log("Tab=竞价委买")
        time.sleep(1)
        d = json.loads(ev(rows_js))
        log("  首行: %s" % (d.get("rows", [])[0] if d.get("rows") else d))
        if d.get("rows") and len(d["rows"][0]) >= 7 and d["rows"][0][5] not in ("NaN", "0.00", ""):
            log("✅ 委买额=%s 净额=%s" % (d["rows"][0][5], d["rows"][0][6]))
        else:
            failed.append("竞价委买异常: %s" % d)

        # Tab2 竞价爆量
        log("Tab=竞价爆量")
        click_tab("竞价爆量")
        time.sleep(2)
        d = json.loads(ev(rows_js))
        ths = d.get("ths", [])
        log("  表头: %s" % "|".join(ths))
        log("  首行: %s" % (d.get("rows", [])[0] if d.get("rows") else d))
        if "竞价成交额" not in "|".join(ths):
            failed.append("竞价爆量列头未切换: %s" % ths)
        if d.get("rows") and len(d["rows"][0]) >= 7 and d["rows"][0][5] not in ("NaN", "0.00", ""):
            log("✅ 成交额=%s 净额=%s" % (d["rows"][0][5], d["rows"][0][6]))
        else:
            failed.append("竞价爆量数据异常: %s" % d)
        shot("02_boom.png")

        # Tab3 竞价净额
        log("Tab=竞价净额")
        click_tab("竞价净额")
        time.sleep(2)
        d = json.loads(ev(rows_js))
        log("  首行: %s" % (d.get("rows", [])[0] if d.get("rows") else d))
        if d.get("rows") and len(d["rows"][0]) >= 7 and d["rows"][0][6] not in ("NaN", ""):
            log("✅ 净额Tab 首名净额=%s" % d["rows"][0][6])
        else:
            failed.append("竞价净额异常: %s" % d)
        shot("03_net.png")

        # Tab4 昨炸板
        log("Tab=昨炸板")
        click_tab("昨炸板")
        time.sleep(2)
        d = json.loads(ev(rows_js))
        rows = d.get("rows") or []
        log("  行数(前3): %d 首行: %s" % (len(rows), rows[0] if rows else d))
        if rows and len(rows[0]) >= 5 and rows[0][2] not in ("", "-"):
            log("✅ 昨炸板 首行涨幅=%s 炸板次数=%s" % (rows[0][2], rows[0][4]))
        else:
            failed.append("昨炸板异常: %s" % d)
        shot("04_broken_yest.png")

        # Tab5 今炸板
        log("Tab=今炸板")
        click_tab("今炸板")
        time.sleep(2)
        d = json.loads(ev(rows_js))
        rows = d.get("rows") or []
        log("  行数(前3): %d 首行: %s" % (len(rows), rows[0] if rows else d))
        if rows and len(rows[0]) >= 5 and rows[0][2] not in ("", "-"):
            log("✅ 今炸板 首行涨幅=%s 炸板次数=%s" % (rows[0][2], rows[0][4]))
        else:
            failed.append("今炸板异常: %s" % d)
        shot("05_broken_today.png")

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    log("=" * 50)
    if failed:
        log("❌ 失败项:")
        for f in failed:
            log("  - " + f)
        sys.exit(2)
    log("✅✅ 竞价异动页五 Tab(委买/爆量/净额/昨炸板/今炸板)真实浏览器验证全部通过, 截图: %s" % shot_dir)


def main():
    parser = argparse.ArgumentParser(description="快选真实浏览器回归 (测试机专用)")
    parser.add_argument("--user", default=os.environ.get("KX_REG_USER", ""),
                        help="登录用户名 (默认读 KX_REG_USER 环境变量)")
    parser.add_argument("--pwd", default=os.environ.get("KX_REG_PWD", ""),
                        help="登录密码 (默认读 KX_REG_PWD 环境变量)")
    parser.add_argument("--shot-dir", default="/opt/kuaixuan/logs/browser_reg",
                        help="截图目录")
    args = parser.parse_args()
    if not args.user or not args.pwd:
        parser.error("需提供 --user/--pwd 或设置 KX_REG_USER/KX_REG_PWD 环境变量")
    run(args.shot_dir, args.user, args.pwd)


if __name__ == "__main__":
    main()