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

## Follow-up: `jev find` (search code by meaning)

Our history showed 424 "keyword hunts" (3+ searches in a row) across 704 Claude Code sessions: median 41 s, about 62 h in total. So we built `jev find "<question>" [dir]` and compared it with grep and with [jegrep](https://github.com/can1357/jegrep), a Rust tool built on the same jev model.

**Method.** The test app is a private 27-file, 10k-line Swift app. One agent wrote an answer key of questions: some with an obvious keyword, some phrased by behaviour with words that do not appear in the code. A separate fresh agent with no access to the key answered with grep, timed per question. Version 1 of `jev find` was tuned on a 10-question set. The final scores come from a **held-out set of 20** that was written afterwards and never used for tuning. The question files stay private because they quote that app's code.

### v1 → v2 (what fixed the misses)

| Miss cause in v1 | Fix in v2 |
|---|---|
| Functions split mid-body; a 5-line header chunk got picked | Whole declarations; doc comments stay attached; chunks under 8 lines merge forward |
| jev saw only the first 12 lines of a chunk | jev sees the signature plus every identifier and string used inside |
| One lane, one guess | A free grep lane (question words vs identifier parts) feeds candidates to jev, and grep's best 2 stay visible |
| Top 3 only | Top 5 plus 2 grep hits |

Tuning set (10): v1 got 6/10 top-1 and 9/10 found. v2 got 10/10 top-1.

### Held-out set (20 questions: 8 keyword, 12 behaviour)

| | Claude + grep | `jev find` v2 | jegrep 0.1.3 |
|---|---|---|---|
| Found at all | **20/20** | **20/20** | 16/20 |
| Right on the first pick | 20/20 | 17/20 | 16/20 |
| In the top 3 | – | 19/20 | 16/20 |
| Keyword questions, first pick | 8/8 | 6/8 | **8/8** |
| Behaviour questions, found | 12/12 | **12/12** | 8/12 |
| Lines to read to reach the hit (median) | – | **35** | 162 (often whole files) |
| Time per question | 7.9 s (1.9 tool calls) | **0.86 s** + one read | 1.16 s + one read |

**Verdict.**
- **v2 is the better jev search:** it found every answer, and its ranges are about 5× tighter than jegrep's.
- **Against Claude + grep on a small repo, the gain is modest.** One `jev find` plus one read comes to about 4–5 s, against 7.9 s for grep. That is roughly 1.6× faster, with 3 first-pick misses that still sat in the top 5.
- **Where it should matter more:** big repos, where grep hunts run long. Not yet tested.

Raw scores: [`bench/find_before_heldout.json`](bench/find_before_heldout.json), [`bench/find_questions_heldout_compare.json`](bench/find_questions_heldout_compare.json). Harness: [`bench/compare_find.py`](bench/compare_find.py) `<repo> <questions.json> [jegrep]`.

### Big repo: a private 1,967-file, 362k-line Flutter app (20 held-out questions)

Same method: an answer key written by one agent, a fresh grep agent that never saw the key, and no tuning on these questions. For this repo `jev find` gained a folder stage (jev's hard limit is about 250 options per question), a cache, and a filter for generated files.

| | Claude + grep | `jev find` v3 | jegrep 0.1.3 |
|---|---|---|---|
| Found at all | **20/20** | 16/20 | 12/20 |
| Right on the first pick | **20/20** | 10/20 | 12/20 |
| In the top 3 | – | 15/20 | 12/20 |
| Keyword questions, found | 8/8 | 8/8 | 8/8 (all first pick) |
| Behaviour questions, found | **12/12** | 8/12 | 4/12 |
| Lines to read to reach the hit (median) | – | 118 | 112 |
| Time per question | 9.7 s (2.5 tool calls, max 16 s) | 2.15 s + one read | 1.54 s + one read |

**Verdict: do not replace grep with a jev search.**
- **Grep did not get lost, even in a big repo:** it was right on all 20, with no answer taking more than 16 s.
- **Both jev tools miss 20–40 % at scale.** Almost all the misses are behaviour-phrased questions, which is exactly where a meaning-based search should shine.
- **Jev's confidence does not flag its misses,** so every miss costs a jev call + a wasted read + the grep hunt anyway. At a 20 % miss rate that roughly cancels the speed gain.
- **jegrep is the better keyword finder** (8/8 first pick). `jev find` is the better behaviour finder (8/12 against 4/12). Neither one is reliable enough to trust over grep.

**Caveat.** The 62 h of long hunts in our history did not show up in either test. An agent that knew the code wrote the questions, and even the behaviour questions keep strong words from the code. Real hunts start vaguer. That case is still untested.

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
