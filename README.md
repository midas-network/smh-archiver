This tool processes RSV Scenario Modeling Hub model outputs and generates
consolidated JSON-LD metadata files for each scenario round.

The current beta release is
[`v0.1.0-beta.1`](https://github.com/midas-network/smh-archiver/releases/tag/v0.1.0-beta.1).

## Installation

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
git clone https://github.com/midas-network/smh-archiver.git
cd smh-archiver
uv sync
```

This creates a `.venv` virtual environment and installs all dependencies from `uv.lock`. To run any script, prefix it with `uv run`:

```bash
uv run python run_pipeline.py
```

## Usage

### Quick Start: Run Complete Pipeline

The easiest way to run the entire pipeline is with a single command:

```bash
# Run all steps: update data, create JSON-LD, generate HTML
uv run python run_pipeline.py

# Skip data update and use existing data
uv run python run_pipeline.py --skip-update

# Process specific rounds only
uv run python run_pipeline.py --rounds 2024-07-28 2023-11-12

# Stop on first error
uv run python run_pipeline.py --stop-on-error
```

Options:
- `--skip-update`: Skip updating source data (use existing data)
- `--skip-jsonld`: Skip JSON-LD creation
- `--skip-html`: Skip HTML generation
- `--rounds ROUND_ID [ROUND_ID ...]`: Process only specific round IDs
- `--stop-on-error`: Stop pipeline execution on first error
- `--verbose`: Show verbose output

### Individual Pipeline Steps

You can also run each step of the pipeline individually:

### 1. Download Source Data

First, download the latest model output data:

```bash
uv run python pipeline/update_source_data.py
```

This will download the necessary model outputs and metadata from the RSV Forecasting Hub repository.

### 2. Generate JSON-LD Files

Each round is processed with the script that matches its Hubverse tasks-schema version.
Use `run_pipeline.py` to dispatch automatically:

```bash
uv run python run_pipeline.py
```

Or run a specific round manually:

```bash
# Hubverse schema v6.0.0 rounds (e.g. 2025-07-27)
uv run python pipeline/create_jsonld_v6_0_0.py --round_dir data/2025-07-27

# Hubverse schema v5.1.0 rounds (e.g. 2024-07-28, 2023-11-12)
uv run python pipeline/create_jsonld_v5_1_0.py --round_dir data/2024-07-28
uv run python pipeline/create_jsonld_v5_1_0.py --round_dir data/2023-11-12

# Process all v5.1.0 rounds at once
uv run python pipeline/create_jsonld_v5_1_0.py
```

Options:
- `--round_dir`: Path to a single round directory (e.g. `data/2025-07-27`)
- `--output`: Custom output directory (default: `output`)

The scripts will:
- Process all model outputs for the round
- Extract metadata including age groups, locations, and output types
- Generate consolidated JSON-LD files in the `output` directory. When
  `additional_metadata.round_name` is present, spaces are replaced with
  underscores (e.g. `output/Round_1_-_2025-2026_v6.0.0.jsonld`). Otherwise,
  filenames fall back to `round_<ROUND_ID>_v<SCHEMA_VERSION>.jsonld`.
- Add scenario/round definition links when `additional_metadata.internal_round_name`
  is present. These point to the hub repository markdown file, e.g.
  `https://github.com/midas-network/rsv-scenario-modeling-hub/blob/main/auxiliary-data/rounds/round3.md`.

### 3. Generate HTML Visualization

To convert JSON-LD files to HTML for easy viewing:

```bash
# Convert a specific v6.0.0 round
uv run python pipeline/jsonld_to_html.py -i output/round_2025-07-27_v6.0.0.jsonld -o output/round_2025-07-27_v6.0.0.html -r 2025-07-27

# Convert a specific v5.1.0 round
uv run python pipeline/jsonld_to_html.py -i output/round_2024-07-28_v5.1.0.jsonld -o output/round_2024-07-28_v5.1.0.html -r 2024-07-28
```

Options:
- `-i, --input`: Input JSON-LD file path (default: output/round_2024-07-28.jsonld)
- `-o, --output`: Output HTML file path (default: output/round_2024-07-28.html)
- `-r, --round-id`: Round identifier for loading sample data (default: 2024-07-28)
- `--no-sample-data`: Skip loading sample output data from parquet files

### 4. Run Complete Test Suite

To test the entire pipeline using pytest:

```bash
# Run all tests
uv run pytest test_pipeline.py

# Skip data update (use existing data)
uv run pytest test_pipeline.py --skip-update

# Run only specific tests
uv run pytest test_pipeline.py::TestCreateJsonLD

# Run with verbose output
uv run pytest test_pipeline.py -v

# Generate HTML test report
uv run pytest test_pipeline.py --html=report.html --self-contained-html

# Run tests in verbose mode with detailed output
uv run pytest test_pipeline.py --skip-update -v -s
```

Options:
- `--skip-update`: Skip updating source data (assumes data already exists)
- `-v, --verbose`: Increase verbosity
- `-s`: Show print statements (don't capture output)
- `-k EXPRESSION`: Only run tests matching the given expression
- `--html=FILE`: Generate an HTML test report
- `--self-contained-html`: Create a self-contained HTML report

## Output

The generated JSON-LD files contain comprehensive metadata about each forecasting round, including:
- Model descriptions and metadata
- Spatial coverage (US states and territories)
- Age group breakdowns
- Output types and targets
- Temporal coverage

Each round's data is saved as `round_[ROUND_ID].jsonld` in the output directory.

## Publish to Zenodo Sandbox

`publish_to_zenodo.py` creates one self-contained archive per round containing
its source Parquet/configuration data and generated JSON-LD/HTML. It also
uploads the consolidated JSON-LD as a `rsv-smh-<round>.jsonld` file for
metadata harvesting and creates `README.md`, `README.pdf`, `LICENSES.json`,
`release_metadata.json`, and `SHA256SUMS`. The generated HTML remains inside the
round archive instead of being uploaded separately, because Zenodo does not render
uploaded HTML on the record landing page.

Zenodo metadata is derived from the consolidated JSON-LD: the Scenario Modeling
Hub Coordination Group is the creator, model authors and teams are deduplicated
contributors, projection intervals become dates, measured-variable ontology
terms become subjects, and methods, keywords, and round documentation links are
retained. Model-team funding statements are preserved in `README.md` and
`README.pdf`, while deduplicated grants and funders are supplied through Zenodo's
native Funding section. Registry identifiers are used when available; otherwise
the submitted funder name is preserved, including Amazon Web Services and the
Scenario Modeling Hub fellowship. `release_metadata.json` includes a readable
funder directory and team-model source statements alongside the native funding
payload. It also records PSI's reported PO8169 subaward relationship to
NU38OT000297. Custom award titles come from the source rather than a generic
grant-type label. Missing or unrecognized
source licenses block the upload for clarification with the rights holder;
the publisher does not assign fallback terms. The uploader uses
Zenodo's native record API for the Funding section, mixed-license list, and
copyright statement, because the compatibility deposition API supports only one
license and cannot express the full native funding payload.

The actual projection results are inside each `rsv-smh-<round>.tar.gz` archive
under `data/<round>/model-output/<team-model>/*.parquet`. The
`rsv-smh-<round>.jsonld` file is for discovery and metadata harvesting; it
describes the results but is not the result table itself.

Each archive includes a `README.md` directory guide. Consolidated files inside
`output/` use `rsv-smh-<round>.jsonld` and `rsv-smh-<round>.html`, without a
parser/schema version suffix. The release README and Zenodo description count
team-model outputs and ensembles separately, identifying ensembles by the
case-insensitive substring `ensemble` in their identifiers. The Zenodo title,
description, Methods text, and copyright use Lucie's reviewed wording; counts
and season keywords are generated independently for each round.

The default Zenodo-facing record version is `1.0.0`. Source tags, source
commits, the parser release, and the parser commit are stored separately in
`release_metadata.json` and surfaced in the generated README/PDF. If deposited
files change after publication, create a linked new Zenodo version and increment
the record version; metadata-only corrections can stay on the same published
record.

`LICENSES.json` is available separately because Zenodo record-level rights do not
show which license applies to which model-round dataset or archive path.

`README.md` is the canonical human-facing documentation source. `README.pdf` is
generated from the same Markdown content, and the uploader asks Zenodo's native
draft API to use the PDF as the default file preview.

README PDFs are generated with WeasyPrint. `uv sync` installs the Python package;
on macOS, if WeasyPrint cannot import its native rendering libraries, install
the Homebrew package as well:

```bash
brew install weasyprint
```

Build the split release locally without contacting Zenodo:

```bash
uv run publish_to_zenodo.py \
  --dry-run \
  --split-by-round \
  --reuse-archives
```

Create or update one unpublished Sandbox draft per round:

```bash
uv run --env-file .env publish_to_zenodo.py \
  --split-by-round \
  --reuse-archives
```

Split mode is Sandbox-only by default, never publishes, and never reserves or
mints a DOI. It resumes from `release/zenodo/sandbox-split.json`, so rerunning
the command updates the existing draft IDs instead of creating fresh drafts.
Use this path for review records such as the current Sandbox drafts.

The expected files for each round draft are:

- `rsv-smh-<round>.tar.gz`
- `rsv-smh-<round>.jsonld`
- `README.md`
- `README.pdf`
- `LICENSES.json`
- `release_metadata.json`
- `SHA256SUMS`

For a single combined draft, omit `--split-by-round`:

```bash
uv run --env-file .env publish_to_zenodo.py \
  --reuse-archives
```

Review the returned draft URL and its metadata before publication. Model outputs
have model-specific licenses, so publication requires an explicit rights-review
acknowledgement. Publish a reviewed combined draft by ID:

```bash
uv run --env-file .env publish_to_zenodo.py \
  --publish-draft YOUR_DEPOSITION_ID \
  --acknowledge-rights-reviewed
```

For automation that should create, upload, and publish in one run, use
`--publish --acknowledge-rights-reviewed` instead.

Synchronize regenerated files and metadata into an existing unpublished combined
draft:

```bash
uv run --env-file .env publish_to_zenodo.py \
  --reuse-archives \
  --update-draft YOUR_DEPOSITION_ID
```

A published record's files are fixed, so a release with changed files goes out as
a linked new version rather than an edit. Open one from the published record,
upload the rebuilt release into it, and leave it unpublished:

```bash
uv run --env-file .env publish_to_zenodo.py \
  --round 2025-07-27 \
  --reuse-archives \
  --new-version-of PUBLISHED_RECORD_ID
```

The draft keeps the record's concept DOI, so the version history stays linked.
When a split manifest names that round, its entry is repointed at the new draft,
so the next `--split-by-round` run does not resume against a published record.
Publish the draft with `--publish-draft` as above.

A new version must carry a new record version. The publisher reads the parent
record's version first and refuses to open the draft when it matches the version
this run would deposit, because two records in one lineage claiming the same
version cannot be told apart. Increment `DEFAULT_RECORD_VERSION`, or pass
`--version`, before rebuilding changed files.

On production Zenodo, `--community midas-network` requests inclusion in the
MIDAS Network community. Sandbox has a separate community registry, so that
production community should not be added to a Sandbox draft.

Use `--metadata-file metadata.json` for complete creator, affiliation, ORCID,
license, and related-identifier metadata. Use `--help` for round, packaging,
and Sandbox API options. Tokens are sent as HTTPS bearer headers and are never
written into the release files.

## License

The `smh-archiver` software in this repository is licensed under the
[Apache License 2.0](LICENSE). This software license does not relicense the
Scenario Modeling Hub model outputs, metadata, or auxiliary data. Those
research objects retain their model- and source-specific terms; Zenodo release
packages record them in `LICENSES.json`.
