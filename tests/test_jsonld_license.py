from pathlib import Path

import pytest
import yaml

from utils.jsonld import normalize_license, yaml_to_jsonld


@pytest.mark.parametrize(
    "value",
    [None, "", "NA", "na", "N/A", "NaN", "TBD", "unknown", " null "],
)
def test_unknown_license_does_not_assign_permissions(value):
    assert normalize_license(value) is None


def test_declared_license_is_preserved():
    assert normalize_license(" CC-BY-4.0 ") == "CC-BY-4.0"


def test_yaml_to_jsonld_omits_missing_license(tmp_path: Path):
    metadata_path = tmp_path / "model.yaml"
    metadata_path.write_text(
        yaml.safe_dump(
            {
                "team_abbr": "NIH",
                "model_abbr": "RSV",
                "model_version": "1.0",
                "methods": "Test model",
                "license": "NA",
                "website_url": "NA",
                "team_name": "NIH",
                "model_contributors": [],
            }
        ),
        encoding="utf-8",
    )

    result = yaml_to_jsonld(metadata_path)

    assert "license" not in result
