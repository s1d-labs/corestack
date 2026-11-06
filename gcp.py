"""Task `gcp`: one Green Credit Programme (MoEFCC) registry land parcel per queue item.

Payload: {"id": "<project id>", "epid": "<land ownership id or null>", "row": {<row of the public list>}}.
Public endpoints of www.moefcc-gcp.in (no login): /api/project/<id>, /api/download-kml/<id>, /api/land-ownership/<epid>,
/api/monitoring-target?landOwnershipEpid=, /api/progress-history?targetId=, /api/progress-images?progressId=.

PRIVACY: /api/land-ownership/<epid> also returns bank account numbers, IFSC codes, the participant's e-mail, phone and address,
and the names of the officials who handled queries. This task keeps ONLY the fields listed in OWNERSHIP_KEEP (allow-list), so
none of that is ever stored, uploaded or recorded. Payment receipts and ownership documents are not downloaded.

Try one parcel without the hub:  python gcp.py --local <project id> [<epid>]
"""
import json
import re
import sys
import time

import requests

from common import LocalHub, Skip, get, run, sha256

SITE = "https://www.moefcc-gcp.in"
ORG_WORDS = re.compile(r"\b(limited|ltd|corporation|corp|company|co\.|foundation|trust|society|llp|pvt|private|bank|refinery|"
                       r"coalfields|ntpc|nhpc|industries|enterprises|technologies|association|council|board|authority|"
                       r"university|institute|federation|group|power|energy|oil|gas|petroleum|steel|cement|mining|"
                       r"railway|airport|port)\b", re.I)

OWNERSHIP_KEEP = {
    "id", "status", "totalArea", "areaUnit", "costEstimateDate", "demandNote", "amountPaid", "transactionDate", "transactionType",
    "remark", "paymentRemark", "snoRemark", "icfreCharges", "adminCharges", "ecoRestorationCost", "ecoRestorationCostTDS",
    "adminChargesTDS", "additionalComment", "additionalInfo", "treeCanopy", "netPlantationArea", "tentativeDateForPlantation",
    "created_at", "updated_at", "dprStatus", "utilization", "nonTradableCredit", "nonTransferableCredit", "adminSelectionRemark",
    "adminRejectRemark", "isEditable",
}
PLANNING_KEEP = {"pitSize", "totalNumberOfSeedlingToBePlanted", "treeSeedlingPacingX", "treeSeedlingPacingY", "description",
                 "remark", "created_at", "updated_at"}


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w-])(?:\+?91[\s-]?|0)?(?:[6-9]\d{9}|[6-9]\d{4}[\s-]\d{5})(?![\w-])")


def scrub(o):
    """Pure: blank e-mail addresses and Indian mobile numbers inside any text (officials type them into remarks and queries)."""
    if isinstance(o, str):
        return PHONE.sub("[phone removed]", EMAIL.sub("[e-mail removed]", o))
    if isinstance(o, list):
        return [scrub(v) for v in o]
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items()}
    return o


def entity_type(name):
    """Pure: 'organisation' when the participant's name looks like an organisation, else 'person_or_unknown' (never publish those)."""
    return "organisation" if name and ORG_WORDS.search(name) else "person_or_unknown"


def strip_url(u):
    """Pure: presigned S3 links expire and carry credentials in the query: keep only the object key."""
    return u.split("?", 1)[0] if isinstance(u, str) else u


def clean_project(d):
    """Pure: the project detail without expiring links (document/image keys are kept)."""
    out = {k: v for k, v in d.items() if k not in ("images", "documents")}
    out["image_keys"] = [strip_url(u).rsplit("/", 1)[-1] for u in d.get("images") or []]
    out["documents"] = [{"label": x.get("label"), "key": strip_url(x.get("url", "")).rsplit("/", 1)[-1]} for x in d.get("documents") or []]
    return scrub(out)


