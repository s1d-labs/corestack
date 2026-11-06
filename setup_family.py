"""Put this family's projects in the pool and register its health checks. Run by the Setup workflow (GitHub Actions: the hub
trusts this repo, s1d-labs/<family>, through its OIDC token). Safe to run again: queue items that exist are kept, new ones are added.

Optional env (workflow inputs): ONLY=<project names, comma-separated> sets up only those projects; MAX_ITEMS=<n> seeds only the
first n items of each queue (first pool trial). Empty = everything.
"""
import os

from scrape_hub_client import register_project, seed_queue, set_pool, set_probes

from family import FAMILY, HELD, PROBES, PROJECTS

ONLY = {x.strip() for x in os.environ.get("ONLY", "").split(",") if x.strip()}
MAX_ITEMS = int(os.environ.get("MAX_ITEMS") or 0)

print("health checks:", set_probes(FAMILY, PROBES))
for p in PROJECTS:
    if ONLY and p["name"] not in ONLY:
        continue
    if p["name"] in HELD:
        print(p["name"], "held:", HELD[p["name"]], set_pool(p["name"], FAMILY, p["task"], target=p["target"], pool=False))
        continue
    items = p["items"]()
    total = len(items)
    if MAX_ITEMS:
        items = items[:MAX_ITEMS]
    register_project(p["name"], title=p["title"], unit_label=p["unit_label"], record_label=p["record_label"],
                     expected_units=total, expected_records=total)
    set_pool(p["name"], FAMILY, p["task"], target=p["target"], env=p["env"], probe=p["probe"],
             max_workers=p.get("max_workers"), threads=p.get("threads", 1), delay_ms=p.get("delay_ms", 1000))
    for i in range(0, len(items), 2000):
        print(p["name"], f"queue {i}-{i + len(items[i:i + 2000])} of {total}:", seed_queue(p["name"], items[i:i + 2000]))
