#!/usr/bin/env python3
"""Idea B: can jev pick the right chunk of a big file faster than rg?
usage: focus_probe.py <file> "<question>" <needle-that-marks-the-right-answer>"""
import os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for l in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
    if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
from jev_ultrafast.model import post_json
path, question, needle = sys.argv[1:4]
lines = open(path, errors="replace").read().splitlines()
starts = [0] + [i for i, l in enumerate(lines) if re.match(r"\s*(func|def|class|private func|override func|fileprivate func)\b", l)]
chunks = []
for a, b in zip(starts, starts[1:] + [len(lines)]):
    for s in range(a, b, 60): chunks.append((s, min(b, s + 60)))
crit = {str(i + 1): f"lines {a+1}-{b}: " + "\n".join(lines[a:a + 12]) for i, (a, b) in enumerate(chunks)}
t = time.time()
ans = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"],
                {"model": "jev-latest", "state": {"file": os.path.basename(path)},
                 "questions": {"q": {"type": "choice", "criteria": crit, "instructions": {"question": question}}}})["answers"]["q"]
jt = time.time() - t
a, b = chunks[int(ans["choice"]) - 1]
hit = any(needle in l for l in lines[a:b])
t = time.time(); rg = subprocess.run(["grep", "-nF", needle, path], capture_output=True, text=True).stdout; rt = time.time() - t
print(f"chunks={len(chunks)} jev picked lines {a+1}-{b} p={ans['probabilities'][ans['choice']]:.2f} correct={hit} {jt:.2f}s | grep {rt*1000:.0f}ms -> {rg.split(':')[0] or 'miss'}")
