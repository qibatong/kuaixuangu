# -*- coding: utf-8 -*-
"""
竞价抢筹 上下布局 专项浏览器回归 (测试机 chromium CDP)
验证: .qc-dual 纵向排列, 两 panel 上下堆叠且各占满整行
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
PORT = 9223


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(shot_dir, user, pwd):
    os.makedirs(shot_dir, exist_ok=True)
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

        log("登录 %s" % user)
        cdp("Page.navigate", {"url": BASE + "/login"})
        form_ok = False
        for _ in range(20):
            if ev("!!document.querySelector('input[placeholder*=\"用户名\"]')"):
                form_ok = True
                break
            time.sleep(1)
        if not form_ok:
            log("❌ 登录表单未出现")
            shot("00_login_fail.png")
            sys.exit(1)
        ev("""
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
        time.sleep(3)
        url = ev("location.href")
        if "login" in (url or ""):
            log("❌ 登录失败 url=%s" % url)
            shot("00_login_fail.png")
            sys.exit(1)
        log("✅ 登录成功 -> %s" % url)

        log("打开 /auction")
        cdp("Page.navigate", {"url": BASE + "/auction"})
        time.sleep(5)

        def click_tab(text):
            return ev("""
            (() => {
              const b = [...document.querySelectorAll('button')].find(x => x.innerText.includes('%s'));
              if (b) { b.click(); return true; } return false;
            })()
            """ % text)

        log("Tab=竞价抢筹")
        if not click_tab("竞价抢筹"):
            failed.append("找不到竞价抢筹 Tab")
        time.sleep(3)

        # 布局断言
        d = json.loads(ev("""
        (() => {
          const dual = document.querySelector('.qc-dual');
          if (!dual) return JSON.stringify({err: 'NO_QC_DUAL'});
          const fd = getComputedStyle(dual).flexDirection;
          const panels = [...dual.querySelectorAll('.qc-panel')];
          const rects = panels.map(p => {
            const r = p.getBoundingClientRect();
            return {top: Math.round(r.top), bottom: Math.round(r.bottom),
                    left: Math.round(r.left), width: Math.round(r.width),
                    title: (p.querySelector('.qc-panel-title') || {}).innerText || ''};
          });
          const vw = window.innerWidth;
          return JSON.stringify({fd, panelCount: panels.length, vw, rects});
        })()
        """))
        log("  布局: %s" % json.dumps(d, ensure_ascii=False))
        if d.get("err"):
            failed.append("qc 布局缺失: %s" % d)
        elif d.get("panelCount") != 2:
            failed.append("qc panel 数量 != 2: %s" % d)
        else:
            fd = d.get("fd")
            if fd != "column":
                failed.append("flex-direction=%s 应为 column" % fd)
            r0, r1 = d["rects"][0], d["rects"][1]
            if not (r1["top"] >= r0["bottom"] - 1):
                failed.append("两表未上下堆叠: %s" % d)
            for r in d["rects"]:
                if r["width"] < d["vw"] * 0.9:
                    failed.append("panel 未占满整行(宽%d/%d): %s" % (r["width"], d["vw"], d))
            log("✅ 表1[%s] 表2[%s] 上下堆叠正确" % (r0["title"], r1["title"]))
        shot("qc_vertical_layout.png")

        # 表格列头完整性(两张表各11列: 排名/代码/名称/实时涨幅/竞价金额/抢筹幅度/竞价换手/竞价涨幅/流通/概念/操作)
        cols = json.loads(ev("""
        (() => {
          const panels = [...document.querySelectorAll('.qc-panel')];
          return JSON.stringify(panels.map(p => {
            const ths = [...p.querySelectorAll('thead th')].map(x => x.innerText.trim());
            return {title: (p.querySelector('.qc-panel-title') || {}).innerText || '', cols: ths.length, ths};
          }));
        })()
        """))
        log("  表头: %s" % json.dumps(cols, ensure_ascii=False))
        for c in cols:
            if c.get("cols") != 11:
                failed.append("表[%s] 列数=%s 应为11: %s" % (c.get("title"), c.get("cols"), c))
            elif c.get("ths", [])[1] != "代码" or c.get("ths", [])[2] != "名称":
                failed.append("表[%s] 第2/3列应为 代码/名称: %s" % (c.get("title"), c.get("ths")))
        if not failed:
            log("✅ 两表各11列完整(代码|名称分列), 无错位")

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
    log("✅✅ 竞价抢筹上下布局真实浏览器验证通过, 截图: %s" % shot_dir)


def main():
    parser = argparse.ArgumentParser(description="快选 竞价抢筹上下布局 浏览器回归 (测试机)")
    parser.add_argument("--user", default=os.environ.get("KX_REG_USER", ""))
    parser.add_argument("--pwd", default=os.environ.get("KX_REG_PWD", ""))
    parser.add_argument("--shot-dir", default="/opt/kuaixuan/logs/browser_reg")
    args = parser.parse_args()
    if not args.user or not args.pwd:
        parser.error("需提供 --user/--pwd 或设置 KX_REG_USER/KX_REG_PWD 环境变量")
    run(args.shot_dir, args.user, args.pwd)


if __name__ == "__main__":
    main()
