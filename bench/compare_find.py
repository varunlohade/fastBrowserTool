#!/usr/bin/env python3
"""jev find vs jegrep on the same answer key. Hit = needle line inside a returned range.
Also reports how many lines Claude must read to reach the first hit (read cost).
usage: compare_find.py <repo> <questions.json> [path/to/jegrep]"""
import json, os, re, subprocess, sys, time
root, qfile = os.path.abspath(sys.argv[1]), sys.argv[2]
ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
for l in open(ENV) if os.path.exists(ENV) else []:  # jegrep reads the key from the environment
    if "=" in l and not l.startswith("#"):
        k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
JEGREP = sys.argv[3] if len(sys.argv) > 3 else "jegrep"
qs = json.load(open(qfile))

def jevfind(q):
    out = subprocess.run(["jev", "find", q, root], capture_output=True, text=True).stdout
    return [(m.group(1), [(int(m.group(2)), int(m.group(3)))]) for m in
            re.finditer(r"^\s+(?:[\d.]+|grep)\s+(\S+?):(\d+)-(\d+)", out, re.M)]

def jegrep(q):
    out = subprocess.run([JEGREP, q, root, "--only", "typesafe", "--compact"], capture_output=True, text=True).stdout
    rows = []
    for l in out.splitlines():
        p = l.split("\t")
        if len(p) == 3 and not l.startswith("#"):
            spans = [tuple(map(int, s.split("-"))) for s in p[2].split(",") if re.match(r"\d+-\d+", s)]
            rows.append((p[0], spans or [(1, 10**6)]))
    return list(reversed(rows))  # jegrep prints weakest first

res = {"jev find": [], "jegrep": []}
for q in qs:
    lines = open(os.path.join(root, q["file"]), errors="replace").read().splitlines()
    needle = [i + 1 for i, l in enumerate(lines) if q["needle"] in l and q["start"] <= i + 1 <= q["end"]]
    for name, fn in (("jev find", jevfind), ("jegrep", jegrep)):
        t = time.time(); hits = fn(q["question"]); secs = time.time() - t
        rank, read = None, 0
        for i, (f, spans) in enumerate(hits):
            size = sum(min(b, len(lines) if f == q["file"] else b) - a + 1 for a, b in spans)
            read += min(size, 2000)
            if f == q["file"] and any(a <= n <= b for a, b in spans for n in needle):
                rank = i + 1; break
        res[name].append({"id": q["id"], "kind": q["kind"], "rank": rank, "read_lines": read if rank else None, "s": round(secs, 2)})
    a, b = res["jev find"][-1], res["jegrep"][-1]
    print(f"#{q['id']:3} {q['kind']:9} jev find rank={a['rank']} read={a['read_lines']} {a['s']}s | jegrep rank={b['rank']} read={b['read_lines']} {b['s']}s")
for name, rows in res.items():
    for kind in ("keyword", "behaviour", None):
        r = [x for x in rows if kind is None or x["kind"] == kind]
        found = [x for x in r if x["rank"]]
        print(f"{name:8} {kind or 'all':9}: top1 {sum(x['rank'] == 1 for x in r)}/{len(r)} top3 {sum(bool(x['rank']) and x['rank'] <= 3 for x in r)}/{len(r)} "
              f"found {len(found)}/{len(r)} · median read {sorted(x['read_lines'] for x in found)[len(found)//2] if found else '-'} lines · avg {sum(x['s'] for x in r)/len(r):.2f}s")
json.dump(res, open(qfile.replace(".json", "_compare.json"), "w"), indent=1)
