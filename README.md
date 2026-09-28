# jevduo

**Claude thinks. jev clicks.** A two-way loop between Claude Code and [jev-ultrafast](https://github.com/browser-use/jev-ultrafast), so browser work stops costing a screenshot and a full model turn per click.

Claude hands jev a URL and a goal. jev drives Chrome at about 0.4 s per step, with no Claude tokens spent. jev stops and asks Claude only when:
- it is stuck,
- it needs a value it can't work out, or
- it reaches a pay, send, submit or delete control (only a human presses those).

Claude answers in one line, and jev carries on.

## Results

| Task | Claude alone | Claude + jev | Speedup |
|---|---|---|---|
| Wikipedia: search and open an article | 22.3 s · 2 screenshots | **3.7 s** · 0 screenshots | **6.0×** |
| Google Flights: one-way search to results | 48.3 s · 5 screenshots | **9.1 s** · 0 screenshots | **5.3×** |
| 10-field form, stop before submit | 24.9 s · 9/10 fields | ≈ 24 s · 9/10 fields | ≈ 1× |
| 3 lookups at once (swarm) | 12.2 s | **3.6 s** | **3.4×** |

Two ideas were tested and **killed**: jev as a pass/fail checker (it gave one false PASS; a keyword check got 8/8) and jev as a file-chunk finder (`grep` is 50× faster).
Code search by meaning now lives in its own tool, [jevgrep](https://github.com/varunlohade/jevgrep). Here it found **20/20** held-out answers in 0.86 s, with ranges about 5× tighter than [jegrep](https://github.com/can1357/jegrep) (16/20). Against Claude + grep (20/20, 7.9 s) it is about 1.6× faster on a small repo. **On a 1,967-file repo it found only 16/20 (jegrep 12/20) while grep found 20/20, so grep stays the default.**
The full numbers, the failures and the method are in **[RESULTS.md](RESULTS.md)**.

## Install

You need a **TypeSafe API key**. jev is TypeSafe's model, and it does not work without one.

```sh
git clone https://github.com/varunlohade/fastBrowserTool && cd fastBrowserTool
uv sync
cp .env.example .env        # add your TypeSafe key and an OpenAI-compatible text-model key
ln -s "$PWD/bin/jev" ~/.local/bin/jev
```

jev starts its own Chrome with a separate, empty profile (port 9444). It never touches your logins, cookies or autofill. To use another browser, set `BU_CDP_WS`.

## Use

```sh
jev web <url> "<goal>"            # jev drives; prints [DONE] or [ASK] plus a short page brief
jev say <id> "<hint>"             # answer jev with a hint, then it continues
jev click <id> <idx>              # do one step for jev, then it continues
jev type <id> <idx> "<text>"      # give jev a field value it could not work out
jev look <id> [--all]             # see the page as jev sees it
jev close <id|all>                # close sessions
jev pick "<question>" opt1 opt2   # one fast multiple-choice call (~0.4 s)
jev stop                          # stop the daemon
```

A real exchange from the tests:

```
$ jev web https://httpbin.org/forms/post "Fill the pizza order form ... do not submit"
[ASK] session f0c7a9 · 8.3s · 10 actions
reason: last 3 actions changed nothing on the page.
...
$ jev say f0c7a9 "The delivery time field cannot be set here; skip it."
[DONE] session f0c7a9 · 0.4s · 10 actions
$ jev click f0c7a9 12
reason: your action failed: refused: 'Submit order' is a risky control; a human must press it
```

### Use it as Claude Code's browser tool (MCP)

```sh
claude mcp add --scope user fastbrowser -- python3 "$PWD/jev_mcp.py"
```

Claude then gets `browser_task` (jev drives toward a goal), plus `browser_open`, `browser_click`, `browser_type`, `browser_select`, `browser_navigate`, `browser_scroll`, `browser_look`, `browser_screenshot`, `browser_reply` and `browser_close`. Every action returns the new page as text, so Claude never needs a separate "read the page" call.

### Or tell Claude Code to use the CLI

Add this to your `CLAUDE.md`:

```md
For any website task, run `jev web <url> "<goal>"` first instead of driving the browser by screenshots.
On [ASK], answer with `jev say|click|type`. On [DONE], check the page brief before you report success.
Never press pay/send/submit/delete for the user.
```

## How it works

- `server.py` is a local daemon (127.0.0.1:8791). It holds live jev-ultrafast sessions, so Claude can talk to a session across many shell calls.
- Each step is one TypeSafe `systemone` call: jev picks the operation and the target together. A small text model writes the field values.
- Before jev asks Claude, it looks at the page again, up to 2 times, because pages animate.
- The risky-control guard is a label filter. It covers pay, purchase, buy, book now, reserve, place or submit order, checkout, subscribe, confirm, send, post, publish, delete, transfer and withdraw. Neither jev nor Claude can press those.

## Limits

- It does not see iframes, shadow DOM, canvas apps or `<input type=time>`.
- jev can report DONE too early, so Claude should check the brief.
- It is text only. For visual checks of your own UI, you still need screenshots.
- Each task was measured once. See [RESULTS.md](RESULTS.md) before you trust the numbers.

## License

MIT. Built on [jev-ultrafast](https://github.com/browser-use/jev-ultrafast) by browser-use and the TypeSafe jev model.
