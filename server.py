#!/usr/bin/env python3
"""jevduo daemon: holds live jev browser sessions so Claude can talk to them.

jev drives fast (one choice call per step). It stops and hands control back to
Claude when it is unsure, blocked, needs a text value, or hits a risky control.
Claude answers with a hint, a click, or a text value, and jev carries on.
"""
import json, os, re, sys, time, threading, traceback, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("JEVDUO_PORT", "8791"))

def load_dotenv():
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_dotenv()

CHROME_PORT = int(os.environ.get("JEVDUO_CHROME_PORT", "9444"))
CHROME_BIN = os.environ.get("JEVDUO_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

def own_chrome():
    """jev works in its own Chrome profile: no personal logins, cookies, or autofill.
    Set BU_CDP_WS yourself to point it at another browser instead."""
    if os.environ.get("BU_CDP_WS"):
        return
    import subprocess, urllib.request
    def ws():
        try:
            return json.load(urllib.request.urlopen(f"http://127.0.0.1:{CHROME_PORT}/json/version", timeout=1))["webSocketDebuggerUrl"]
        except Exception:
            return None
    if not ws():
        subprocess.Popen([CHROME_BIN, f"--user-data-dir={os.path.join(ROOT, '.chrome')}",
                          f"--remote-debugging-port={CHROME_PORT}", "--no-first-run",
                          "--no-default-browser-check", "--window-size=1280,900", "about:blank"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(100):
            if ws():
                break
            time.sleep(0.1)
    os.environ["BU_CDP_WS"] = ws() or ""
    os.environ.setdefault("BU_NAME", "jevduo")

own_chrome()

from jev_ultrafast import Agent                      # noqa: E402
from jev_ultrafast.browser import StalePage          # noqa: E402
from jev_ultrafast.model import action_space, field_context, post_json  # noqa: E402

SAFETY = (" Never enter payment details. Never click pay, purchase, buy, book-now, place-order,"
          " confirm-booking, send, post, publish, or delete controls; stop before them.")
# Money and one-way controls. jev never presses these and neither does Claude: a human does.
RISKY = re.compile(r"\b(pay\w*|purchase|buy|book(ing)? now|reserve|place order|submit order|check ?out|"
                   r"subscribe|confirm|send|post(?!\s*code)|publish|delete|transfer|withdraw)\b", re.I)

SESSIONS = {}

def show(target):
    """Bring jev's tab and its Chrome window to the front so a human can watch."""
    from browser_harness.helpers import cdp
    import subprocess
    try:
        cdp("Target.activateTarget", targetId=target)
        pid = subprocess.run(["pgrep", "-f", f"remote-debugging-port={CHROME_PORT}"],
                             capture_output=True, text=True).stdout.split()[:1]
        if pid:
            subprocess.run(["osascript", "-e", 'tell application "System Events" to set frontmost of '
                            f'(first process whose unix id is {pid[0]}) to true'], capture_output=True, timeout=3)
    except Exception as e:  # noqa: BLE001
        print(f"show failed: {e!r}", flush=True)

class Session:
    def __init__(self, url, goal, min_conf, max_steps):
        self.id = uuid.uuid4().hex[:6]
        self.goal = goal
        self.min_conf = min_conf
        self.max_steps = max_steps
        self.lock = threading.Lock()
        self.log = []
        self.total = 0
        self.agent = Agent(url, goal + SAFETY)
        if os.environ.get("JEVDUO_SHOW", "1") == "1":
            show(self.agent.browser.target)

    # ---- helpers -------------------------------------------------------
    @property
    def state(self):
        return self.agent.state

    def targets(self):
        return action_space(self.state["page"]["actions"])

    def page_brief(self, text_chars=600, max_elements=40):
        page = self.state["page"]
        elements, _, _ = self.targets()
        lines = []
        for e in elements[:max_elements]:
            ops = "/".join(o[0] for o in e["operations"])  # C T S
            val = f" ={e['value']!r}" if e.get("value") else ""
            lines.append(f"  [{e['index']}] {e['label'][:70]}{val} ({ops})")
            for o in e.get("options", [])[:8]:
                lines.append(f"      select {o['index']}: {o['value'][:40]}")
        more = f"\n  … {len(elements) - max_elements} more (jev look {self.id} --all)" if len(elements) > max_elements else ""
        text = re.sub(r"\s+", " ", page.get("text") or "")[:text_chars]
        return f"page: {page['title'][:80]} | {page['url'][:120]}\ntext: {text}\nelements:\n" + "\n".join(lines) + more

    def candidates(self, decision):
        page_actions = {a["id"]: a for a in self.state["page"]["actions"]}
        ops = sorted(decision["operation_probabilities"].items(), key=lambda kv: -kv[1])[:3]
        out = ["jev's options: " + ", ".join(f"{k} {v:.2f}" for k, v in ops)]
        if decision.get("target_probabilities"):
            _, targets, _ = self.targets()
            group = targets.get(decision["operation"], {})
            tops = sorted(decision["target_probabilities"].items(), key=lambda kv: -kv[1])[:3]
            out.append("targets: " + ", ".join(
                f"[{i}] {group[i]['label'][:40] if i in group else '?'} {p:.2f}" for i, p in tops))
        return "\n".join(out)

    def act(self, decision):
        """Execute a decision through jev's own act path (freshness checks, history)."""
        self.state["decision"] = decision
        self.state["started_at"] = self.state["started_at"] or time.perf_counter()
        self.agent.command("act", {"fingerprint": self.state["page"]["fingerprint"]})
        h = self.state["history"][-1]
        typed = f" ← {h['text']!r} (text {h['text_latency_ms']}ms)" if h.get("text") else ""
        step_ms = h["elapsed_ms"] - (self.state["history"][-2]["elapsed_ms"] if len(self.state["history"]) > 1 else 0)
        self.log.append(f"{len(self.log) + 1}. {h['kind'].upper()} {h['action'][:60]}{typed}"
                        f" (p={h['probability']:.2f}, jev {h['latency_ms']}ms, step {step_ms}ms)")

    # ---- the fast loop ---------------------------------------------------
    def run(self):
        """Drive until done, or until jev needs Claude. Returns (status, reason, started)."""
        started = time.perf_counter()
        steps = stale = relooks = 0
        while True:
            if steps >= self.max_steps or self.total >= 60:
                return "ask", f"step budget used ({self.total} actions this session); confirm progress or give a hint", started
            if stale >= 5:
                return "ask", "the page keeps changing under jev (5 stale reads in a row)", started
            try:
                self.state["status"] = "ready"
                self.agent.command("predict", {})
            except StalePage:
                stale += 1; continue
            except ValueError as e:
                return "ask", f"jev stopped: {e}", started
            d = self.state["decision"]
            choice = d["choice"]
            op_p = d["operation_probabilities"].get(d["operation"], d["confidence"])
            tgt_p = d["probabilities"].get(choice, 1.0) if d.get("target") else 1.0
            # Boss man's rule: jev's top pick already beats every alternative, so act on it.
            # min_conf 0 (default) = never ask for confidence alone; raise it for extra caution.
            sure = op_p >= self.min_conf and tgt_p >= self.min_conf
            if (choice == "BLOCKED" or not sure) and relooks < 2:
                # Pages animate (menus closing, results loading). Look again before bothering Claude.
                relooks += 1
                time.sleep(0.5)
                self.state["page"] = self.state["browser"].observe(screenshot=False)
                continue
            if choice == "DONE":
                if sure:
                    self.state["status"] = "done"
                    self.log.append(f"{len(self.log) + 1}. DONE (conf {d['confidence']:.2f})")
                    return "done", "jev sees every requirement met. Verify before trusting it.", started
                return "ask", f"jev thinks it is done but is unsure (conf {d['confidence']:.2f}). Verify.\n" + self.candidates(d), started
            if choice == "BLOCKED":
                return "ask", "jev is blocked: no visible control makes progress.\n" + self.candidates(d), started
            if not sure:
                return "ask", f"jev is unsure (operation {op_p:.2f}, target {tgt_p:.2f}).\n" + self.candidates(d), started
            action = next(a for a in self.state["page"]["actions"] if a["id"] == choice)
            if RISKY.search(action["label"]):
                return "ask", f"jev reached a money/one-way control: {action['label'][:80]!r}. A human must press it.", started
            try:
                self.act(d)
            except StalePage:
                stale += 1; continue
            except (ValueError, RuntimeError) as e:
                if "text" in str(e).lower():
                    return "ask", f"jev needs a value for field {action['label'][:60]!r}. Reply: jev type {self.id} <idx> \"…\"", started
                return "ask", f"jev stopped: {e}", started
            steps += 1; self.total += 1; stale = relooks = 0
            if self.state["status"] == "blocked":  # jev's own 3-no-change loop guard
                return "ask", "last 3 actions changed nothing on the page.", started

    def report(self, status, reason, started):
        secs = time.perf_counter() - started
        body = [f"[{status.upper()}] session {self.id} · {secs:.1f}s · {len(self.state['history'])} actions",
                f"reason: {reason}"]
        body += self.log[-12:]
        if status != "done":
            body.append(self.page_brief())
            body.append(f"reply with: jev say {self.id} \"hint\" | jev click {self.id} <idx> | "
                        f"jev type {self.id} <idx> \"text\" | jev close {self.id}")
        else:
            body.append(self.page_brief(text_chars=900, max_elements=0).split("\nelements:")[0])
        return "\n".join(body)

    # ---- Claude's replies ----------------------------------------------
    def manual(self, op, index, text=None, label=None, tries=3):
        _, targets, _ = self.targets()
        key = {"click": "CLICK", "type": "TYPE_TEXT", "select": "SELECT"}[op]
        action = targets.get(key, {}).get(str(index))
        if label is not None and (not action or action["label"] != label):
            # the page moved under us: find the same control by its label
            index, action = next(((i, a) for i, a in targets.get(key, {}).items() if a["label"] == label),
                                 (index, None))
        if not action:
            raise ValueError(f"no {key} target [{index}] on this page")
        try:
            return self._manual(key, action, index, text)
        except StalePage:
            if tries <= 1:
                raise
            self.state["page"] = self.agent.browser.observe(screenshot=False)
            return self.manual(op, index, text, action["label"], tries - 1)

    def _manual(self, key, action, index, text):
        if RISKY.search(action["label"]):
            raise ValueError(f"refused: {action['label']!r} is a risky control; a human must press it")
        if text is not None:
            ctx = field_context(self.state["goal"], action, self.state["page"], self.state["history"])
            self.agent.pending_text = (ctx, text, {"model": "claude", "latency_ms": 0, "usage": {}})
        self.act({"choice": action["id"], "operation": key, "target": str(index), "confidence": 1.0,
                  "probabilities": {action["id"]: 1.0}, "latency_ms": 0, "usage": {}})

    def hint(self, text):
        self.state["goal"] = self.state["goal"] + f"\nAdvisor note (trusted, from the user's assistant): {text}"


def pick(question, options, context=""):
    keys = [str(i + 1) for i in range(len(options))]
    body = {"model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
            "state": {"context": context[:8000]},
            "questions": {"q": {"type": "choice", "criteria": dict(zip(keys, options)),
                                "instructions": {"question": question}}}}
    t = time.perf_counter()
    res = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    ans = res["answers"]["q"]
    ms = round((time.perf_counter() - t) * 1000)
    ranked = sorted(ans["probabilities"].items(), key=lambda kv: -kv[1])[:5]
    lines = [f"{options[int(ans['choice']) - 1]}", f"(conf {ans['confidence']:.2f}, {ms}ms)"]
    lines += [f"  {p:.2f}  {options[int(k) - 1][:100]}" for k, p in ranked]
    return "\n".join(lines)


def handle(cmd):
    op = cmd.get("op")
    if op == "pick":
        return pick(cmd["question"], cmd["options"], cmd.get("context", ""))
    if op == "web":
        t0 = time.perf_counter()
        s = Session(cmd["url"], cmd["goal"], float(cmd.get("min_conf", 0.0)), int(cmd.get("max_steps", 25)))
        s.log.append(f"0. open page {round((time.perf_counter() - t0) * 1000)}ms")
        SESSIONS[s.id] = s
        with s.lock:
            return s.report(*s.run())
    if op == "open":
        t0 = time.perf_counter()
        s = Session(cmd["url"], cmd.get("goal") or "Claude drives this page step by step.", 0.0, 0)
        SESSIONS[s.id] = s
        return f"[OPEN] session {s.id} · {time.perf_counter() - t0:.1f}s\n" + s.page_brief()
    if op == "close" and cmd.get("id") == "all":
        for sid in list(SESSIONS):
            SESSIONS.pop(sid).agent.close()
        return "closed all sessions"
    s = SESSIONS.get(cmd.get("id", ""))
    if not s:
        return f"no session {cmd.get('id')!r}. Live: {', '.join(SESSIONS) or 'none'}"
    with s.lock:
        if op == "look":
            return s.page_brief(text_chars=3000 if cmd.get("all") else 600, max_elements=400 if cmd.get("all") else 40)
        if op == "close":
            s.agent.close(); SESSIONS.pop(s.id, None)
            return f"closed {s.id}"
        if op == "screenshot":
            return "IMG:" + s.agent.browser.observe(screenshot=True)["screenshot"]
        if op in {"navigate", "scroll"}:
            b = s.agent.browser
            if op == "navigate":
                b.call("Page.navigate", url=cmd["url"])
                time.sleep(0.3)
                for _ in range(50):
                    try:
                        if b.evaluate("document.readyState") == "complete":
                            break
                    except (StalePage, RuntimeError):
                        pass
                    time.sleep(0.1)
            else:
                dy = {"down": 1, "up": -1}.get(cmd.get("direction", "down"), 1) * int(cmd.get("pixels", 800))
                b.evaluate(f"window.scrollBy(0,{dy})")
                time.sleep(0.15)
            s.state["page"] = b.observe(screenshot=False)
            return s.page_brief()
        started = time.perf_counter()
        if op == "say":
            s.hint(cmd["text"])
            s.max_steps = s.max_steps or 25  # a hint on an opened page hands it to jev
        elif op in {"click", "type", "select"}:
            try:
                s.manual(op, cmd["index"], cmd.get("text"))
            except (ValueError, RuntimeError) as e:
                return s.report("ask", f"your action failed: {e}", started)
        elif op == "go":
            pass
        else:
            return f"unknown op {op!r}"
        if not s.max_steps:
            return s.report("ok", "your action ran", started)
        if cmd.get("stop"):
            return s.report("ask", "paused after your action (--stop)", started)
        return s.report(*s.run())


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, text):
        b = text.encode()
        self.send_response(code); self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        self._send(200, "ok " + ",".join(SESSIONS)) if self.path == "/health" else self._send(404, "nf")

    def do_POST(self):
        try:
            cmd = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if cmd.get("op") not in ("close", "stop") and not os.environ.get("TYPESAFE_API_KEY"):
                return self._send(400, "[ERROR] jev needs a TypeSafe API key. Add TYPESAFE_API_KEY to .env.")
            self._send(200, handle(cmd))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send(500, f"[ERROR] {e}")

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    print(f"jevduo listening on 127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
