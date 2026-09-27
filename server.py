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
    def manual(self, op, index, text=None):
        _, targets, _ = self.targets()
        key = {"click": "CLICK", "type": "TYPE_TEXT", "select": "SELECT"}[op]
        action = targets.get(key, {}).get(str(index))
        if not action:
            raise ValueError(f"no {key} target [{index}] on this page")
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


CODE_EXT = {".swift", ".py", ".js", ".ts", ".tsx", ".jsx", ".dart", ".go", ".kt", ".java", ".rb", ".rs",
            ".c", ".cc", ".cpp", ".h", ".m", ".mm", ".cs", ".php", ".scala", ".sh"}
SKIP_DIRS = {".git", "node_modules", "build", "dist", ".venv", "venv", "Pods", ".dart_tool", "DerivedData", "__pycache__"}
DECL = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|fileprivate|internal|open|static|final|override|"
                  r"async|export|default|mutating|abstract|pub)\s+)*(?:func|fn|def|function|class|struct|enum|extension|"
                  r"protocol|interface|impl|trait)\b")

def code_files(root):
    import subprocess
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True, timeout=10).stdout.split("\n")
        files = [f for f in out if f]
    except Exception:
        files = []
    if not files:
        for d, dirs, names in os.walk(root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
            files += [os.path.relpath(os.path.join(d, n), root) for n in names]
    generated = re.compile(r"\.(g|freezed|gr|mocks|pb|pbenum|pbjson|pbserver)\.dart$|\.min\.js$|\.d\.ts$")
    return [f for f in files if os.path.splitext(f)[1] in CODE_EXT and not generated.search(f)
            and not any(p in SKIP_DIRS for p in f.split("/"))]

STOP = set("where what which when does do is are the a an of to in on for and or how app code that this it its from with by be as at user users when into get gets".split())

def split_ident(word):
    return [w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", word)]

def stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w

def chunks_of(lines, cap=150):
    """Whole declarations: doc comments stay with their function, tiny headers merge forward."""
    starts = sorted({0, *[i for i, l in enumerate(lines) if DECL.match(l)]})
    fixed = []
    for s in starts:  # pull leading comments / attributes into the chunk
        while s > 0 and re.match(r"\s*(//|@|#|/\*|\*)", lines[s - 1]):
            s -= 1
        fixed.append(s)
    starts = sorted(set(fixed))
    spans = list(zip(starts, starts[1:] + [len(lines)]))
    merged = []
    for a, b in spans:
        if merged and merged[-1][1] - merged[-1][0] < 8:   # header too small to stand alone
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    out = []
    for a, b in merged:
        for s in range(a, b, cap):
            out.append((a, s, min(b, s + cap)))  # (decl start, piece start, piece end)
    return out

def summary(f, lines, a, s, b):
    """What jev sees per chunk: signature + every identifier and string used inside."""
    sig = next((l.strip() for l in lines[a:b] if DECL.match(l)), lines[a].strip())[:120]
    body = "\n".join(lines[s:b])
    idents, seen = [], set()
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", body):
        if w not in seen and w.lower() not in STOP:
            seen.add(w); idents.append(w)
    strings = re.findall(r'"([^"\n]{4,40})"', body)[:6]
    text = f"{f}:{s + 1}-{b} {sig} | uses: {' '.join(idents[:45])}"
    if strings:
        text += " | text: " + " / ".join(strings)
    return text[:420], {t for w in seen for t in split_ident(w)} | {w.lower() for w in re.findall(r"[a-z]{4,}", " ".join(strings).lower())}

def ask(question, criteria, state=None):
    body = {"model": os.environ.get("TYPESAFE_MODEL", "jev-latest"), "state": state or {},
            "questions": {"q": {"type": "choice", "criteria": criteria, "instructions": {"question": question}}}}
    return post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)["answers"]["q"]

INDEX = {}  # path -> (mtime, lines, chunks); the daemon keeps it warm between searches

def indexed(root, files):
    out = {}
    for f in files:
        full = os.path.join(root, f)
        try:
            m = os.path.getmtime(full)
        except OSError:
            continue
        hit = INDEX.get(full)
        if not hit or hit[0] != m:
            lines = open(full, errors="replace").read().splitlines()
            hit = (m, lines, [(a, s, b, *summary(f, lines, a, s, b)) for a, s, b in chunks_of(lines)])
            INDEX[full] = hit
        out[f] = hit
    return out

def fit(criteria, limit=80_000):
    """Keep a jev request under its size limit by trimming each option evenly."""
    size = sum(len(v) for v in criteria.values())
    if size <= limit:
        return criteria
    cap = max(60, int(limit / max(len(criteria), 1)))
    return {k: v[:cap] for k, v in criteria.items()}

def top_keys(answer, n, cum_stop=0.97):
    ranked = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])
    out, cum = [], 0.0
    for key, p in ranked[:n]:
        out.append(key); cum += p
        if cum >= cum_stop:
            break
    return out

MAX_OPTIONS = 200  # jev rejects questions with more than ~250 options

