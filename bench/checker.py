#!/usr/bin/env python3
"""Idea C: jev as pass/fail checker vs a plain keyword check. Page texts are real ones from this session's runs."""
import json, subprocess, time
CASES = [  # (goal, page text, expected)
 ("Flight results Goa->Ahmedabad Oct 10 visible", "Flight search 1 Filters All filters Stops Airlines Search results 10 results returned. Best Cheapest from ₹6,738 Top flights 7:10 AM – 4:20 PM IndiGo 9 hr 10 min 1 stop BOM ₹6,738", "PASS"),
 ("Flight results Goa->Ahmedabad Oct 10 visible", "Reset September 27 ₹16.5K 28 ₹11K 29 ₹9,000 October 1 ₹7,940 2 ₹5,840 10 ₹6,740 Underlined prices indicate cheapest compared with other prices shown Done", "FAIL_NOT_SUBMITTED"),
 ("Flight results Goa->Ahmedabad Oct 10 visible", "Explore Flights Hotels Vacation rentals Sign in Flights Explore Flexible? Discover the best flight deals with AI Find cheap flights from India to anywhere Goa New Delhi Mumbai", "FAIL_WRONG_PAGE"),
 ("Wikipedia article about Ada Lovelace is open", "Ada Lovelace Article Talk Read View source From Wikipedia, the free encyclopedia Augusta Ada King, Countess of Lovelace (née Byron; 10 December 1815 – 27 November 1852)", "PASS"),
 ("Wikipedia article about Ada Lovelace is open", "Welcome to Wikipedia, the free encyclopedia that anyone can edit. From today's featured article Kevin O'Halloran was an Australian freestyle swimmer In the news", "FAIL_WRONG_PAGE"),
 ("Wikipedia article about Ada Lovelace is open", "Search results Ada Lovelace (microarchitecture) Ada Lovelace Day Ada Lovelace Institute Results 1 – 20 of 4,512", "FAIL_NOT_SUBMITTED"),
 ("Settings > Camera screen is open", "heading|Camera button|Record Video button|Record Slo-mo button|Formats switch|Grid", "PASS"),
 ("Settings > Camera screen is open", "heading|Home Screen & App Library button|App Library switch|Show in App Library", "FAIL_WRONG_PAGE"),
]
OPTS = ["PASS: the goal is visibly met", "FAIL_NOT_SUBMITTED: in progress, e.g. a picker or suggestion list is open, or results are not yet the final page", "FAIL_WRONG_PAGE: this is a different page"]
KEYWORD = {"Flight": "results returned", "Wikipedia": "Countess of Lovelace", "Settings": "Record Video"}
jev_ok = kw_ok = 0; times = []
for goal, text, want in CASES:
    t = time.time()
    out = subprocess.run(["jev", "pick", f"Goal: {goal}. Page text: {text}. Which verdict is right?", *OPTS], capture_output=True, text=True).stdout
    times.append(time.time() - t)
    got = out.split(":")[0].strip()
    kw = "PASS" if KEYWORD[goal.split()[0]] in text else "FAIL"
    jev_ok += got == want; kw_ok += (kw == "PASS") == (want == "PASS")
    print(f"{want:20} jev={got:20} kw={kw:5} {times[-1]:.2f}s")
print(json.dumps({"cases": len(CASES), "jev_correct": jev_ok, "keyword_passfail_correct": kw_ok,
                  "jev_avg_s": round(sum(times) / len(times), 2), "jev_max_s": round(max(times), 2)}))
