#!/usr/bin/env python3
"""Baseline driver: Claude alone, the claude-in-chrome way (screenshot -> reason -> click x,y).

  claude_drive.py open <url>        claude_drive.py shot [out.jpg]
  claude_drive.py click <x> <y>     claude_drive.py type "<text>"     claude_drive.py key Enter
Talks CDP to the same jevduo Chrome (port 9444) so both sides use one browser.
"""
import base64, json, os, sys, time, urllib.request
import websocket  # from browser-harness deps

PORT = int(os.environ.get("JEVDUO_CHROME_PORT", "9444"))
STATE = os.path.join(os.path.dirname(__file__), ".baseline_tab")

def tab_ws():
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
    want = open(STATE).read().strip() if os.path.exists(STATE) else None
    for t in tabs:
        if t["type"] == "page" and (want is None or t["id"] == want):
            return t["webSocketDebuggerUrl"]
    sys.exit("no baseline tab; run: open <url>")

class CDP:
    def __init__(self, url):
        self.ws, self.n = websocket.create_connection(url, suppress_origin=True, timeout=15), 0
    def __call__(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                return m.get("result", {})

def main():
    op, a = sys.argv[1], sys.argv[2:]
    if op == "open":
        t = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{PORT}/json/new?{a[0]}", method="PUT")).read())
        open(STATE, "w").write(t["id"]); time.sleep(2); print("opened", t["id"]); return
    c = CDP(tab_ws())
    c("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
    if op == "shot":
        out = a[0] if a else "/tmp/claude_drive.jpg"
        img = c("Page.captureScreenshot", format="jpeg", quality=70)["data"]
        open(out, "wb").write(base64.b64decode(img)); print(out)
    elif op == "click":
        x, y = float(a[0]), float(a[1])
        for t in ("mousePressed", "mouseReleased"):
            c("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)
        time.sleep(0.8); print("clicked", x, y)
    elif op == "type":
        c("Input.insertText", text=a[0]); time.sleep(0.4); print("typed")
    elif op == "key" and a[0] == "SelectAll":
        c("Input.dispatchKeyEvent", type="keyDown", key="a", code="KeyA", modifiers=4, commands=["selectAll"])
        c("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=4); print("selected all")
    elif op == "key":
        k = a[0]; code = {"Enter": 13, "Tab": 9, "Escape": 27}.get(k, 0)
        for t in ("keyDown", "keyUp"):
            c("Input.dispatchKeyEvent", type=t, key=k, windowsVirtualKeyCode=code,
              **({"text": "\r"} if k == "Enter" and t == "keyDown" else {}))
        time.sleep(1.2); print("key", k)
    elif op == "close":
        tid = open(STATE).read().strip()
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/close/{tid}"); os.remove(STATE); print("closed")

if __name__ == "__main__":
    main()
