#!/usr/bin/env python3
"""Score a grep-baseline log (START/END lines) against an answer key.
usage: score_before.py <log> <questions.json> <repo-root> <out.json>"""
import json, re, sys
log, qfile, root, out = sys.argv[1:5]
qs = {q["id"]: q for q in json.load(open(qfile))}
st, rows = {}, []
for l in open(log):
    p = l.split()
    if not p:
        continue
    if p[0] == "START":
        st[int(p[1])] = float(p[2])
    elif p[0] == "END":
        i = int(p[1]); q = qs[i]; m = re.search(r"(\S+?):(\d+)-(\d+)", l); c = re.search(r"calls=(\d+)", l); ok = False
        if m:
            f, a, b = m.group(1), int(m.group(2)), int(m.group(3))
            lines = open(f"{root}/{q['file']}", errors="replace").read().splitlines()
            nl = [k + 1 for k, x in enumerate(lines) if q["needle"] in x and q["start"] <= k + 1 <= q["end"]]
            ok = (f == q["file"] or f.endswith("/" + q["file"]) or q["file"].endswith("/" + f)) and any(a <= n <= b for n in nl)
        rows.append({"id": i, "kind": q["kind"], "ok": ok, "seconds": round(float(p[2]) - st[i], 1),
                     "calls": int(c.group(1)) if c else None})
for k in ("keyword", "behaviour", None):
    r = [x for x in rows if k is None or x["kind"] == k]
    s = sorted(x["seconds"] for x in r)
    print(f"{k or 'all':9}: correct {sum(x['ok'] for x in r)}/{len(r)} avg {sum(s)/len(s):.1f}s median {s[len(s)//2]:.1f}s "
          f"max {s[-1]:.1f}s calls {sum(x['calls'] or 0 for x in r)/len(r):.1f}")
json.dump(rows, open(out, "w"), indent=1)
