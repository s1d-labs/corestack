"""Shared helpers for this family's tasks: polite HTTP with the hub's standard counters, a local stand-in hub, the main loop."""
import hashlib
import json
import os
import sys
import time

import requests

UA = "s1d-labs scraper (https://github.com/s1d-labs)"
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 300


class Skip(Exception):
    """The item can never succeed (the source says the file does not exist): mark it done with 0 records and list it."""


def get(hub, session, url, *, target=True, timeout=120, stream=False):
    """GET with the hub's counters. `target=False` for requests that do not hit the project's target host (not in n_http).
    Returns the Response for 200, else None."""
    t = time.time()
    try:
        r = session.get(url, timeout=timeout, headers={"User-Agent": UA}, stream=stream)
        if not stream:
            r.content  # read the body inside the timer
    except requests.RequestException:
        hub.metric("http_error" if target else "other_http_error")
        if target:
            hub.error()
        return None
    if target:
        hub.metric("n_http")
        hub.metric("t_http_s", time.time() - t)
    else:
        hub.metric("other_http")
    if r.status_code != 200:
        hub.metric(f"http_{r.status_code}" if target else f"other_http_{r.status_code}")
        if target and r.status_code in (403, 429, 503):
            hub.block()
        r.close()
        return None if r.status_code not in (404, 410) else r
    return r


def sha256(b):
    return hashlib.sha256(b).hexdigest()


class LocalHub:
    """Stand-in for HubClient so `--local` runs without the hub: records to stdout, uploads to ./local-out/, counters to stderr."""
    settings = {"threads": 1, "delay_ms": 0, "paused": False}

    def __init__(self):
        self.counts, self.records = {}, {}

    def metric(self, name, value=1):
        self.counts[name] = self.counts.get(name, 0) + value

    def error(self, n=1):
        self.metric("errors", n)

    def block(self, n=1):
        self.metric("blocked", n)

    def record(self, rid, data):
        self.records[rid] = data
        print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False)[:2000])

    def upload_many(self, files):
        out = []
        for path, body, ctype in files:
            p = os.path.join("local-out", path)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(body)
            out.append({"path": path, "bytes": len(body), "sha256": sha256(body)})
        return out

    def failure(self, kind, key, reason, **detail):
        print(f"failure {kind} {key}: {reason} {detail}", file=sys.stderr)


def run(process):
    """Main loop for the pool: claim one item at a time, call process(hub, session, item) -> number of records."""
    from scrape_hub_client import HubClient
    hub = HubClient(os.environ["SCRAPE_POOL_PROJECT"], job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")),
                    total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    session = requests.Session()
    try:
        while time.time() < DEADLINE:
            if hub.settings.get("paused"):
                time.sleep(30)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break
            item = items[0]
            try:
                n = process(hub, session, item)
                hub.complete([item["key"]], "done", fetched=n)
            except Skip as e:
                hub.failure("not_available", item["key"], str(e)[:300])
                hub.complete([item["key"]], "done", fetched=0)
            except Exception as e:  # noqa: BLE001 - one bad item must not stop the worker; the hub retries it later
                hub.complete([item["key"]], "failed", error=f"{type(e).__name__}: {e}"[:200])
            time.sleep(max(1.0, hub.settings.get("delay_ms", 0) / 1000))
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise
