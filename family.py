"""What this family scrapes.

1. corestack-layers   CoRE Stack (core-stack.org) map layers per tehsil: every item of the public STAC catalogue
                      (s3://spatio-temporal-asset-catalog, CorestackCatalogs_merged_collection/tehsil_wise), data from the CoRE
                      Stack GeoServer (WFS GeoJSON / WCS GeoTIFF). About 56,000 layers in about 4,600 tehsils.
2. corestack-panindia CoRE Stack India-wide layers (PanIndiaCatalogs): files on Google Drive; Earth Engine-only layers as metadata.
3. corestack-gcp      Green Credit Programme registry (MoEFCC, www.moefcc-gcp.in): every land parcel in the public land inventory
                      with its detail, boundary (KML), payments, plantation targets, reported progress and site photos.
                      Personal and bank data that the site exposes are dropped (gcp.py, OWNERSHIP_KEEP).

Queues are built by setup_family.py from the live sources (the functions below), so a new Setup run adds what is new.
"""
import json
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

FAMILY = json.loads((Path(__file__).parent / "scrape.json").read_text())["family"]
UA = {"User-Agent": "s1d-labs scraper (https://github.com/s1d-labs)"}
STAC_BUCKET = "https://spatio-temporal-asset-catalog.s3.ap-south-1.amazonaws.com/"
GCP = "https://www.moefcc-gcp.in"
GEOSERVER = "geoserver.core-stack.org"


def _get(url, timeout=120):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def list_stac(prefix):
    """All object keys under prefix in the public STAC bucket (S3 ListObjectsV2, no credentials)."""
    keys, token = [], None
    ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    while True:
        url = f"{STAC_BUCKET}?list-type=2&max-keys=1000&prefix={urllib.parse.quote(prefix)}"
        if token:
            url += "&continuation-token=" + urllib.parse.quote(token, safe="")
        root = ElementTree.fromstring(_get(url))
        keys += [k.text for k in root.findall("s:Contents/s:Key", ns)]
        nxt = root.find("s:NextContinuationToken", ns)
        if nxt is None:
            return keys
        token = nxt.text


def is_item(key):
    """Pure: a STAC item JSON is <...>/<id>/<id>.json (collections and catalogues are not items)."""
    parts = key.split("/")
    return key.endswith(".json") and len(parts) >= 2 and parts[-1] == parts[-2] + ".json"


def layer_items():
    keys = list_stac("CorestackCatalogs_merged_collection/tehsil_wise/")
    return [{"key": k.rsplit("/", 1)[-1][:-5][:200], "payload": {"item": k, "expected": 1}} for k in keys if is_item(k)]


def panindia_items():
    return [{"key": k.rsplit("/", 1)[-1][:-5][:200], "payload": {"item": k, "expected": 1}}
            for k in list_stac("PanIndiaCatalogs/") if is_item(k)]


def gcp_items():
    """Every parcel of the public land inventory: approved/leased (/api/projects) and still available (/api/available-land)."""
    items, seen = [], set()
    for path in ("/api/projects?limit=50&offset={}", "/api/available-land?projectType=LAND_LEASED&limit=50&offset={}"):
        off = 0
        while True:
            page = json.loads(_get(GCP + path.format(off)))
            rows = page.get("rows") or []
            for row in rows:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                lo = row.get("LandOwnership") or {}
                row = {k: v for k, v in row.items() if k != "LandOwnership"}
                items.append({"key": row["id"], "payload": {"id": row["id"], "epid": lo.get("id"), "row": row, "expected": 1}})
            off += 50
            if not rows or off >= (page.get("count") or 0):
                break
    return items


PROJECTS = [
    {
        "name": f"{FAMILY}-gcp", "title": "Green Credit Programme registry: land parcels, payments, targets, progress",
        "task": "gcp", "target": "www.moefcc-gcp.in", "env": {}, "probe": f"{FAMILY}:moefcc-gcp",
        "unit_label": "parcel", "record_label": "parcel", "items": gcp_items, "max_workers": 1, "threads": 1, "delay_ms": 1000,
    },
    {
        "name": f"{FAMILY}-panindia", "title": "CoRE Stack India-wide layers (Google Drive files)",
        "task": "panindia", "target": "drive.usercontent.google.com", "env": {}, "probe": f"{FAMILY}:stac",
        "unit_label": "layer", "record_label": "layer", "items": panindia_items, "max_workers": 1, "threads": 1, "delay_ms": 2000,
    },
    {
        "name": f"{FAMILY}-layers", "title": "CoRE Stack map layers per tehsil (GeoServer)",
        "task": "layers", "target": GEOSERVER, "env": {}, "probe": f"{FAMILY}:geoserver",
        "unit_label": "layer", "record_label": "layer", "items": layer_items, "max_workers": 2, "threads": 1, "delay_ms": 1000,
    },
]

# projects kept out of the pool (name -> reason); setup_family.py takes them out
HELD = {}

PROBES = [
    {"name": f"{FAMILY}:geoserver", "url": f"https://{GEOSERVER}:8443/geoserver/web/", "expect": "GeoServer"},
    {"name": f"{FAMILY}:moefcc-gcp", "url": f"{GCP}/api/stats", "expect": "totalEntries"},
    {"name": f"{FAMILY}:stac", "url": STAC_BUCKET + "CorestackCatalogs_merged_collection/catalog.json", "expect": "corestack"},
]
