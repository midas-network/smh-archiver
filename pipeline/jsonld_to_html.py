import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from html import escape
from pathlib import Path

# Add parent directory to path to allow imports and access to data/output directories
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd

from utils.jsonld import as_list
from utils.markdown import basic_markdown_to_html
from utils.output_types import (
    definition_source_label,
    get_output_type_definition,
    human_iri_link,
)
from utils.output_type_summary import (
    load_model_output_from_dir,
    summarize_output_type_metadata,
    summarize_quantile_output,
    summarize_sample_output,
)

SMH_OUTPUT_DOC_URL = "https://github.com/midas-network/rsv-scenario-modeling-hub/blob/main/model-output/README.md"
SMH_SAMPLE_OUTPUT_DOC_URL = SMH_OUTPUT_DOC_URL + "#sample"


def load_model_output_dataframe(round_id, model):
    model_dir = Path("data") / round_id / "model-output" / model
    return load_model_output_from_dir(model_dir, model)


# Submissions store these columns in at least three different orders. The
# catalog is a comparison document, so it always presents them the same way.
CANONICAL_SNIPPET_COLUMNS = [
    "origin_date",
    "scenario_id",
    "target",
    "horizon",
    "location",
    "age_group",
    "output_type",
    "output_type_id",
    "value",
    "run_grouping",
    "stochastic_run",
]

# The illustrative table shows a readable subset. output_type_id is kept even
# though it is not strictly identifying: without it every quantile row looks
# like a duplicate of its neighbours with an unexplained different value.
SNIPPET_DISPLAY_COLUMNS = [
    "scenario_id",
    "target",
    "horizon",
    "location",
    "age_group",
    "output_type",
    "output_type_id",
    "value",
]

MISSING_DISPLAY = "\u2014"
ELLIPSIS_DISPLAY = "\u2026"
MISSING_TOKENS = {"na", "nan", "none", "null", "<na>", ""}


def canonicalize_snippet(df):
    """Impose one display order and one missing-value representation."""
    ordered = [c for c in CANONICAL_SNIPPET_COLUMNS if c in df.columns]
    remaining = [c for c in df.columns if c not in ordered]
    return df[ordered + remaining]


