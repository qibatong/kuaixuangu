# -*- coding: utf-8 -*-
"""竞价异动页表格排序交互验证 (chromium CDP): 点击表头排序 → 行序应变化"""
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
PORT = 9224


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(user, pwd):
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

        cdp("Page.enable")
        cdp("Runtime.enable")
        cdp("Page.navigate", {"url": BASE + "/login"})
        for _ in range(20):
            if ev("!!document.querySelector('input[placeholder*=\"用户名\"]')"):
                break
            time.sleep(1)
        ev("""
        (() => {
          const setVal = (el, v) => {
            const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            s.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true}));
          };
          const u = document.querySelector('input[placeholder*="用户名"]');
          const p = document.querySelector('input[placeholder*="密码"]');
          if (u) setVal(u, '%s');
          if (p) setVal(p, '%s');
          const b = [...document.querySelectorAll('button')].find(x => x.innerText.includes('登录'));
          if (b) b.click();
        })()
        """ % (user, pwd))
        time.sleep(3)
        cdp("Page.navigate", {"url": BASE + "/auction"})
        time.sleep(5)

        def first_rows():
            return json.loads(ev("""
            (() => {
              const t = document.querySelector('table.stock-table');
              if (!t) return JSON.stringify([]);
              return JSON.stringify([...t.querySelectorAll('tbody tr')].slice(0, 5).map(tr =>
                [...tr.querySelectorAll('td')].map(td => td.innerText.trim())));
            })()
            """))

        def click_th(text):
            return ev("""
            (() => {
              const th = [...document.querySelectorAll('table.stock-table thead th')].find(x => x.innerText.includes('%s'));
              if (th) { th.click(); return true; } return false;
            })()
            """ % text)

        def find_tab(t):
            return ev("""
            (() => {
              const b = [...document.querySelectorAll('button')].find(x => x.innerText.includes('%s'));
              if (b) { b.click(); return true; } return false;
            })()
            """ % t)

        # Tab1 竞价委买: 排序前
        time.sleep(1)
        before = first_rows()
        log("委买排序前首行: %s" % (before[0] if before else []))
        # 点击"实时涨幅"表头(列索引3: 代码0 名称1 实时涨幅2? 看表头)
        ths = json.loads(ev("""
        (() => {
          const t = document.querySelector('table.stock-table');
          return JSON.stringify(t ? [...t.querySelectorAll('thead th')].map(x => x.innerText.trim()) : []);
        })()
        """))
        log("表头: %s" % ths)
        if click_th("实时涨幅"):
            time.sleep(2)
            after = first_rows()
            log("点击[实时涨幅]后首行: %s" % (after[0] if after else []))
            if before and after and before[0] != after[0]:
                log("✅ 排序生效: 首行从 %s → %s" % (before[0][:3], after[0][:3]))
            else:
                # 可能首行没变(已是最新), 比较全部5行
                if before != after:
                    log("✅ 排序生效: 行序变化(首行相同但后续变化)")
                else:
                    failed.append("排序后行序无变化: %s" % before)
        else:
            failed.append("找不到[实时涨幅]表头")

        # 再点一次(切反序)
        if click_th("实时涨幅"):
            time.sleep(2)
            after2 = first_rows()
            log("第二次点击后首行: %s" % (after2[0] if after2 else []))
            if after and after2 and after[0] == after2[0]:
                log("⚠️ 两次点击首行相同(可能该列首个值相同, 检查后3行)")
            if after != after2:
                log("✅ 反序切换生效")
            else:
                failed.append("反序切换无变化")

        # Tab2 竞价抢筹: 验证抢筹幅度排序
        find_tab("竞价抢筹")
        time.sleep(2)
        if click_th("抢筹幅度"):
            time.sleep(2)
            qc_rows = first_rows()
            log("抢筹Tab 点击[抢筹幅度]后首行: %s" % (qc_rows[0] if qc_rows else []))
            log("✅ 抢筹Tab排序点击正常" if qc_rows else "⚠️ 抢筹Tab无数据(非竞价时段)")

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
    log("✅✅ 竞价异动表格排序真实浏览器验证通过")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default=os.environ.get("KX_REG_USER", ""))
    parser.add_argument("--pwd", default=os.environ.get("KX_REG_PWD", ""))
    args = parser.parse_args()
    if not args.user or not args.pwd:
        parser.error("需 --user/--pwd")
    run(args.user, args.pwd)
