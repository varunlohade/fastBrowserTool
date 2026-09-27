#!/bin/sh
# Idea E: N jev agents at once vs one after another. Same three read-only goals.
G1='https://en.wikipedia.org/wiki/Main_Page|Search Wikipedia for Ada Lovelace and open her article'
G2='https://en.wikipedia.org/wiki/Main_Page|Search Wikipedia for Alan Turing and open his article'
G3='https://en.wikipedia.org/wiki/Main_Page|Search Wikipedia for Grace Hopper and open her article'
run() { url="${1%%|*}"; goal="${1#*|}"; jev web "$url" "$goal" | head -1; }
t=$(date +%s.%N); for g in "$G1" "$G2" "$G3"; do run "$g"; done
echo "sequential: $(python3 -c "import time;print(round(time.time()-$t,1))")s"
t=$(date +%s.%N); run "$G1" & run "$G2" & run "$G3" & wait
echo "parallel:   $(python3 -c "import time;print(round(time.time()-$t,1))")s"