def clean_ownership(lo):
    """Pure: allow-list of /api/land-ownership fields. Everything personal or financial-identifying is dropped."""
    out = {k: lo.get(k) for k in OWNERSHIP_KEEP if k in lo}
    user = lo.get("user") or {}
    name = " ".join(x for x in (user.get("firstName"), user.get("lastName")) if x).strip() or None
    out["entity_type"] = entity_type(name)
    out["entity"] = name if out["entity_type"] == "organisation" else None
    pd = lo.get("projectDetail") or {}
    spec = pd.get("landSpecification") or {}
    addr = pd.get("address") or {}
    out["project"] = {"registrationNo": pd.get("registrationNo"), "status": pd.get("status"), "landArea": pd.get("landArea"),
                      "state": (addr.get("stateDetail") or {}).get("name"), "district": (addr.get("districtDetail") or {}).get("name"),
                      "division": spec.get("division"), "range": spec.get("range"), "beat": spec.get("beat"),
                      "totalArea": spec.get("totalArea"), "occupiedArea": spec.get("occupiedArea"),
                      "treeCanopyDensity": spec.get("treeCanopyDensity")}
    pp = lo.get("plantationPlanning") or {}
    out["plantation_planning"] = {k: pp.get(k) for k in PLANNING_KEEP if k in pp} or None
    out["queries"] = [{"query": q.get("query"), "status": q.get("status"), "created_at": q.get("created_at"),
                       "updated_at": q.get("updated_at"), "by_designation": (q.get("user") or {}).get("designation")}
                      for q in lo.get("landOwnerShipQuery") or []]
    out["document_types"] = sorted({x.get("docType") for x in lo.get("landOwnerShipDocument") or [] if x.get("docType")})
    out["site_image_keys"] = sorted({strip_url(x.get("imageUrl", "")).rsplit("/", 1)[-1] for x in
                                     (lo.get("geoTaggedImagesKeys") or []) + (lo.get("projectImage") or []) if x.get("imageUrl")})
    return scrub(out)


def site_image_urls(lo):
    """Pure: presigned links of the site photos (geotagged and project images) - not payment or ownership documents."""
    return sorted({x["imageUrl"] for x in (lo.get("geoTaggedImagesKeys") or []) + (lo.get("projectImage") or []) if x.get("imageUrl")})


def clean_monitoring(mt):
    """Pure: targets per year, seedlings and planning (no personal fields in this endpoint); nested lookups flattened."""
    d = (mt or {}).get("data") or {}
    targets = []
    for k, v in d.items():
        if k.endswith("Targets") or k == "otherNecessaryProvisions":
            for t in v or []:
                targets.append({"group": k, "id": t.get("id"), "year": t.get("year"), "financialYear": t.get("financialYear"),
                                "indicator": (t.get("indicator") or {}).get("name"), "activity": (t.get("activity") or {}).get("name"),
                                "item": t.get("perticularOfItem"), "unit": t.get("unit"), "numberOfUnits": t.get("numberOfUnits"),
                                "progressInUnits": t.get("progressInUnits"), "costPerUnit": t.get("costPerUnit"),
                                "costRequired": t.get("costRequired"), "costProgress": t.get("costProgress"),
                                "totalManDays": t.get("totalManDays"), "status": t.get("status"),
                                "created_at": t.get("created_at"), "updated_at": t.get("updated_at")})
    seedlings = [{k: s.get(k) for k in ("commonName", "botanicalName", "numberOfSeedlings", "numberOfPlantedSeedlings",
                                         "fromDate", "toDate", "created_at", "updated_at")} for s in d.get("seedlings") or []]
    return scrub({"status": d.get("status"), "totalCostRequired": d.get("totalCostRequired"), "targets": targets, "seedlings": seedlings,
            "plantation_planning": {k: (d.get("plantationPlanning") or {}).get(k) for k in PLANNING_KEEP} if d.get("plantationPlanning") else None})


