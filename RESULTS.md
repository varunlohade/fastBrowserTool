# Results: Claude alone vs Claude + jev

Measured on 2026-09-27 on one MacBook: Chrome 154, jev-latest, `qwen/qwen3.5-flash-02-23` as the text helper, Claude Opus 5.5 in Claude Code.
Each task ran once per side (n=1). Treat the numbers as a first look, not a study.

## Headline

| Task | Claude alone | Claude + jev | Speedup |
|---|---|---|---|
| **T1** Wikipedia: search "Ada Lovelace", open the article | 22.3 s · 3 Claude turns · 2 screenshots | **3.7 s** · 1 turn · 0 screenshots | **6.0×** |
| **T2** Google Flights: one-way Goa → Ahmedabad, 10 Oct, show results | 48.3 s · 6 turns · 5 screenshots | **9.1 s** · 1 turn · 0 screenshots | **5.3×** |
| **T3** httpbin pizza form: fill 10 fields, do not submit | 24.9 s · 3 turns · 2 screenshots · 9/10 fields | 9.3 s of jev + 2 Claude replies (≈ 24 s) · 9/10 fields | **≈ 1×** |
| **Swarm:** 3 Wikipedia lookups | 12.2 s one after another | **3.6 s** all at once | **3.4×** |

A screenshot at 1120×780 costs Claude about 1.2k input tokens. jev sends Claude one text report of about 300–700 tokens, and only when the run ends or when jev needs help.

## What each idea scored

Five ideas went to five skeptical reviewer agents first. Then each idea got a real test.

| Idea | What it does | Test result | Verdict |
|---|---|---|---|
| **A. Browser loop** | jev clicks, Claude only answers questions | T1 6×, T2 5.3×, T3 no gain | **Keep.** This is the tool. |
| **E. Swarm** | several jev tabs at once | 3.4× on 3 read-only lookups | **Keep** for batches. |
| **D. Simulator thumbs** | AXe reads the iOS Simulator screen as text, jev picks, AXe taps | 1.8–2.5 s per step · 2/3 right · 250 tokens per screen vs ~1.5k for a screenshot | **Promising, not shipped.** It needs scrolling. |
| **C. Checker** | jev judges pass/fail from page text | 6/8 right, **1 false PASS** (the date picker was still open), 0.42 s | **Killed.** A keyword check got 8/8 in 0 ms. |
| **B. Focus** | jev picks the right chunk of a big file | 2/2 right, 0.45–0.55 s (the reviewer saw 2.5–21 s with bigger prompts) | **Killed.** `grep` takes 10 ms when you know a keyword. |

## What went wrong, so you don't repeat it

- **Time inputs are invisible to jev.** jev-ultrafast's page snapshot skips `<input type=time>`. jev looped 3 times on the next field, its loop guard fired, and it asked Claude. Claude by screenshot also failed that field: typed text doesn't land in a time input.
- **Stale reads.** jev once read the page while a menu was still closing and called itself blocked. Fix: jev now looks again (up to 2 times, 0.5 s apart) before it asks Claude.
- **The confidence gate was too strict.** jev's operation confidence runs low (0.3–0.5) even when its target pick is 0.85+. Now jev acts on its top pick. It asks Claude only when it is stuck, needs a value, or reaches a risky control.
- **A reasoning text model is slow.** With DeepSeek v4.1-flash in thinking mode, one run took 30.9 s. The same run with qwen3.5-flash and thinking off took 7.9 s. Each typed value took 0.4–1.0 s.
- **Off-screen targets.** In the Simulator test, "change the wallpaper" had no visible Wallpaper button. jev picked "Home Screen & App Library" at 0.81 instead of scrolling.
- **The safety guard works.** `jev click <id> <Submit order>` was refused. Money and one-way controls always go back to a human.

## How the "before" side was measured

The claude-in-chrome extension was not connected, so the baseline used [`bench/claude_drive.py`](bench/claude_drive.py). It does the same loop: screenshot → Claude reads it → click at x,y. It drives the same Chrome.

This baseline favours Claude in three ways:
1. Claude batched several clicks into one tool call when it could.
2. In T2, Claude already knew the page layout from an earlier attempt.
3. The real extension adds its own overhead per call.

Times on both sides start at Claude's first browser action and end when the goal is visibly met.

Raw data: [`bench/results.jsonl`](bench/results.jsonl).

## Rerun it

```sh
bench/swarm.sh                                   # idea E
.venv/bin/python bench/checker.py                # idea C
.venv/bin/python bench/focus_probe.py <file> "<question>" "<needle>"   # idea B
jev web "https://www.google.com/travel/flights?hl=en" "Search one-way flights from Goa to Ahmedabad on Saturday October 10 2026, 1 adult, economy. Done when the results list is visible."
```
