"""Tests for the pure parts of the tasks. All data here is synthetic (the repo is public: never put real people's details here)."""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import family  # noqa: E402
import gcp  # noqa: E402
import layers  # noqa: E402
import panindia  # noqa: E402

SECRET_STRINGS = ["000111222333", "TEST0000123", "someone@example.org", "9999999999", "Example Street 1", "Officer", "Surname",
                  "officer.login", "TXN-SECRET-1"]


def ownership_fixture(first="Example Power Limited"):
    return {
        "id": "EPID1", "status": "APPROVED", "totalArea": "10.0", "amountPaid": 123.4, "demandNote": 100, "treeCanopy": "10",
        "transactionNumber": "TXN-SECRET-1", "bankAccountDetailId": "b1",
        "bankDetail": {"accountNumber": "000111222333", "ifscCode": "TEST0000123", "bankName": "Bank"},
        "user": {"firstName": first, "lastName": None, "email": "someone@example.org", "phoneNumber": "9999999999",
                 "address": {"addressLine1": "Example Street 1", "zipCode": "000000"}},
        "landOwnerShipQuery": [{"query": "Please upload the estimate.", "status": "RESOLVED",
                                "user": {"firstName": "Officer", "lastName": "Surname", "userName": "officer.login",
                                         "designation": "DFO"}}],
        "landOwnerShipDocument": [{"docType": "LEASE", "docUrl": "https://bucket.example/doc?X-Amz-Signature=abc"}],
        "geoTaggedImagesKeys": [{"key": "IMG1", "imageUrl": "https://bucket.example/IMG1?X-Amz-Signature=abc"}],
        "projectDetail": {"registrationNo": 7, "landSpecification": {"division": "Div", "treeCanopyDensity": "0.1"},
                          "address": {"stateDetail": {"name": "STATE"}, "districtDetail": {"name": "District"}}},
        "plantationPlanning": {"pitSize": "45cm", "totalNumberOfSeedlingToBePlanted": 100, "id": "x"},
    }


def test_ownership_drops_personal_and_bank_data():
    out = gcp.clean_ownership(ownership_fixture())
    text = json.dumps(out)
    for s in SECRET_STRINGS:
        assert s not in text, s
    assert out["amountPaid"] == 123.4 and out["treeCanopy"] == "10"
    assert out["entity"] == "Example Power Limited" and out["entity_type"] == "organisation"
    assert out["queries"] == [{"query": "Please upload the estimate.", "status": "RESOLVED", "created_at": None,
                               "updated_at": None, "by_designation": "DFO"}]
    assert out["project"]["state"] == "STATE" and out["document_types"] == ["LEASE"]
    assert out["site_image_keys"] == ["IMG1"]


def test_person_participant_name_not_kept():
    out = gcp.clean_ownership(ownership_fixture(first="Jane Example"))
    assert out["entity"] is None and out["entity_type"] == "person_or_unknown"


def test_project_detail_has_no_presigned_links():
    d = {"name": "Land Based Project", "images": ["https://b.example/GCDOC1?X-Amz-Signature=s"],
         "documents": [{"label": "Land Kml File", "url": "https://b.example/GCDOC2?X-Amz-Credential=c"}]}
    out = gcp.clean_project(d)
    assert "X-Amz" not in json.dumps(out)
    assert out["image_keys"] == ["GCDOC1"] and out["documents"] == [{"label": "Land Kml File", "key": "GCDOC2"}]


def test_monitoring_flattened():
    mt = {"data": {"status": "S", "firstYearTargets": [{"id": "t1", "progressInUnits": "5", "costProgress": "0",
                                                        "indicator": {"name": "Fencing"}, "activity": {"name": "Chain link"}}],
                   "seedlings": [{"commonName": "Tree", "numberOfSeedlings": 10, "numberOfPlantedSeedlings": 0, "id": "s"}]}}
    out = gcp.clean_monitoring(mt)
    assert out["targets"][0]["indicator"] == "Fencing" and out["targets"][0]["group"] == "firstYearTargets"
    assert out["seedlings"][0]["numberOfPlantedSeedlings"] == 0
    assert gcp.has_progress(out["targets"][0]) and not gcp.has_progress({"progressInUnits": "0", "costProgress": None})


def test_describe_tehsil_item():
    path = "CorestackCatalogs_merged_collection/tehsil_wise/st/dist/teh/st_dist_teh_dem_raster/st_dist_teh_dem_raster.json"
    item = {"id": "st_dist_teh_dem_raster", "bbox": [1, 2, 3, 4], "properties": {"title": "DEM", "gsd": 30},
            "assets": {"data": {"href": "https://geoserver.core-stack.org:8443/geoserver/x/wcs?service=WCS", "type": "image/tiff",
                                "roles": ["data"]}}}
    info = layers.describe(path, item)
    assert (info["state"], info["district"], info["tehsil"], info["layer"], info["kind"]) == ("st", "dist", "teh", "dem_raster", "raster")
    assert layers.bucket_path(info, item["id"], ".tif") == "layers/st/dist/teh/st_dist_teh_dem_raster.tif"


def test_check_body():
    assert layers.check_body("raster", b"II*\x00rest")[0]
    assert not layers.check_body("raster", b"<?xml version='1.0'?><ServiceExceptionReport>")[0]
    ok, n, _ = layers.check_body("vector", json.dumps({"type": "FeatureCollection", "features": [{}, {}]}).encode())
    assert ok and n == 2
    assert not layers.check_body("vector", b"<ows:ExceptionReport>Feature type unknown</ows:ExceptionReport>")[0]


def test_is_item_and_drive_id():
    assert family.is_item("a/b/x/x.json") and not family.is_item("a/b/x/collection.json")
    assert panindia.drive_id("https://drive.google.com/file/d/1AbCdEfGhIjKlMn_op/view?usp=sharing") == "1AbCdEfGhIjKlMn_op"
    assert panindia.drive_id("https://code.earthengine.google.com/?asset=x") is None


def test_gzip_roundtrip():
    assert gzip.decompress(gzip.compress(b"{}")) == b"{}"
