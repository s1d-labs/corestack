# corestack

Scraper family for the s1d-labs pool. It collects two public sources about land, water and trees in rural India.

## Projects

| Project | Source | One queue item | Size |
|---|---|---|---|
| `corestack-layers` | [CoRE Stack](https://core-stack.org) GeoServer, listed by its public STAC catalogue | one map layer of one tehsil | about 56,000 layers, 4,583 tehsils |
| `corestack-panindia` | CoRE Stack India-wide layers (Google Drive files) | one India-wide layer | 73 layers (28 files, the rest Earth Engine only) |
| `corestack-gcp` | [Green Credit Programme registry](https://www.moefcc-gcp.in/registry/land-inventory) (MoEFCC) | one land parcel | about 314 parcels |

## What each task keeps

- **layers**: the GeoJSON (gzip) or GeoTIFF file and the STAC item in the bucket under `corestack-layers/layers/<state>/<district>/<tehsil>/`.
  One record per layer: place, layer name, title, description, dates, columns, bounding box, size, SHA-256, feature count.
- **panindia**: the Drive file (gzip) and the STAC item under `corestack-panindia/pan_india/<layer>/`. Earth Engine layers: metadata only.
- **gcp**: per parcel under `corestack-gcp/gcp/<project id>/`: project detail, boundary KML, registration and site photos, progress
  photos, and `record.json`. One record per parcel: detail, payments, plantation plan, yearly targets, seedlings, reported progress.

## Privacy

The registry endpoint `/api/land-ownership/<id>` returns bank account numbers, IFSC codes, the participant's e-mail, phone and
address, and the names of officials. `gcp.py` keeps only an allow-list of fields (`OWNERSHIP_KEEP`). It does not store or upload
any of the data above. It does not download payment receipts or ownership documents. When the participant is not an
organisation, the record does not keep the name (`entity_type = person_or_unknown`). `tests/test_tasks.py` checks this.

## Terms

- CoRE Stack: the code is AGPL-3.0. The documentation states no licence for the data. Ask CoRE Stack before you publish raw layers.
- Green Credit registry: the copyright policy permits reproduction after permission by e-mail, with the source acknowledged.
  The terms do not mention automated access.

## Speed

- GeoServer: 2 workers, 1 thread, 1 s delay (one academic server; capabilities documents take about 13 s).
- Registry: 1 worker, 1 thread, 1 s delay. One parcel takes 1 to 3 minutes.
- Drive: 1 worker, 2 s delay.

## Use

```
python -m pytest tests -q
python layers.py --local <STAC item path>
python gcp.py --local <project id> [<land ownership id>]
scrape-check --project corestack-layers --task layers --items 3
```

Setup workflow inputs: `only` (projects), `max_items` (seed the first N items for a trial).
