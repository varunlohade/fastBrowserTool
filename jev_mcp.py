#!/usr/bin/env python3
"""fastBrowserTool as a Claude Code browser tool (MCP over stdio, stdlib only).

  claude mcp add --scope user fastbrowser -- python3 /path/to/jev_mcp.py

Each tool forwards to the jevduo daemon (server.py), which it starts if needed.
"""
import json, os, re, subprocess, sys, threading, time, urllib.error, urllib.request

OUT = threading.Lock()

ROOT = os.path.dirname(os.path.realpath(__file__))
BASE = f"http://127.0.0.1:{int(os.environ.get('JEVDUO_PORT', '8791'))}"

S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}

def tool(name, desc, props, required=()):
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": list(required)}}

TOOLS = [
    tool("browser_task",
         "FASTEST way to do a web task. Opens the url and lets jev (a fast click model, ~0.4s per step, "
         "no screenshots) work toward the goal. Returns [DONE] with a page brief, or [ASK] with a numbered "
         "element list when jev needs you. Answer an [ASK] with browser_reply, browser_click or browser_type. "
         "For a web search, pass a search URL, e.g. https://duckduckgo.com/?q=... "
         "jev can finish too early: check the brief before you report success.",
         {"url": S, "goal": {"type": "string", "description": "What done looks like, in one or two sentences."},
          "max_steps": I}, ["url", "goal"]),
    tool("browser_reply", "Give jev a hint on an [ASK] session, then jev carries on.",
         {"session": S, "hint": S}, ["session", "hint"]),
    tool("browser_open",
         "Open a page for you to drive step by step (jev does not act). Returns the page text and a numbered "
         "element list. Use browser_task instead when a goal fits.",
         {"url": S}, ["url"]),
    tool("browser_click",
         "Click element [index] from the latest element list. Returns the new page. On a browser_task "
         "session jev carries on after your click unless continue_jev is false.",
         {"session": S, "index": I, "continue_jev": B}, ["session", "index"]),
    tool("browser_type",
         "Type text into field [index] from the latest element list. Returns the new page.",
         {"session": S, "index": I, "text": S, "continue_jev": B}, ["session", "index", "text"]),
    tool("browser_select", "Choose option [index] of a select element (option indexes appear under it).",
         {"session": S, "index": I, "continue_jev": B}, ["session", "index"]),
    tool("browser_navigate", "Go to a url in an existing session. Returns the new page.",
         {"session": S, "url": S}, ["session", "url"]),
    tool("browser_scroll", "Scroll the page. Returns the new page.",
         {"session": S, "direction": {"type": "string", "enum": ["down", "up"]}, "pixels": I}, ["session"]),
    tool("browser_look", "Read the current page again. all=true gives up to 3000 chars and every element.",
         {"session": S, "all": B}, ["session"]),
    tool("browser_screenshot", "Screenshot the page. Only for visual checks; text tools are faster.",
         {"session": S}, ["session"]),
    tool("browser_close", "Close a session, or every session with session='all'.", {"session": S}, ["session"]),
]

def up():
    try:
        return urllib.request.urlopen(BASE + "/health", timeout=1).status == 200
    except Exception:
        return False

START = threading.Lock()

def ensure():
    if up():
        return
    with START:
        if not up():
            start()

def start():
    os.makedirs(os.path.join(ROOT, "runs"), exist_ok=True)
    log = open(os.path.join(ROOT, "runs", "daemon.log"), "a")
    subprocess.Popen([os.path.join(ROOT, ".venv", "bin", "python"), os.path.join(ROOT, "server.py")],
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(150):
        if up():
            return
        time.sleep(0.1)
    raise RuntimeError("fastbrowser daemon did not start; see runs/daemon.log")

def daemon(cmd):
    ensure()
    req = urllib.request.Request(BASE + "/cmd", data=json.dumps(cmd).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        return e.read().decode()

def run(name, a):
    sid = a.get("session")
    stop = a.get("continue_jev") is False
    cmd = {
        "browser_task": lambda: {"op": "web", "url": a["url"], "goal": a["goal"], "min_conf": 0.0,
                                 "max_steps": a.get("max_steps", 25)},
        "browser_reply": lambda: {"op": "say", "id": sid, "text": a["hint"]},
        "browser_open": lambda: {"op": "open", "url": a["url"]},
        "browser_click": lambda: {"op": "click", "id": sid, "index": a["index"], "stop": stop},
        "browser_type": lambda: {"op": "type", "id": sid, "index": a["index"], "text": a["text"], "stop": stop},
        "browser_select": lambda: {"op": "select", "id": sid, "index": a["index"], "stop": stop},
        "browser_navigate": lambda: {"op": "navigate", "id": sid, "url": a["url"]},
        "browser_scroll": lambda: {"op": "scroll", "id": sid, "direction": a.get("direction", "down"),
                                   "pixels": a.get("pixels", 800)},
        "browser_look": lambda: {"op": "look", "id": sid, "all": bool(a.get("all"))},
        "browser_screenshot": lambda: {"op": "screenshot", "id": sid},
        "browser_close": lambda: {"op": "close", "id": sid},
    }[name]()
    out = daemon(cmd)
    if out.startswith("IMG:"):
        return [{"type": "image", "data": out[4:], "mimeType": "image/jpeg"}]
    out = re.sub(r"\nreply with: jev say.*$", "\nreply with: browser_reply | browser_click | browser_type | browser_close",
                 out)
    return [{"type": "text", "text": out}]

def reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    msg.update({"error": error} if error else {"result": result})
    with OUT:
        sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()

def call(mid, params):
    try:
        reply(mid, {"content": run(params["name"], params.get("arguments") or {})})
    except Exception as e:  # noqa: BLE001
        reply(mid, {"content": [{"type": "text", "text": f"[ERROR] {e}"}], "isError": True})

def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:
            continue  # notification
        if method == "initialize":
            reply(mid, {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fastbrowser", "version": "0.2.0"}})
        elif method == "tools/list":
            reply(mid, {"tools": TOOLS})
        elif method == "tools/call":
            threading.Thread(target=call, args=(mid, params), daemon=True).start()
        elif method == "ping":
            reply(mid, {})
        else:
            reply(mid, error={"code": -32601, "message": f"unknown method {method}"})

if __name__ == "__main__":
    main()
