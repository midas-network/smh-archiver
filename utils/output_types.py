"""Lookup for Hubverse output-type definitions and ontology IRIs.

Single source of truth: utils/output_type_definitions.json (resolved in
midas-network/smh-to-jsonld issue #25). Read relative to this file, not the
process CWD, so the lookup works regardless of where the pipeline is invoked.
"""

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "output_type_definitions.json"

LEARN_MORE_URL = "https://docs.hubverse.io/en/latest/user-guide/model-output.html"


@lru_cache(maxsize=1)
def _load():
    with open(_DATA) as f:
        return json.load(f)["output_types"]


def get_output_type_definition(name):
    """Return {label, definition, definition_source, iri, iri_match} or None.

    Lookup is case- and whitespace-insensitive; unknown names return None.
    """
    if name is None:
        return None
    return _load().get(str(name).strip().lower())


def definition_source_label(definition_source):
    """Short human-facing label for a ``definition_source`` (ProbOnto, STATO, ...)."""
    source = str(definition_source or "ontology")
    return "ProbOnto" if "probonto" in source.lower() else source


def human_iri_link(iri):
    """Human-facing link target; probonto.org IRIs go to the EBI OLS4 class page."""
    from urllib.parse import quote

    iri = str(iri)
    if "probonto.org" in iri:
        return (
            "https://www.ebi.ac.uk/ols4/ontologies/probonto/classes/"
            + quote(quote(iri, safe=""), safe="")
        )
    return iri