def pick_folders(question, folders, n=6):
    """Pick likely folders. Too many? Group them by path prefix, pick groups first, then folders inside."""
    names = sorted(folders)
    if len(names) > MAX_OPTIONS:
        depth = max(len(d.split("/")) for d in names)
        while depth > 1 and len({"/".join(d.split("/")[:depth]) for d in names}) > MAX_OPTIONS:
            depth -= 1
        groups = {}
        for d in names:
            groups.setdefault("/".join(d.split("/")[:depth]), []).append(d)
        gnames = sorted(groups)[:MAX_OPTIONS]
        crit = {str(i + 1): f"{g}/: " + ", ".join(sorted({d[len(g):].strip("/").split("/")[0] or "." for d in groups[g]}))[:300]
                for i, g in enumerate(gnames)}
        a = ask(f"Which folder most likely contains the code that answers: {question}", fit(crit, 70_000))
        names = [d for key in top_keys(a, n, 0.95) for d in groups[gnames[int(key) - 1]]][:MAX_OPTIONS]
    crit = {str(i + 1): f"{d}: " + ", ".join(os.path.splitext(os.path.basename(x))[0] for x in folders[d])
            for i, d in enumerate(names)}
    a = ask(f"Which folder most likely contains the code that answers: {question}", fit(crit, 70_000))
    return [names[int(key) - 1] for key in top_keys(a, n, 0.95)]

def find(root, question, k=5, max_files=6, max_chunks=MAX_OPTIONS):
    """Search code by meaning. Recall first: grep and jev both nominate; jev makes the final pick.
    Big repos: grep + a folder pick shortlist files, jev picks files, then jev picks chunks."""
    t0 = time.perf_counter()
    files = code_files(root)
    if not files:
        return f"no code files under {root}"
    idx = indexed(root, files)
    files = [f for f in files if f in idx]
    t_index = time.perf_counter()
    qwords = {stem(w) for w in re.findall(r"[a-z]{3,}", question.lower()) if w not in STOP}
    def gscore(tokens):
        return sum(any(t.startswith(q) or q.startswith(t) and len(t) >= 4 for t in tokens) for q in qwords)
    graded = sorted(((gscore(c[4]), f, c) for f in files for c in idx[f][2]), key=lambda x: -x[0])
    grep_hits = [(f, c) for sc, f, c in graded[:8] if sc > 0]
    decls = {f: "; ".join(re.sub(r"\s+", " ", l.strip())[:60] for l in idx[f][1] if DECL.match(l)) for f in files}
    # Shortlist files. Small repos: all of them. Big repos: grep's best files + jev's folder pick.
    if len(files) <= 120:
        shortlist = list(files)
    else:
        by_file = {}
        for sc, f, c in graded:
            if sc > 0 and len(by_file) < 40:
                by_file.setdefault(f, sc)
        folders = {}
        for f in files:
            folders.setdefault(os.path.dirname(f) or ".", []).append(f)
        picked = [f for d in pick_folders(question, folders) for f in folders[d]]
        shortlist = list(dict.fromkeys(list(by_file) + picked))[:MAX_OPTIONS]
    t_short = time.perf_counter()
    if len(shortlist) <= max_files:
        top_files = shortlist
    else:
        crit = {str(i + 1): f"{f}: {decls[f]}" for i, f in enumerate(shortlist)}
        a = ask(f"Which source file most likely contains the code that answers: {question}", fit(crit))
        top_files = [shortlist[int(key) - 1] for key in top_keys(a, max_files)]
    t1 = time.perf_counter()
    cands, seen = [], set()
    for f, c in grep_hits + [(f, c) for f in top_files for c in idx[f][2]]:
        if (f, c[1]) not in seen:
            seen.add((f, c[1])); cands.append((f, c))
    cands = cands[:max_chunks]  # never above jev's option limit
    a = ask(f"Which code chunk answers: {question}", fit({str(i + 1): c[3] for i, (f, c) in enumerate(cands)}))
    ranked = sorted(a["probabilities"].items(), key=lambda kv: -kv[1])[:k]
    t2 = time.perf_counter()
    picks = [cands[int(key) - 1] + (p,) for key, p in ranked]
    top = {(f, c[1]) for f, c, _ in picks}
    extra = [(f, c) for f, c in grep_hits[:2] if (f, c[1]) not in top]
    ms = lambda a_, b_: round((b_ - a_) * 1000)
    lines = [f"{question}  ({len(files)} files → {len(shortlist)} → {len(top_files)} + grep → {len(cands)} chunks; "
             f"{ms(t0, t2)}ms = index {ms(t0, t_index)} + shortlist {ms(t_index, t_short)} + files {ms(t_short, t1)} + chunks {ms(t1, t2)})"]
    for f, c, p in picks:
        lines.append(f"  {p:.2f}  {f}:{c[1] + 1}-{c[2]}  {c[3].split(' | ')[0].split(' ', 1)[-1][:80]}")
    for f, c in extra:
        lines.append(f"  grep  {f}:{c[1] + 1}-{c[2]}  {c[3].split(' | ')[0].split(' ', 1)[-1][:80]}")
    return "\n".join(lines)


def handle(cmd):
    op = cmd.get("op")
    if op == "find":
        return find(cmd["root"], cmd["question"], int(cmd.get("k", 5)))
    if op == "pick":
        return pick(cmd["question"], cmd["options"], cmd.get("context", ""))
    if op == "web":
        t0 = time.perf_counter()
        s = Session(cmd["url"], cmd["goal"], float(cmd.get("min_conf", 0.0)), int(cmd.get("max_steps", 25)))
        s.log.append(f"0. open page {round((time.perf_counter() - t0) * 1000)}ms")
        SESSIONS[s.id] = s
        with s.lock:
            return s.report(*s.run())
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
        started = time.perf_counter()
        if op == "say":
            s.hint(cmd["text"])
        elif op in {"click", "type", "select"}:
            try:
                s.manual(op, cmd["index"], cmd.get("text"))
            except (ValueError, RuntimeError) as e:
                return s.report("ask", f"your action failed: {e}", started)
        elif op == "go":
            pass
        else:
            return f"unknown op {op!r}"
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
            self._send(200, handle(cmd))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send(500, f"[ERROR] {e}")

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    print(f"jevduo listening on 127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
