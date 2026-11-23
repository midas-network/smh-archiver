"""Tests for report.json handling, report JSON-LD entries and retag detection."""

import json

from pipeline.update_source_data import (
    copy_round_reports,
    select_report_entry,
    write_source_snapshot,
)
from utils.jsonld import (
    apply_round_documentation,
    new_report_subject_of,
    read_model_abstract,
)

REPORT_JSON = {
    "rounds": [
        {"round_id": "2023-11-12", "report": [{"filename": "r1.pdf"}], "path": "reports/"},
        {
            "round_id": "2024-07-28",
            "report": [{"filename": "r2.pdf"}],
            "path": "auxiliary-data/reports/",
        },
    ]
}


def _checkout(tmp_path, files):
    root = tmp_path / "checkout"
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF")
    return root


def _no_git(monkeypatch):
    monkeypatch.setattr("pipeline.update_source_data.subprocess.run", lambda *a, **k: None)


def test_select_report_entry_by_round_id_from_cumulative_config():
    assert select_report_entry(REPORT_JSON, "2024-07-28")["report"][0]["filename"] == "r2.pdf"
    assert select_report_entry(REPORT_JSON, "2026-08-02") is None
    assert select_report_entry(None, "2024-07-28") is None


def test_copy_filters_directory_holding_two_rounds(tmp_path, monkeypatch):
    _no_git(monkeypatch)
    checkout = _checkout(tmp_path, ["auxiliary-data/reports/r1.pdf", "auxiliary-data/reports/r2.pdf"])
    out = tmp_path / "out"

    records = copy_round_reports(str(checkout), REPORT_JSON, "2024-07-28", str(out), "2024-07-28-v4")

    assert sorted(p.name for p in (out / "reports").glob("*.pdf")) == ["r2.pdf"]
    sidecar = json.loads((out / "reports" / "reports.json").read_text())
    assert sidecar == records
    assert sidecar[0]["ref"] == "2024-07-28-v4"
    assert sidecar[0]["path"] == "auxiliary-data/reports/r2.pdf"


def test_declared_path_is_per_tag_and_stale_path_falls_back(tmp_path, monkeypatch, capsys):
    _no_git(monkeypatch)
    # Round 1 at a later tag: report.json still says reports/, file is under auxiliary-data/.
    checkout = _checkout(tmp_path, ["auxiliary-data/reports/r1.pdf"])
    out = tmp_path / "out"

    records = copy_round_reports(str(checkout), REPORT_JSON, "2023-11-12", str(out), "2025-07-27-v4")

    assert records[0]["path"] == "auxiliary-data/reports/r1.pdf"
    assert "not at declared path" in capsys.readouterr().out

    # Round 1 at its own tag resolves at the declared path.
    checkout2 = _checkout(tmp_path / "b", ["reports/r1.pdf"])
    records = copy_round_reports(str(checkout2), REPORT_JSON, "2023-11-12", str(tmp_path / "o2"), "2023-11-12-v4")
    assert records[0]["path"] == "reports/r1.pdf"


def test_missing_entry_or_file_warns_without_aborting(tmp_path, monkeypatch, capsys):
    _no_git(monkeypatch)
    checkout = _checkout(tmp_path, [])

    assert copy_round_reports(str(checkout), REPORT_JSON, "2026-08-02", str(tmp_path / "o"), "t") == []
    assert copy_round_reports(str(checkout), REPORT_JSON, "2024-07-28", str(tmp_path / "o"), "t") == []
    out = capsys.readouterr().out
    assert "No report.json entry" in out
    assert "Report file not found" in out
    assert not (tmp_path / "o" / "reports").exists()


def test_report_subject_of_uses_pinned_ref_and_name_fallback():
    reports = new_report_subject_of(
        "https://github.com/midas-network/rsv-scenario-modeling-hub",
        "2025-07-27-v4",
        [
            {"filename": "a.pdf", "short_description": "Executive Summary", "path": "auxiliary-data/reports/a.pdf"},
            {"filename": "b.pdf", "short_description": None, "path": "auxiliary-data/reports/b.pdf"},
        ],
    )

    assert reports[0] == {
        "@type": "Report",
        "name": "Executive Summary",
        "encodingFormat": "application/pdf",
        "url": "https://github.com/midas-network/rsv-scenario-modeling-hub/blob/2025-07-27-v4/auxiliary-data/reports/a.pdf",
    }
    assert reports[1]["name"] == "Report"
    assert "/main/" not in reports[1]["url"]


def test_subject_of_is_list_with_definition_first():
    data = {}
    apply_round_documentation(data, {"url": "https://x/def.md"}, [{"@type": "Report", "name": "R"}])

    assert [i["@type"] for i in data["subjectOf"]] == ["CreativeWork", "Report"]


def test_read_model_abstract(tmp_path):
    d = tmp_path / "model-abstracts" / "M-1"
    d.mkdir(parents=True)
    (d / "2024-07-28-M-1.md").write_text("## Text\n", encoding="utf-8")

    assert read_model_abstract(tmp_path, "2024-07-28", "M-1") == "## Text\n"
    assert read_model_abstract(tmp_path, "2025-07-27", "M-1") is None


def test_retag_warning_when_same_ref_changes_commit(tmp_path, capsys):
    write_source_snapshot(tmp_path, "https://x/r", "2023-11-12-v4", "tag", "abe944d2")
    capsys.readouterr()
    write_source_snapshot(tmp_path, "https://x/r", "2023-11-12-v4", "tag", "47173645")
    out = capsys.readouterr().out
    assert "RETAGGED" in out and "abe944d2" in out and "47173645" in out

    write_source_snapshot(tmp_path, "https://x/r", "2023-11-12-v4", "tag", "47173645")
    assert "RETAGGED" not in capsys.readouterr().out
