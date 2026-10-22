#!/usr/bin/env python3
"""Build guide files for many lines with a worker pool. Each worker owns whole line files.

  python3 run_lines.py --shard 0 --nshards 6 [--list lines.txt]
"""
import json, os, sys, time, argparse, random
sys.path.insert(0, "/data/work")
from build_line import main as build_one

ap = argparse.ArgumentParser()
ap.add_argument("--shard", type=int, default=0)
ap.add_argument("--nshards", type=int, default=1)
ap.add_argument("--list", default="/data/work/lines.txt")
a = ap.parse_args()
lines = [x.strip() for x in open(a.list, encoding="utf-8") if x.strip()]
mine = [l for i, l in enumerate(lines) if i % a.nshards == a.shard]
print(f"shard {a.shard}/{a.nshards}: {len(mine)} lines", flush=True)
ok = fail = 0
t0 = time.time()
for i, l in enumerate(mine):
    try:
        rc = build_one(l)
        ok += rc == 0
        fail += rc != 0
    except Exception as e:
        fail += 1
        print(f"FAIL {l}: {type(e).__name__} {e}", flush=True)
    if i % 20 == 0:
        print(f"  shard{a.shard} {i}/{len(mine)} ok={ok} fail={fail} {time.time()-t0:.0f}s", flush=True)
print(f"shard {a.shard} done ok={ok} fail={fail} {time.time()-t0:.0f}s", flush=True)
