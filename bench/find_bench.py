#!/usr/bin/env python3
"""Score `jev find` on bench/find_questions.json.
A hit = the needle's line falls inside a returned range. Reports top-1 and top-3 hits and time.
usage: find_bench.py <repo-root>"""
import json, os, re, subprocess, sys, time
root = os.path.abspath(sys.argv[1])
qfile = sys.argv[2] if len(sys.argv) > 2 else "find_questions.json"
qs = json.load(open(os.path.join(os.path.dirname(__file__), qfile)))
rows = []
for q in qs:
    t = time.time()
    out = subprocess.run(["jev", "find", q["question"], root], capture_output=True, text=True).stdout
    secs = time.time() - t
    lines = open(os.path.join(root, q["file"]), errors="replace").read().splitlines()
    needle_lines = [i + 1 for i, l in enumerate(lines) if q["needle"] in l and q["start"] <= i + 1 <= q["end"]]
    hits = []
    for m in re.finditer(r"^\s+(?:[\d.]+|grep)\s+(\S+?):(\d+)-(\d+)", out, re.M):
        f, a, b = m.group(1), int(m.group(2)), int(m.group(3))
        hits.append(f == q["file"] and any(a <= n <= b for n in needle_lines))
    rank = hits.index(True) + 1 if True in hits else None
    rows.append({"id": q["id"], "kind": q["kind"], "rank": rank, "seconds": round(secs, 2)})
    print(f"#{q['id']:2} {q['kind']:9} rank={rank} {secs:.2f}s  {q['question'][:70]}")
for kind in ("keyword", "behaviour", None):
    r = [x for x in rows if kind is None or x["kind"] == kind]
    print(f"{kind or 'all':9}: top1 {sum(x['rank'] == 1 for x in r)}/{len(r)}  top3 {sum(x['rank'] is not None for x in r)}/{len(r)}"
          f"  avg {sum(x['seconds'] for x in r) / len(r):.2f}s")
json.dump(rows, open(os.path.join(os.path.dirname(__file__), qfile.replace("questions", "after")), "w"), indent=1)