def has_progress(t):
    def num(x):
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
    return num(t.get("progressInUnits")) > 0 or num(t.get("costProgress")) > 0


def api(hub, session, path):
    r = get(hub, session, SITE + path, timeout=60)
    if r is None:
        raise RuntimeError(f"{path} did not load")
    if r.status_code != 200:
        return None
    return r.json()


def image(hub, session, url):
    r = get(hub, session, url, target=False, timeout=120)
    if r is None or r.status_code != 200:
        hub.metric("image_failed")
        return None
    return r.content, r.headers.get("content-type", "application/octet-stream").split(";")[0]


def ext(ctype):
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}.get(ctype, ".bin")


def process(hub, session, item):
    p = item["payload"]
    pid, epid = p["id"], p.get("epid")
    base = f"gcp/{pid}"
    files = []
    detail = api(hub, session, f"/api/project/{pid}")
    if detail is None:
        raise Skip("project not found")
    files.append((f"{base}/project.json", json.dumps(clean_project(detail)).encode(), "application/json"))
    kml = get(hub, session, f"{SITE}/api/download-kml/{pid}", timeout=60)
    kml_sha = None
    if kml is not None and kml.status_code == 200 and b"<kml" in kml.content[:500]:
        files.append((f"{base}/land.kml", kml.content, "application/vnd.google-earth.kml+xml"))
        kml_sha = sha256(kml.content)
    for i, u in enumerate(detail.get("images") or []):       # registration photos of the land
        got = image(hub, session, u)
        if got:
            files.append((f"{base}/images/registration_{i}{ext(got[1])}", got[0], got[1]))
    rec = {"project_id": pid, "list_row": p.get("row"), "project": clean_project(detail), "kml_sha256": kml_sha,
           "ownership": None, "monitoring": None, "progress": []}
    if epid:
        lo = api(hub, session, f"/api/land-ownership/{epid}")
        if lo:
            rec["ownership"] = clean_ownership(lo)
            for u in site_image_urls(lo):
                got = image(hub, session, u)
                if got:
                    files.append((f"{base}/images/site_{strip_url(u).rsplit('/', 1)[-1]}{ext(got[1])}", got[0], got[1]))
        mt = api(hub, session, f"/api/monitoring-target?landOwnershipEpid={epid}")
        rec["monitoring"] = clean_monitoring(mt)
        for t in rec["monitoring"]["targets"]:
            if not has_progress(t):
                continue
            ph = api(hub, session, f"/api/progress-history?targetId={t['id']}")
            rows = (((ph or {}).get("data") or {}).get("progress") or {}).get("rows") or []
            for row in rows:
                entry = {k: row.get(k) for k in ("id", "targetId", "progressDate", "fromDate", "toDate", "quantityInNumber", "cost",
                                                  "totalManDays", "created_at", "updated_at")}
                pi = api(hub, session, f"/api/progress-images?progressId={row['id']}")
                entry["image_keys"] = []
                for im in (pi or {}).get("data") or []:
                    got = image(hub, session, im.get("url", ""))
                    if got:
                        key = im.get("key") or strip_url(im["url"]).rsplit("/", 1)[-1]
                        entry["image_keys"].append(key)
                        files.append((f"{base}/images/progress_{key}{ext(got[1])}", got[0], got[1]))
                rec["progress"].append(scrub(entry))
                time.sleep(0.3)
    files.append((f"{base}/record.json", json.dumps(rec).encode(), "application/json"))
    for i in range(0, len(files), 250):
        hub.upload_many(files[i:i + 250])
    hub.metric("images", sum(1 for f in files if "/images/" in f[0]))
    hub.record(pid, {**rec, "bucket_prefix": base, "files": len(files)})
    return 1


def local(pid, epid=None):
    hub = LocalHub()
    process(hub, requests.Session(), {"key": pid, "payload": {"id": pid, "epid": epid}})
    print(f"counters {hub.counts}", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        run(process)
