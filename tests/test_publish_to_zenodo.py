import hashlib
import json
import tarfile
from unittest.mock import Mock

import pandas as pd
import pytest
import yaml
from pypdf import PdfReader

from publish_to_zenodo import (
    COMBINED_RECORD_TITLE,
    COORDINATION_GROUP_CREATORS,
    DEFAULT_PREVIEW_FILE,
    DEFAULT_RECORD_VERSION,
    HUB_WEBSITE,
    RELATED_PUBLICATIONS,
    ZenodoClient,
    ZenodoError,
    assert_no_reserved_doi,
    assert_record_version_advances,
    build_metadata,
    build_license_manifest,
    build_native_funding,
    build_funding_attributions,
    _dedupe_native_funding,
    build_parser,
    build_release,
    derive_record_title,
    derive_record_version,
    load_split_manifest,
    main,
    publish_existing_draft,
    split_release_by_round,
    _record_new_version_in_split_manifest,
    update_draft_metadata_only,
    update_existing_draft,
    upload_release,
)


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def make_client(session):
    session.headers = {}
    return ZenodoClient("not-a-real-token", session=session)


def make_repository(root):
    round_id = "2025-07-27"
    round_dir = root / "data" / round_id
    metadata_dir = round_dir / "model-metadata"
    model_output_dir = round_dir / "model-output" / "NIH-RSV"
    generated_round_dir = root / "output" / round_id
    metadata_dir.mkdir(parents=True)
    model_output_dir.mkdir(parents=True)
    generated_round_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "model_id": "NIH-RSV",
                "origin_date": "2025-07-27",
                "scenario_id": "A",
                "target": "inc hosp",
                "horizon": horizon,
                "location": "US",
                "age_group": "0-130",
                "output_type": "sample",
                "output_type_id": 1,
                "value": f"Visible sample trajectory {horizon}",
                "run_grouping": 1,
                "stochastic_run": 1,
            }
            for horizon in range(1, 6)
        ]
    ).to_parquet(model_output_dir / "model.parquet", index=False)
    (round_dir / ".DS_Store").write_bytes(b"excluded")
    (round_dir / "source_snapshot.json").write_text(
        json.dumps(
            {
                "repository": "https://example.org/source",
                "ref": "2025-07-27-v3",
                "ref_type": "tag",
                "commit": "0123456789abcdef0123456789abcdef01234567",
                "retrieved_at": "2026-08-24T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (metadata_dir / "NIH-RSV.yaml").write_text(
        yaml.safe_dump({"license": "MIT"}), encoding="utf-8"
    )
    (generated_round_dir / "NIH-RSV.jsonld").write_text("{}\n", encoding="utf-8")
    reports_dir = round_dir / "reports"
    reports_dir.mkdir()
    (reports_dir / "Report_Stub.pdf").write_bytes(b"%PDF-1.4 stub\n")
    (reports_dir / "reports.json").write_text(
        json.dumps(
            [
                {
                    "filename": "Report_Stub.pdf",
                    "short_description": "National Report",
                    "path": "auxiliary-data/reports/Report_Stub.pdf",
                    "ref": "2025-07-27-v3",
                }
            ]
        ),
        encoding="utf-8",
    )
    abstract_dir = round_dir / "model-abstracts" / "NIH-RSV"
    abstract_dir.mkdir(parents=True)
    (abstract_dir / "2025-07-27-NIH-RSV.md").write_text("## Aims\n\nStub.\n", encoding="utf-8")
    consolidated = {
        "@type": "Dataset",
        "roundId": round_id,
        "identifier": round_id,
        "name": "Round 1 - 2025-2026 Scenario Projection Models Collection",
        "hasPart": [
            {
                "name": "NIH-RSV",
                "producer": {
                    "name": "National Institutes of Health",
                    "funder": {"description": "NIH test grant"},
                },
                "author": [
                    {
                        "name": "Doe, Jane",
                        "affiliation": {"name": "NIH"},
                    },
                    {
                        "name": "Doe, Jane",
                        "affiliation": {"name": "Research Center"},
                    },
                ],
                "workExample": {
                    "temporalCoverage": "2025-07-27 00:00:00/2026-06-06 00:00:00",
                    "output_type": [["sample"]],
                    "ageGroups": ["0-130"],
                    "spatialCoverage": [
                        {
                            "@id": "http://sws.geonames.org/fips_US/",
                            "gn:name": "United States",
                            "gn:fipsCode": "US",
                            "iso3166-2:code": "US-US",
                        }
                    ],
                    "variableMeasured": [
                        {
                            "name": "Weekly incident RSV hospitalizations",
                            "identifier": "http://example.org/inc-hosp",
                            "unitText": "count",
                            "target_id": "inc hosp",
                            "target_type": "continuous",
                            "available_output_types": ["sample"],
                            "temporalUnit": "week",
                        }
                    ],
                    "isPartOf": {"subjectOf": {"url": "https://example.org/round-3"}},
                },
            }
        ],
    }
    consolidated_path = root / "output" / "Round_1_2025-2026_v6.0.0.jsonld"
    consolidated_path.write_text(json.dumps(consolidated), encoding="utf-8")
    consolidated_path.with_suffix(".html").write_text(
        """<!doctype html>
<html>
<head>
  <style>
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    .tab-title { display: none; }
  </style>
</head>
<body>
  <h1>Round 1 - 2025-2026 Scenario Projection Models Collection</h1>
  <div class="model" id="model-0">
    <div class="model-header">
      <h2>NIH-RSV</h2>
      <a href="#index" class="back-to-top">Back to Index</a>
    </div>
    <div class="tab-buttons">
      <button>Projection Data Snippet</button>
      <button>Targets</button>
    </div>
    <div class="tab-content active" id="model-0-content-sample">
      <h3 class="tab-title">Projection Data Snippet</h3>
      <div class="tab-body">
        <table class="dataframe">
          <thead><tr><th>origin_date</th><th>target</th></tr></thead>
          <tbody><tr><td>2025-07-27</td><td>Visible sample trajectory</td></tr></tbody>
        </table>
      </div>
    </div>
    <div class="tab-content" id="model-0-content-targets">
      <h3 class="tab-title">Targets</h3>
      <div class="tab-body">
        Hidden target metadata sentinel
      </div>
    </div>
  </div>
</body>
</html>
""",
        encoding="utf-8",
    )
    return round_id


def test_build_release_packages_rounds_outputs_and_checksums(tmp_path):
    repository = tmp_path / "repository"
    round_id = make_repository(repository)

    files = build_release(
        repository_root=repository,
        release_dir=tmp_path / "release",
        rounds=[round_id],
    )

    assert [path.name for path in files] == [
        "rsv-smh-2025-07-27.tar.gz",
        "rsv-smh-2025-07-27.jsonld",
        "Report_Stub.pdf",
        "README.md",
        "README.pdf",
        "LICENSES.json",
        "release_metadata.json",
        "SHA256SUMS",
    ]

    # The consolidated metadata is uploaded outside the archive too, so it can be
    # fetched directly instead of only existing inside a tarball.
    loose_jsonld = files[1]
    assert loose_jsonld.parent == tmp_path / "release"
    assert json.loads(loose_jsonld.read_text(encoding="utf-8"))["roundId"] == round_id
    assert (
        loose_jsonld.read_bytes()
        == (repository / "output" / "Round_1_2025-2026_v6.0.0.jsonld").read_bytes()
    )

    with tarfile.open(files[0], "r:gz") as archive:
        names = archive.getnames()
        assert "data/2025-07-27/model-output/NIH-RSV/model.parquet" in names
        assert "output/2025-07-27/NIH-RSV.jsonld" in names
        assert "output/rsv-smh-2025-07-27.jsonld" in names
        assert "output/rsv-smh-2025-07-27.html" in names
        assert not any("v6.0.0" in name for name in names)
        guide = archive.extractfile("README.md").read().decode()
        assert "data/2025-07-27/model-output/<team-model>/" in guide
        assert "output/rsv-smh-2025-07-27.html" in guide
        assert not any(name.endswith(".DS_Store") for name in archive.getnames())

    checksum_lines = files[-1].read_text(encoding="utf-8").splitlines()
    assert len(checksum_lines) == 7
    assert any(
        line.endswith("  rsv-smh-2025-07-27.jsonld")
        for line in checksum_lines
    )
    assert any(line.endswith("  README.md") for line in checksum_lines)
    assert any(line.endswith("  README.pdf") for line in checksum_lines)
    metadata = json.loads(files[-2].read_text(encoding="utf-8"))
    assert metadata["rounds"] == ["2025-07-27"]
    assert metadata["source_snapshots"][0]["ref"] == "2025-07-27-v3"
    assert metadata["parser_release"] == "v0.1.0-beta.1"
    assert metadata["record_version"] == DEFAULT_RECORD_VERSION
    assert metadata["zenodo_funding"][0]["funder"]["id"] == "04q48ey07"
    assert (
        metadata["artifacts"][0]["sha256"]
        == hashlib.sha256(files[0].read_bytes()).hexdigest()
    )
    assert "README.pdf" in {
        artifact["name"] for artifact in metadata["artifacts"]
    }
    by_name = {path.name: path for path in files}
    assert "Round_1_2025-2026_v6.0.0.html" not in by_name
    release_pdf = by_name["README.pdf"]
    assert release_pdf.read_bytes().startswith(b"%PDF")
    pdf_reader = PdfReader(str(release_pdf))
    assert len(pdf_reader.pages) > 0
    pdf_text = "\n".join(page.extract_text() or "" for page in pdf_reader.pages)
    normalized_pdf_text = " ".join(pdf_text.split())
    assert "RSV Scenario Modeling Hub data and metadata" in normalized_pdf_text
    assert "interventions, pathogens, and populations" in normalized_pdf_text
    assert "2025-07-27-v3" in normalized_pdf_text
    assert f"Zenodo record version: {DEFAULT_RECORD_VERSION}" in normalized_pdf_text
    assert "model-output" in normalized_pdf_text
    assert "model.parquet" in normalized_pdf_text
    assert "Visible sample" in normalized_pdf_text
    assert "trajectory 1" in normalized_pdf_text
    release_readme = by_name["README.md"].read_text(encoding="utf-8")
    assert release_readme.startswith("# RSV Scenario Modeling Hub data and metadata")
    assert "Round 1 - 2025-2026." in release_readme
    assert "interventions, pathogens, and populations" in release_readme
    assert "2025-07-27-v3" in release_readme
    assert "## Round summary" in release_readme
    assert "| Round | Round ID | Team-model outputs |" in release_readme
    assert "| Round 1 - 2025-2026 | 2025-07-27 |" in release_readme
    assert "## Results data" in release_readme
    assert f"Zenodo record version: `{DEFAULT_RECORD_VERSION}`" in release_readme
    assert "`data/2025-07-27/model-output/NIH-RSV/model.parquet`" in release_readme
    assert "| NIH-RSV | Team-model | `data/2025-07-27/model-output/NIH-RSV/model.parquet` | 5 |" in release_readme
    assert "## Funding by team-model" in release_readme
    assert (
        "| Team-model or ensemble | Team/group | Funding statement |" in release_readme
    )
    assert (
        "| NIH-RSV | National Institutes of Health | NIH test grant |" in release_readme
    )
    assert "### Team-model outputs and ensembles at a glance" in release_readme
    assert "### NIH-RSV" in release_readme
    assert "Projection data snippet" in release_readme
    assert "Visible sample trajectory 1" in release_readme
    assert HUB_WEBSITE in release_readme
    assert "## References" in release_readme
    for publication in RELATED_PUBLICATIONS:
        assert publication["doi"] in release_readme
    assert "unpublished Zenodo Sandbox drafts" not in release_readme
    assert "Cite the version-specific DOI shown on the Zenodo record" in release_readme
    assert "| Team-model or ensemble | Team | Version | Declared license | Effective license |" in release_readme
    assert "| scenario_id | target | horizon | location | age_group | output_type |" in release_readme
    licenses = json.loads(by_name["LICENSES.json"].read_text(encoding="utf-8"))
    assert licenses["models"][0]["effective_license"] == "mit"
    assert licenses["models"][0]["fallback_applied"] is False
    assert licenses["license_counts"] == {"mit": 1}
    assert licenses["record_level_licenses"] == ["mit"]


def test_build_metadata_uses_internal_discovery_fields(tmp_path):
    repository = tmp_path / "repository"
    round_id = make_repository(repository)
    args = build_parser().parse_args([])

    metadata = build_metadata(args, [round_id], repository)

    assert metadata["license"] == "mit"
    assert "participating Scenario Modeling Hub model projection teams" in metadata["copyright"]
    coordination = [dict(creator) for creator in COORDINATION_GROUP_CREATORS]
    assert metadata["creators"][: len(coordination)] == coordination
    assert metadata["creators"][0] == {
        "name": "Truelove, Shaun",
        "affiliation": "Johns Hopkins University",
    }
    assert {
        "name": "Doe, Jane",
        "affiliation": "NIH; Research Center",
    } in metadata["creators"]
    assert all(
        contributor["type"] == "ResearchGroup"
        for contributor in metadata["contributors"]
    )
    assert metadata["dates"][0]["start"] == "2025-07-27"
    assert any(
        subject["identifier"] == "http://example.org/inc-hosp"
        for subject in metadata["subjects"]
    )
    assert any(
        subject["identifier"] == "http://id.nlm.nih.gov/mesh/D018357"
        for subject in metadata["subjects"]
    )
    assert any(
        related["identifier"].endswith("/releases/tag/v0.1.0-beta.1")
        for related in metadata["related_identifiers"]
    )
    assert metadata["version"] == DEFAULT_RECORD_VERSION
    assert "data/{round}/source_snapshot.json" in metadata["method"]
    assert "data/&lt;round_id&gt;/model-output/&lt;team-model&gt;/" in metadata["description"]
    assert HUB_WEBSITE in metadata["description"]
    assert "<code>round_id</code>: <code>2025-07-27</code>" in metadata["description"]
    assert metadata["references"] == [
        f"{item['citation']} {item['doi']}" for item in RELATED_PUBLICATIONS
    ]
    assert metadata["notes"] == ""
    assert "NIH-RSV" not in metadata["notes"]
    assert "NIH test grant" not in metadata["notes"]


@pytest.mark.parametrize("declaration", [None, "NA", "unknown", "custom-unknown", "zenodo-freetoread-1.0"])
def test_unresolved_licenses_block_packaging(tmp_path, declaration):
    repository = tmp_path / "repository"
    round_id = make_repository(repository)
    (repository / "data" / round_id / "model-metadata" / "NIH-RSV.yaml").write_text(
        yaml.safe_dump({"license": declaration}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="NIH-RSV.yaml"):
        build_release(repository_root=repository, release_dir=tmp_path / "release", rounds=(round_id,))
    assert not (tmp_path / "release").exists()


def test_build_native_funding_extracts_deduplicated_grants(tmp_path):
    repository = tmp_path / "repository"
    round_id = make_repository(repository)
    consolidated_path = repository / "output" / "Round_1_2025-2026_v6.0.0.jsonld"
    consolidated = json.loads(consolidated_path.read_text(encoding="utf-8"))
    consolidated["hasPart"][0]["producer"]["funder"]["description"] = (
        "Supported by NSF Award # 2135784 and CSTE/CDC - Cooperative Agreement "
        "1 NU38OT000297, Subaward PO8169. Amazon Web Services. "
        "Scenario Modeling Hub fellowship to G Espana and S Moore."
    )
    consolidated["hasPart"].append(consolidated["hasPart"][0])
    consolidated_path.write_text(json.dumps(consolidated), encoding="utf-8")

    funding = build_native_funding(repository, (round_id,))

    assert funding[:2] == [
        {
            "funder": {"id": "04q48ey07"},
            "award": {
                "number": "U24GM132013",
            },
        },
        {
            "funder": {"id": "04q48ey07"},
            "award": {
                "number": "R24GM153920",
            },
        },
    ]
    assert {"funder": {"id": "021nxhr62"}, "award": {"id": "021nxhr62::2135784"}} in funding
    assert sum(
        item.get("award", {}).get("number") == "NU38OT000297" for item in funding
    ) == 1
    assert {"funder": {"id": "03ax9j741"}, "award": {"number": "PO8169"}} in funding
    assert {"funder": {"name": "Amazon Web Services"}} in funding
    assert {
        "funder": {"name": "Scenario Modeling Hub"},
        "award": {"title": {"en": "Scenario Modeling Hub fellowship"}},
    } in funding
    assert len(funding) == 7

    attributions = build_funding_attributions(repository, (round_id,))
    assert len(attributions) == 1
    assert "Subaward PO8169" in attributions[0]["statement"]
    assert attributions[0]["award_relationships"] == [{
        "relation": "subaward_of",
        "funder": {"id": "03ax9j741"},
        "award": {"number": "PO8169"},
        "parent_award": {"number": "NU38OT000297"},
    }]


def test_funding_deduplication_preserves_distinct_named_funders_and_awards():
    aws = {"funder": {"name": "Amazon Web Services"}}
    smh = {"funder": {"name": "Scenario Modeling Hub"}}
    fellowship = {**smh, "award": {"title": {"en": "Fellowship"}}}
    workshop = {**smh, "award": {"title": {"en": "Workshop support"}}}
    assert _dedupe_native_funding([aws, smh, fellowship, workshop, aws, fellowship]) == [
        aws, smh, fellowship, workshop,
    ]


def test_client_enriches_native_rights_and_copyright():
    session = Mock()
    session.headers = {}
    session.get.return_value = FakeResponse(
        200,
        {
            "metadata": {"title": "Dataset", "rights": [{"id": "old"}]},
            "access": {"record": "public", "files": "public"},
            "files": {"enabled": True},
            "custom_fields": {"legacy:subjects": []},
        },
    )
    session.put.return_value = FakeResponse(200, {"id": "46"})

    make_client(session).enrich_draft_metadata(
        46,
        rights=["cc-by-4.0", "mit", "cc-by-4.0"],
        copyright_statement="Copyright retained by the respective authors.",
        funding=[{"funder": {"id": "021nxhr62"}, "award": {"id": "021nxhr62::2127976"}}],
    )

    request = session.put.call_args
    assert request.args[0].endswith("/records/46/draft")
    assert request.kwargs["headers"]["Accept"] == (
        "application/vnd.inveniordm.v1+json"
    )
    assert request.kwargs["json"]["metadata"]["rights"] == [
        {"id": "cc-by-4.0"},
        {"id": "mit"},
    ]
    assert request.kwargs["json"]["metadata"]["copyright"].startswith("Copyright")
    assert request.kwargs["json"]["metadata"]["funding"] == [
        {"funder": {"id": "021nxhr62"}, "award": {"id": "021nxhr62::2127976"}}
    ]


def test_client_sets_native_default_preview_and_file_order():
    session = Mock()
    session.headers = {}
    session.get.return_value = FakeResponse(
        200,
        {
            "metadata": {"title": "Dataset"},
            "access": {"record": "public", "files": "public"},
            "files": {
                "enabled": True,
                "order": ["release_metadata.json"],
                "entries": {
                    "README.md": {},
                    "README.pdf": {},
                    "rsv-smh-2025-07-27.tar.gz": {},
                    "release_metadata.json": {},
                },
            },
            "custom_fields": {},
        },
    )
    session.put.return_value = FakeResponse(200, {"id": "46"})

    make_client(session).set_default_preview(
        46,
        DEFAULT_PREVIEW_FILE,
        order=[
            "rsv-smh-2025-07-27.tar.gz",
            "README.md",
        ],
    )

    request = session.put.call_args
    assert request.args[0].endswith("/records/46/draft")
    assert request.kwargs["json"]["files"] == {
        "enabled": True,
        "default_preview": DEFAULT_PREVIEW_FILE,
        "order": [
            DEFAULT_PREVIEW_FILE,
            "rsv-smh-2025-07-27.tar.gz",
            "README.md",
            "release_metadata.json",
        ],
    }


def test_upload_release_creates_draft_without_publishing(tmp_path):
    release_file = tmp_path / "release.tar.gz"
    release_file.write_bytes(b"release")
    checksum = hashlib.md5(release_file.read_bytes()).hexdigest()
    session = Mock()
    session.headers = {}
    session.post.return_value = FakeResponse(
        201,
        {
            "id": 42,
            "links": {
                "bucket": "https://sandbox.zenodo.org/api/files/bucket-id",
                "html": "https://sandbox.zenodo.org/deposit/42",
            },
        },
    )
    session.put.return_value = FakeResponse(
        200,
        {
            "key": release_file.name,
            "size": release_file.stat().st_size,
            "checksum": f"md5:{checksum}",
        },
    )

    result = upload_release(
        make_client(session),
        [release_file],
        metadata={"title": "Test"},
        publish=False,
    )

    assert result["deposition_id"] == 42
    assert result["published"] is False
    assert session.post.call_count == 1
    session.delete.assert_not_called()


def test_upload_release_publishes_only_when_requested(tmp_path):
    release_file = tmp_path / "release.tar.gz"
    release_file.write_bytes(b"release")
    checksum = hashlib.md5(release_file.read_bytes()).hexdigest()
    session = Mock()
    session.headers = {}
    session.post.side_effect = [
        FakeResponse(
            201,
            {
                "id": 43,
                "links": {
                    "bucket": "https://sandbox.zenodo.org/api/files/bucket-id",
                    "html": "https://sandbox.zenodo.org/deposit/43",
                },
            },
        ),
        FakeResponse(
            202,
            {
                "id": 43,
                "doi": "10.5072/zenodo.43",
                "links": {"record_html": "https://sandbox.zenodo.org/records/43"},
            },
        ),
    ]
    session.put.return_value = FakeResponse(
        200,
        {
            "key": release_file.name,
            "size": release_file.stat().st_size,
            "checksum": f"md5:{checksum}",
        },
    )

    result = upload_release(
        make_client(session),
        [release_file],
        metadata={"title": "Test"},
        publish=True,
    )

    assert result["published"] is True
    assert result["doi"] == "10.5072/zenodo.43"
    assert session.post.call_count == 2


def test_missing_bucket_url_cleans_up_new_draft(tmp_path):
    release_file = tmp_path / "release.tar.gz"
    release_file.write_bytes(b"release")
    session = Mock()
    session.headers = {}
    session.post.return_value = FakeResponse(201, {"id": 44, "links": {}})
    session.delete.return_value = FakeResponse(204)

    with pytest.raises(ZenodoError, match="bucket URL"):
        upload_release(
            make_client(session),
            [release_file],
            metadata={"title": "Test"},
            publish=False,
        )

    session.delete.assert_called_once()


def test_publish_existing_draft_does_not_create_or_upload():
    session = Mock()
    session.headers = {}
    session.post.return_value = FakeResponse(
        202,
        {
            "id": 45,
            "doi": "10.5072/zenodo.45",
            "links": {"record_html": "https://sandbox.zenodo.org/records/45"},
        },
    )

    result = publish_existing_draft(make_client(session), 45)

    assert result["deposition_id"] == 45
    assert result["published"] is True
    assert result["doi"] == "10.5072/zenodo.45"
    session.put.assert_not_called()
    session.delete.assert_not_called()


def test_update_existing_draft_replaces_files_metadata_and_removes_obsolete(tmp_path):
    release_file = tmp_path / "rsv-smh-2025-07-27.tar.gz"
    release_file.write_bytes(b"release")
    checksum = hashlib.md5(release_file.read_bytes()).hexdigest()
    session = Mock()
    session.headers = {}
    session.get.side_effect = [
        FakeResponse(
            200,
            {
                "id": 46,
                "submitted": False,
                "links": {
                    "bucket": "https://sandbox.zenodo.org/api/files/bucket-id",
                    "html": "https://sandbox.zenodo.org/uploads/46",
                },
                "files": [],
            },
        ),
        FakeResponse(
            200,
            {
                "id": 46,
                "files": [
                    {
                        "id": "obsolete-id",
                        "filename": "rsv-smh-generated-output.tar.gz",
                    },
                    {"id": "readme-txt-id", "filename": "README.txt"},
                    {
                        "id": "html-id",
                        "filename": "Round_1_2025-2026_v6.0.0.html",
                    },
                    {
                        "id": "old-pdf-id",
                        "filename": "Round_1_2025-2026_v6.0.0.pdf",
                    },
                ],
            },
        ),
    ]
    session.put.side_effect = [
        FakeResponse(
            200,
            {
                "id": 46,
                "metadata": {"title": "Updated"},
                "links": {"html": "https://sandbox.zenodo.org/uploads/46"},
            },
        ),
        FakeResponse(
            200,
            {
                "key": release_file.name,
                "size": release_file.stat().st_size,
                "checksum": f"md5:{checksum}",
            },
        ),
    ]
    session.delete.return_value = FakeResponse(204)

    result = update_existing_draft(
        make_client(session),
        46,
        [release_file],
        metadata={"title": "Updated"},
    )

    assert result["published"] is False
    assert result["removed_files"] == [
        "README.txt",
        "Round_1_2025-2026_v6.0.0.html",
        "Round_1_2025-2026_v6.0.0.pdf",
        "rsv-smh-generated-output.tar.gz",
    ]
    assert result["metadata"]["title"] == "Updated"
    assert session.delete.call_count == 4


def test_update_existing_draft_skips_files_with_matching_checksums(tmp_path):
    release_file = tmp_path / "rsv-smh-2025-07-27.tar.gz"
    release_file.write_bytes(b"release")
    checksum = hashlib.md5(release_file.read_bytes()).hexdigest()
    existing_file = {
        "id": "existing-id",
        "filename": release_file.name,
        "checksum": checksum,
    }
    session = Mock()
    session.headers = {}
    session.get.side_effect = [
        FakeResponse(
            200,
            {
                "id": 47,
                "submitted": False,
                "links": {
                    "bucket": "https://sandbox.zenodo.org/api/files/bucket-id",
                    "html": "https://sandbox.zenodo.org/uploads/47",
                },
                "files": [existing_file],
            },
        ),
        FakeResponse(200, {"id": 47, "files": [existing_file]}),
    ]
    session.put.return_value = FakeResponse(
        200,
        {
            "id": 47,
            "metadata": {"title": "Updated"},
            "links": {"html": "https://sandbox.zenodo.org/uploads/47"},
        },
    )

    result = update_existing_draft(
        make_client(session),
        47,
        [release_file],
        metadata={"title": "Updated"},
    )

    assert result["files"] == []
    assert result["unchanged_files"] == [release_file.name]
    assert session.put.call_count == 1
    session.delete.assert_not_called()


def test_client_refuses_unexpected_upload_host(tmp_path):
    release_file = tmp_path / "release.tar.gz"
    release_file.write_bytes(b"release")
    session = Mock()
    session.headers = {}

    with pytest.raises(ZenodoError, match="unexpected upload host"):
        make_client(session).upload_file(
            "https://example.com/api/files/bucket-id", release_file
        )

    session.put.assert_not_called()


# ---------------------------------------------------------------------------
# Per-round split mode
# ---------------------------------------------------------------------------


def test_display_round_name_keeps_official_round_prefix():
    from publish_to_zenodo import _display_round_name

    # RSV SMH restarts numbering each season, so "Round 1 - <season>" is the
    # hub's official name and must survive intact.
    assert (
        _display_round_name("Round 1 - 2025-2026 Scenario Projection Models Collection")
        == "Round 1 - 2025-2026"
    )


def test_derive_record_title_is_round_first():
    assert derive_record_title("Round 1 - 2025-2026").startswith("Round 1 - 2025-2026:")


def test_derive_record_version_is_dataset_version(tmp_path):
    repository = tmp_path / "repository"
    round_id = make_repository(repository)

    version = derive_record_version(repository, (round_id,))

    assert version == DEFAULT_RECORD_VERSION


def test_single_round_metadata_uses_round_title_and_version(tmp_path):
    repository = tmp_path / "repository"
    round_id = make_repository(repository)
    args = build_parser().parse_args([])

    metadata = build_metadata(args, (round_id,), repository)

    assert metadata["title"].startswith("Round 1 - 2025-2026:")
    assert metadata["version"] == DEFAULT_RECORD_VERSION
    assert "This record covers" in metadata["description"]
    assert "It covers 1 rounds" not in metadata["description"]


def test_combined_metadata_uses_default_record_version(tmp_path):
    repository = tmp_path / "repository"
    first_round = make_repository(repository)
    second_round = "2026-01-04"
    second_data_dir = repository / "data" / second_round
    second_data_dir.mkdir(parents=True)
    (second_data_dir / "model-metadata").mkdir()
    (second_data_dir / "source_snapshot.json").write_text(
        json.dumps(
            {
                "repository": "https://example.org/source",
                "ref": "2026-01-04-v1",
                "ref_type": "tag",
                "commit": "abcdef0123456789abcdef0123456789abcdef01",
                "retrieved_at": "2026-08-25T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (repository / "output" / "Round_2_2026-2027_v6.0.0.jsonld").write_text(
        json.dumps(
            {
                "@type": "Dataset",
                "roundId": second_round,
                "identifier": second_round,
                "name": "Round 2 - 2026-2027 Scenario Projection Models Collection",
                "hasPart": [],
            }
        ),
        encoding="utf-8",
    )
    args = build_parser().parse_args([])

    metadata = build_metadata(args, (first_round, second_round), repository)

    assert metadata["title"] == COMBINED_RECORD_TITLE
    assert metadata["version"] == DEFAULT_RECORD_VERSION


def test_explicit_title_overrides_derived_title(tmp_path):
    repository = tmp_path / "repository"
    round_id = make_repository(repository)
    args = build_parser().parse_args(["--title", "Custom title"])

    assert build_metadata(args, (round_id,), repository)["title"] == "Custom title"


def test_metadata_counts_ensembles_and_uses_reviewed_season_text(tmp_path):
    repository = tmp_path / "repository"
    round_id = make_repository(repository)
    path = repository / "output" / "Round_1_2025-2026_v6.0.0.jsonld"
    collection = json.loads(path.read_text())
    for name in ("Ensemble", "ensemble_LOP", "ENSEMBLE_LOP_untrimmed"):
        collection["hasPart"].append({"name": name})
    path.write_text(json.dumps(collection))
    metadata = build_metadata(build_parser().parse_args([]), (round_id,), repository)
    assert metadata["title"] == "Round 1 - 2025-2026: RSV Scenario Modeling Hub scenario projections and metadata"
    assert "1 team-model output datasets and 3 ensemble projection datasets" in metadata["description"]
    assert "(origin" not in metadata["description"]
    assert "model-output/README.md" in metadata["description"]
    assert "2025-2026 season" in metadata["keywords"]
    assert round_id not in metadata["keywords"]
    assert {"hubverse", "HTML", "YAML"}.issubset(metadata["keywords"])


def test_assert_no_reserved_doi_ignores_synthetic_prereserve():
    # The legacy serializer reports a production-prefix DOI for drafts that have
    # reserved nothing, so it must not be treated as evidence of a real DOI.
    assert_no_reserved_doi(
        {"id": 1, "metadata": {"prereserve_doi": {"doi": "10.5281/zenodo.1"}}}
    )


def test_assert_no_reserved_doi_rejects_native_pid():
    with pytest.raises(ZenodoError, match="must not reserve DOIs"):
        assert_no_reserved_doi(
            {"id": 2, "pids": {"doi": {"identifier": "10.5072/zenodo.2"}}}
        )


def test_metadata_only_update_performs_no_file_operations():
    client = Mock(spec=ZenodoClient)
    client.update_deposition.return_value = {"links": {"html": "https://example/1"}}

    update_draft_metadata_only(
        client, 1, metadata={"title": "T", "copyright": "c"}, record_license_ids=["cc0"]
    )

    client.upload_file.assert_not_called()
    client.delete_deposition_file.assert_not_called()


def test_metadata_only_update_applies_legacy_before_native():
    # The legacy payload carries a single license; applying it after the native
    # enrichment would erase the mixed-license and copyright fields.
    client = Mock(spec=ZenodoClient)
    client.update_deposition.return_value = {"links": {}}
    calls = []
    client.update_deposition.side_effect = lambda *a, **k: (
        calls.append("legacy"),
        {"links": {}},
    )[1]
    client.enrich_draft_metadata.side_effect = lambda *a, **k: calls.append("native")

    update_draft_metadata_only(
        client, 1, metadata={"copyright": "c"}, record_license_ids=["cc0"]
    )

    assert calls == ["legacy", "native"]


def test_metadata_only_update_applies_native_funding():
    client = Mock(spec=ZenodoClient)
    client.update_deposition.return_value = {"links": {}}
    funding = [{"funder": {"id": "021nxhr62"}, "award": {"id": "021nxhr62::2127976"}}]

    update_draft_metadata_only(client, 1, metadata={"title": "T"}, native_funding=funding)

    client.enrich_draft_metadata.assert_called_once_with(
        1,
        rights=("",),
        copyright_statement=None,
        funding=funding,
    )


def test_split_manifest_refuses_foreign_host(tmp_path):
    manifest = tmp_path / "sandbox-split.json"
    manifest.write_text(json.dumps({"api_url": "https://zenodo.org/api"}), "utf-8")

    with pytest.raises(ValueError, match="Refusing to resume"):
        load_split_manifest(manifest, api_url="https://sandbox.zenodo.org/api")


def test_split_dry_run_builds_one_release_dir_per_round(tmp_path):
    repository = tmp_path / "repository"
    round_id = make_repository(repository)
    args = build_parser().parse_args(
        [
            "--dry-run",
            "--split-by-round",
            "--version",
            "1.0.1",
            "--release-dir",
            str(tmp_path / "rel"),
        ]
    )

    result = split_release_by_round(None, args, (round_id,), repository)

    assert result["dry_run"] is True
    assert result["environment"] == "zenodo-sandbox"
    assert result["web_url"] == "https://sandbox.zenodo.org"
    entry = result["rounds"][0]
    assert entry["title"].startswith("Round 1 - 2025-2026:")
    assert entry["version"] == "1.0.1"
    assert entry["native_funding_entries"] == 2
    assert "Round_1_2025-2026_v6.0.0.html" not in entry["files"]
    assert "Round_1_2025-2026_v6.0.0.pdf" not in entry["files"]
    assert "README.md" in entry["files"]
    assert "README.pdf" in entry["files"]
    release_dir = tmp_path / "rel" / round_id
    assert release_dir.is_dir()
    release_metadata = json.loads((release_dir / "release_metadata.json").read_text())
    assert release_metadata["record_version"] == "1.0.1"
    assert "Zenodo record version: `1.0.1`" in (release_dir / "README.md").read_text()


@pytest.mark.parametrize(
    "extra",
    [
        ["--publish", "--acknowledge-rights-reviewed"],
        ["--update-draft", "5"],
        ["--metadata-file", "meta.json"],
    ],
)
def test_split_mode_rejects_conflicting_flags(extra, capsys):
    from publish_to_zenodo import main

    with pytest.raises(SystemExit):
        main(["--dry-run", "--split-by-round", *extra])

    assert "--split-by-round" in capsys.readouterr().err


def test_combined_title_unchanged(tmp_path):
    repository = tmp_path / "repository"
    make_repository(repository)
    args = build_parser().parse_args([])

    # Two rounds would normally be discovered; simulate the combined path by
    # asserting the constant itself is still what the combined record uses.
    assert COMBINED_RECORD_TITLE.endswith("2023–2026")


def test_report_pdfs_are_archived_but_not_loose_when_flag_disabled(tmp_path, monkeypatch):
    import publish_to_zenodo

    monkeypatch.setattr(publish_to_zenodo, "PUBLISH_LOOSE_REPORT_PDFS", False)
    repository = tmp_path / "repository"
    round_id = make_repository(repository)

    files = build_release(
        repository_root=repository, release_dir=tmp_path / "release", rounds=[round_id]
    )

    assert "Report_Stub.pdf" not in {path.name for path in files}
    assert not (tmp_path / "release" / "Report_Stub.pdf").exists()
    with tarfile.open(files[0], "r:gz") as archive:
        assert "data/2025-07-27/reports/Report_Stub.pdf" in archive.getnames()
        assert "data/2025-07-27/model-abstracts/NIH-RSV/2025-07-27-NIH-RSV.md" in archive.getnames()
    assert "## Round reports" in (tmp_path / "release" / "README.md").read_text()


def test_report_pdfs_published_loose_when_flag_enabled(tmp_path, monkeypatch):
    import publish_to_zenodo

    monkeypatch.setattr(publish_to_zenodo, "PUBLISH_LOOSE_REPORT_PDFS", True)
    repository = tmp_path / "repository"
    round_id = make_repository(repository)

    files = build_release(
        repository_root=repository, release_dir=tmp_path / "release", rounds=[round_id]
    )

    names = [path.name for path in files]
    assert "Report_Stub.pdf" in names
    checksum_lines = files[-1].read_text(encoding="utf-8").splitlines()
    assert any(line.endswith("  Report_Stub.pdf") for line in checksum_lines)
    metadata = json.loads(files[-2].read_text(encoding="utf-8"))
    assert "Report_Stub.pdf" in {artifact["name"] for artifact in metadata["artifacts"]}
    assert "`Report_Stub.pdf`" in (tmp_path / "release" / "README.md").read_text()


def test_new_version_draft_id_reads_the_draft_from_the_links():
    session = Mock()
    session.post.return_value = FakeResponse(
        201,
        {
            "id": 607138,
            "links": {"latest_draft": "https://sandbox.zenodo.org/api/deposit/depositions/632160"},
        },
    )

    draft_id = make_client(session).new_version_draft_id(607138)

    assert draft_id == 632160
    assert session.post.call_args.args[0].endswith(
        "/deposit/depositions/607138/actions/newversion"
    )


def test_new_version_draft_id_rejects_a_draft_on_another_host():
    session = Mock()
    session.post.return_value = FakeResponse(
        201,
        {"links": {"latest_draft": "https://zenodo.example.net/api/deposit/depositions/9"}},
    )

    with pytest.raises(ZenodoError, match="unexpected host"):
        make_client(session).new_version_draft_id(607138)


def test_new_version_draft_id_rejects_a_response_without_a_draft():
    session = Mock()
    session.post.return_value = FakeResponse(201, {"id": 607138, "links": {}})

    with pytest.raises(ZenodoError, match="opened no draft"):
        make_client(session).new_version_draft_id(607138)


def test_new_version_is_refused_when_the_record_version_would_not_advance():
    session = Mock()
    session.get.return_value = FakeResponse(200, {"metadata": {"version": "1.0.3"}})

    with pytest.raises(ZenodoError, match="already published as version '1.0.3'"):
        assert_record_version_advances(make_client(session), 638310, "1.0.3")

    assert session.get.call_args.args[0].endswith("/deposit/depositions/638310")


def test_new_version_is_allowed_when_the_record_version_advances():
    session = Mock()
    session.get.return_value = FakeResponse(200, {"metadata": {"version": "1.0.2"}})

    previous = assert_record_version_advances(make_client(session), 638310, "1.0.3")

    assert previous == "1.0.2"


def test_new_version_is_allowed_when_the_parent_records_no_version():
    session = Mock()
    session.get.return_value = FakeResponse(200, {"metadata": {}})

    assert assert_record_version_advances(make_client(session), 638310, "1.0.3") == ""


def test_recording_a_new_version_repoints_the_split_manifest(tmp_path):
    manifest_path = tmp_path / "sandbox-split.json"
    manifest_path.write_text(
        json.dumps(
            {
                "api_url": "https://sandbox.zenodo.org/api",
                "rounds": {
                    "2023-11-12": {
                        "deposition_id": 592565,
                        "status": "verified",
                        "version": "1.0.0",
                        "default_preview": "README.pdf",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    updated = _record_new_version_in_split_manifest(
        manifest_path,
        api_url="https://sandbox.zenodo.org/api",
        round_id="2023-11-12",
        previous_record_id=607138,
        deposition_id=632160,
        version="1.0.2",
        files=["rsv-smh-2023-11-12.tar.gz", "README.pdf"],
    )

    assert updated is True
    entry = json.loads(manifest_path.read_text(encoding="utf-8"))["rounds"]["2023-11-12"]
    assert entry["deposition_id"] == 632160
    assert entry["previous_record_id"] == 607138
    assert entry["version"] == "1.0.2"
    assert entry["status"] == "draft"
    assert entry["doi"] is None
    assert entry["draft_url"] == "https://sandbox.zenodo.org/uploads/632160"
    # Unrelated keys survive so the manifest keeps its other provenance.
    assert entry["default_preview"] == "README.pdf"


def test_recording_a_new_version_without_a_manifest_is_a_no_op(tmp_path):
    assert (
        _record_new_version_in_split_manifest(
            tmp_path / "missing.json",
            api_url="https://sandbox.zenodo.org/api",
            round_id="2023-11-12",
            previous_record_id=607138,
            deposition_id=632160,
            version="1.0.2",
            files=[],
        )
        is False
    )


def test_new_version_of_cannot_be_combined_with_other_actions():
    with pytest.raises(SystemExit):
        main(["--new-version-of", "607138", "--publish"])
    with pytest.raises(SystemExit):
        main(["--new-version-of", "607138", "--update-draft", "4"])


def test_creators_dedupe_and_format_model_people(tmp_path, monkeypatch):
    import publish_to_zenodo as pz

    repository = tmp_path / "repository"
    round_id = make_repository(repository)
    real = pz._extract_discovery_metadata

    def fake(collections):
        data = real(collections)
        data["people"] = [
            {"name": "Shaun Truelove", "affiliation": "Elsewhere"},
            {"name": "Ada Marie Lovelace"},
            {"name": "Cher"},
        ]
        return data

    monkeypatch.setattr(pz, "_extract_discovery_metadata", fake)
    metadata = build_metadata(build_parser().parse_args([]), [round_id], repository)
    names = [c["name"] for c in metadata["creators"]]
    assert names.count("Truelove, Shaun") == 1
    assert "Shaun Truelove" not in names
    assert metadata["creators"][0]["affiliation"] == "Johns Hopkins University"
    assert names[len(COORDINATION_GROUP_CREATORS):] == ["Lovelace, Ada Marie", "Cher"]
    assert all(c["type"] == "ResearchGroup" for c in metadata["contributors"])
