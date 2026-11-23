#!/usr/bin/env python3
"""Package SMH data and publish it to a Zenodo Sandbox deposition.

The safe default creates an unpublished draft. Pass ``--publish`` together
with ``--acknowledge-rights-reviewed`` to publish the draft and mint a Sandbox
DOI. The access token is read only from an environment variable.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests
import yaml

from utils.output_type_summary import (
    load_model_output_from_dir,
    summarize_output_type_metadata,
)
from utils.output_types import (
    definition_source_label,
    get_output_type_definition,
    human_iri_link,
)
from utils.jsonld import DEFAULT_REPORT_NAME, as_list, normalize_license
from utils.markdown import basic_markdown_to_html

REPOSITORY_ROOT = Path(__file__).resolve().parent
DEFAULT_API_URL = "https://sandbox.zenodo.org/api"
DEFAULT_TOKEN_ENV = "ZENODO_SANDBOX_TOKEN"
DEFAULT_RELEASE_DIR = REPOSITORY_ROOT / "release" / "zenodo"
NATIVE_JSON_MEDIA_TYPE = "application/vnd.inveniordm.v1+json"
DEFAULT_PREVIEW_FILE = "README.pdf"
SOURCE_REPOSITORY = "https://github.com/midas-network/rsv-scenario-modeling-hub"
HUB_WEBSITE = "https://rsvscenariomodelinghub.org/"
PARSER_REPOSITORY = "https://github.com/midas-network/smh-archiver"
PARSER_RELEASE_TAG = "v0.1.0-beta.1"
PARSER_RELEASE_URL = f"{PARSER_REPOSITORY}/releases/tag/{PARSER_RELEASE_TAG}"
DEFAULT_RECORD_VERSION = "1.0.4"
# Report PDFs are always archived. Uploading them as loose Zenodo files is built but held back
# until the hub confirms they may be redistributed (the hub README excludes auxiliary data from
# its MIT license). Flip to True to copy them into the release and publish them.
PUBLISH_LOOSE_REPORT_PDFS = True
SMH_OUTPUT_DOC_URL = f"{SOURCE_REPOSITORY}/blob/main/model-output/README.md"
DATASET_COPYRIGHT = (
    "Copyright, where applicable, is retained by the respective participating "
    "Scenario Modeling Hub model projection teams and authors. Reuse terms vary by model-round; "
    "see LICENSES.json."
)

COORDINATION_GROUP_CREATORS = (
    {"name": "Truelove, Shaun", "affiliation": "Johns Hopkins University"},
    {"name": "Viboud, Cécile", "affiliation": "NIH Fogarty"},
    {"name": "Lessler, Justin", "affiliation": "University of North Carolina"},
    {"name": "Loo, Sara", "affiliation": "Johns Hopkins University"},
    {"name": "Contamin, Lucie", "affiliation": "University of Pittsburgh"},
    {"name": "Howerton, Emily", "affiliation": "Penn State University"},
    {"name": "Smith, Claire", "affiliation": "Johns Hopkins University"},
    {"name": "Hochheiser, Harry", "affiliation": "University of Pittsburgh"},
    {"name": "Shea, Katriona", "affiliation": "Penn State University"},
    {"name": "Runge, Michael", "affiliation": "USGS"},
    {"name": "Carcelen, Erica", "affiliation": "Johns Hopkins University"},
    {"name": "Jung, Sung-mok", "affiliation": "University of North Carolina"},
    {"name": "Espino, Jessi", "affiliation": "University of Pittsburgh"},
    {"name": "Levander, John", "affiliation": "University of Pittsburgh"},
    {"name": "Bents, Samantha", "affiliation": "NIH Fogarty"},
    {"name": "Yan, Katie", "affiliation": "Penn State University"},
)

RELATED_PUBLICATIONS = (
    {
        "citation": (
            "Howerton E, et al. (2023). Evaluation of the US COVID-19 Scenario "
            "Modeling Hub for informing pandemic response under uncertainty. "
            "Nature Communications 14:7260."
        ),
        "doi": "https://doi.org/10.1038/s41467-023-42680-x",
    },
    {
        "citation": (
            "Loo SL, et al. (2024). The US COVID-19 and Influenza Scenario "
            "Modeling Hubs: Delivering long-term projections to guide policy. "
            "Epidemics 46:100738."
        ),
        "doi": "https://doi.org/10.1016/j.epidem.2023.100738",
    },
    {
        "citation": (
            "Runge MC, et al. (2024). Scenario design for infectious disease "
            "projections: Integrating concepts from decision analysis and "
            "experimental design. Epidemics 47:100775."
        ),
        "doi": "https://doi.org/10.1016/j.epidem.2024.100775",
    },
)
COORDINATION_FUNDING = (
    "The Scenario Modeling Hub is supported by the MIDAS Coordination Center, "
    "NIGMS grants U24GM132013 (2019–2024) and R24GM153920 (2024–2029) to "
    "the University of Pittsburgh."
)
RSV_MESH_SUBJECT = {
    "term": "Respiratory Syncytial Virus Infections",
    "identifier": "http://id.nlm.nih.gov/mesh/D018357",
    "scheme": "url",
}
OBSOLETE_RELEASE_FILES = {"rsv-smh-generated-output.tar.gz", "README.txt"}
COMBINED_RECORD_TITLE = "RSV Scenario Modeling Hub data and JSON-LD metadata, 2023–2026"
SPLIT_MANIFEST_NAME = "sandbox-split.json"
SPLIT_MANIFEST_SCHEMA_VERSION = "1.0"
LICENSE_IDS = {
    "bsd simplified": "bsd-2-clause",
    "bsd-2-clause": "bsd-2-clause",
    "cc-by-4.0": "cc-by-4.0",
    "cc-by-sa-4.0": "cc-by-sa-4.0",
    "cc-by_sa-4.0": "cc-by-sa-4.0",
    "mit": "mit",
}
LICENSE_URLS = {
    "bsd-2-clause": "https://opensource.org/license/bsd-2-clause",
    "cc-by-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "cc-by-sa-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "mit": "https://opensource.org/license/mit/",
}
RECORD_LICENSE_IDS = (
    "cc-by-4.0",
    "cc-by-sa-4.0",
    "mit",
    "bsd-2-clause",
)
ZENODO_FUNDERS = {
    # Preserve submitted names when no exact registry match is available.
    "aws": {"name": "Amazon Web Services"},
    "smh": {"name": "Scenario Modeling Hub"},
    "cdc": {
        "id": "042twtr12",
        "name": "Centers for Disease Control and Prevention",
    },
    "cste": {
        "id": "03ax9j741",
        "name": "Council of State and Territorial Epidemiologists",
    },
    "dhs": {
        "id": "00jyr0d86",
        "name": "United States Department of Homeland Security",
    },
    "hhs": {
        "id": "033jnv181",
        "name": "United States Department of Health and Human Services",
    },
    "jhu": {
        "id": "00za53h95",
        "name": "Johns Hopkins University",
    },
    "nih": {
        "id": "01cwqze88",
        "name": "National Institutes of Health",
    },
    "nigms": {
        "id": "04q48ey07",
        "name": "National Institute of General Medical Sciences",
    },
    "nsf": {
        "id": "021nxhr62",
        "name": "U.S. National Science Foundation",
    },
    "california_dph": {
        "id": "011cc8156",
        "name": "California Department of Public Health",
    },
}
MISSING_DISPLAY = "—"
ELLIPSIS_DISPLAY = "…"
README_SNIPPET_ROWS = 3
README_SNIPPET_DISPLAY_COLUMNS = (
    "scenario_id",
    "target",
    "horizon",
    "location",
    "age_group",
    "output_type",
    "output_type_id",
    "value",
)
PDF_PRINT_CSS = """
@page {
  size: Letter landscape;
  margin: 0.45in;
  @bottom-right {
    content: "Page " counter(page) " of " counter(pages);
    font-family: Arial, Helvetica, sans-serif;
    color: #666;
    font-size: 8pt;
  }
}

html {
  font-size: 10pt;
}

body {
  max-width: none !important;
  margin: 0 !important;
  padding: 0 !important;
  color: #111;
  line-height: 1.35 !important;
}

script,
.tab-buttons,
.back-to-top,
.screen-only,
.screen-header {
  display: none !important;
}

/* Structural sections the generator emits for print only. */
.print-only {
  display: block !important;
}

.print-only-inline {
  display: inline !important;
  color: #5b6b7c !important;
  font-style: italic;
}

table.facts.print-only {
  display: table !important;
}

.cover {
  break-after: page;
  padding-top: 1.1in;
}

/* Anchor the round link low so the title page reads as composed. */
.cover > p:last-child {
  margin-top: 2.2in;
}

.cover-title {
  margin: 0 0 0.1in;
  font-size: 2.6em;
}

.cover-lede {
  color: #4a5a6a;
  font-size: 1.15em;
}

.cover-metric {
  margin-right: 0.55in;
}

.matrix-section {
  break-after: page;
}

table.matrix {
  width: 100% !important;
  table-layout: fixed;
}

/* Every column sized explicitly: with table-layout fixed, an unsized column
   gets squeezed and its chips overflow into the neighbouring cell. */
/* Eight columns: Model, Team, Version, License, Age groups, Output types,
   Targets, Page. Widths must cover every one and sum to 100%. */
table.matrix th:nth-child(1), table.matrix td:nth-child(1) { width: 16%; }
table.matrix th:nth-child(2), table.matrix td:nth-child(2) { width: 22%; }
table.matrix th:nth-child(3), table.matrix td:nth-child(3) { width: 9%; }
table.matrix th:nth-child(4), table.matrix td:nth-child(4) { width: 13%; }
table.matrix th:nth-child(5), table.matrix td:nth-child(5) { width: 9%; }
table.matrix th:nth-child(6), table.matrix td:nth-child(6) { width: 18%; }
table.matrix th:nth-child(7), table.matrix td:nth-child(7) { width: 7%; }
table.matrix th:nth-child(8), table.matrix td:nth-child(8) { width: 6%; }

/* Percentage widths must include the cell padding, or the columns sum to more
   than the table and the last one is pushed off the page. */
table.matrix td, table.matrix th {
  box-sizing: border-box;
  overflow: hidden;
}

table.matrix .chip {
  padding: 0 3px;
  margin: 1px 2px 1px 0;
  font-size: 0.85em;
}

/* Break long model names between word parts, not mid-syllable. */
table.matrix td a {
  overflow-wrap: break-word !important;
  word-break: normal;
}

table.matrix th {
  padding: 4px 6px !important;
}

.matrix-page,
.matrix-page-head {
  text-align: right;
}

.matrix-page a::after {
  content: target-counter(attr(href), page);
}

/* A model's opening block stays whole, so a model never begins at the foot of
   a page with its content stranded overleaf. */
.model-intro {
  break-inside: avoid;
  break-after: avoid;
}

.snippet-note {
  color: #5b6b7c;
  font-size: 0.92em;
  margin: 0.04in 0 0 !important;
}

.glossary-entry {
  break-inside: avoid;
  margin-bottom: 0.09in;
}

.chip {
  break-inside: avoid;
}

.model-separator {
  margin: 0.3in 0 !important;
  break-after: avoid;
}

/* Prose set across the full landscape measure runs to ~200 characters a line,
   roughly twice a comfortable reading width. */
p,
.metadata {
  max-width: 6.6in;
}

/* Never strand a heading at the foot of a page. */
h1,
h2,
h3,
h4,
.tab-title {
  break-after: avoid;
  page-break-after: avoid;
  break-inside: avoid;
}

.index {
  border: 1px solid #cfd8e3 !important;
}

/* The screen grids do not survive pagination, so short list items flow into
   print columns instead of collapsing into one narrow stack. */
.index ul,
.authors-grid,
.variables-grid {
  display: block !important;
}

.index ul {
  columns: 2;
  column-gap: 0.4in !important;
}

.authors-grid {
  columns: 2.6in;
  column-gap: 0.3in !important;
}

.variables-grid {
  columns: 2.9in;
  column-gap: 0.3in !important;
}

.tab-content[id$="-content-spatial"] .tab-body {
  columns: 5;
  column-gap: 0.25in;
}

/* These lists are short; keep each one whole rather than split across a page. */
.tab-content[id$="-content-spatial"],
.tab-content[id$="-content-age"] {
  break-inside: avoid;
}

.tab-content[id$="-content-age"] .author {
  display: inline-block !important;
  margin-right: 0.4in !important;
}

.tab-content[id$="-content-output"] .tab-body {
  columns: auto;
}

.index li,
.author {
  break-inside: avoid;
}

/* Targets and output types are emitted as <br>-separated label/value stacks,
   seven lines deep for what is really one row of facts. Let them flow inline;
   the bold labels are enough of a delimiter. */
.tab-content[id$="-content-targets"] .author br,
.tab-content[id$="-content-output"] .author br {
  display: none !important;
}

/* Except each output type's term, which would otherwise run into its definition. */
.tab-content[id$="-content-output"] .author br:first-of-type {
  display: block !important;
}

.tab-content[id$="-content-targets"] .author strong,
.tab-content[id$="-content-output"] .author strong {
  margin-left: 0.12in;
}

.tab-content[id$="-content-targets"] .author,
.tab-content[id$="-content-output"] .author {
  display: block !important;
  margin-bottom: 0.06in !important;
}

/* Inline items fill the measure, so the column split no longer helps. */
.tab-content[id$="-content-targets"] .variables-grid {
  columns: auto !important;
}

/* A printed index needs page numbers; the anchors alone are not enough. */
.index li a::after {
  content: leader(".") " " target-counter(attr(href), page);
  color: #555;
}

.model {
  border-left: 3px solid #3498db !important;
  border-radius: 0 !important;
  page-break-inside: auto;
}

.model-header {
  display: block !important;
  break-inside: avoid;
  break-after: avoid;
}

.tab-content {
  display: block !important;
  padding: 0 !important;
  margin: 0.18in 0 0.12in !important;
}

.tab-title {
  display: block !important;
  margin: 0.08in 0 0.04in;
  color: #34495e !important;
  font-size: 12pt !important;
  font-weight: 700;
}

a {
  color: #1f5d8a !important;
  overflow-wrap: anywhere;
}

table {
  page-break-inside: auto;
}

tr {
  page-break-inside: avoid;
}

thead {
  display: table-header-group;
}

table.dataframe {
  font-size: 7pt !important;
  table-layout: fixed;
  width: 100% !important;
}

table.dataframe th,
table.dataframe td {
  padding: 3px 4px !important;
  overflow-wrap: anywhere;
}
"""

README_PDF_CSS = """
@page {
  size: Letter portrait;
  margin: 0.52in 0.6in;
  @bottom-right {
    content: "Page " counter(page) " of " counter(pages);
    color: #666;
    font-size: 8pt;
  }
}

body {
  color: #111827;
  font-family: Arial, Helvetica, sans-serif;
  font-size: 9.5pt;
  line-height: 1.36;
}

h1 {
  border-bottom: 2px solid #2563eb;
  color: #0f172a;
  font-size: 21pt;
  margin: 0 0 0.18in;
  padding-bottom: 0.08in;
}

h2 {
  border-bottom: 1px solid #cbd5e1;
  color: #1e3a8a;
  font-size: 14pt;
  margin: 0.22in 0 0.06in;
  padding-bottom: 0.03in;
}

h3 {
  color: #334155;
  font-size: 11.5pt;
  margin: 0.14in 0 0.035in;
}

h4 {
  color: #475569;
  font-size: 9.8pt;
  margin: 0.08in 0 0.025in;
}

p {
  margin: 0 0 0.075in;
}

.citation-contact {
  break-inside: avoid;
}

.citation-contact h2 {
  margin-top: 0.12in;
}

ul {
  margin: 0 0 0.09in 0.2in;
  padding: 0;
}

