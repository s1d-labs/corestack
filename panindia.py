"""Task `panindia`: one India-wide CoRE Stack layer per queue item (files the STAC catalogue links on Google Drive).

Payload: {"item": "<path of the STAC item JSON>"}. Downloads the Drive file (public link), gzips it into the family's bucket under
pan_india/<item id>/, stores one record per layer. Earth Engine-only layers are recorded as metadata (no download possible).

Try one without the hub:  python panindia.py --local PanIndiaCatalogs/<group>/<id>/<id>.json
"""
import gzip
import json
import re
import sys
import tempfile

import requests

from common import LocalHub, get, run
from layers import STAC, describe

DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download&confirm=t"


def drive_id(href):
    """Pure: the file id of a Google Drive link, or None."""
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([A-Za-z0-9_-]{10,})", href or "")
    return m.group(1) if m else None


def process(hub, session, item):
    path = item["payload"]["item"]
    r = get(hub, session, STAC + path, target=False, timeout=60)
    if r is None or r.status_code != 200:
        raise RuntimeError("STAC item did not load")
    stac = r.json()
    info = describe(path, stac)
    hrefs = [a.get("href", "") for a in stac.get("assets", {}).values()]
    fid = next((drive_id(h) for h in hrefs if drive_id(h)), None)
    info["all_links"] = hrefs
    base = f"pan_india/{stac['id']}"
    files = [(f"{base}/stac.json", json.dumps(stac).encode(), "application/json")]
    if not fid:
        hub.upload_many(files)
        hub.record(stac["id"], {**info, "downloaded": False, "reason": "no Google Drive file (Earth Engine only)"})
        return 1
    r = get(hub, session, DRIVE.format(fid), timeout=1800, stream=True)
    if r is None or r.status_code != 200:
        raise RuntimeError("Drive file did not load")
    name = re.search(r'filename="([^"]+)"', r.headers.get("content-disposition", ""))
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name.group(1) if name else f"{stac['id']}.bin")
    head = b""
    with tempfile.TemporaryFile() as tmp:
        with gzip.GzipFile(fileobj=tmp, mode="wb", compresslevel=6) as gz:
            size = 0
            for chunk in r.iter_content(4 << 20):
                head = head or chunk[:200]
                gz.write(chunk)
                size += len(chunk)
        if head.lstrip()[:15].lower().startswith(b"<!doctype html") or head.lstrip()[:5].lower() == b"<html":
            raise RuntimeError("Drive answered an HTML page (quota or permission)")
        tmp.seek(0)
        body = tmp.read()
    files.append((f"{base}/{name}.gz", body, "application/gzip"))
    up = hub.upload_many(files)
    hub.metric("layer_bytes", size)
    hub.record(stac["id"], {**info, "downloaded": True, "file_name": name, "bucket_path": up[-1]["path"], "bytes": size,
                            "gzip_bytes": len(body)})
    return 1


def local(path):
    hub = LocalHub()
    process(hub, requests.Session(), {"key": path, "payload": {"item": path}})
    print(f"counters {hub.counts}", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(sys.argv[2])
    else:
        run(process)
