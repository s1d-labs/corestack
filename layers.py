"""Task `layers`: one CoRE Stack map layer per queue item.

Payload: {"item": "<path of the STAC item JSON in the public bucket>"}. The task reads the STAC item (public S3), downloads its
`data` asset from the CoRE Stack GeoServer (WFS GeoJSON for vectors, WCS GeoTIFF for rasters), keeps the file and the STAC item in
the family's bucket and stores one record per layer (where it is, what it covers, size, checksum, feature count, columns).

Try one item without the hub:
  python layers.py --local CorestackCatalogs_merged_collection/tehsil_wise/jharkhand/ranchi/angara/<id>/<id>.json
"""
import gzip
import json
import sys
from urllib.parse import urlparse

import requests

from common import LocalHub, Skip, get, run, sha256

STAC = "https://spatio-temporal-asset-catalog.s3.ap-south-1.amazonaws.com/"
GEOSERVER_HOST = "geoserver.core-stack.org"
MAX_BYTES = 2_000_000_000


def place(item_path):
    """(scope, state, district, tehsil) from the STAC item path."""
    parts = item_path.split("/")
    if len(parts) >= 7 and parts[1] == "tehsil_wise":
        return "tehsil", parts[2], parts[3], parts[4]
    return "pan_india", None, None, None


def describe(item_path, item):
    """Pure: what the STAC item says about the layer and where its data is."""
    scope, state, district, tehsil = place(item_path)
    props = item.get("properties", {})
    assets = item.get("assets", {})
    data = assets.get("data") or next((a for a in assets.values() if "data" in (a.get("roles") or [])), None)
    href = (data or {}).get("href", "")
    layer = item["id"]
    if tehsil:
        prefix = f"{state}_{district}_{tehsil}_"
        layer = layer[len(prefix):] if layer.startswith(prefix) else layer
    kind = "raster" if ("wcs" in href.lower() or "geotiff" in (data or {}).get("type", "")) else "vector"
    return {
        "scope": scope, "state": state, "district": district, "tehsil": tehsil, "layer": layer,
        "title": props.get("title"), "description": (props.get("description") or "")[:2000],
        "start_datetime": props.get("start_datetime"), "end_datetime": props.get("end_datetime"),
        "stac_datetime": props.get("datetime"), "gsd": props.get("gsd"), "keywords": props.get("keywords"),
        "columns": [c.get("name") for c in props.get("table:columns", []) if c.get("name") not in ("geom", "the_geom")] or None,
        "bbox": item.get("bbox"), "kind": kind, "source_url": href,
        "style_url": (assets.get("style") or {}).get("href"), "thumbnail_url": (assets.get("thumbnail") or {}).get("href"),
        "stac_item": STAC + item_path,
    }


def bucket_path(info, item_id, ext):
    if info["scope"] == "tehsil":
        return f"layers/{info['state']}/{info['district']}/{info['tehsil']}/{item_id}{ext}"
    return f"pan_india/{item_id}{ext}"


def check_body(kind, body):
    """Pure: (ok, n_features, reason). GeoServer answers errors as XML with status 200, so look at the bytes."""
    if kind == "raster":
        return (body[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"), None, body[:200].decode(errors="replace"))
    head = body[:200].lstrip()
    if not head.startswith(b"{"):
        return False, None, head.decode(errors="replace")
    try:
        doc = json.loads(body)
    except ValueError as e:
        return False, None, f"bad JSON: {e}"
    return True, len(doc.get("features") or []), ""


def process(hub, session, item):
    path = item["payload"]["item"]
    r = get(hub, session, STAC + path, target=False, timeout=60)
    if r is None or r.status_code != 200:
        raise RuntimeError("STAC item did not load")
    stac = r.json()
    info = describe(path, stac)
    href = info["source_url"]
    files = [(bucket_path(info, stac["id"], ".stac.json"), json.dumps(stac).encode(), "application/json")]
    if urlparse(href).hostname != GEOSERVER_HOST:
        # Google Drive / Earth Engine links: keep the metadata only (project corestack-panindia downloads the Drive files)
        hub.upload_many(files)
        hub.record(stac["id"], {**info, "downloaded": False, "reason": "data is not on the CoRE Stack GeoServer"})
        return 1
    r = get(hub, session, href, timeout=900, stream=True)
    if r is None:
        raise RuntimeError("GeoServer did not answer")
    if r.status_code in (404, 410):
        raise Skip(f"GeoServer {r.status_code} for {href}")
    body = bytearray()
    for chunk in r.iter_content(1 << 20):
        body += chunk
        if len(body) > MAX_BYTES:
            r.close()
            raise Skip(f"larger than {MAX_BYTES} bytes")
    body = bytes(body)
    ok, n_features, reason = check_body(info["kind"], body)
    if not ok:
        if "not exist" in reason.lower() or "unknown" in reason.lower() or "could not find" in reason.lower():
            raise Skip(f"GeoServer: {reason[:200]}")
        raise RuntimeError(f"GeoServer error: {reason[:150]}")
    if info["kind"] == "raster":
        files.append((bucket_path(info, stac["id"], ".tif"), body, "image/tiff"))
    else:
        files.append((bucket_path(info, stac["id"], ".geojson.gz"), gzip.compress(body, 6), "application/gzip"))
    up = hub.upload_many(files)
    hub.metric("layer_bytes", len(body))
    hub.record(stac["id"], {**info, "downloaded": True, "bucket_path": up[-1]["path"], "bytes": len(body),
                            "sha256": sha256(body), "features": n_features})
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