li {
  margin-bottom: 0.025in;
}

table {
  border-collapse: collapse;
  font-size: 7.1pt;
  margin: 0.06in 0 0.1in;
  table-layout: fixed;
  width: 100%;
}

th,
td {
  border: 1px solid #cbd5e1;
  overflow-wrap: anywhere;
  padding: 3px 4px;
  vertical-align: top;
}

th {
  background: #eff6ff;
  color: #1e3a8a;
  font-weight: 700;
}

tr {
  break-inside: avoid;
}

code {
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 3px;
  color: #7f1d1d;
  font-family: "Courier New", monospace;
  font-size: 8.7pt;
  padding: 0 2px;
}

a {
  color: #1d4ed8;
  overflow-wrap: anywhere;
}
"""


class ZenodoError(RuntimeError):
    """An error that is safe to show without exposing the access token."""


@dataclass(frozen=True)
class UploadedFile:
    name: str
    size: int
    checksum: str | None


class ZenodoClient:
    """Small client for the parts of the Zenodo deposition API that we use."""

    def __init__(
        self,
        token: str,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 300.0,
        session: requests.Session | None = None,
    ) -> None:
        if not token.strip():
            raise ValueError("The Zenodo access token is empty.")

        self.api_url = api_url.rstrip("/")
        parsed_url = urlparse(self.api_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise ValueError("The Zenodo API URL must be an HTTPS URL.")

        self._allowed_hostname = parsed_url.hostname
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "User-Agent": "smh-parser-zenodo-publisher/1.0",
            }
        )

    def create_deposition(self, metadata: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            f"{self.api_url}/deposit/depositions",
            json={"metadata": metadata},
            timeout=self.timeout,
        )
        return self._json_response(response, {201}, "create the draft deposition")

    def create_new_version(self, record_id: int) -> dict[str, Any]:
        """Open an unpublished new version of an already published record."""
        response = self.session.post(
            f"{self.api_url}/deposit/depositions/{record_id}/actions/newversion",
            timeout=self.timeout,
        )
        # Zenodo has answered this action with both 200 and 201 over time.
        return self._json_response(
            response, {200, 201}, "open a new version of the published record"
        )

    def new_version_draft_id(self, record_id: int) -> int:
        """Return the draft deposition id for a new version of ``record_id``."""
        deposition = self.create_new_version(record_id)
        draft_url = (deposition.get("links") or {}).get("latest_draft")
        if not draft_url:
            raise ZenodoError(
                f"Zenodo opened no draft for a new version of record {record_id}."
            )
        parsed = urlparse(str(draft_url))
        if parsed.hostname != self._allowed_hostname:
            raise ZenodoError(
                "Zenodo returned a new-version draft on an unexpected host: "
                f"{draft_url}"
            )
        match = re.search(r"(\d+)/?$", parsed.path)
        if not match:
            raise ZenodoError(f"Could not read a draft id from {draft_url}.")
        return int(match.group(1))

    def get_deposition(self, deposition_id: int) -> dict[str, Any]:
        response = self.session.get(
            f"{self.api_url}/deposit/depositions/{deposition_id}",
            timeout=self.timeout,
        )
        return self._json_response(response, {200}, "retrieve the draft deposition")

    def update_deposition(
        self, deposition_id: int, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        response = self.session.put(
            f"{self.api_url}/deposit/depositions/{deposition_id}",
            json={"metadata": metadata},
            timeout=self.timeout,
        )
        return self._json_response(response, {200}, "update the draft metadata")

    def enrich_draft_metadata(
        self,
        deposition_id: int,
        *,
        rights: Iterable[str],
        copyright_statement: str | None,
        funding: Iterable[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Set native fields that Zenodo's legacy deposition API drops."""
        headers = {
            "Accept": NATIVE_JSON_MEDIA_TYPE,
            "Content-Type": "application/json",
        }
        draft_url = f"{self.api_url}/records/{deposition_id}/draft"
        response = self.session.get(
            draft_url,
            headers=headers,
            timeout=self.timeout,
        )
        draft = self._json_response(
            response, {200}, "retrieve the native draft metadata"
        )
        native_metadata = draft.get("metadata")
        if not isinstance(native_metadata, dict):
            raise ZenodoError("Zenodo did not return native metadata for the draft.")

        license_ids = tuple(dict.fromkeys(value for value in rights if value))
        if license_ids:
            native_metadata["rights"] = [
                {"id": license_id} for license_id in license_ids
            ]
        if copyright_statement:
            native_metadata["copyright"] = copyright_statement
        if funding is not None:
            native_metadata["funding"] = list(funding)

        files = draft.get("files") or {}
        payload = {
            "metadata": native_metadata,
            "access": draft.get("access") or {},
            "files": {"enabled": bool(files.get("enabled", True))},
            "custom_fields": draft.get("custom_fields") or {},
        }
        response = self.session.put(
            draft_url,
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        return self._json_response(
            response, {200}, "update the native draft metadata"
        )

    def set_default_preview(
        self, deposition_id: int, filename: str, *, order: Iterable[str] = ()
    ) -> dict[str, Any]:
        """Ask the native Zenodo draft API to preview ``filename`` by default."""
        headers = {
            "Accept": NATIVE_JSON_MEDIA_TYPE,
            "Content-Type": "application/json",
        }
        draft_url = f"{self.api_url}/records/{deposition_id}/draft"
        response = self.session.get(
            draft_url,
            headers=headers,
            timeout=self.timeout,
        )
        draft = self._json_response(response, {200}, "retrieve the native draft")

        files = draft.get("files") or {}
        entries = files.get("entries") if isinstance(files, dict) else {}
        if not isinstance(entries, dict) or filename not in entries:
            raise ZenodoError(
                f"Cannot set {filename!r} as the default preview for draft "
                f"{deposition_id}; the file is not present in the native draft."
            )

        available_names = set(entries)
        ordered_names = [
            name
            for name in dict.fromkeys(
                [filename, *order, *(files.get("order") or []), *sorted(available_names)]
            )
            if name in available_names
        ]
        payload = {
            "metadata": draft.get("metadata") or {},
            "access": draft.get("access") or {},
            "files": {
                "enabled": bool(files.get("enabled", True)),
                "default_preview": filename,
                "order": ordered_names,
            },
            "custom_fields": draft.get("custom_fields") or {},
        }
        response = self.session.put(
            draft_url,
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        return self._json_response(
            response, {200}, f"set {filename} as the default preview"
        )

    def upload_file(self, bucket_url: str, path: Path) -> UploadedFile:
        parsed_bucket = urlparse(bucket_url)
        if (
            parsed_bucket.scheme != "https"
            or parsed_bucket.hostname != self._allowed_hostname
        ):
            raise ZenodoError(
                "Zenodo returned an unexpected upload host; refusing to send the token."
            )

        print(
            f"Uploading {path.name} ({_human_size(path.stat().st_size)})...",
            file=sys.stderr,
        )
        upload_url = f"{bucket_url.rstrip('/')}/{quote(path.name, safe='')}"
        with path.open("rb") as stream:
            response = self.session.put(
                upload_url,
                data=stream,
                timeout=self.timeout,
            )
        payload = self._json_response(response, {200, 201}, f"upload {path.name}")
        remote_checksum = payload.get("checksum")
        _verify_zenodo_md5(path, remote_checksum)
        return UploadedFile(
            name=payload.get("key") or payload.get("filename") or path.name,
            size=int(
                payload.get("size") or payload.get("filesize") or path.stat().st_size
            ),
            checksum=remote_checksum,
        )

    def publish_deposition(self, deposition_id: int) -> dict[str, Any]:
        response = self.session.post(
            f"{self.api_url}/deposit/depositions/{deposition_id}/actions/publish",
            timeout=self.timeout,
        )
        return self._json_response(response, {202}, "publish the deposition")

    def delete_deposition(self, deposition_id: int) -> None:
        response = self.session.delete(
            f"{self.api_url}/deposit/depositions/{deposition_id}",
            timeout=self.timeout,
        )
        if response.status_code not in {201, 204}:
            raise self._response_error(response, "delete the incomplete draft")

    def delete_deposition_file(self, deposition_id: int, file_id: str) -> None:
        response = self.session.delete(
            f"{self.api_url}/deposit/depositions/{deposition_id}/files/{file_id}",
            timeout=self.timeout,
        )
        if response.status_code != 204:
            raise self._response_error(response, "delete the obsolete draft file")

    @staticmethod
    def _json_response(
        response: requests.Response, expected: set[int], operation: str
    ) -> dict[str, Any]:
        if response.status_code not in expected:
            raise ZenodoClient._response_error(response, operation)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZenodoError(
                f"Zenodo returned a non-JSON response while trying to {operation}."
            ) from exc
        if not isinstance(payload, dict):
            raise ZenodoError(
                f"Zenodo returned an unexpected response while trying to {operation}."
            )
        return payload

    @staticmethod
    def _response_error(response: requests.Response, operation: str) -> ZenodoError:
        detail: Any = response.text.strip()
        try:
            payload = response.json()
            detail = payload.get("message") or payload.get("errors") or detail
        except (ValueError, AttributeError):
            pass
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        detail = detail[:500] if detail else "No error detail was returned."
        return ZenodoError(
            f"Could not {operation} (HTTP {response.status_code}): {detail}"
        )


def build_release(
    *,
    repository_root: Path,
    release_dir: Path,
    rounds: Iterable[str],
    include_source_data: bool = True,
    include_generated_output: bool = True,
    reuse_archives: bool = False,
    record_version: str | None = None,
    native_funding: list[dict[str, Any]] | None = None,
    extra_provenance: dict[str, Any] | None = None,
) -> list[Path]:
    """Create self-contained round archives, rights metadata, and checksums."""
    selected_rounds = tuple(rounds)
    # Validate source declarations before writing artifacts or contacting Zenodo.
    license_manifest = build_license_manifest(repository_root, selected_rounds)
    record_version = record_version or derive_record_version(repository_root, selected_rounds)
    native_funding = native_funding if native_funding is not None else build_native_funding(
        repository_root, selected_rounds
    )
    release_dir.mkdir(parents=True, exist_ok=True)
    _remove_obsolete_local_release_files(release_dir)
    artifacts: list[Path] = []

    if not include_source_data and not include_generated_output:
        raise ValueError(
            "At least one of source data or generated output must be included."
        )

    consolidated_outputs = _find_consolidated_outputs(repository_root, selected_rounds)
    readme_collections = _load_readme_collections(consolidated_outputs)
    round_names = _round_display_names(consolidated_outputs)
    loose_output_names: dict[str, list[str]] = {
        round_id: [] for round_id in selected_rounds
    }
    loose_outputs: list[Path] = []
    round_reports = _load_round_reports(repository_root, selected_rounds)
    loose_report_paths: list[Path] = []
    for round_id in selected_rounds:
        entries: list[tuple[Path, str]] = []
        if include_source_data:
            source = repository_root / "data" / round_id
            if not source.is_dir():
                raise ValueError(f"Round data directory does not exist: {source}")
            entries.append((source, f"data/{round_id}"))
        if include_generated_output:
            round_output = repository_root / "output" / round_id
            if not round_output.is_dir():
                raise ValueError(
                    f"Generated round output directory does not exist: {round_output}"
                )
            entries.append((round_output, f"output/{round_id}"))
            for output in consolidated_outputs[round_id]:
                entries.append((output, f"output/rsv-smh-{round_id}{output.suffix}"))

        destination = release_dir / f"rsv-smh-{round_id}.tar.gz"
        guide_reports, guide_abstracts = _round_archive_contents(
            repository_root, round_id
        )
        with tempfile.TemporaryDirectory(prefix="smh-archive-guide-") as guide_dir:
            guide = Path(guide_dir) / "README.md"
            guide.write_text(
                f"# {round_names.get(round_id, round_id)} archive guide\n\n"
                + _archive_layout(
                    round_id,
                    include_source_data,
                    include_generated_output,
                    has_reports=guide_reports,
                    has_abstracts=guide_abstracts,
                ),
                encoding="utf-8",
            )
            entries.append((guide, "README.md"))
            _create_archive(
                entries,
                destination,
                reuse=reuse_archives,
                required_prefixes=[archive_name for _, archive_name in entries],
            )
        artifacts.append(destination)

        if include_generated_output:
            # Publish the consolidated JSON-LD as a loose file as well. The
            # archive keeps a citable, self-contained bundle together, but a file
            # sealed inside a tarball cannot be fetched over HTTP or read by a
            # crawler or metadata harvester -- which is most of the reason to
            # publish JSON-LD at all. The HTML remains inside the archive because
            # Zenodo intentionally does not render loose HTML in the record page.
            for output in consolidated_outputs[round_id]:
                if output.suffix != ".jsonld":
                    continue
                loose_path = release_dir / _loose_jsonld_name(round_id)
                if loose_path != output:
                    shutil.copy2(output, loose_path)
                loose_outputs.append(loose_path)
                loose_output_names[round_id].append(loose_path.name)

    for round_id in selected_rounds:
        for report in round_reports[round_id]:
            loose_path = release_dir / report["filename"]
            if PUBLISH_LOOSE_REPORT_PDFS:
                shutil.copy2(report["source"], loose_path)
                loose_report_paths.append(loose_path)
            else:
                loose_path.unlink(missing_ok=True)

    # This was used by the first prototype. Generated files now live with each round.
    (release_dir / "rsv-smh-generated-output.tar.gz").unlink(missing_ok=True)

    source_snapshots = _load_source_snapshots(repository_root, selected_rounds)

    # README.md is the canonical human-facing source text. README.pdf is rendered
    # from it and is the requested Zenodo default preview.
    (release_dir / "README.txt").unlink(missing_ok=True)
    readme_path = release_dir / "README.md"
    readme_path.write_text(
        _build_release_readme(
            selected_rounds,
            license_manifest,
            source_snapshots,
            round_names,
            loose_output_names,
            readme_collections,
            repository_root,
            record_version,
            native_funding,
            round_reports,
            PUBLISH_LOOSE_REPORT_PDFS,
        ),
        encoding="utf-8",
    )
    readme_pdf_path = release_dir / "README.pdf"
    _render_markdown_pdf(readme_path, readme_pdf_path)

    licenses_path = release_dir / "LICENSES.json"
    licenses_path.write_text(
        json.dumps(license_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    provenance_path = release_dir / "release_metadata.json"
    provenance = {
        "generated_at": datetime.now(UTC).isoformat(),
        "parser_repository": PARSER_REPOSITORY,
        "parser_release": PARSER_RELEASE_TAG,
        "parser_release_url": PARSER_RELEASE_URL,
        "parser_commit": _git_value(repository_root, "rev-parse", "HEAD"),
        "parser_worktree_dirty": bool(
            _git_value(repository_root, "status", "--porcelain", "--untracked-files=no")
        ),
        "record_version": record_version,
        "source_repository": SOURCE_REPOSITORY,
        "source_snapshots": source_snapshots,
        "zenodo_funding": native_funding,
        "funders": _funding_funder_directory(native_funding),
        "funding_attributions": build_funding_attributions(repository_root, selected_rounds),
        "rounds": list(selected_rounds),
        # Every uploaded payload file is hashed here except SHA256SUMS and this
        # file, neither of which can contain its own digest.
        "artifacts": [
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": _file_digest(path, "sha256"),
            }
            for path in (
                *artifacts,
                *loose_outputs,
                *loose_report_paths,
                readme_path,
                readme_pdf_path,
                licenses_path,
            )
        ],
    }
    if extra_provenance:
        provenance.update(extra_provenance)
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    checksum_inputs = [
        *artifacts,
        *loose_outputs,
        *loose_report_paths,
        readme_path,
        readme_pdf_path,
        licenses_path,
        provenance_path,
    ]
    checksum_path = release_dir / "SHA256SUMS"
    checksum_path.write_text(
        "".join(
            f"{_file_digest(path, 'sha256')}  {path.name}\n" for path in checksum_inputs
        ),
        encoding="utf-8",
    )
    return [*checksum_inputs, checksum_path]


def build_license_manifest(
    repository_root: Path, rounds: Iterable[str]
) -> dict[str, Any]:
    """Build a model-level license manifest from the source YAML metadata."""
    models: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for round_id in rounds:
        metadata_dir = repository_root / "data" / round_id / "model-metadata"
        if not metadata_dir.is_dir():
            raise ValueError(f"Model metadata directory does not exist: {metadata_dir}")
        for path in sorted((*metadata_dir.glob("*.yaml"), *metadata_dir.glob("*.yml"))):
            if path.name.casefold() == "readme.md":
                continue
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            declared = raw.get("license")
            try:
                effective = _zenodo_license_id(normalize_license(declared))
            except ValueError:
                unresolved.append(f"{round_id}/{path.name}: {declared!r}")
                continue
            model_id = path.stem
            models.append(
                {
                    "round_id": round_id,
                    "model_id": model_id,
                    "declared_license": declared,
                    "effective_license": effective,
                    "license_url": LICENSE_URLS.get(effective),
                    "fallback_applied": False,
                    "source_metadata": f"data/{round_id}/model-metadata/{path.name}",
                    "covered_paths": [
                        f"data/{round_id}/model-output/{model_id}/",
                        f"output/{round_id}/{model_id}.jsonld",
                    ],
                }
            )

    if unresolved:
        raise ValueError(
            "Missing or unrecognized source licenses; obtain or verify the rights "
            "holder's terms before upload. No fallback license is assigned: "
            + "; ".join(unresolved)
        )
    license_counts: dict[str, int] = {}
    for model in models:
        license_id = model["effective_license"]
        license_counts[license_id] = license_counts.get(license_id, 0) + 1

    record_level_licenses = [
        license_id for license_id in RECORD_LICENSE_IDS if license_id in license_counts
    ]
    record_level_licenses.extend(
        sorted(set(license_counts).difference(record_level_licenses))
    )
    return {
        "schema_version": "1.0",
        "record_level_licenses": record_level_licenses,
        "license_counts": dict(sorted(license_counts.items())),
        "license_policy": "Preserve declared licenses; unresolved licenses block upload.",
        "models": models,
    }


def _license_policy_note(license_manifest: dict[str, Any] | None) -> str:
    """Describe the source license mapping without introducing new terms."""
    return (
        "LICENSES.json records the declared and effective license for every "
        "team-model and ensemble projection dataset. The Apache-2.0 license "
        "for the parser software does not relicense these research data."
    )


def _load_source_snapshots(
    repository_root: Path, rounds: Iterable[str]
) -> list[dict[str, Any]]:
    """Load the exact upstream tag and commit recorded during data retrieval."""
    snapshots: list[dict[str, Any]] = []
    required = ("repository", "ref", "ref_type", "commit", "retrieved_at")
    for round_id in rounds:
        path = repository_root / "data" / round_id / "source_snapshot.json"
        if not path.is_file():
            raise ValueError(
                f"Source provenance is missing for {round_id}; rerun "
                "pipeline/update_source_data.py for that round."
            )
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict) or any(
            not snapshot.get(field) for field in required
        ):
            raise ValueError(f"Source provenance is incomplete: {path}")
        snapshots.append({"round_id": round_id, **snapshot})
    return snapshots


def derive_record_title(round_name: str) -> str:
    """Title a single-round record round-first, using the hub's official name."""
    return (
        f"{round_name}: RSV Scenario Modeling Hub scenario projections "
        "and metadata"
    )


def derive_record_version(repository_root: Path, rounds: Iterable[str]) -> str:
    """Return the Zenodo-facing dataset version.

    Source tags and parser releases are recorded separately as provenance. The
    record version stays independent so it can be incremented when a deposited
    Zenodo version is revised.
    """
    _load_source_snapshots(repository_root, rounds)
    return DEFAULT_RECORD_VERSION


def _dataset_kind(name: str) -> str:
    return "Ensemble" if "ensemble" in name.casefold() else "Team-model"


def _projection_counts(models: Iterable[dict[str, Any]]) -> tuple[int, int]:
    names = [str(model.get("name") or model.get("model_id") or "") for model in models]
    ensembles = sum(_dataset_kind(name) == "Ensemble" for name in names)
    return len(names) - ensembles, ensembles


def _projection_count_text(models: Iterable[dict[str, Any]]) -> str:
    team_models, ensembles = _projection_counts(models)
    return f"{team_models} team-model output datasets and {ensembles} ensemble projection datasets"


def _round_archive_contents(repository_root: Path, round_id: str) -> tuple[bool, bool]:
    """Return (has_reports, has_abstracts) for a round's copied source data.

    The archive guide describes what a reader will actually find, so a round
    without abstracts must not advertise a directory that is not there.
    """
    round_data = repository_root / "data" / round_id
    reports_dir = round_data / "reports"
    has_reports = reports_dir.is_dir() and any(
        item.suffix.lower() == ".pdf" for item in reports_dir.iterdir()
    )
    abstracts_dir = round_data / "model-abstracts"
    has_abstracts = abstracts_dir.is_dir() and any(
        item.suffix.lower() == ".md" and item.name.casefold() != "readme.md"
        for item in abstracts_dir.rglob("*")
    )
    return has_reports, has_abstracts


def _single_round_archive_layout(
    repository_root: Path, rounds: tuple[str, ...]
) -> str:
    """Archive guide for the release README, round-specific when there is one round."""
    if len(rounds) != 1:
        return _archive_layout("<round_id>")
    has_reports, has_abstracts = _round_archive_contents(repository_root, rounds[0])
    return _archive_layout(
        rounds[0], has_reports=has_reports, has_abstracts=has_abstracts
    )


def _archive_layout(
    round_id: str,
    include_source: bool = True,
    include_generated: bool = True,
    *,
    has_reports: bool = True,
    has_abstracts: bool = True,
) -> str:
    rows = [["`README.md`", "This guide to the archive contents."]]
    if include_source:
        rows.extend([
            [f"`data/{round_id}/model-output/<team-model>/`", "Submitted team-model and ensemble projection results in Apache Parquet files."],
            [f"`data/{round_id}/model-metadata/`", "Submitted YAML files describing teams, models, ensembles, funding, and licenses."],
            [f"`data/{round_id}/hub-config/`", "SMH hub configuration, including tasks and metadata settings."],
        ])
        if has_reports:
            rows.append([
                f"`data/{round_id}/reports/`",
                "Round report PDFs listed by the hub. The same PDFs are attached separately to the Zenodo record.",
            ])
        if has_abstracts:
            rows.append([
                f"`data/{round_id}/model-abstracts/<team-model>/`",
                "Model abstracts (Markdown) submitted for this round.",
            ])
        rows.append(
            [f"`data/{round_id}/source_snapshot.json`", "Exact upstream repository, tag, commit, and retrieval time."]
        )
    if include_generated:
        rows.extend([
            [f"`output/{round_id}/`", "Generated JSON-LD metadata for individual team-model outputs and ensembles."],
            [f"`output/rsv-smh-{round_id}.jsonld`", "Consolidated metadata for the round."],
            [f"`output/rsv-smh-{round_id}.html`", "Human-readable catalog for the round; open in a web browser."],
        ])
    return (
        "Archive paths use the source round identifier. Team-model identifiers combine "
        "team and model abbreviations; ensemble identifiers contain 'ensemble' "
        "(case-insensitive).\n\n"
        + _markdown_table(["Path", "Contents"], rows)
        + f"\n\nFile format: {_markdown_link('SMH model output specification', SMH_OUTPUT_DOC_URL)}.\n"
    )


def _build_release_readme(
    rounds: tuple[str, ...],
    license_manifest: dict[str, Any],
    source_snapshots: list[dict[str, Any]],
    round_names: dict[str, str],
    loose_output_names: dict[str, list[str]],
    readme_collections: dict[str, dict[str, Any]],
    repository_root: Path,
    record_version: str,
    native_funding: list[dict[str, Any]],
    round_reports: dict[str, list[dict[str, Any]]] | None = None,
    reports_loose: bool = False,
) -> str:
    """Build the human-readable guide uploaded alongside the round archives."""
    license_by_model = {
        (str(model.get("round_id")), str(model.get("model_id"))): model
        for model in license_manifest["models"]
    }
    round_summary_rows = []
    for round_id in rounds:
        collection = readme_collections.get(round_id, {})
        metrics = _collection_metrics_for_readme(collection.get("hasPart") or [])
        round_doc = _round_documentation_url(collection)
        round_summary_rows.append(
            [
                round_names.get(round_id, round_id),
                round_id,
                metrics["team_models"],
                metrics["ensembles"],
                metrics["geographies"],
                metrics["age_groups"],
                metrics["targets"],
                ", ".join(metrics["output_types"]) or MISSING_DISPLAY,
                _markdown_link(f"rsv-smh-{round_id}.tar.gz", f"rsv-smh-{round_id}.tar.gz"),
                ", ".join(loose_output_names.get(round_id) or [MISSING_DISPLAY]),
                _markdown_link("round definition", round_doc) if round_doc else MISSING_DISPLAY,
            ]
        )
    source_rows = [
        [
            round_names.get(item["round_id"], item["round_id"]),
            item["round_id"],
            item["ref"],
            item["commit"],
            item["retrieved_at"],
        ]
        for item in source_snapshots
    ]
    license_rows = [
        [license_id, count]
        for license_id, count in license_manifest["license_counts"].items()
    ]
    single_round = len(rounds) == 1
    result_rows = _result_data_rows_for_readme(
        repository_root,
        readme_collections,
        rounds,
        round_names,
        include_round=not single_round,
    )
    result_headers = ["Team-model or ensemble", "Type", "Result data path inside archive", "Rows"]
    if not single_round:
        result_headers.insert(0, "Round")
    funding_rows = _funding_rows_for_readme(
        readme_collections,
        rounds,
        round_names,
        include_round=not single_round,
    )
    funding_headers = ["Team-model or ensemble", "Team/group", "Funding statement"]
    if not single_round:
        funding_headers.insert(0, "Round")
    funding_intro = (
        "Team-model funding statements below are preserved from each team's "
        "submitted model metadata, so grant acknowledgements stay associated "
        "with the model or group that reported them. Grants and funders are also "
        f"supplied to the Zenodo Funding section ({len(native_funding)} "
        "deduplicated entries), using registry identifiers where available and "
        "submitted funder names otherwise. release_metadata.json links these "
        "entries to the source statements and records parent/subaward relationships."
        if funding_rows
        else "No model-specific funding statements were recorded in the model metadata."
    )
    round_summary = ", ".join(
        round_names.get(round_id, round_id)
        for round_id in rounds
    )
    round_noun = "round" if single_round else "rounds"
    round_count_text = {1: "one", 2: "two", 3: "three"}.get(len(rounds), str(len(rounds)))
    parts = [
        "# RSV Scenario Modeling Hub data and metadata",
        (
            "This release contains respiratory syncytial virus (RSV) scenario "
            "projections submitted to the U.S. "
            f"{_markdown_link('RSV Scenario Modeling Hub', HUB_WEBSITE)}. These "
            "projections compare trajectories under different assumptions governing "
            "key features of interventions, pathogens, and populations that drive "
            "disease dynamics."
        ),
        (
            f"This upload covers {round_count_text} {round_noun}: {round_summary}. "
            f"The source round {'identifier is' if single_round else 'identifiers are'} "
            f"{', '.join(f'`{round_id}`' for round_id in rounds)}. It contains "
            f"{_projection_count_text(license_manifest['models'])}."
        ),
        "## Round summary",
        _markdown_table(
            [
                "Round",
                "Round ID",
                "Team-model outputs",
                "Ensembles",
                "Geographies",
                "Age groups",
                "Targets",
                "Output types",
                "Archive",
                "JSON-LD",
                "Hub round documentation",
            ],
            round_summary_rows,
        ),
        "## File layout",
        _markdown_table(
            ["Path or file", "Where it appears", "Purpose"],
            [
                [
                    "`rsv-smh-<round>.tar.gz`",
                    "Zenodo download",
                    "Self-contained round archive with source data, SMH hub configuration, model metadata, generated per-dataset JSON-LD, consolidated JSON-LD, consolidated HTML, and an archive guide.",
                ],
                [
                    "`rsv-smh-<round>.jsonld`",
                    "Zenodo download",
                    "Consolidated round-level Schema.org JSON-LD for metadata harvesting and direct retrieval.",
                ],
                [
                    "`README.md`",
                    "Zenodo download",
                    "Canonical human-readable documentation source.",
                ],
                [
                    "`README.pdf`",
                    "Zenodo download",
                    "PDF rendered from README.md and intended Zenodo default preview.",
                ],
                [
                    "`LICENSES.json`",
                    "Zenodo download",
                    "Declared licenses and their normalized identifiers for team-model and ensemble projection datasets.",
                ],
                [
                    "`release_metadata.json`",
                    "Zenodo download",
                    "Parser/source provenance, draft context, artifact sizes, and checksums.",
                ],
                [
                    "`SHA256SUMS`",
                    "Zenodo download",
                    "SHA-256 checksums for release files.",
                ],
                [
                    "Round report PDFs",
                    "Zenodo download and inside the archive",
                    "The reports the hub lists for this round. The same files appear twice on purpose: separately on this record, so they open in the Zenodo preview without downloading the archive, and under `data/<round_id>/reports/` inside the archive, so a downloaded copy is complete on its own. The Round reports section below names them.",
                ],
                [
                    "`output/*.html`",
                    "Inside the archive only",
                    "Generated human-readable team-model and ensemble catalog; open in a web browser.",
                ],
                [
                    "`data/<round_id>/model-output/<team-model>/*.parquet`",
                    "Inside the archive only",
                    "Actual submitted projection results. These are the data files described by the generated JSON-LD metadata.",
                ],
                [
                    "`data/<round_id>/model-abstracts/<team-model>/*.md`",
                    "Inside the archive only",
                    "Model abstracts submitted for this round, for the rounds that have them. Their text is also embedded in the generated metadata, the HTML catalog, and this README.",
                ],
            ],
        ),
        "### Inside each archive",
        _single_round_archive_layout(repository_root, rounds),
        "## Results data",
        (
            "The result data are Apache Parquet files inside the round archive, "
            "under `data/<round_id>/model-output/<team-model>/`. The JSON-LD file "
            "describes these datasets, but the projection "
            "records live in the Parquet files listed here."
        ),
        _markdown_table(result_headers, result_rows),
        (
            "After downloading and extracting the archive, read the Parquet files "
            "with arrow in R, or pandas or pyarrow in Python. Replace `path/to/file.parquet` "
            "in these examples with one of the paths listed above."
        ),
        _markdown_table(["Language/library", "Example"], [
            ["R / arrow", "`arrow::read_parquet('path/to/file.parquet')`"],
            ["Python / pandas", "`import pandas as pd; df = pd.read_parquet('path/to/file.parquet')`"],
            ["Python / pyarrow", "`import pyarrow.parquet as pq; table = pq.read_table('path/to/file.parquet')`"],
        ]),
        (
            "The projections follow the SMH file format, based on the hubverse "
            "format with differences for sample outputs. See the "
            f"{_markdown_link('SMH model output specification', SMH_OUTPUT_DOC_URL)}. "
            "For more information, visit the "
            f"{_markdown_link('Scenario Modeling Hub website', 'https://scenariomodelinghub.org/')} "
            f"and the {_markdown_link('hubverse website', 'https://hubverse.io/')}."
        ),
        *_round_reports_readme_parts(rounds, round_names, round_reports or {}, reports_loose),
        "## Zenodo record version",
        (
            f"Zenodo record version: `{record_version}`. The source tags, source "
            "commits, and parser release listed below identify the exact hub data "
            "and parser code this upload was built from. They are not the same as "
            "this Zenodo record's version number."
        ),
        "## Source provenance",
        f"The source repository is {_markdown_link(SOURCE_REPOSITORY, SOURCE_REPOSITORY)}.",
        _markdown_table(
            ["Round", "Source round ID", "Source tag", "Source commit", "Retrieved at"],
            source_rows,
        ),
        (
            f"Generated metadata was produced with {PARSER_RELEASE_TAG} of "
            f"{_markdown_link('smh-archiver', PARSER_RELEASE_URL)}. "
            "See release_metadata.json for the exact parser commit used to build this upload."
        ),
        "## Funding by team-model",
        COORDINATION_FUNDING,
        funding_intro,
        _markdown_table(funding_headers, funding_rows),
        "## Licensing",
        (
            "The upload is mixed-license. LICENSES.json is authoritative for assigning "
            "terms to each model-round dataset; no single license applies to every file."
        ),
        _markdown_table(["Effective license", "Model-round dataset count"], license_rows),
        _license_policy_note(license_manifest),
        DATASET_COPYRIGHT,
        "## Data dictionary",
        _readme_data_dictionary(readme_collections, rounds),
    ]
    for round_id in rounds:
        parts.append(
            _readme_round_catalog(
                round_id,
                readme_collections.get(round_id, {}),
                round_names.get(round_id, round_id),
                license_by_model,
                repository_root,
            )
        )
    parts.extend(
        [
            "## References",
            "Relevant scenario-modeling methodology and evaluation papers:",
            "\n".join(
                f"- {item['citation']} {_markdown_link(item['doi'], item['doi'])}"
                for item in RELATED_PUBLICATIONS
            ),
            "## Citation and contact",
            (
                "Cite the version-specific DOI shown on the Zenodo record for the "
                "release used in your analysis."
            ),
            (
                "Preserve team-model attribution and applicable license terms when "
                "redistributing permitted subsets. Contact: questions@midasnetwork.us."
            ),
        ]
    )
    return "\n\n".join(part for part in parts if part).rstrip() + "\n"


def _readme_round_catalog(
    round_id: str,
    collection: dict[str, Any],
    round_name: str,
    license_by_model: dict[tuple[str, str], dict[str, Any]],
    repository_root: Path,
) -> str:
    models = sorted(
        collection.get("hasPart") or [], key=lambda model: str(model.get("name", "")).lower()
    )
    documentation_url = _round_documentation_url(collection)
    sections = [
        f"## {round_name} team-model and ensemble catalog",
        f"This round contains {_projection_count_text(models)}.",
    ]
    if documentation_url:
        sections.append(
            f"Hub round documentation: {_markdown_link(documentation_url, documentation_url)}."
        )
    sections.extend(
        [
            "### Team-model outputs and ensembles at a glance",
            _markdown_table(
                [
                    "Team-model or ensemble",
                    "Team",
                    "Version",
                    "Declared license",
                    "Effective license",
                    "Age groups",
                    "Output types",
                    "Targets",
                    "Locations",
                    "Temporal coverage",
                ],
                [
                    _model_inventory_row(round_id, model, license_by_model)
                    for model in models
                ],
            ),
        ]
    )
    for model in models:
        sections.append(
            _readme_model_detail(round_id, model, license_by_model, repository_root)
        )
    return "\n\n".join(section for section in sections if section)


def _model_inventory_row(
    round_id: str,
    model: dict[str, Any],
    license_by_model: dict[tuple[str, str], dict[str, Any]],
) -> list[Any]:
    work = model.get("workExample") or {}
    model_name = str(model.get("name") or "Unknown model")
    producer = model.get("producer") or {}
    team = producer.get("name") if isinstance(producer, dict) else producer
    targets = work.get("variableMeasured") or []
    places = work.get("spatialCoverage") or []
    effective_license = _model_effective_license(round_id, model_name, model, license_by_model)
    return [
        model_name,
        team or MISSING_DISPLAY,
        model.get("version") or MISSING_DISPLAY,
        _format_license_value(model.get("license")),
        effective_license,
        len(work.get("ageGroups") or []),
        ", ".join(_iter_output_types(model)) or MISSING_DISPLAY,
        len(targets),
        len(places),
        _format_temporal_coverage(work.get("temporalCoverage")),
    ]


def _model_team_name(model: dict[str, Any]) -> str:
    producer = model.get("producer") or {}
    team = producer.get("name") if isinstance(producer, dict) else producer
    return _clean_text(team)


def _model_funding_statement(model: dict[str, Any]) -> str:
    producer = model.get("producer") or {}
    if not isinstance(producer, dict):
        return ""
    funder = producer.get("funder") or {}
    if not isinstance(funder, dict):
        return ""
    return _clean_text(funder.get("description"))


def build_native_funding(
    repository_root: Path, rounds: Iterable[str]
) -> list[dict[str, Any]]:
    """Build Zenodo native funding entries from verified funder/award metadata."""
    selected_rounds = tuple(rounds)
    entries = [
        _zenodo_funding_entry(
            "nigms",
            number="U24GM132013",
        ),
        _zenodo_funding_entry(
            "nigms",
            number="R24GM153920",
        ),
    ]
    for collection in _load_round_collections(repository_root, selected_rounds):
        for model in collection.get("hasPart") or []:
            if not isinstance(model, dict):
                continue
            entries.extend(
                _native_funding_entries_from_statement(
                    _model_funding_statement(model)
                )
            )
    return _dedupe_native_funding(entries)


def _native_funding_entries_from_statement(
    statement: str,
) -> list[dict[str, Any]]:
    normalized = statement.casefold()
    entries: list[dict[str, Any]] = []

    if "nu38ot000297" in normalized:
        entries.append(
            _zenodo_funding_entry(
                "cste",
                number="NU38OT000297",
            )
        )
        if "subaward po8169" in normalized:
            entries.append(_zenodo_funding_entry("cste", number="PO8169"))
    if "nu38ot00297" in normalized:
        entries.append(
            _zenodo_funding_entry(
                "cste",
                number="NU38OT00297",
            )
        )
    if "cdc-rfa-ft-23-0069" in normalized:
        entries.append(
            _zenodo_funding_entry(
                "cdc",
                number="CDC-RFA-FT-23-0069",
            )
        )
    if "200-2016-91781" in normalized:
        entries.append(
            _zenodo_funding_entry(
                "cdc",
                number="200-2016-91781",
            )
        )
    if "2127976" in normalized:
        entries.append(
            _zenodo_funding_entry("nsf", award_id="021nxhr62::2127976")
        )
    if "2135784" in normalized:
        entries.append(
            _zenodo_funding_entry("nsf", award_id="021nxhr62::2135784")
        )
    if "dms2436227" in normalized:
        entries.append(
            _zenodo_funding_entry(
                "nsf",
                number="DMS2436227",
            )
        )
    if "r01gm140564" in normalized:
        entries.append(
            _zenodo_funding_entry(
                "nih",
                number="R01GM140564",
            )
        )
    if "5r01ai102939" in normalized:
        entries.append(
            _zenodo_funding_entry(
                "nih",
                number="5R01AI102939",
            )
        )
    if "department of health and human services" in normalized:
        entries.append(_zenodo_funding_entry("hhs"))
    if "department of homeland security" in normalized:
        entries.append(_zenodo_funding_entry("dhs"))
    if "california department of public health" in normalized:
        entries.append(_zenodo_funding_entry("california_dph"))
    if "johns hopkins university" in normalized:
        entries.append(_zenodo_funding_entry("jhu"))
    if "amazon web services" in normalized:
        entries.append(_zenodo_funding_entry("aws"))
    if "scenario modeling hub fellowship" in normalized:
        entries.append(_zenodo_funding_entry("smh", title="Scenario Modeling Hub fellowship"))
    return entries


def build_funding_attributions(
    repository_root: Path, rounds: Iterable[str]
) -> list[dict[str, Any]]:
    """Connect native funding to submitted statements and reported subawards."""
    attributions = _extract_discovery_metadata(
        _load_round_collections(repository_root, tuple(rounds))
    )["funding"]
    for item in attributions:
        statement = item["statement"]
        item["zenodo_funding"] = _native_funding_entries_from_statement(statement)
        if "nu38ot000297" in statement.casefold() and "subaward po8169" in statement.casefold():
            item["award_relationships"] = [{
                "relation": "subaward_of",
                "funder": {"id": ZENODO_FUNDERS["cste"]["id"]},
                "award": {"number": "PO8169"},
                "parent_award": {"number": "NU38OT000297"},
            }]
    return attributions


def _funding_funder_directory(entries: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    known = {item["id"]: item for item in ZENODO_FUNDERS.values() if "id" in item}
    directory: dict[tuple[str, str], dict[str, str]] = {}
    for entry in entries:
        funder = entry["funder"]
        identifier = funder.get("id")
        if identifier:
            item = {"id": identifier}
            name = known.get(identifier, {}).get("name") or funder.get("name")
            if name:
                item["name"] = name
            if identifier in known:
                item["url"] = f"https://ror.org/{identifier}"
            key = ("id", identifier)
        else:
            item = {"name": funder["name"]}
            key = ("name", funder["name"].casefold())
        directory.setdefault(key, item)
    return list(directory.values())


def _zenodo_funding_entry(
    funder_key: str,
    *,
    award_id: str | None = None,
    number: str | None = None,
    title: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    funder = ZENODO_FUNDERS[funder_key]
    entry: dict[str, Any] = {
        "funder": {"id": funder["id"]} if funder.get("id") else {"name": funder["name"]}
    }
    award: dict[str, Any] = {}
    if award_id:
        award["id"] = award_id
    if number:
        award["number"] = number
    if title:
        award["title"] = {"en": title}
    if url:
        award["identifiers"] = [{"scheme": "url", "identifier": url}]
    if award:
        entry["award"] = award
    return entry


def _dedupe_native_funding(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for entry in entries:
        funder = entry.get("funder") or {}
        award = entry.get("award") or {}
        key = (
            str(funder.get("id") or ""),
            str(funder.get("name") or "").casefold() if not funder.get("id") else "",
            str(award.get("id") or ""),
            str(award.get("number") or ""),
            json.dumps(award.get("title") or {}, sort_keys=True)
            if not (award.get("id") or award.get("number")) else "",
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _funding_rows_for_readme(
    readme_collections: dict[str, dict[str, Any]],
    rounds: tuple[str, ...],
    round_names: dict[str, str],
    *,
    include_round: bool = True,
) -> list[list[str]]:
    rows: list[list[str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for round_id in rounds:
        collection = readme_collections.get(round_id, {})
        for model in sorted(
            collection.get("hasPart") or [],
            key=lambda item: str(item.get("name", "")).casefold(),
        ):
            if not isinstance(model, dict):
                continue
            funding = _model_funding_statement(model)
            if not funding:
                continue
            model_name = _clean_text(model.get("name")) or "Unknown model"
            team = _model_team_name(model) or MISSING_DISPLAY
            key = (round_id, model_name, team, funding)
            if key in seen:
                continue
            seen.add(key)
            row = [model_name, team, funding]
            if include_round:
                row.insert(0, round_names.get(round_id, round_id))
            rows.append(row)
    return rows


def _result_data_rows_for_readme(
    repository_root: Path,
    readme_collections: dict[str, dict[str, Any]],
    rounds: tuple[str, ...],
    round_names: dict[str, str],
    *,
    include_round: bool = True,
) -> list[list[str]]:
    rows: list[list[str]] = []
    for round_id in rounds:
        collection = readme_collections.get(round_id, {})
        for model in sorted(
            collection.get("hasPart") or [],
            key=lambda item: str(item.get("name", "")).casefold(),
        ):
            if not isinstance(model, dict):
                continue
            model_name = _clean_text(model.get("name")) or "Unknown model"
            output_dir = repository_root / "data" / round_id / "model-output" / model_name
            parquet_files = sorted(output_dir.glob("*.parquet"))
            if not parquet_files:
                row = [model_name, _dataset_kind(model_name), MISSING_DISPLAY, MISSING_DISPLAY]
                if include_round:
                    row.insert(0, round_names.get(round_id, round_id))
                rows.append(row)
                continue
            for path in parquet_files:
                archive_path = path.relative_to(repository_root).as_posix()
                row = [
                    model_name,
                    _dataset_kind(model_name),
                    f"`{archive_path}`",
                    _format_integer(_parquet_row_count(path)),
                ]
                if include_round:
                    row.insert(0, round_names.get(round_id, round_id))
                rows.append(row)
    return rows


def _parquet_row_count(path: Path) -> int | None:
    try:
        import pyarrow.parquet as pq

        return int(pq.ParquetFile(path).metadata.num_rows)
    except Exception:
        return None


def _format_integer(value: int | None) -> str:
    return f"{value:,}" if value is not None else MISSING_DISPLAY


def _round_reports_readme_parts(
    rounds: tuple[str, ...],
    round_names: dict[str, str],
    round_reports: dict[str, list[dict[str, Any]]],
    reports_loose: bool,
) -> list[str]:
    rows = []
    for round_id in rounds:
        for report in round_reports.get(round_id, []):
            filename = report["filename"]
            rows.append(
                [
                    round_names.get(round_id, round_id),
                    report.get("short_description") or DEFAULT_REPORT_NAME,
                    f"`{filename}`" if reports_loose else MISSING_DISPLAY,
                    f"`data/{round_id}/reports/{filename}`",
                ]
            )
    if not rows:
        return []
    intro = (
        "Report PDFs published by the hub for each round are included in the round "
        "archives"
        + (" and uploaded as separate files on this record." if reports_loose else ".")
        + " Reports are hub-authored documents and are not covered by the licenses "
        "declared for model projection data. The hub does not assign the reports DOIs of "
        "their own, so cite this record's DOI when referring to the copies here."
    )
    return [
        "## Round reports",
        intro,
        _markdown_table(
            ["Round", "Report", "Loose file on this record", "Path inside archive"], rows
        ),
    ]


def _demote_markdown_headings(text: str, levels: int) -> str:
    return re.sub(r"^(#+)", lambda m: m.group(1) + "#" * levels, text, flags=re.MULTILINE)


def _readme_model_detail(
    round_id: str,
    model: dict[str, Any],
    license_by_model: dict[tuple[str, str], dict[str, Any]],
    repository_root: Path,
) -> str:
    model_name = str(model.get("name") or "Unknown model")
    work = model.get("workExample") or {}
    team = _model_team_name(model)
    funding = _model_funding_statement(model)
    output_context = _model_output_context(repository_root, round_id, model_name)
    output_type_metadata = work.get("output_type_metadata") or output_context["metadata"]
    sections = [
        f"### {model_name}",
        _markdown_table(
            ["Field", "Value"],
            [
                ["Dataset type", _dataset_kind(model_name)],
                ["Team", team or MISSING_DISPLAY],
                ["Version", model.get("version") or MISSING_DISPLAY],
                ["Declared license", _format_license_value(model.get("license"))],
                [
                    "Effective Zenodo license",
                    _model_effective_license(round_id, model_name, model, license_by_model),
                ],
                [
                    "Website",
                    _markdown_link(str(model.get("website")), str(model.get("website")))
                    if model.get("website")
                    else MISSING_DISPLAY,
                ],
                ["Temporal coverage", _format_temporal_coverage(work.get("temporalCoverage"))],
                ["Output types", ", ".join(_iter_output_types(model)) or MISSING_DISPLAY],
                ["Age groups", ", ".join(map(str, work.get("ageGroups") or [])) or MISSING_DISPLAY],
                ["Spatial locations", _format_location_summary(work.get("spatialCoverage") or [])],
                ["Funding", funding or MISSING_DISPLAY],
            ],
        ),
    ]
    if model.get("description"):
        sections.extend(["#### Description", str(model["description"])])
    abstract = str(model.get("abstract") or "").strip()
    if abstract:
        # The abstract's own `##` headings must sit below this `####` subsection.
        sections.extend(["#### Model abstract", _demote_markdown_headings(abstract, 3)])
    data_sources = _clean_text((model.get("isBasedOn") or {}).get("description"))
    if data_sources:
        sections.extend(["#### Data sources", data_sources])
    author_rows = [
        [
            author.get("name") or MISSING_DISPLAY,
            (author.get("affiliation") or {}).get("name") or MISSING_DISPLAY,
            author.get("email") or MISSING_DISPLAY,
        ]
        for author in model.get("author") or []
        if isinstance(author, dict)
    ]
    if author_rows:
        sections.extend(
            [
                "#### Authors",
                _markdown_table(["Name", "Affiliation", "Email"], author_rows),
            ]
        )
    target_rows = [
        _target_row(variable) for variable in work.get("variableMeasured") or []
    ]
    if target_rows:
        sections.extend(
            [
                "#### Targets",
                _markdown_table(
                    [
                        "Target",
                        "Target ID",
                        "Unit",
                        "Type",
                        "Temporal unit",
                        "Possible output types",
                        "Ontology",
                    ],
                    target_rows,
                ),
            ]
        )
    output_rows = [
        _output_type_row(output_type, output_type_metadata, model_name)
        for output_type in _iter_output_types(model)
    ]
    if output_rows:
        sections.extend(
            [
                "#### Output types",
                _markdown_table(
                    ["Output type", "Meaning", "Dataset-specific metadata", "Ontology"],
                    output_rows,
                ),
            ]
        )
    location_rows = [
        [
            place.get("iso3166-2:code") or place.get("gn:fipsCode") or MISSING_DISPLAY,
            place.get("gn:name") or MISSING_DISPLAY,
            place.get("@id") or MISSING_DISPLAY,
        ]
        for place in work.get("spatialCoverage") or []
        if isinstance(place, dict)
    ]
    if location_rows:
        sections.extend(
            [
                "#### Spatial coverage",
                _markdown_table(["Code", "Name", "Geographic identifier"], location_rows),
            ]
        )
    if output_context["headers"] and output_context["rows"]:
        sections.extend(
            [
                "#### Projection data snippet",
                _markdown_table(output_context["headers"], output_context["rows"]),
            ]
        )
        if output_context["hidden_columns"]:
            sections.append(
                "Snippet note: example records only. Columns not shown: "
                + ", ".join(output_context["hidden_columns"])
                + f". Full schema: {_markdown_link('SMH model output specification', SMH_OUTPUT_DOC_URL)}."
            )
    elif output_context["error"]:
        sections.append(f"Projection data snippet unavailable: {output_context['error']}.")
    return "\n\n".join(section for section in sections if section)


def _readme_data_dictionary(
    readme_collections: dict[str, dict[str, Any]], rounds: tuple[str, ...]
) -> str:
    models = [
        model
        for round_id in rounds
        for model in (readme_collections.get(round_id, {}).get("hasPart") or [])
    ]
    output_types: dict[str, Any] = {}
    targets: dict[str, dict[str, Any]] = {}
    for model in models:
        for output_type in _iter_output_types(model):
            output_types.setdefault(_normalize_output_type_name(output_type), output_type)
        for variable in (model.get("workExample") or {}).get("variableMeasured") or []:
            if not isinstance(variable, dict):
                continue
            key = str(variable.get("target_id") or variable.get("name") or "")
            if key:
                targets.setdefault(key, variable)
    sections: list[str] = []
    if targets:
        sections.extend(
            [
                "### Targets",
                _markdown_table(
                    [
                        "Target",
                        "Target ID",
                        "Unit",
                        "Type",
                        "Temporal unit",
                        "Possible output types",
                        "Ontology",
                    ],
                    [_target_row(variable) for _, variable in sorted(targets.items())],
                ),
            ]
        )
    if output_types:
        sections.extend(
            [
                "### Output types",
                _markdown_table(
                    ["Output type", "Meaning", "Ontology"],
                    [
                        _output_type_dictionary_row(output_type)
                        for _, output_type in sorted(output_types.items())
                    ],
                ),
            ]
        )
    return "\n\n".join(sections)


def _collection_metrics_for_readme(models: list[dict[str, Any]]) -> dict[str, Any]:
    team_models, ensembles = _projection_counts(models)
    geographies, age_groups, targets, output_types = set(), set(), set(), set()
    for model in models:
        work = model.get("workExample") or {}
        for place in work.get("spatialCoverage") or []:
            if isinstance(place, dict):
                geographies.add(place.get("iso3166-2:code") or place.get("gn:name"))
        age_groups.update(work.get("ageGroups") or [])
        for variable in work.get("variableMeasured") or []:
            if isinstance(variable, dict):
                targets.add(variable.get("target_id") or variable.get("name"))
        output_types.update(_iter_output_types(model))
    return {
        "models": len(models),
        "team_models": team_models,
        "ensembles": ensembles,
        "geographies": len({item for item in geographies if item}),
        "age_groups": len({item for item in age_groups if item}),
        "targets": len({item for item in targets if item}),
        "output_types": sorted(str(item) for item in output_types if item),
    }


def _target_row(variable: dict[str, Any]) -> list[Any]:
    ontology = variable.get("identifier")
    return [
        variable.get("name") or MISSING_DISPLAY,
        variable.get("target_id") or MISSING_DISPLAY,
        variable.get("unitText") or MISSING_DISPLAY,
        variable.get("target_type") or MISSING_DISPLAY,
        variable.get("temporalUnit") or MISSING_DISPLAY,
        _join_values(variable.get("available_output_types")),
        _markdown_link("ontology reference", str(ontology)) if ontology else MISSING_DISPLAY,
    ]


def _output_type_dictionary_row(output_type: Any) -> list[Any]:
    definition = get_output_type_definition(output_type)
    if not definition:
        return [output_type, MISSING_DISPLAY, MISSING_DISPLAY]
    ontology = definition.get("iri")
    return [
        output_type,
        f"{definition.get('label')}: {definition.get('definition')}",
        _markdown_link(
            f"{definition_source_label(definition.get('definition_source'))} reference",
            human_iri_link(ontology),
        )
        if ontology
        else MISSING_DISPLAY,
    ]


def _output_type_row(
    output_type: Any, output_type_metadata: Any, model_name: str
) -> list[Any]:
    definition = get_output_type_definition(output_type)
    metadata = (
        output_type_metadata.get(_normalize_output_type_name(output_type))
        if isinstance(output_type_metadata, dict)
        else None
    )
    ontology = definition.get("iri") if definition else None
    meaning = (
        f"{definition.get('label')}: {definition.get('definition')}"
        if definition
        else MISSING_DISPLAY
    )
    return [
        output_type,
        meaning,
        _format_output_type_metadata(output_type, metadata, is_ensemble="ensemble" in model_name.lower()),
        _markdown_link(
            f"{definition_source_label(definition.get('definition_source'))} reference",
            human_iri_link(ontology),
        )
        if ontology
        else MISSING_DISPLAY,
    ]


def _format_output_type_metadata(
    output_type: Any, metadata: Any, *, is_ensemble: bool = False
) -> str:
    if not isinstance(metadata, dict) or not metadata:
        return MISSING_DISPLAY
    normalized = _normalize_output_type_name(output_type)
    if normalized == "sample":
        missing = metadata.get("missing_columns")
        if missing:
            return "Sample metadata unavailable; missing " + _join_values(missing)
        sample_count = metadata.get("sample_count")
        compound = metadata.get("compound_task_id_set", [])
        return (
            f"Number of samples: {sample_count}; compound task ID set: "
            f"{_join_values(compound, empty='[]')}; "
            f"{_markdown_link('SMH sample output documentation', SMH_OUTPUT_DOC_URL + '#sample')}"
        )
    if normalized == "quantile" and metadata.get("quantiles"):
        label = "Calculated quantiles" if is_ensemble else "Submitted quantiles"
        return f"{label}: {_join_values(metadata['quantiles'])}"
    return json.dumps(metadata, sort_keys=True)


def _model_output_context(
    repository_root: Path, round_id: str, model_name: str
) -> dict[str, Any]:
    try:
        df = load_model_output_from_dir(
            repository_root / "data" / round_id / "model-output" / model_name,
            model_name,
        )
    except Exception as exc:
        return {
            "headers": [],
            "rows": [],
            "hidden_columns": [],
            "metadata": {},
            "error": str(exc),
        }
    metadata = summarize_output_type_metadata(df) if not df.empty else {}
    if df.empty:
        return {
            "headers": [],
            "rows": [],
            "hidden_columns": [],
            "metadata": metadata,
            "error": "no parquet rows were found",
        }
    display_columns = [column for column in README_SNIPPET_DISPLAY_COLUMNS if column in df.columns]
    if not display_columns:
        display_columns = list(df.columns[:8])
    hidden_columns = [column for column in df.columns if column not in display_columns]
    display_df = df[display_columns]
    if len(display_df) > README_SNIPPET_ROWS:
        import pandas as pd

        display_df = pd.concat(
            [
                display_df.head(README_SNIPPET_ROWS),
                pd.DataFrame(
                    [[ELLIPSIS_DISPLAY] * len(display_columns)],
                    columns=display_columns,
                ),
                display_df.tail(1),
            ],
            ignore_index=True,
        )
    rows = [
        [_format_readme_value(value) for value in row]
        for row in display_df.itertuples(index=False, name=None)
    ]
    return {
        "headers": display_columns,
        "rows": rows,
        "hidden_columns": hidden_columns,
        "metadata": metadata,
        "error": None,
    }


def _format_readme_value(value: Any) -> str:
    try:
        import pandas as pd

        if pd.isna(value):
            return MISSING_DISPLAY
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _iter_output_types(model: dict[str, Any]) -> list[str]:
    types: list[str] = []
    for item in (model.get("workExample") or {}).get("output_type") or []:
        if isinstance(item, list):
            types.extend(str(value) for value in item)
        else:
            types.append(str(item))
    return sorted(dict.fromkeys(types))


def _normalize_output_type_name(output_type: Any) -> str:
    return str(output_type).strip().lower()


def _model_effective_license(
    round_id: str,
    model_name: str,
    model: dict[str, Any],
    license_by_model: dict[tuple[str, str], dict[str, Any]],
) -> str:
    manifest_entry = license_by_model.get((round_id, model_name))
    if manifest_entry:
        return str(manifest_entry.get("effective_license") or MISSING_DISPLAY)
    declared = normalize_license(model.get("license"))
    return _zenodo_license_id(declared) if declared else MISSING_DISPLAY


def _format_license_value(value: Any) -> str:
    normalized = normalize_license(value)
    return str(normalized or value or MISSING_DISPLAY)


def _format_temporal_coverage(value: Any) -> str:
    text = str(value or "")
    if "/" not in text:
        return text or MISSING_DISPLAY
    start, end = (part.strip().split(" ", 1)[0] for part in text.split("/", 1))
    return f"{start} to {end}"


def _format_location_summary(locations: list[dict[str, Any]]) -> str:
    if not locations:
        return MISSING_DISPLAY
    names = [
        str(place.get("gn:name") or place.get("iso3166-2:code") or "")
        for place in locations
        if isinstance(place, dict)
    ]
    names = [name for name in names if name]
    if len(names) <= 8:
        return ", ".join(names)
    return f"{len(names)} locations: {', '.join(names[:8])}, …"


def _round_documentation_url(collection: dict[str, Any]) -> str | None:
    for subject in as_list(collection.get("subjectOf")):
        if isinstance(subject, dict) and subject.get("@type") != "Report":
            return subject.get("url") or subject.get("sameAs")
    work_example = collection.get("workExample")
    if isinstance(work_example, dict):
        event = work_example.get("isPartOf")
        if isinstance(event, dict):
            return event.get("url") or event.get("sameAs")
    return collection.get("url") or collection.get("sameAs")


def _join_values(value: Any, *, empty: str = MISSING_DISPLAY) -> str:
    if value is None:
        return empty
    if isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value if str(item)]
        return ", ".join(values) if values else empty
    text = str(value)
    return text if text else empty


def _clean_text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _escape_html_text(value: Any) -> str:
    return html.escape(_clean_text(value), quote=True)


def _funding_notes_html(funding: list[dict[str, str]]) -> str:
    if not funding:
        return (
            "<p>No model-specific funding statements were recorded in the model "
            "metadata.</p>"
        )
    include_round = len(
        {
            _clean_text(item.get("round_id") or item.get("round_name"))
            for item in funding
            if _clean_text(item.get("round_id") or item.get("round_name"))
        }
    ) > 1
    items = []
    for item in funding:
        label_parts = [
            _escape_html_text(item.get("model")),
            _escape_html_text(item.get("group")),
        ]
        if include_round:
            label_parts.insert(0, _escape_html_text(item.get("round_name")))
        label = " — ".join(part for part in label_parts if part)
        items.append(
            f"<li><strong>{label}:</strong> "
            f"{_escape_html_text(item.get('statement'))}</li>"
        )
    return (
        "<p>Model-specific funding statements preserved from model metadata:</p>"
        f"<ul>{''.join(items)}</ul>"
    )


def _markdown_link(label: str, url: str | None) -> str:
    clean_label = _clean_text(label)
    clean_url = _clean_text(url)
    if not clean_url:
        return clean_label
    return f"[{clean_label}]({clean_url})"


def _markdown_table(headers: list[Any], rows: list[list[Any]]) -> str:
    if not rows:
        return "_None recorded._"
    header_line = "| " + " | ".join(_markdown_table_cell(header) for header in headers) + " |"
    delimiter = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(_markdown_table_cell(cell) for cell in row) + " |"
        for row in rows
    ]
    return "\n".join([header_line, delimiter, *body])


def _markdown_table_cell(value: Any) -> str:
    text = _clean_text(value) or MISSING_DISPLAY
    return text.replace("|", "\\|")


def upload_release(
    client: ZenodoClient,
    files: Iterable[Path],
    *,
    metadata: dict[str, Any],
    publish: bool,
    keep_failed_draft: bool = False,
    record_license_ids: Iterable[str] | None = None,
    native_funding: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    paths = tuple(path.resolve() for path in files)
    _validate_upload_files(paths)
    default_preview: str | None = None
    deposition = client.create_deposition(metadata)
    try:
        deposition_id = int(deposition["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ZenodoError("Zenodo did not return an ID for the new draft.") from exc
    links = deposition.get("links", {})

    try:
        if (
            metadata.get("copyright")
            or record_license_ids is not None
            or native_funding is not None
        ):
            client.enrich_draft_metadata(
                deposition_id,
                rights=(
                    record_license_ids
                    if record_license_ids is not None
                    else (str(metadata.get("license") or ""),)
                ),
                copyright_statement=metadata.get("copyright"),
                funding=native_funding,
            )
        bucket_url = links.get("bucket")
        if not isinstance(bucket_url, str):
            raise ZenodoError("Zenodo did not return a bucket URL for the new draft.")
        uploaded = [client.upload_file(bucket_url, path) for path in paths]
        default_preview = _set_default_preview_if_present(
            client, deposition_id, paths
        )
    except (OSError, requests.RequestException, ValueError, ZenodoError):
        if not keep_failed_draft:
            try:
                client.delete_deposition(deposition_id)
            except (
                OSError,
                requests.RequestException,
                ValueError,
                ZenodoError,
            ) as cleanup_error:
                print(
                    f"Warning: draft {deposition_id} could not be cleaned up: {cleanup_error}",
                    file=sys.stderr,
                )
        raise

    published_record: dict[str, Any] | None = None
    if publish:
        # A publish failure intentionally leaves the complete draft available for review.
        try:
            published_record = client.publish_deposition(deposition_id)
        except (OSError, requests.RequestException, ValueError, ZenodoError):
            print(
                f"Draft {deposition_id} is complete but could not be published; "
                "it remains available for review.",
                file=sys.stderr,
            )
            raise

    final_record = published_record or deposition
    final_links = final_record.get("links", {})
    return {
        "deposition_id": deposition_id,
        "published": publish,
        "doi": final_record.get("doi"),
        "url": (
            final_links.get("record_html")
            or final_links.get("html")
            or links.get("html")
            or links.get("self")
        ),
        "default_preview": default_preview,
        "files": [asdict(item) for item in uploaded],
    }


def _set_default_preview_if_present(
    client: ZenodoClient,
    deposition_id: int,
    paths: tuple[Path, ...],
    *,
    filename: str = DEFAULT_PREVIEW_FILE,
) -> str | None:
    if filename not in {path.name for path in paths}:
        return None
    client.set_default_preview(
        deposition_id, filename, order=(path.name for path in paths)
    )
    return filename


def publish_existing_draft(client: ZenodoClient, deposition_id: int) -> dict[str, Any]:
    record = client.publish_deposition(deposition_id)
    links = record.get("links", {})
    return {
        "deposition_id": deposition_id,
        "published": True,
        "doi": record.get("doi"),
        "url": links.get("record_html") or links.get("html") or links.get("self"),
    }


def update_draft_metadata_only(
    client: ZenodoClient,
    deposition_id: int,
    *,
    metadata: dict[str, Any],
    record_license_ids: Iterable[str] | None = None,
    native_funding: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Update a draft's metadata without touching any of its files.

    Deliberately separate from update_existing_draft(), which uploads new files and
    deletes obsolete ones. Metadata-only edits (sibling links, corrected titles)
    must never put an uploaded payload at risk.

    Ordering matters: the legacy deposition API is applied first, then the native
    record API adds the mixed-license list and copyright statement. The legacy
    serializer carries only one license, so applying it after the native call would
    erase the native rights and copyright fields.
    """
    updated = client.update_deposition(deposition_id, metadata)
    if (
        metadata.get("copyright")
        or record_license_ids is not None
        or native_funding is not None
    ):
        client.enrich_draft_metadata(
            deposition_id,
            rights=(
                record_license_ids
                if record_license_ids is not None
                else (str(metadata.get("license") or ""),)
            ),
            copyright_statement=metadata.get("copyright"),
            funding=native_funding,
        )
    links = updated.get("links", {})
    return {
        "deposition_id": deposition_id,
        "published": False,
        "url": links.get("html") or links.get("self"),
    }


def update_existing_draft(
    client: ZenodoClient,
    deposition_id: int,
    files: Iterable[Path],
    *,
    metadata: dict[str, Any],
    record_license_ids: Iterable[str] | None = None,
    native_funding: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Upload the desired release, remove known obsolete files, then update metadata."""
    paths = tuple(path.resolve() for path in files)
    _validate_upload_files(paths)
    deposition = client.get_deposition(deposition_id)
    if deposition.get("submitted"):
        raise ZenodoError(f"Deposition {deposition_id} is already published.")
    links = deposition.get("links", {})
    bucket_url = links.get("bucket")
    if not isinstance(bucket_url, str):
        raise ZenodoError("Zenodo did not return a bucket URL for the draft.")

    # Validate and apply metadata before touching files. If this fails, the draft's
    # existing file set is left unchanged.
    updated = client.update_deposition(deposition_id, metadata)
    if (
        metadata.get("copyright")
        or record_license_ids is not None
        or native_funding is not None
    ):
        client.enrich_draft_metadata(
            deposition_id,
            rights=(
                record_license_ids
                if record_license_ids is not None
                else (str(metadata.get("license") or ""),)
            ),
            copyright_statement=metadata.get("copyright"),
            funding=native_funding,
        )
    remote_by_name = {
        str(item.get("filename") or item.get("name")): item
        for item in deposition.get("files", [])
    }
    desired_names = {path.name for path in paths}
    unchanged: list[str] = []
    pending_uploads: list[Path] = []
    for path in paths:
        remote = remote_by_name.get(path.name)
        if remote and _md5_matches(path, remote.get("checksum")):
            unchanged.append(path.name)
        else:
            pending_uploads.append(path)
    uploaded = [client.upload_file(bucket_url, path) for path in pending_uploads]
    refreshed = client.get_deposition(deposition_id)
    removed: list[str] = []
    for remote_file in refreshed.get("files", []):
        filename = remote_file.get("filename") or remote_file.get("name")
        file_id = remote_file.get("id")
        if _is_obsolete_release_file(filename, desired_names) and file_id:
            client.delete_deposition_file(deposition_id, str(file_id))
            removed.append(str(filename))
    default_preview = _set_default_preview_if_present(client, deposition_id, paths)

    updated_links = updated.get("links", {})
    return {
        "deposition_id": deposition_id,
        "published": False,
        "url": updated_links.get("html") or links.get("html") or links.get("self"),
        "files": [asdict(item) for item in uploaded],
        "unchanged_files": sorted(unchanged),
        "removed_files": sorted(removed),
        "default_preview": default_preview,
        "metadata": updated.get("metadata"),
    }


def _person_name_key(name: Any) -> frozenset[str]:
    """Order-insensitive, casefolded token set for matching person names."""
    text = str(name).strip().rstrip(".").strip()
    if "," in text:
        family, _, given = text.partition(",")
        text = f"{given} {family}"
    return frozenset(token.rstrip(".").casefold() for token in text.split() if token.strip("."))


def _family_given_name(name: Any) -> str:
    """Render ``Given Family`` as ``Family, Given``; leave other forms alone."""
    text = str(name).strip()
    tokens = text.split()
    if "," in text or len(tokens) < 2:
        return text
    return f"{tokens[-1]}, {' '.join(tokens[:-1])}"


def build_metadata(
    args: argparse.Namespace,
    rounds: Iterable[str],
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, Any]:
    selected_rounds = tuple(rounds)
    collections = _load_round_collections(repository_root, selected_rounds)
    internal = _extract_discovery_metadata(collections)
    license_manifest = build_license_manifest(repository_root, selected_rounds)
    if args.creator:
        creators = [{"name": name} for name in args.creator]
    else:
        creators = []
        seen_names: set[frozenset[str]] = set()
        coordination = [dict(c) for c in COORDINATION_GROUP_CREATORS]
        model_people = [
            {**person, "name": _family_given_name(person["name"])}
            for person in internal["people"]
        ]
        for person in [*coordination, *model_people]:
            key = _person_name_key(person["name"])
            if key not in seen_names:
                seen_names.add(key)
                creators.append(person)
    round_labels = ", ".join(
        _display_round_name(item['name'])
        for item in internal["rounds"]
    )
    parts = [part for collection in collections for part in collection.get("hasPart") or []]
    single_round = len(internal["rounds"]) == 1
    coverage = (
        f"<p>This record covers {round_labels} RSV projections "
        f"(<code>round_id</code>: <code>{internal['rounds'][0]['round_id']}</code>) "
        "and contains "
        f"{_projection_count_text(parts)}.</p>"
        if single_round
        else (
            f"<p>It covers {len(internal['rounds'])} rounds and "
            f"{_projection_count_text(parts)}: {round_labels}. Source round IDs: "
            + ", ".join(
                f"<code>{item['round_id']}</code>" for item in internal["rounds"]
            )
            + ".</p>"
        )
    )
    description = args.description or (
        "<p>This dataset contains respiratory syncytial virus (RSV) scenario "
        "projections, associated model metadata files and model abstracts (if "
        "available) submitted to the U.S. "
        f'<a href="{HUB_WEBSITE}">RSV Scenario Modeling Hub (SMH)</a> '
        f'(<a href="{SOURCE_REPOSITORY}">GitHub repository</a>), '
        "as well as round-specific report(s) containing an executive summary "
        "with key messages and results, and analyses of ensemble and individual "
        "projections, together with generated JSON-LD and HTML describing the "
        "model outputs and metadata. README.md is the canonical human-facing documentation, "
        "and README.pdf is rendered from the same content for the intended Zenodo "
        "preview.</p>"
        f"{coverage}"
        "<p>Each round archive is self-contained and includes SMH hub configuration "
        "files, model metadata in YAML format, model outputs in Apache Parquet "
        "format, a generated JSON-LD file for each team-model output and ensemble, "
        "and consolidated round-level JSON-LD and HTML. JSON-LD files are directly "
        "fetchable for metadata harvesting. Outputs cover "
        "United States national and participating RSV-NET state locations, weekly "
        "hospitalization targets, multiple age groups, and scenarios. The model "
        "projections follow the SMH file format, based on the hubverse format "
        "with differences for sample outputs. See the "
        f'<a href="{SMH_OUTPUT_DOC_URL}">SMH model output specification</a>.</p>'
        "<p>The actual projection results are the Apache Parquet files inside "
        "each archive under "
        "<code>data/&lt;round_id&gt;/model-output/&lt;team-model&gt;/</code>. "
        "The JSON-LD files describe those result datasets for discovery and "
        "metadata harvesting.</p>"
        "<p>For more information on the U.S. Scenario Modeling Hub, including "
        "file formats, visit the "
        '<a href="https://scenariomodelinghub.org/">Scenario Modeling Hub website</a>. '
        "For more information on hubverse standards, visit the "
        '<a href="https://hubverse.io/">hubverse website</a>.</p>'
    )
    subjects = [RSV_MESH_SUBJECT, *internal["subjects"]]
    # A single-round record is titled from that round; the combined record keeps
    # the span title. Both use the same Zenodo-facing dataset version.
    title = args.title or (
        derive_record_title(_display_round_name(internal["rounds"][0]["name"]))
        if single_round
        else COMBINED_RECORD_TITLE
    )
    version = args.version or derive_record_version(repository_root, selected_rounds)
    metadata: dict[str, Any] = {
        "title": title,
        "upload_type": "dataset",
        "description": description,
        "copyright": DATASET_COPYRIGHT,
        "access_right": "open",
        "license": args.license or next(iter(license_manifest["record_level_licenses"]), None),
        "creators": creators,
        "contributors": internal["contributors"],
        "references": [
            f"{item['citation']} {item['doi']}" for item in RELATED_PUBLICATIONS
        ],
        "version": version,
        "keywords": [
            "respiratory syncytial virus",
            "Respiratory Syncytial Virus Infections",
            "RSV",
            "RSV-NET",
            "scenario modeling",
            "scenario projections",
            "RSV hospitalization",
            "weekly incident hospital admissions",
            "age-stratified projections",
            "state-level projections",
            "ensemble modeling",
            "infectious disease modeling",
            "hubverse",
            "Apache Parquet",
            "JSON-LD",
            "HTML",
            "YAML",
            "United States",
            *dict.fromkeys(f"{season} season" for season in re.findall(r"\b\d{4}-\d{4}\b", round_labels)),
        ],
        "subjects": sorted(subjects, key=lambda item: item["term"]),
        "dates": internal["dates"],
        "language": "eng",
        "method": (
            "Source snapshots are obtained from release-tagged rounds of the RSV "
            "Scenario Modeling Hub. Each data/{round}/source_snapshot.json records "
            "the exact upstream tag and commit. The smh-archiver pipeline preserves "
            "the SMH hub configuration files, model metadata, and model output projections; "
            "derives per-model and consolidated Schema.org JSON-LD; renders HTML "
            "views for the archive; and renders README.pdf from README.md for the "
            "Zenodo default preview and download copy. The round reports the hub "
            "declares in hub-config/report.json are copied into "
            "data/{round}/reports/ inside the archive and are also attached to this "
            "record as separate PDF files, so they can be read without downloading "
            "the archive. Model abstracts submitted under model-abstracts/ are "
            "carried into the archive for the rounds that have them, and their text "
            "is embedded in the generated metadata, the HTML catalog, and README.md. "
            "release_metadata.json records "
            "source snapshots, the parser release and commit, and artifact checksums."
        ),
        # An empty value clears the legacy Notes entry on existing drafts.
        "notes": "",
        "related_identifiers": [
            {
                "identifier": PARSER_REPOSITORY,
                "relation": "isCompiledBy",
                "resource_type": "software",
            },
            {
                "identifier": PARSER_RELEASE_URL,
                "relation": "isCompiledBy",
                "resource_type": "software",
            },
            {
                "identifier": SOURCE_REPOSITORY,
                "relation": "isDerivedFrom",
                "resource_type": "dataset",
            },
            *[
                {
                    "identifier": url,
                    "relation": "isDocumentedBy",
                    "resource_type": "publication-other",
                }
                for url in internal["documentation_urls"]
            ],
        ],
    }

    if args.metadata_file:
        custom = json.loads(args.metadata_file.read_text(encoding="utf-8"))
        if not isinstance(custom, dict):
            raise ValueError("The metadata file must contain a JSON object.")
        if isinstance(custom.get("metadata"), dict):
            custom = custom["metadata"]
        metadata.update(custom)

    if args.community:
        metadata["communities"] = [
            {"identifier": identifier} for identifier in args.community
        ]

    required = ("title", "upload_type", "description", "creators")
    missing = [field for field in required if not metadata.get(field)]
    if missing:
        raise ValueError(f"Required Zenodo metadata is missing: {', '.join(missing)}")
    return metadata


def _load_round_collections(
    repository_root: Path, rounds: Iterable[str]
) -> list[dict[str, Any]]:
    outputs = _find_consolidated_outputs(repository_root, tuple(rounds))
    collections: list[dict[str, Any]] = []
    for round_id, paths in outputs.items():
        jsonld_paths = [path for path in paths if path.suffix == ".jsonld"]
        if len(jsonld_paths) != 1:
            raise ValueError(
                f"Expected one consolidated JSON-LD file for {round_id}; "
                f"found {len(jsonld_paths)}."
            )
        data = json.loads(jsonld_paths[0].read_text(encoding="utf-8"))
        collections.append(data)
    return collections


def _extract_discovery_metadata(
    collections: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    people: dict[str, dict[str, Any]] = {}
    organizations: set[str] = set()
    subjects: dict[str, dict[str, str]] = {}
    dates: dict[tuple[str, str, str], dict[str, str]] = {}
    funding: list[dict[str, str]] = []
    funding_keys: set[tuple[str, str, str, str]] = set()
    documentation_urls: set[str] = set()
    rounds: list[dict[str, str]] = []
    model_round_count = 0

    for collection in collections:
        round_id = str(collection.get("roundId") or collection.get("identifier"))
        round_name = str(collection.get("name") or f"Round {round_id}")
        rounds.append({"round_id": round_id, "name": round_name})
        parts = collection.get("hasPart") or []
        model_round_count += len(parts)
        for part in parts:
            if not isinstance(part, dict):
                continue
            model_name = _clean_text(part.get("name")) or "Unknown model"
            producer_name = _model_team_name(part)
            if producer_name and "coordination group" not in producer_name.casefold():
                organizations.add(producer_name)
            statement = _model_funding_statement(part)
            if statement:
                key = (round_id, model_name, producer_name, statement)
                if key not in funding_keys:
                    funding_keys.add(key)
                    funding.append(
                        {
                            "round_id": round_id,
                            "round_name": _display_round_name(round_name),
                            "model": model_name,
                            "group": producer_name or MISSING_DISPLAY,
                            "statement": statement,
                        }
                    )

            for author in part.get("author") or []:
                if not isinstance(author, dict):
                    continue
                name = _clean_text(author.get("name"))
                if not name or "coordination group" in name.casefold():
                    continue
                key = " ".join(name.casefold().split())
                person = people.setdefault(key, {"name": name, "affiliations": set()})
                affiliation = author.get("affiliation") or {}
                if isinstance(affiliation, dict):
                    affiliation = affiliation.get("name")
                clean_affiliation = _clean_text(affiliation)
                if clean_affiliation:
                    person["affiliations"].update(
                        value
                        for part in clean_affiliation.split(";")
                        if (value := _clean_text(part))
                    )

            work = part.get("workExample") or {}
            for variable in work.get("variableMeasured") or []:
                if not isinstance(variable, dict):
                    continue
                term = _clean_text(variable.get("name"))
                identifier = _clean_text(variable.get("identifier"))
                if term and identifier:
                    subjects[identifier] = {
                        "term": term,
                        "identifier": identifier,
                        "scheme": "url",
                    }

            coverage = _clean_text(work.get("temporalCoverage"))
            if coverage and "/" in coverage:
                start, end = (_iso_date(value) for value in coverage.split("/", 1))
                dates[(start, end, round_id)] = {
                    "start": start,
                    "end": end,
                    "type": "Valid",
                    "description": _display_round_name(round_name),
                }

            event = work.get("isPartOf") or {}
            # ``subjectOf`` is a list: the round definition first, then any reports.
            subject_of = next(
                (
                    subject
                    for subject in as_list(event.get("subjectOf"))
                    if isinstance(subject, dict) and subject.get("@type") != "Report"
                ),
                {},
            )
            url = _clean_text(subject_of.get("url") or event.get("url"))
            if url:
                documentation_urls.add(url)

    model_people = [
        {
            "name": person["name"],
            **(
                {"affiliation": "; ".join(sorted(person["affiliations"]))}
                if person["affiliations"]
                else {}
            ),
        }
        for person in sorted(people.values(), key=lambda item: item["name"].casefold())
    ]
    contributors = [
        {"name": name, "type": "ResearchGroup"}
        for name in sorted(organizations, key=str.casefold)
    ]
    return {
        "people": model_people,
        "contributors": contributors,
        "subjects": sorted(subjects.values(), key=lambda item: item["term"]),
        "dates": sorted(dates.values(), key=lambda item: item["start"]),
        "funding": sorted(
            funding,
            key=lambda item: (
                item["round_id"],
                item["group"].casefold(),
                item["model"].casefold(),
                item["statement"].casefold(),
            ),
        ),
        "documentation_urls": sorted(documentation_urls),
        "rounds": sorted(rounds, key=lambda item: item["round_id"]),
        "model_round_count": model_round_count,
    }


def discover_rounds(repository_root: Path) -> tuple[str, ...]:
    data_dir = repository_root / "data"
    if not data_dir.is_dir():
        return ()
    return tuple(
        path.name
        for path in sorted(data_dir.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package SMH data and upload it to Zenodo Sandbox."
    )
    parser.add_argument(
        "--round",
        action="append",
        dest="rounds",
        help="Round directory to include; repeat as needed. Defaults to every data round.",
    )
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=DEFAULT_RELEASE_DIR,
        help=f"Archive output directory (default: {DEFAULT_RELEASE_DIR}).",
    )
    parser.add_argument(
        "--reuse-archives",
        action="store_true",
        help="Reuse existing non-empty archives instead of rebuilding them.",
    )
    parser.add_argument(
        "--split-by-round",
        action="store_true",
        help=(
            "Create one unpublished draft per round instead of a single combined "
            "record. Never publishes and never reserves a DOI."
        ),
    )
    parser.add_argument(
        "--skip-source-data",
        action="store_true",
        help="Do not package the source Parquet/configuration data.",
    )
    parser.add_argument(
        "--skip-generated-output",
        action="store_true",
        help="Do not package generated JSON-LD/HTML outputs.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help=(
            "Record title. Defaults to a round-first title for a single round, "
            f"or {COMBINED_RECORD_TITLE!r} for a combined record."
        ),
    )
    parser.add_argument(
        "--description",
        help="HTML-capable description. Defaults to one generated from internal metadata.",
    )
    parser.add_argument(
        "--creator",
        action="append",
        help="Creator name; repeat to preserve Zenodo creator order.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help=(
            f"Record version. Defaults to {DEFAULT_RECORD_VERSION!r}; source tags "
            "and parser releases are recorded separately as provenance."
        ),
    )
    parser.add_argument(
        "--license",
        default=None,
        help=(
            "Legacy record-level license identifier (defaults to a declared source "
            "license; native rights retain all model-specific licenses)."
        ),
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        help="JSON object whose values override the generated Zenodo metadata.",
    )
    parser.add_argument(
        "--community",
        action="append",
        help="Zenodo community identifier; repeat as needed (e.g. midas-network).",
    )
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help=f"Environment variable holding the token (default: {DEFAULT_TOKEN_ENV}).",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("ZENODO_API_URL", DEFAULT_API_URL),
        help="Zenodo API URL. Defaults to Zenodo Sandbox.",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and describe release files without contacting Zenodo.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Create, upload, and publish in one run. Otherwise a draft is created.",
    )
    parser.add_argument(
        "--publish-draft",
        type=int,
        metavar="DEPOSITION_ID",
        help="Publish an existing reviewed draft without repackaging or uploading.",
    )
    parser.add_argument(
        "--update-draft",
        type=int,
        metavar="DEPOSITION_ID",
        help="Replace release files and metadata in an existing unpublished draft.",
    )
    parser.add_argument(
        "--new-version-of",
        type=int,
        metavar="RECORD_ID",
        help=(
            "Open a new version of an already published record, upload the "
            "rebuilt release into it, and leave it unpublished."
        ),
    )
    parser.add_argument(
        "--acknowledge-rights-reviewed",
        action="store_true",
        help="Confirm model-specific redistribution rights were reviewed before publishing.",
    )
    parser.add_argument(
        "--keep-failed-draft",
        action="store_true",
        help="Keep a newly created draft if an upload fails.",
    )
    return parser


def _web_url_for(api_url: str) -> str:
    """Derive the web host from the API URL so record links follow --api-url."""
    parsed = urlparse(api_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Cannot derive a web URL from api_url: {api_url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def assert_no_reserved_doi(deposition: dict[str, Any]) -> None:
    """Fail if a draft carries a real DOI.

    Only the native ``pids.doi`` and the top-level ``doi`` are trusted. The legacy
    serializer's ``metadata.prereserve_doi`` is synthetic: it reports a production
    prefix (10.5281) for Sandbox drafts that have reserved nothing, so treating it
    as evidence of a DOI would make this check fail on every draft.
    """
    pids = deposition.get("pids")
    if isinstance(pids, dict):
        doi_pid = pids.get("doi")
        if isinstance(doi_pid, dict) and doi_pid.get("identifier"):
            raise ZenodoError(
                f"Draft {deposition.get('id')} unexpectedly carries DOI "
                f"{doi_pid['identifier']}; split mode must not reserve DOIs."
            )
    if deposition.get("doi"):
        raise ZenodoError(
            f"Draft {deposition.get('id')} unexpectedly carries DOI "
            f"{deposition['doi']}; split mode must not reserve DOIs."
        )


def load_split_manifest(path: Path, *, api_url: str) -> dict[str, Any]:
    """Load a split manifest, refusing one written against another Zenodo host."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"Split manifest must contain a JSON object: {path}")
    recorded = str(manifest.get("api_url") or "")
    if recorded != api_url:
        raise ValueError(
            f"Split manifest {path} was written for {recorded or '<unset>'}, "
            f"not {api_url}. Refusing to resume against a different host."
        )
    return manifest


def _write_split_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Persist progress. Called after every remote operation so a crash resumes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _record_new_version_in_split_manifest(
    manifest_path: Path,
    *,
    api_url: str,
    round_id: str,
    previous_record_id: int,
    deposition_id: int,
    version: str,
    files: Iterable[str],
) -> bool:
    """Point a split manifest's round at a freshly opened new-version draft.

    Without this the manifest keeps naming the previous, now published record,
    and the next split run refuses to touch it.
    """
    if not manifest_path.is_file():
        return False
    manifest = load_split_manifest(manifest_path, api_url=api_url)
    web_url = _web_url_for(api_url)
    rounds = manifest.setdefault("rounds", {})
    entry = dict(rounds.get(round_id) or {})
    entry.update(
        {
            "deposition_id": deposition_id,
            "previous_record_id": previous_record_id,
            "doi": None,
            "draft_url": f"{web_url}/uploads/{deposition_id}",
            "future_record_url": f"{web_url}/records/{deposition_id}",
            "files": list(files),
            "status": "draft",
            "version": version,
        }
    )
    rounds[round_id] = entry
    _write_split_manifest(manifest_path, manifest)
    return True


def assert_record_version_advances(
    client: "ZenodoClient", record_id: int, version: str
) -> str:
    """Refuse to open a new version that reuses the published record's version.

    A Zenodo version exists to tell two sets of deposited files apart, so
    publishing changed files under the version already on the parent record
    leaves two records in one lineage claiming to be the same thing. Returns the
    parent's version for reporting.
    """
    parent = client.get_deposition(record_id)
    parent_version = str((parent.get("metadata") or {}).get("version") or "").strip()
    if parent_version and parent_version == str(version).strip():
        raise ZenodoError(
            f"Record {record_id} is already published as version "
            f"{parent_version!r}, so a new version cannot reuse it. Increment "
            f"DEFAULT_RECORD_VERSION (currently {DEFAULT_RECORD_VERSION!r}) or "
            "pass --version with the next value."
        )
    return parent_version


def split_release_by_round(
    client: ZenodoClient | None,
    args: argparse.Namespace,
    rounds: Iterable[str],
    repository_root: Path,
) -> dict[str, Any]:
    """Create or resume one Sandbox draft per round. Never publishes.

    Drafts are created first so every deposition id is known before any release
    directory is built; sibling record URLs are recorded in the manifest and in
    each round's release_metadata.json, but deliberately not written into record
    metadata while the records are drafts (those URLs 404 until publication).
    """
    selected_rounds = tuple(rounds)
    release_root = args.release_dir.resolve()
    api_url = args.api_url
    web_url = _web_url_for(api_url)
    manifest_path = release_root / SPLIT_MANIFEST_NAME
    assert_pdf_renderer_available()

    # Validate every round locally before any network call, so a bad round fails
    # the whole run before partial drafts exist.
    per_round_metadata: dict[str, dict[str, Any]] = {}
    per_round_licenses: dict[str, list[str]] = {}
    per_round_native_funding: dict[str, list[dict[str, Any]]] = {}
    for round_id in selected_rounds:
        per_round_metadata[round_id] = build_metadata(
            args, (round_id,), repository_root
        )
        per_round_licenses[round_id] = build_license_manifest(
            repository_root, (round_id,)
        )["record_level_licenses"]
        per_round_native_funding[round_id] = build_native_funding(
            repository_root, (round_id,)
        )

    if args.dry_run:
        results = []
        for round_id in selected_rounds:
            files = build_release(
                repository_root=repository_root,
                release_dir=release_root / round_id,
                rounds=(round_id,),
                include_source_data=not args.skip_source_data,
                include_generated_output=not args.skip_generated_output,
                reuse_archives=args.reuse_archives,
                record_version=per_round_metadata[round_id]["version"],
                native_funding=per_round_native_funding[round_id],
            )
            results.append(
                {
                    "round_id": round_id,
                    "title": per_round_metadata[round_id]["title"],
                    "version": per_round_metadata[round_id]["version"],
                    "native_funding_entries": len(per_round_native_funding[round_id]),
                    "release_dir": str(release_root / round_id),
                    "files": [path.name for path in files],
                }
            )
        return {
            "dry_run": True,
            "split_by_round": True,
            "environment": "zenodo-sandbox",
            "api_url": api_url,
            "web_url": web_url,
            "rounds": results,
        }

    if manifest_path.is_file():
        manifest = load_split_manifest(manifest_path, api_url=api_url)
        recorded_commit = str(manifest.get("repository_commit") or "")
        current_commit = _git_value(repository_root, "rev-parse", "HEAD") or ""
        if recorded_commit and current_commit and recorded_commit != current_commit:
            print(
                f"Warning: split manifest was written at {recorded_commit[:8]} but "
                f"HEAD is {current_commit[:8]}; uploaded content may differ.",
                file=sys.stderr,
            )
    else:
        manifest = {
            "schema_version": SPLIT_MANIFEST_SCHEMA_VERSION,
            "environment": "zenodo-sandbox",
            "api_url": api_url,
            "web_url": web_url,
            "repository_commit": _git_value(repository_root, "rev-parse", "HEAD"),
            "created_at": datetime.now(UTC).isoformat(),
            "rounds": {},
        }

    assert client is not None
    entries: dict[str, dict[str, Any]] = manifest.setdefault("rounds", {})

    # Phase 1 - every draft exists and its id is persisted before anything is built.
    for round_id in selected_rounds:
        entry = entries.get(round_id) or {}
        deposition_id = entry.get("deposition_id")
        if deposition_id:
            existing = client.get_deposition(int(deposition_id))
            if existing.get("submitted"):
                raise ZenodoError(
                    f"Draft {deposition_id} for round {round_id} is already "
                    "published; refusing to modify it. Open a new version "
                    "instead with --round and --new-version-of."
                )
            assert_no_reserved_doi(existing)
        else:
            created = client.create_deposition(per_round_metadata[round_id])
            try:
                deposition_id = int(created["id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ZenodoError(
                    f"Zenodo did not return an ID for the {round_id} draft."
                ) from exc
            assert_no_reserved_doi(created)
            entry = {
                "deposition_id": deposition_id,
                "draft_url": f"{web_url}/uploads/{deposition_id}",
                "future_record_url": f"{web_url}/records/{deposition_id}",
                "doi": None,
                "release_dir": str(release_root / round_id),
                "status": "created",
            }
            entries[round_id] = entry
            _write_split_manifest(manifest_path, manifest)

    # Phase 2 - sibling URLs are known only once every draft exists.
    for round_id in selected_rounds:
        entries[round_id]["sibling_record_urls"] = [
            entries[other]["future_record_url"]
            for other in selected_rounds
            if other != round_id
        ]
    _write_split_manifest(manifest_path, manifest)

    # Phase 3 - build, upload, verify, one round at a time.
    for round_id in selected_rounds:
        entry = entries[round_id]
        deposition_id = int(entry["deposition_id"])
        release_files = build_release(
            repository_root=repository_root,
            release_dir=release_root / round_id,
            rounds=(round_id,),
            include_source_data=not args.skip_source_data,
            include_generated_output=not args.skip_generated_output,
            reuse_archives=args.reuse_archives,
            extra_provenance={
                "environment": "zenodo-sandbox",
                "api_url": api_url,
                "web_url": web_url,
                "deposition_id": deposition_id,
                "future_record_url": entry["future_record_url"],
                "sibling_record_urls": entry["sibling_record_urls"],
                "doi": None,
            },
            record_version=per_round_metadata[round_id]["version"],
            native_funding=per_round_native_funding[round_id],
        )
        update_draft_metadata_only(
            client,
            deposition_id,
            metadata=per_round_metadata[round_id],
            record_license_ids=per_round_licenses[round_id],
            native_funding=per_round_native_funding[round_id],
        )
        deposition = client.get_deposition(deposition_id)
        bucket_url = (deposition.get("links") or {}).get("bucket")
        if not isinstance(bucket_url, str):
            raise ZenodoError(f"No bucket URL for draft {deposition_id}.")
        remote = {
            item.get("filename"): item for item in deposition.get("files") or []
        }
        desired_names = {path.name for path in release_files}
        for path in release_files:
            existing = remote.get(path.name)
            if existing and _file_digest(path, "md5") == str(
                existing.get("checksum") or ""
            ).removeprefix("md5:"):
                continue
            client.upload_file(bucket_url, path)
        entry["status"] = "uploaded"
        entry["files"] = [path.name for path in release_files]
        entry["version"] = per_round_metadata[round_id]["version"]
        entry["native_funding_entries"] = len(per_round_native_funding[round_id])
        _write_split_manifest(manifest_path, manifest)

        refreshed = client.get_deposition(deposition_id)
        removed: list[str] = []
        for remote_file in refreshed.get("files", []):
            filename = remote_file.get("filename") or remote_file.get("name")
            file_id = remote_file.get("id")
            if _is_obsolete_release_file(filename, desired_names) and file_id:
                client.delete_deposition_file(deposition_id, str(file_id))
                removed.append(str(filename))
        entry["removed_files"] = sorted(removed)
        entry["default_preview"] = _set_default_preview_if_present(
            client, deposition_id, release_files
        )
        _write_split_manifest(manifest_path, manifest)

        final = client.get_deposition(deposition_id)
        assert_no_reserved_doi(final)
        uploaded_names = {item.get("filename") for item in final.get("files") or []}
        missing = [path.name for path in release_files if path.name not in uploaded_names]
        if missing:
            raise ZenodoError(
                f"Draft {deposition_id} is missing uploaded files: {', '.join(missing)}"
            )
        entry["status"] = "verified"
        _write_split_manifest(manifest_path, manifest)

    return {
        "split_by_round": True,
        "published": False,
        "environment": "zenodo-sandbox",
        "api_url": api_url,
        "web_url": web_url,
        "manifest": str(manifest_path),
        "rounds": [
            {
                "round_id": round_id,
                "deposition_id": entries[round_id]["deposition_id"],
                "title": per_round_metadata[round_id]["title"],
                "version": per_round_metadata[round_id]["version"],
                "draft_url": entries[round_id]["draft_url"],
                "future_record_url": entries[round_id]["future_record_url"],
                "status": entries[round_id]["status"],
                "default_preview": entries[round_id].get("default_preview"),
                "native_funding_entries": len(per_round_native_funding[round_id]),
            }
            for round_id in selected_rounds
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    selected_actions = sum(
        bool(action)
        for action in (
            args.publish,
            args.publish_draft,
            args.update_draft,
            args.new_version_of,
        )
    )
    if selected_actions > 1:
        parser.error(
            "Use only one of --publish, --publish-draft, --update-draft, or "
            "--new-version-of."
        )
    if args.dry_run and args.publish_draft:
        parser.error("--dry-run cannot be combined with --publish-draft.")
    if (args.publish or args.publish_draft) and not args.acknowledge_rights_reviewed:
        parser.error(
            "--publish and --publish-draft require --acknowledge-rights-reviewed."
        )
    if not args.dry_run and not os.getenv(args.token_env):
        parser.error(f"Set {args.token_env} before contacting Zenodo Sandbox.")

    if args.split_by_round:
        # Split mode creates several drafts at once, so any flag that targets or
        # publishes a single record is ambiguous or forbidden rather than ignored.
        for flag, value in (
            ("--publish", args.publish),
            ("--publish-draft", args.publish_draft),
            ("--update-draft", args.update_draft),
            ("--new-version-of", args.new_version_of),
        ):
            if value:
                parser.error(f"{flag} cannot be combined with --split-by-round.")
        if args.metadata_file:
            parser.error(
                "--metadata-file cannot be combined with --split-by-round; it has "
                "no per-round schema yet."
            )

    if args.publish_draft:
        try:
            client = ZenodoClient(
                os.environ[args.token_env],
                api_url=args.api_url,
                timeout=args.timeout,
            )
            result = publish_existing_draft(client, args.publish_draft)
        except (OSError, requests.RequestException, ValueError, ZenodoError) as exc:
            print(f"Zenodo release failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        print("Sandbox deposition published.", file=sys.stderr)
        return 0

    rounds = tuple(args.rounds or discover_rounds(REPOSITORY_ROOT))
    if not rounds:
        parser.error("No data round directories were found.")

    if args.split_by_round:
        if args.title and len(rounds) > 1:
            parser.error(
                "--title cannot be combined with --split-by-round across multiple "
                "rounds; one title cannot serve several records."
            )
        try:
            client = (
                None
                if args.dry_run
                else ZenodoClient(
                    os.environ[args.token_env],
                    api_url=args.api_url,
                    timeout=args.timeout,
                )
            )
            result = split_release_by_round(client, args, rounds, REPOSITORY_ROOT)
        except (OSError, requests.RequestException, ValueError, ZenodoError) as exc:
            print(f"Zenodo split release failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        if args.dry_run:
            print("Dry run complete; Zenodo was not contacted.", file=sys.stderr)
        else:
            print(
                f"{len(rounds)} Sandbox drafts created or updated; none published.",
                file=sys.stderr,
            )
        return 0

    try:
        metadata = build_metadata(args, rounds, REPOSITORY_ROOT)
        record_license_ids = build_license_manifest(REPOSITORY_ROOT, rounds)[
            "record_level_licenses"
        ]
        native_funding = build_native_funding(REPOSITORY_ROOT, rounds)
        release_files = build_release(
            repository_root=REPOSITORY_ROOT,
            release_dir=args.release_dir.resolve(),
            rounds=rounds,
            include_source_data=not args.skip_source_data,
            include_generated_output=not args.skip_generated_output,
            reuse_archives=args.reuse_archives,
            record_version=metadata["version"],
            native_funding=native_funding,
        )

        if args.dry_run:
            result = {
                "dry_run": True,
                "would_publish": args.publish,
                "would_update_draft": args.update_draft,
                "would_create_new_version_of": args.new_version_of,
                "rounds": list(rounds),
                "metadata": metadata,
                "native_funding": native_funding,
                "files": [
                    {
                        "path": str(path),
                        "bytes": path.stat().st_size,
                        "sha256": _file_digest(path, "sha256"),
                    }
                    for path in release_files
                ],
            }
        else:
            token = os.environ[args.token_env]
            client = ZenodoClient(token, api_url=args.api_url, timeout=args.timeout)
            if args.new_version_of:
                previous_version = assert_record_version_advances(
                    client, args.new_version_of, metadata["version"]
                )
                print(
                    f"Record {args.new_version_of} is published as "
                    f"{previous_version or 'an unrecorded version'}; opening "
                    f"version {metadata['version']}...",
                    file=sys.stderr,
                )
                deposition_id = client.new_version_draft_id(args.new_version_of)
                result = update_existing_draft(
                    client,
                    deposition_id,
                    release_files,
                    metadata=metadata,
                    record_license_ids=record_license_ids,
                    native_funding=native_funding,
                )
                result["new_version_of"] = args.new_version_of
                result.setdefault("deposition_id", deposition_id)
                if len(rounds) == 1:
                    result["split_manifest_updated"] = (
                        _record_new_version_in_split_manifest(
                            args.release_dir.resolve() / SPLIT_MANIFEST_NAME,
                            api_url=args.api_url,
                            round_id=rounds[0],
                            previous_record_id=args.new_version_of,
                            deposition_id=deposition_id,
                            version=metadata["version"],
                            files=[path.name for path in release_files],
                        )
                    )
            elif args.update_draft:
                result = update_existing_draft(
                    client,
                    args.update_draft,
                    release_files,
                    metadata=metadata,
                    record_license_ids=record_license_ids,
                    native_funding=native_funding,
                )
            else:
                result = upload_release(
                    client,
                    release_files,
                    metadata=metadata,
                    publish=args.publish,
                    keep_failed_draft=args.keep_failed_draft,
                    record_license_ids=record_license_ids,
                    native_funding=native_funding,
                )
    except (OSError, requests.RequestException, ValueError, ZenodoError) as exc:
        print(f"Zenodo release failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    if args.dry_run:
        print("Dry run complete; Zenodo was not contacted.", file=sys.stderr)
    elif args.publish:
        print("Sandbox deposition published.", file=sys.stderr)
    elif args.new_version_of:
        print(
            "New version draft opened and populated; it remains unpublished. "
            "Publish it with --publish-draft and --acknowledge-rights-reviewed.",
            file=sys.stderr,
        )
    elif args.update_draft:
        print(
            "Sandbox draft files and metadata updated; it remains unpublished.",
            file=sys.stderr,
        )
    else:
        print("Sandbox draft created; it has not been published.", file=sys.stderr)
    return 0


def _create_archive(
    entries: Iterable[tuple[Path, str]],
    destination: Path,
    *,
    reuse: bool,
    required_prefixes: Iterable[str],
) -> None:
    archive_entries = tuple(entries)
    if reuse and _archive_contains(destination, required_prefixes):
        print(
            f"Reusing {destination.name} "
            f"({_human_size(destination.stat().st_size)})...",
            file=sys.stderr,
        )
        return

    print(
        f"Building {destination.name} from {len(archive_entries)} source paths...",
        file=sys.stderr,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", delete=False
        ) as raw_stream:
            temporary_name = raw_stream.name
            with (
                gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw_stream, mtime=0
                ) as gzip_stream,
                tarfile.open(fileobj=gzip_stream, mode="w") as archive,
            ):
                for source, archive_name in archive_entries:
                    archive.add(
                        source,
                        arcname=archive_name,
                        recursive=True,
                        filter=_archive_filter,
                    )
        os.replace(temporary_name, destination)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def _archive_contains(destination: Path, required_prefixes: Iterable[str]) -> bool:
    if not destination.is_file() or destination.stat().st_size == 0:
        return False
    try:
        with tarfile.open(destination, "r:gz") as archive:
            names = set(archive.getnames())
    except (OSError, tarfile.TarError):
        return False
    return all(
        prefix in names or any(name.startswith(f"{prefix}/") for name in names)
        for prefix in required_prefixes
    )


def _archive_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if Path(info.name).name == ".DS_Store":
        return None
    path = Path(info.name)
    if path.name == "reports.json" and path.parent.name == "reports":
        return None
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def assert_pdf_renderer_available() -> None:
    """Fail early if HTML-to-PDF rendering cannot run in this environment."""
    _load_pdf_renderer()


def _load_pdf_renderer() -> tuple[Any, Any]:
    try:
        from weasyprint import CSS, HTML
    except Exception as exc:
        raise RuntimeError(
            "PDF conversion requires WeasyPrint and its native rendering "
            "libraries. Run `uv sync` to install Python dependencies. On macOS, "
            "if the import still fails, install the native package with "
            "`brew install weasyprint`."
        ) from exc
    return HTML, CSS


def _render_markdown_pdf(markdown_path: Path, pdf_path: Path) -> None:
    HTML, CSS = _load_pdf_renderer()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_text = markdown_path.read_text(encoding="utf-8")
    html_document = _markdown_to_html_document(markdown_text, title=markdown_path.name)
    try:
        HTML(string=html_document, base_url=str(markdown_path.parent)).write_pdf(
            str(pdf_path),
            stylesheets=[CSS(string=README_PDF_CSS)],
        )
    except Exception as exc:
        raise RuntimeError(
            f"Could not render PDF from {markdown_path.name}: {exc}"
        ) from exc
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"PDF rendering produced an empty file: {pdf_path}")


def _markdown_to_html_document(markdown_text: str, *, title: str) -> str:
    body = basic_markdown_to_html(markdown_text)
    # The generated README ends with this short section; keep it on one page.
    preceding, heading, following = body.partition("<h2>Citation and contact</h2>")
    if heading:
        body = f'{preceding}<section class="citation-contact">{heading}{following}</section>'
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
</head>
<body>
{body}
</body>
</html>
"""


def _is_obsolete_release_file(filename: Any, desired_names: set[str] | None = None) -> bool:
    name = str(filename or "")
    if not name or (desired_names is not None and name in desired_names):
        return False
    if name in OBSOLETE_RELEASE_FILES:
        return True
    if name.endswith(".html"):
        return True
    return name.startswith("Round_") and (
        name.endswith(".jsonld") or name.endswith(".pdf")
    )


def _load_round_reports(
    repository_root: Path, rounds: Iterable[str]
) -> dict[str, list[dict[str, Any]]]:
    """Read each round's reports.json sidecar; rounds without reports map to []."""
    reports: dict[str, list[dict[str, Any]]] = {}
    for round_id in rounds:
        reports_dir = repository_root / "data" / round_id / "reports"
        sidecar = reports_dir / "reports.json"
        entries: list[dict[str, Any]] = []
        if sidecar.is_file():
            for item in json.loads(sidecar.read_text(encoding="utf-8")):
                path = reports_dir / str(item.get("filename") or "")
                if path.is_file():
                    entries.append({**item, "source": path})
        reports[round_id] = entries
    return reports


def _loose_jsonld_name(round_id: str) -> str:
    return f"rsv-smh-{round_id}.jsonld"


def _remove_obsolete_local_release_files(release_dir: Path) -> None:
    for path in release_dir.iterdir():
        if path.is_file() and _is_obsolete_release_file(path.name):
            path.unlink()


def _round_display_names(outputs: dict[str, list[Path]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for round_id, paths in outputs.items():
        for path in paths:
            if path.suffix != ".jsonld":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                break
            names[round_id] = _display_round_name(payload.get("name"))
            break
        names.setdefault(round_id, round_id)
    return names


def _load_readme_collections(
    outputs: dict[str, list[Path]]
) -> dict[str, dict[str, Any]]:
    collections: dict[str, dict[str, Any]] = {}
    for round_id, paths in outputs.items():
        for path in paths:
            if path.suffix != ".jsonld":
                continue
            collections[round_id] = json.loads(path.read_text(encoding="utf-8"))
            break
    return collections


def _find_consolidated_outputs(
    repository_root: Path, rounds: tuple[str, ...]
) -> dict[str, list[Path]]:
    wanted = set(rounds)
    outputs: dict[str, list[Path]] = {round_id: [] for round_id in rounds}
    output_dir = repository_root / "output"
    if not output_dir.is_dir():
        raise ValueError(f"Generated output directory does not exist: {output_dir}")
    for path in sorted(output_dir.glob("*.jsonld")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        round_id = str(payload.get("roundId") or payload.get("identifier"))
        if round_id not in wanted or not isinstance(payload.get("hasPart"), list):
            continue
        outputs[round_id].append(path)
        html_path = path.with_suffix(".html")
        if html_path.is_file():
            outputs[round_id].append(html_path)
    missing = [round_id for round_id, paths in outputs.items() if not paths]
    if missing:
        raise ValueError(
            "No consolidated generated output found for round(s): " + ", ".join(missing)
        )
    return outputs


def _zenodo_license_id(value: str | None) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized not in LICENSE_IDS:
        raise ValueError(f"Missing or unrecognized license: {value!r}")
    return LICENSE_IDS[normalized]


def _display_round_name(value: Any) -> str:
    """Return the hub's official round name, e.g. "Round 1 - 2025-2026".

    RSV SMH restarts round numbering each season, so every season's first round is
    officially "Round 1 - <season>"; only internal_round_name increments across
    seasons. An earlier version stripped the "Round 1 - " prefix on the assumption
    that it was redundant, which discarded the official name the hub publishes.
    """
    name = _clean_text(value)
    suffix = " Scenario Projection Models Collection"
    name = name.removesuffix(suffix)
    return name or "Scenario round"


def _iso_date(value: str) -> str:
    return value.strip().split(" ", 1)[0]


def _validate_upload_files(paths: tuple[Path, ...]) -> None:
    if not paths:
        raise ValueError("At least one release file is required.")
    names: set[str] = set()
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Release file does not exist: {path}")
        if path.name in names:
            raise ValueError(f"Duplicate upload filename: {path.name}")
        names.add(path.name)


def _verify_zenodo_md5(path: Path, remote_checksum: Any) -> None:
    if not isinstance(remote_checksum, str) or not remote_checksum.startswith("md5:"):
        return
    expected = remote_checksum.removeprefix("md5:")
    if _file_digest(path, "md5") != expected:
        raise ZenodoError(f"Checksum verification failed for {path.name}.")


def _md5_matches(path: Path, remote_checksum: Any) -> bool:
    if not isinstance(remote_checksum, str):
        return False
    expected = remote_checksum.removeprefix("md5:")
    return _file_digest(path, "md5") == expected


def _file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(repository_root: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