def normalize_missing(value):
    """Render NA / NaN / None uniformly; the raw values stay in the data files."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return MISSING_DISPLAY
    try:
        if pd.isna(value):
            return MISSING_DISPLAY
    except (TypeError, ValueError):
        pass
    return MISSING_DISPLAY if str(value).strip().lower() in MISSING_TOKENS else value


def format_first_n_rows_of_output(n, df):
    if df.empty:
        return ""

    df = canonicalize_snippet(df)
    display_columns = [c for c in SNIPPET_DISPLAY_COLUMNS if c in df.columns]
    hidden = [c for c in df.columns if c not in display_columns]
    if display_columns:
        df = df[display_columns]

    # Get the first `n` rows and the last row
    if len(df) > n:
        df = pd.concat([
            df.head(n),
            pd.DataFrame([[ELLIPSIS_DISPLAY] * len(df.columns)], columns=df.columns),
            df.tail(1),
        ])

    df = df.astype(object).map(normalize_missing)
    html_output = df.to_html(index=False, border=0, na_rep=MISSING_DISPLAY)
    if hidden:
        html_output += (
            '<p class="snippet-note">Example records. '
            f'Columns not shown: {", ".join(hidden)}. '
            f'<a href="{SMH_OUTPUT_DOC_URL}" target="_blank">View SMH output specification</a></p>\n'
        )
    return html_output


def get_first_n_rows_of_output(n, round_id, model):
    df = load_model_output_dataframe(round_id, model)
    return format_first_n_rows_of_output(n, df)


def load_geodata_mapping():
    """Load geodata CSV and create ISO code to Geonames URL mapping."""
    mapping = {}
    geodata_file = Path('geodata/geodata.csv')

    if geodata_file.exists():
        with open(geodata_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                iso_code = row['iso3166_2']
                geoname_url = row['geoname_url']
                mapping[iso_code] = geoname_url

    return mapping


LICENSE_DISPLAY_NAMES = {
    "cc-by-4.0": "CC BY 4.0",
    "cc-by-sa-4.0": "CC BY-SA 4.0",
    "cc-by_sa-4.0": "CC BY-SA 4.0",
    "cc-by-nc-4.0": "CC BY-NC 4.0",
    "mit": "MIT",
    "bsd simplified": "BSD Simplified",
    "bsd-2-clause": "BSD 2-Clause",
    "gpl-3.0": "GPL 3.0",
    "apache-2.0": "Apache 2.0",
    "zenodo-freetoread-1.0": "Zenodo Free-to-Read",
}


def format_license_name(value):
    """Human-facing license label; submissions vary only by case and spacing."""
    if not value:
        return "N/A"
    return LICENSE_DISPLAY_NAMES.get(str(value).strip().lower(), str(value))


def get_license_map():
    """Return mapping of license names to their URLs."""
    return {
        # Open Source / Software
        "MIT":          "https://opensource.org/license/mit/",
        "GPL-3.0":      "https://www.gnu.org/licenses/gpl-3.0.en.html",
        "APACHE-2.0":   "https://www.apache.org/licenses/LICENSE-2.0",
        "BSD SIMPLIFIED": "https://opensource.org/license/bsd-2-clause",

        # Creative Commons
        "CC0-1.0":      "https://creativecommons.org/publicdomain/zero/1.0/",
        "CC-BY-4.0":    "https://creativecommons.org/licenses/by/4.0/",
        "CC-BY-NC-4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
        "CC BY-NC 4.0": "https://creativecommons.org/licenses/by-nc/4.0/",   # alternate spacing variant
        "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
        "CC-BY_SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",   # legacy underscore variant

        # Open Data Commons
        "ODC-BY":       "https://opendatacommons.org/licenses/by/",
        "ODBL":         "https://opendatacommons.org/licenses/odbl/",
        "PDDL":         "https://opendatacommons.org/licenses/pddl/",

        # Government / Other
        "OGL-3.0":      "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
        "ZENODO-FREETOREAD-1.0": (
            "https://zenodo.org/api/vocabularies/licenses/zenodo-freetoread-1.0"
        ),
    }
    

def generate_html_head(title):
    """Generate HTML head section with styles."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        th {{
          text-align: left;
        }}
        body {{
            font-family: Arial, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }}
        h1 {{ color: #2c3e50; }}
        h2 {{ color: #34495e; border-bottom: 2px solid #3498db; padding-bottom: 5px; }}
        h3 {{ color: #7f8c8d; }}
        .model {{
            background: #ffffff;
            padding: 20px;
            margin: 20px 0;
            border-radius: 5px;
            border-left: 4px solid #3498db;
        }}
        .model-separator {{
            border: 0;
            height: 2px;
            margin: 32px 0;
            width: 100%;
            background: #cfd8e3;
            border-radius: 0;
        }}
        .author {{
            margin: 0px 0;
            padding: 0px 10px;
            background: white;
            border-radius: 0px;
        }}
        .authors-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 15px;
            margin: 10px 0;
        }}
        .location {{
            display: inline-block;
            margin: 5px;
            padding: 0px 5px;
            background: #ffffff;
            border-radius: 3px;
            font-size: 0.9em;
        }}
        .metadata {{
            color: #7f8c8d;
            font-size: 0.9em;
        }}
        .variables-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 15px;
            margin: 10px 0;
        }}
        .no-wrap {{ white-space: nowrap; }}
        a {{ color: #3498db; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .age-groups {{ margin: 10px 0; }}
        .index {{
            background: #fff;
            padding: 20px;
            margin: 20px 0;
            border-radius: 5px;
            border: 2px solid #3498db;
        }}
        .index h2 {{ margin-top: 0; }}
        .index ul {{
            list-style: none;
            padding: 0;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 10px;
        }}
        .index li {{
            padding: 8px;
            background: #ffffff;
            border-radius: 3px;
        }}
        .index li:hover {{ background: #e8f4f8; }}
        .back-to-top {{
            display: inline-block;
            margin-left: 15px;
            padding: 5px 10px;
            background: #3498db;
            color: white;
            border-radius: 3px;
            font-size: 0.9em;
            vertical-align: middle;
        }}
        .back-to-top:hover {{ background: #2980b9; text-decoration: none; }}
        .model-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 10px;
        }}
        .model-header h2 {{
            margin: 0;
            border-bottom: none;
            padding-bottom: 0;
        }}
        
        /* Tab styles */
        .tabs {{
            margin: 20px 0;
        }}
        .tab-buttons {{
            display: flex;
            gap: 5px;
            border-bottom: 2px solid #3498db;
            margin-bottom: 15px;
        }}
        .tab-button {{
            padding: 10px 20px;
            background: #ecf0f1;
            border: none;
            cursor: pointer;
            font-size: 1em;
            border-radius: 5px 5px 0 0;
            transition: background 0.3s;
        }}
        .tab-button:hover {{
            background: #bdc3c7;
        }}
        .tab-button.active {{
            background: #3498db;
            color: white;
        }}
        .tab-content {{
            display: none;
            padding: 5px 0;
        }}
        .tab-content.active {{
            display: block;
        }}
        .tab-title {{ display: none; }}
        .print-only, .print-only-inline {{ display: none; }}
        .chip {{
            display: inline-block;
            padding: 1px 6px;
            margin: 1px 3px 1px 0;
            border: 1px solid #cfd8e3;
            border-radius: 3px;
            background: #f7fafc;
            font-size: 0.92em;
            white-space: nowrap;
        }}
        .facts {{
            border-collapse: collapse;
            margin: 6px 0 10px;
        }}
        .facts td {{ padding: 2px 18px 2px 0; vertical-align: top; }}
        .facts .facts-label {{
            color: #7f8c8d;
            text-transform: uppercase;
            font-size: 0.78em;
            letter-spacing: 0.04em;
        }}
        table.matrix {{
            border-collapse: collapse;
            width: 100%;
            font-size: 0.9em;
        }}
        table.matrix th {{
            background: #3498db;
            color: #fff;
            text-align: left;
            padding: 4px 6px;
        }}
        table.matrix td {{
            border-bottom: 1px solid #e3e9f0;
            padding: 3px 6px;
            vertical-align: top;
        }}
        .cover-metrics {{ margin: 18px 0 6px; }}
        .cover-metric {{ display: inline-block; margin-right: 42px; }}
        .cover-metric .num {{
            display: block;
            font-size: 2.1em;
            font-weight: 700;
            color: #2c3e50;
            line-height: 1.1;
        }}
        .cover-metric .cap {{
            color: #7f8c8d;
            text-transform: uppercase;
            font-size: 0.78em;
            letter-spacing: 0.05em;
        }}
        table {{
            padding: 0px 10px;
        }}
        table.dataframe {{
            border-collapse: collapse;
            width: 100%;
            margin: 10px 0;
            padding: 0;
            font-size: 0.9em;
        }}
        table.dataframe th,
        table.dataframe td {{
            border: 1px solid #cfd8e3;
            padding: 6px 10px;
        }}
        table.dataframe thead th {{
            background: #3498db;
            color: #ffffff;
        }}
        table.dataframe tbody tr:nth-child(even) {{
            background: #f4f8fb;
        }}
        table.dataframe tbody tr:hover {{
            background: #e8f4f8;
        }}
    </style>
    <script>
        function switchTab(modelIdx, tabName) {{
            const modelId = 'model-' + modelIdx;
            const buttons = document.querySelectorAll('#' + modelId + ' .tab-button');
            const contents = document.querySelectorAll('#' + modelId + ' .tab-content');
            
            buttons.forEach(btn => btn.classList.remove('active'));
            contents.forEach(content => content.classList.remove('active'));
            
            document.getElementById(modelId + '-tab-' + tabName).classList.add('active');
            document.getElementById(modelId + '-content-' + tabName).classList.add('active');
        }}
    </script>
</head>
<body>
"""


def split_subject_of(data):
    """Split ``subjectOf`` (dict or list) into (round definition dict, report dicts)."""
    items = [i for i in as_list(data.get('subjectOf')) if isinstance(i, dict)]
    reports = [i for i in items if i.get('@type') == 'Report' and i.get('url')]
    documentation = next((i for i in items if i.get('@type') != 'Report'), {})
    return documentation, reports


def generate_abstract_section(model):
    """Render a model's markdown abstract, escaped, inside a collapsible block."""
    abstract = model.get('abstract')
    if not abstract or not str(abstract).strip():
        return ''
    # Demote the abstract's own headings so they sit below the section heading.
    demoted = re.sub(r'^(#+)', r'\1##', str(abstract), flags=re.MULTILINE)
    body = basic_markdown_to_html(demoted)
    return (
        '        <details class="model-abstract">\n'
        '            <summary><strong>Model abstract</strong></summary>\n'
        f'{body}\n'
        '        </details>\n'
    )


def generate_header_section(data):
    """Generate the header section with dataset information."""
    models = data.get('hasPart') or []
    ensembles = sum('ensemble' in model.get('name', '').casefold() for model in models)
    html = f"""    <h1>{data.get('name', 'Dataset')}</h1>
    <p class="metadata"><strong>Description:</strong> {data.get('description', 'N/A')}</p>
    <p class="metadata"><strong>Identifier:</strong> {data.get('identifier', 'N/A')}</p>
    <p class="metadata"><strong>Team-model outputs:</strong> {len(models) - ensembles}; <strong>Ensemble projection datasets:</strong> {ensembles}</p>
"""

    documentation, reports = split_subject_of(data)
    documentation_url = documentation.get("url") or data.get("url")
    raw_url = documentation.get("sameAs") or data.get("sameAs")

    if documentation_url:
        html += (
            '    <p class="metadata"><strong>Scenario/Round Definition:</strong> '
            f'<a href="{documentation_url}" target="_blank">{documentation_url}</a>'
        )
        if raw_url:
            html += f' (<a href="{raw_url}" target="_blank">raw markdown</a>)'
        html += '</p>\n'

    if reports:
        links = '; '.join(
            f'<a href="{escape(str(r["url"]), quote=True)}" target="_blank">{escape(str(r.get("name") or r["url"]))}</a>'
            for r in reports
        )
        html += f'    <p class="metadata"><strong>Reports:</strong> {links}</p>\n'

    return html


def generate_model_index(models):
    """Generate the clickable model index."""
    html = """    <div class="index" id="index">
        <h2>Team-model and Ensemble Index</h2>
        <ul>
"""
    for idx, model in enumerate(models):
        model_name = model.get('name', 'Unknown Model')
        model_id = f"model-{idx}"
        html += f'            <li><a href="#{model_id}">{model_name}</a></li>\n'

    html += """        </ul>
    </div>
"""
    return html


def generate_target_metadata_section(model):
    """Generate target metadata section."""
    if 'target_metadata' not in model:
        return ''

    target_meta = model['target_metadata']
    return f"""        <h3>Target Metadata</h3>
        <div class="author">
            <strong>Target Keys:</strong> {", ".join(target_meta.get("target_keys", []))}<br>
            <strong>Target Type:</strong> {target_meta.get("target_type", "N/A")}<br>
            <strong>Is Step Ahead:</strong> {target_meta.get("is_step_ahead", "N/A")}<br>
            <strong>Time Unit:</strong> {target_meta.get("time_unit", "N/A")}<br>
        </div>
"""


def generate_ensemble_section(model):
    """Generate ensemble configuration section."""
    if 'workExample' not in model or 'ensemble' not in model['workExample']:
        return ''

    ensemble = model['workExample']['ensemble']
    html = """        <h3>Ensemble Configuration</h3>
        <div class="author">
"""
    html += f'            <strong>Description:</strong> {ensemble.get("description", "N/A")}<br>\n'
    html += f'            <strong>Ensemble Type:</strong> {ensemble.get("ensemble_type", "N/A")}<br>\n'
    if 'task_ids' in ensemble:
        html += f'            <strong>Task IDs:</strong> {", ".join(ensemble["task_ids"].get("required", []))}<br>\n'
    html += '        </div>\n'
    return html


def generate_task_ids_section(model):
    """Generate task IDs section."""
    if 'workExample' not in model or 'task_ids' not in model['workExample']:
        return ''

    task_ids = model['workExample']['task_ids']
    return f"""        <h3>Task IDs</h3>
        <div class="author">
            <strong>Required:</strong> {", ".join(task_ids.get("required", []))}<br>
            <strong>Optional:</strong> {", ".join(task_ids.get("optional", []))}
        </div>
"""


def generate_authors_section(model):
    """Generate authors section in grid layout."""
    if 'author' not in model:
        return ''

    html = """        <h3>Authors</h3>
        <div class="authors-grid">
"""
    for author in model['author']:
        author_name = author.get('name', 'Unknown')
        affiliation = author.get('affiliation', {}).get('name', 'N/A')
        email = author.get('email', '')

        html += '            <div class="author">\n'
        html += f'                <strong>{author_name}</strong><br>\n'
        html += f'                <em>{affiliation}</em><br>\n'
        if email:
            html += f'                <a href="mailto:{email}">{email}</a>\n'
        html += '            </div>\n'

    html += '        </div>\n'
    return html


def generate_variables_measured_section(model):
    """Generate variables measured section in grid layout."""
    if 'workExample' not in model or 'variableMeasured' not in model['workExample']:
        return ''

    label = "Target" if len(model['workExample']['variableMeasured']) == 1 else "Targets"
    html = f"""        <h3>{label}</h3>
        <div class="variables-grid">
"""
    for variable in model['workExample']['variableMeasured']:
        html += '            <div class="author">\n'
        html += f'                <strong>{variable.get("name", "Unknown Variable")}</strong><br>\n'
        html += f'                <strong>Unit:</strong> {variable.get("unitText", "N/A")}<br>\n'
        html += f'                <strong>Target ID:</strong> {variable.get("target_id", "N/A")}<br>\n'
        html += f'                <strong>Type:</strong> {variable.get("target_type", "N/A")}<br>\n'
        html += f'                <strong>Temporal Unit:</strong> {variable.get("temporalUnit", "N/A")}<br>\n'
        if 'identifier' in variable:
            html += f'                <a href="{variable["identifier"]}" target="_blank">Ontology Reference</a><br>\n'
        html += '            </div>\n'

    html += '        </div>\n'
    return html


def format_available_output_types(variable):
    """Format target output-type availability for HTML display."""
    output_types = variable.get("available_output_types")
    if not output_types:
        return "N/A"
    if isinstance(output_types, list):
        return ", ".join(str(output_type) for output_type in output_types)
    return str(output_types)


def generate_spatial_coverage_section(model, geodata_map):
    """Generate spatial coverage section content."""
    if 'workExample' not in model or 'spatialCoverage' not in model['workExample']:
        return ''

    html = ''
    for location in model['workExample']['spatialCoverage']:
        location_name = location.get('gn:name', 'Unknown')
        location_code = location.get('iso3166-2:code', '')

        short_code = location_code.split('-')[-1] if location_code else ''
        label = f'{location_name} \u00b7 {short_code}' if short_code else location_name
        if location_code and location_code in geodata_map:
            target = geodata_map[location_code]
        elif location_code:
            target = f"https://www.geonames.org/search.html?q={location_name.replace(' ', '+')}"
        else:
            target = None
        if target:
            html += f'                <span class="chip"><a href="{target}" target="_blank">{label}</a></span>\n'
        else:
            html += f'                <span class="chip">{label}</span>\n'

    return html


def normalize_output_type_name(output_type):
    return str(output_type).strip().lower()


def generate_output_type_metadata_html(output_type, output_type_metadata, is_ensemble=False):
    metadata = (output_type_metadata or {}).get(normalize_output_type_name(output_type))
    if not metadata:
        return ""

    normalized_output_type = normalize_output_type_name(output_type)
    html = ""

    if normalized_output_type == "sample":
        missing_columns = metadata.get("missing_columns", [])
        if missing_columns:
            missing_display = ", ".join(escape(str(column)) for column in missing_columns)
            html += (
                "                    <strong>Sample metadata unavailable:</strong> "
                f"missing {missing_display}<br>\n"
            )
        else:
            sample_count = metadata.get("sample_count")
            compound_task_id_set = metadata.get("compound_task_id_set", [])
            if compound_task_id_set:
                # The set is a list of column names; quote each one so it reads
                # unambiguously, e.g. "origin_date", "target", "location".
                compound_display = ", ".join(
                    f'"{escape(str(column))}"' for column in compound_task_id_set
                )
            else:
                # An empty set does NOT mean the samples are independent across
                # task variables (it is the opposite). Display it literally as
                # [] so it is never misread.
                compound_display = "[]"

            html += f"                    <strong>Number of samples:</strong> {escape(str(sample_count))}<br>\n"
            html += (
                "                    <strong>Compound task ID set:</strong> "
                f"{compound_display}<br>\n"
            )

        html += (
            f'                    <a href="{SMH_SAMPLE_OUTPUT_DOC_URL}" target="_blank">'
            "SMH sample output documentation</a><br>\n"
        )

    if normalized_output_type == "quantile" and metadata.get("quantiles"):
        quantile_display = ", ".join(escape(str(quantile)) for quantile in metadata["quantiles"])
        # Ensemble models compute their quantiles rather than submitting them.
        quantile_label = "Calculated quantiles" if is_ensemble else "Submitted quantiles"
        html += (
            f"                    <strong>{quantile_label}:</strong> "
            f"{quantile_display}<br>\n"
        )

    return html


def generate_output_types_section(model, output_type_metadata=None):
    """Generate output types section content."""
    if 'workExample' not in model or 'output_type' not in model['workExample']:
        return ''

    # Ensemble models (those with "Ensemble" in the name) calculate their
    # output values instead of having them submitted directly.
    is_ensemble = "ensemble" in model.get("name", "").lower()

    # output_type is stored as [list_of_types]; flatten to get individual type strings
    output_types = []
    for item in model['workExample']['output_type']:
        if isinstance(item, list):
            output_types.extend(item)
        else:
            output_types.append(item)

    html = ''
    for output_type in output_types:
        definition = get_output_type_definition(output_type)
        html += '                <div class="author">\n'
        if definition:
            html += f'                    <strong>{output_type}</strong> — {definition["label"]}'
            # Print carries the shared definition once, in the Data Dictionary.
            html += (
                f'<a class="print-only-inline" href="#glossary-ot-'
                f'{glossary_slug(normalize_output_type_name(output_type))}">'
                ' — see Data Dictionary</a><br>\n'
            )
            html += '                    <span class="screen-only">\n'
            html += f'                        {definition["definition"]}<br>\n'
            if definition.get("iri"):
                html += (
                    f'                        <a href="{human_iri_link(definition["iri"])}" target="_blank">'
                    f'Ontology reference ({definition_source_label(definition["definition_source"])})</a><br>\n'
                )
            html += '                    </span>\n'
        else:
            # Unknown output type: fall back to the bare string, never crash.
            html += f'                    {output_type}<br>\n'
        html += generate_output_type_metadata_html(output_type, output_type_metadata, is_ensemble)
        html += '                </div>\n'

    return html


def generate_age_groups_section(model):
    """Generate age groups section content."""
    if 'workExample' not in model or 'ageGroups' not in model['workExample']:
        return ''

    html = ''
    for age_group in model['workExample']['ageGroups']:
        html += f'                <span class="chip">{age_group}</span>\n'

    return html


def open_tab_content(model_idx, slug, title, is_active):
    """Open a tab panel with a real heading so the PDF outline picks it up."""
    active_class = ' active' if is_active else ''
    return (
        f'            <div class="tab-content{active_class}" '
        f'id="model-{model_idx}-content-{slug}">\n'
        f'                <h3 class="tab-title">{title}</h3>\n'
        '                <div class="tab-body">\n'
    )


CLOSE_TAB_CONTENT = '                </div>\n            </div>\n'


def generate_tabbed_section(model, model_idx, geodata_map, sample_output_html, output_type_metadata=None):
    """Generate tabbed section for sample output, output types, age groups, targets, and spatial coverage."""
    has_output_types = 'workExample' in model and 'output_type' in model['workExample']
    has_age_groups = 'workExample' in model and 'ageGroups' in model['workExample']
    has_targets = 'workExample' in model and 'variableMeasured' in model['workExample']
    has_spatial = 'workExample' in model and 'spatialCoverage' in model['workExample']
    has_sample_output = sample_output_html is not None and sample_output_html != ''

    if not (has_sample_output or has_output_types or has_age_groups or has_targets or has_spatial):
        return ''

    html = '        <div class="tabs">\n'
    html += '            <div class="tab-buttons">\n'

    # Projection Data Snippet is always the default tab if available
    first_tab = 'sample' if has_sample_output else None
    if not first_tab:
        if has_output_types:
            first_tab = 'output'
        elif has_targets:
            first_tab = 'targets'
        elif has_spatial:
            first_tab = 'spatial'
        elif has_age_groups:
            first_tab = 'age'

    # Add tab buttons
    if has_sample_output:
        html += f'                <button class="tab-button active" id="model-{model_idx}-tab-sample" onclick="switchTab({model_idx}, \'sample\')">Projection Data Snippet</button>\n'
    if has_targets:
        active_class = ' active' if first_tab == 'targets' else ''
        html += f'                <button class="tab-button{active_class}" id="model-{model_idx}-tab-targets" onclick="switchTab({model_idx}, \'targets\')">Targets</button>\n'
    if has_spatial:
        active_class = ' active' if first_tab == 'spatial' else ''
        html += f'                <button class="tab-button{active_class}" id="model-{model_idx}-tab-spatial" onclick="switchTab({model_idx}, \'spatial\')">Spatial Coverage</button>\n'
    if has_output_types:
        active_class = ' active' if first_tab == 'output' else ''
        html += f'                <button class="tab-button{active_class}" id="model-{model_idx}-tab-output" onclick="switchTab({model_idx}, \'output\')">Output Types</button>\n'
    if has_age_groups:
       active_class = ' active' if first_tab == 'age' else ''
       html += f'                <button class="tab-button{active_class}" id="model-{model_idx}-tab-age" onclick="switchTab({model_idx}, \'age\')">Age Groups</button>\n'

    html += '            </div>\n'

    # Add tab content for projection data snippet
    if has_sample_output:
        html += open_tab_content(model_idx, 'sample', 'Projection Data Snippet', True)
        html += f'                {sample_output_html}\n'
        html += CLOSE_TAB_CONTENT

    # Add tab content for output types
    if has_output_types:
        html += open_tab_content(
            model_idx, 'output', 'Output Types', first_tab == 'output'
        )
        html += generate_output_types_section(model, output_type_metadata)
        html += CLOSE_TAB_CONTENT

    # Add tab content for targets
    if has_targets:
        html += open_tab_content(model_idx, 'targets', 'Targets', first_tab == 'targets')
        html += '                <div class="variables-grid">\n'
        for variable in model['workExample']['variableMeasured']:
            anchor = glossary_slug(variable.get('target_id') or variable.get('name') or '')
            html += '                    <div class="author">\n'
            html += f'                        <strong>{variable.get("name", "Unknown Variable")}</strong>'
            html += (
                f'<a class="print-only-inline" href="#glossary-target-{anchor}">'
                ' — see Data Dictionary</a><br>\n'
            )
            html += '                        <span class="screen-only">\n'
            html += f'                            <strong>Unit:</strong> {variable.get("unitText", "N/A")}<br>\n'
            html += f'                            <strong>Target ID:</strong> {variable.get("target_id", "N/A")}<br>\n'
            html += f'                            <strong>Type:</strong> {variable.get("target_type", "N/A")}<br>\n'
            html += '                        </span>\n'
            html += f'                        <strong>Possible Output Types:</strong> <span class="no-wrap">{format_available_output_types(variable)}</span> <br>\n'
            html += '                        <span class="screen-only">\n'
            html += f'                            <strong>Temporal Unit:</strong> {variable.get("temporalUnit", "N/A")}<br>\n'
            if 'identifier' in variable:
                html += f'                            <a href="{variable["identifier"]}" target="_blank">Ontology Reference</a><br>\n'
            html += '                        </span>\n'
            html += '                    </div>\n'
        html += '                </div>\n'
        html += CLOSE_TAB_CONTENT

    # Add tab content for spatial coverage
    if has_spatial:
        html += open_tab_content(
            model_idx, 'spatial', 'Spatial Coverage', first_tab == 'spatial'
        )
        html += generate_spatial_coverage_section(model, geodata_map)
        html += CLOSE_TAB_CONTENT

    # Add tab content for age groups
    if has_age_groups:
        html += open_tab_content(model_idx, 'age', 'Age Groups', first_tab == 'age')
        html += generate_age_groups_section(model)
        html += CLOSE_TAB_CONTENT

    html += '        </div>\n'
    return html


def generate_temporal_coverage_section(model):
    """Generate temporal coverage section."""
    if 'workExample' not in model or 'temporalCoverage' not in model['workExample']:
        return ''

    temporal = model['workExample']['temporalCoverage']
    # Parse the date range (format: "start_date/end_date")
    if '/' in temporal:
        start_date, end_date = temporal.split('/')
        #remove time if present
        start_date = start_date.split(' ')[0]
        end_date = end_date.split(' ')[0]
        return f'        <p><strong>Temporal Coverage:</strong> <span class="location">{start_date}</span> to <span class="location">{end_date}</span></p>\n'
    else:
        return f'        <p><strong>Temporal Coverage:</strong> <span class="location">{temporal}</span></p>\n'


def iter_output_types(model):
    """Flatten a model's output_type field, which stores nested lists."""
    types = []
    for item in (model.get('workExample') or {}).get('output_type') or []:
        types.extend(item) if isinstance(item, list) else types.append(item)
    return types


def collection_metrics(models):
    """Summarize a round using only fields actually present in the JSON-LD.

    Deliberately omits scenario counts: the source metadata carries no scenario
    field, and a cover figure that cannot be derived should not be invented.
    """
    geographies, age_groups, targets, output_types, spans = set(), set(), set(), set(), set()
    for model in models:
        work = model.get('workExample') or {}
        for place in work.get('spatialCoverage') or []:
            geographies.add(place.get('iso3166-2:code') or place.get('gn:name'))
        age_groups.update(work.get('ageGroups') or [])
        for variable in work.get('variableMeasured') or []:
            targets.add(variable.get('target_id'))
        output_types.update(iter_output_types(model))
        if work.get('temporalCoverage'):
            spans.add(work['temporalCoverage'])

    weeks = None
    if len(spans) == 1 and '/' in next(iter(spans)):
        start_text, end_text = (part.strip() for part in next(iter(spans)).split('/'))
        try:
            start = datetime.fromisoformat(start_text)
            end = datetime.fromisoformat(end_text)
            weeks = round((end - start).days / 7)
        except ValueError:
            weeks = None

    return {
        'models': len(models),
        'team_models': sum('ensemble' not in model.get('name', '').casefold() for model in models),
        'ensembles': sum('ensemble' in model.get('name', '').casefold() for model in models),
        'geographies': len({g for g in geographies if g}),
        'age_groups': len(age_groups),
        'targets': len({t for t in targets if t}),
        'output_types': sorted(output_types),
        'weeks': weeks,
    }


def generate_cover_section(data, models):
    """Print-only collection overview that opens the PDF."""
    metrics = collection_metrics(models)
    tiles = [(metrics['team_models'], 'Team-model outputs'), (metrics['ensembles'], 'Ensembles'), (metrics['geographies'], 'Geographies')]
    if metrics['weeks']:
        tiles.append((metrics['weeks'], 'Week horizon'))
    tiles.append((metrics['targets'], 'Targets'))
    tiles.append((metrics['age_groups'], 'Age groups'))

    html = '    <div class="print-only cover">\n'
    html += f'        <h1 class="cover-title">{escape(str(data.get("name", "Dataset")))}</h1>\n'
    if data.get('description'):
        html += f'        <p class="cover-lede">{escape(str(data["description"]))}</p>\n'
    html += '        <div class="cover-metrics">\n'
    for value, caption in tiles:
        html += (
            '            <div class="cover-metric">'
            f'<span class="num">{value}</span>'
            f'<span class="cap">{caption}</span></div>\n'
        )
    html += '        </div>\n'
    if metrics['output_types']:
        chips = ' '.join(f'<span class="chip">{escape(str(t))}</span>' for t in metrics['output_types'])
        html += f'        <p><strong>Output types in this round:</strong> {chips}</p>\n'

    documentation, reports = split_subject_of(data)
    round_url = documentation.get('url') or data.get('url')
    if round_url:
        html += f'        <p><strong>Scenario/Round Definition:</strong> <a href="{escape(str(round_url))}">{escape(str(round_url))}</a></p>\n'
    if reports:
        links = '; '.join(
            f'<a href="{escape(str(r["url"]), quote=True)}">{escape(str(r.get("name") or r["url"]))}</a>'
            for r in reports
        )
        html += f'        <p><strong>Reports:</strong> {links}</p>\n'
    html += '    </div>\n'
    return html


def generate_comparison_matrix(models):
    """Print-only cross-model matrix.

    Every column is derived from a structured JSON-LD field. Model approach
    (compartmental, agent-based, ...) is intentionally absent: it exists only in
    free-text descriptions, and inferring it would risk mislabelling methods in a
    DOI-minted record.
    """
    if not models:
        return ''

    html = '    <div class="print-only matrix-section">\n'
    html += '        <h2>Team-model Outputs and Ensembles at a Glance</h2>\n'
    html += '        <table class="matrix">\n'
    html += ('            <thead><tr>'
             '<th>Team-model or ensemble</th><th>Team</th><th>Version</th><th>License</th>'
             '<th>Age groups</th><th>Output types</th><th>Targets</th>'
             '<th class="matrix-page-head">Page</th>'
             '</tr></thead>\n')
    html += '            <tbody>\n'
    for idx, model in enumerate(models):
        work = model.get('workExample') or {}
        producer = model.get('producer') or {}
        team = producer.get('name', '') if isinstance(producer, dict) else str(producer)
        out_types = sorted(set(iter_output_types(model)))
        chips = ' '.join(f'<span class="chip">{escape(str(t))}</span>' for t in out_types) or '&mdash;'
        html += (
            '                <tr>'
            f'<td><a href="#model-{idx}">{escape(str(model.get("name", "Unknown")))}</a></td>'
            f'<td>{escape(str(team))}</td>'
            f'<td>{escape(str(model.get("version", "")))}</td>'
            f'<td>{escape(format_license_name(model.get("license")))}</td>'
            f'<td>{len(work.get("ageGroups") or [])}</td>'
            f'<td>{chips}</td>'
            f'<td>{len(work.get("variableMeasured") or [])}</td>'
            f'<td class="matrix-page"><a href="#model-{idx}"></a></td>'
            '</tr>\n'
        )
    html += '            </tbody>\n        </table>\n    </div>\n'
    return html


def glossary_slug(text):
    """Stable anchor fragment for a glossary entry."""
    return re.sub(r'[^a-z0-9]+', '-', str(text).strip().lower()).strip('-')


def collect_glossary(models):
    """Gather the definitions that are otherwise repeated for every model."""
    output_types, targets = {}, {}
    for model in models:
        for output_type in iter_output_types(model):
            key = normalize_output_type_name(output_type)
            if key not in output_types:
                output_types[key] = (output_type, get_output_type_definition(output_type))
        for variable in (model.get('workExample') or {}).get('variableMeasured') or []:
            key = variable.get('target_id') or variable.get('name')
            if key and key not in targets:
                targets[key] = variable
    return output_types, targets


def generate_glossary_section(models):
    """Print-only glossary carrying the shared output-type and target definitions."""
    output_types, targets = collect_glossary(models)
    if not output_types and not targets:
        return ''

    html = '    <div class="print-only glossary" id="glossary">\n'
    html += '        <h2>Data Dictionary</h2>\n'
    html += ('        <p class="metadata">Definitions below apply to every model in this '
             'round. Each model profile lists which of these it uses.</p>\n')

    if output_types:
        html += '        <h3>Output types</h3>\n'
        for key, (label, definition) in sorted(output_types.items()):
            html += f'        <div class="glossary-entry" id="glossary-ot-{glossary_slug(key)}">\n'
            if definition:
                html += f'            <strong>{escape(str(label))}</strong> &mdash; {escape(str(definition["label"]))}<br>\n'
                html += f'            {escape(str(definition["definition"]))}<br>\n'
                if definition.get('iri'):
                    html += (f'            <a href="{escape(human_iri_link(definition["iri"]))}">Ontology reference '
                             f'({escape(definition_source_label(definition["definition_source"]))})</a>\n')
            else:
                html += f'            <strong>{escape(str(label))}</strong>\n'
            html += '        </div>\n'

    if targets:
        html += '        <h3>Targets</h3>\n'
        for key, variable in sorted(targets.items()):
            html += f'        <div class="glossary-entry" id="glossary-target-{glossary_slug(key)}">\n'
            html += f'            <strong>{escape(str(variable.get("name", key)))}</strong>\n'
            html += f'            <strong>Target ID:</strong> {escape(str(variable.get("target_id", "N/A")))}\n'
            html += f'            <strong>Unit:</strong> {escape(str(variable.get("unitText", "N/A")))}\n'
            html += f'            <strong>Type:</strong> {escape(str(variable.get("target_type", "N/A")))}\n'
            html += f'            <strong>Temporal Unit:</strong> {escape(str(variable.get("temporalUnit", "N/A")))}\n'
            if variable.get('identifier'):
                html += f'            <a href="{escape(str(variable["identifier"]))}">Ontology Reference</a>\n'
            html += '        </div>\n'

    html += '    </div>\n'
    return html


def parse_jsonld_to_html(jsonld_file, round_id):
    """Parse JSON-LD file and generate an HTML webpage."""

    # Load necessary data
    geodata_map = load_geodata_mapping()
    license_map = get_license_map()

    # Read the JSON-LD file
    with open(jsonld_file, 'r') as f:
        data = json.load(f)

    # Build HTML
    html = generate_html_head(data.get('name', 'Dataset'))

    models = sorted(data.get('hasPart', []), key=lambda m: m.get('name', '').lower())

    # Print opens with a collection cover and a cross-model matrix; the screen
    # page keeps its existing header and clickable index.
    html += generate_cover_section(data, models)
    html += generate_comparison_matrix(models)
    html += '    <div class="screen-header">\n'
    html += generate_header_section(data)
    html += generate_model_index(models)
    html += '    </div>\n'

    # Process each model
    for idx, model in enumerate(models):
        license = model.get('license', 'N/A').upper()
        model_id = f"model-{idx}"
        model_name = model.get("name", "Unknown Model")
        model_output_df = load_model_output_dataframe(round_id, model_name)
        parquet_html = format_first_n_rows_of_output(3, model_output_df)
        # Prefer the output-type metadata already stored in the JSON-LD (the
        # canonical source); fall back to recomputing it from the parquet data
        # for older JSON-LD files that predate that field.
        output_type_metadata = model.get("workExample", {}).get("output_type_metadata")
        if not output_type_metadata:
            output_type_metadata = summarize_output_type_metadata(model_output_df)

        # Model header
        html += f"""    <div class="model" id="{model_id}">
        <div class="model-intro">
        <div class="model-header">
            <h2>{model_name}</h2>
            <a href="#index" class="back-to-top">↑ Back to Index</a>
        </div>
"""

        # Version, License, and Website on same line
        info_parts = []

        # Version
        version = model.get('version', 'N/A')
        info_parts.append(f'<strong>Version:</strong> {version}')

        # License with link
        if license in license_map:
            url = license_map[license]
            info_parts.append(f'<strong>License:</strong> <a href="{url}" target="_blank">{format_license_name(license)}</a>')
        else:
            info_parts.append(f'<strong>License:</strong> {format_license_name(license)}')

        # Website
        if 'website' in model:
            info_parts.append(f'<strong>Website:</strong> <a href="{model["website"]}" target="_blank">{model["website"]}</a>')

        producer = model.get('producer') or {}
        team = producer.get('name') if isinstance(producer, dict) else producer
        facts = [('Version', version)]
        if license in license_map:
            facts.append(('License', f'<a href="{license_map[license]}" target="_blank">{format_license_name(license)}</a>'))
        else:
            facts.append(('License', format_license_name(license)))
        if team:
            facts.append(('Team', escape(str(team))))
        if 'website' in model:
            facts.append(('Website', f'<a href="{model["website"]}" target="_blank">Model website \u2197</a>'))
        html += '        <table class="facts print-only"><tr>\n'
        html += ''.join(f'            <td class="facts-label">{label}</td>\n' for label, _ in facts)
        html += '        </tr><tr>\n'
        html += ''.join(f'            <td>{value}</td>\n' for _, value in facts)
        html += '        </tr></table>\n'
        # The web page keeps the original inline form.
        html += f'        <p class="screen-only">{" ".join(info_parts)}</p>\n'
        html += '        </div>\n'

        # Target metadata
        html += generate_target_metadata_section(model)

        # Model tasks
        if 'modelTask' in model:
            html += f'        <p><strong>Model Tasks:</strong> {", ".join(model["modelTask"])}</p>\n'

        # Model category
        if 'modelCategory' in model:
            html += f'        <p><strong>Model Category:</strong> {", ".join(model["modelCategory"])}</p>\n'

        # Ensemble configuration
        html += generate_ensemble_section(model)

        # Task IDs
        html += generate_task_ids_section(model)


        # Description
        if 'description' in model:
            html += f'        <p><strong>Description:</strong> {model["description"]}</p>\n'

        html += generate_abstract_section(model)

        # Data sources
        if 'isBasedOn' in model:
            html += f'        <p><strong>Data Sources:</strong> {model["isBasedOn"].get("description", "N/A")}</p>\n'

        # Producer
        if 'producer' in model:
            producer = model['producer']
            html += f'        <p class="screen-only"><strong>Team:</strong> {producer.get("name", "N/A")}</p>\n'
            if 'funder' in producer:
                html += f'        <p><strong>Funding:</strong> {producer["funder"].get("description", "N/A")}</p>\n'

        # Authors
        html += generate_authors_section(model)

        # Temporal coverage
        html += generate_temporal_coverage_section(model)

        # Projection Data Snippet, Output types, Targets, Spatial Coverage, and Age groups in tabs
        html += generate_tabbed_section(model, idx, geodata_map, parquet_html, output_type_metadata)

        html += '    </div>\n'

        # Add a separator between models for visual structure.
        if idx < len(models) - 1:
            html += '    <hr class="model-separator">\n'

    # Shared definitions live once at the end instead of inside every model.
    html += generate_glossary_section(models)

    # Close HTML
    html += """</body>
</html>
"""

    return html


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description='Convert JSON-LD model metadata to HTML visualization',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert a specific round file
  python jsonld_to_html.py -i output/round_2024-07-28.jsonld -o output/round_2024-07-28.html -r 2024-07-28
  
  # Use default paths (reads from output/round_2024-07-28.jsonld)
  python jsonld_to_html.py
  
  # Process a different round
  python jsonld_to_html.py -i output/round_2023-11-12.jsonld -o output/round_2023-11-12.html -r 2023-11-12
        """
    )

    parser.add_argument(
        '-i', '--input',
        type=str,
        default='output/round_2024-07-28.jsonld',
        help='Input JSON-LD file path (default: output/round_2024-07-28.jsonld)'
    )

    parser.add_argument(
        '-o', '--output',
        type=str,
        default='output/round_2024-07-28.html',
        help='Output HTML file path (default: output/round_2024-07-28.html)'
    )

    parser.add_argument(
        '-r', '--round-id',
        type=str,
        default='2024-07-28',
        help='Round identifier for loading sample data (default: 2024-07-28)'
    )

    parser.add_argument(
        '--no-sample-data',
        action='store_true',
        help='Skip loading sample output data from parquet files'
    )

    args = parser.parse_args()

    # Validate input file exists
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Create output directory if it doesn't exist
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate the HTML
    print(f"Converting {args.input} to HTML...")
    print(f"Round ID: {args.round_id}")

    try:
        html_content = parse_jsonld_to_html(args.input, args.round_id)

        # Write to file
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(html_content)

        print(f"✓ HTML file generated: {args.output}")

    except Exception as e:
        print(f"Error generating HTML: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
