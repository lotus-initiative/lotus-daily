import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from itertools import pairwise
from typing import Any, cast

import requests
from SPARQLWrapper import JSON, SPARQLWrapper
from SPARQLWrapper.SPARQLExceptions import SPARQLWrapperException

logger = logging.getLogger(__name__)

QLEVER_ENDPOINT = "https://qlever.dev/api/wikidata"
WD_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "DailyLotusBot/0.1 (https://www.earthmetabolome.org/; contact@earthmetabolome.org)"
DEFAULT_SPARQL_TIMEOUT = 45
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 2.0

SPARQL_PREFIXES = """
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX ps: <http://www.wikidata.org/prop/statement/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX pr: <http://www.wikidata.org/prop/reference/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
""".strip()

_TRANSIENT_SPARQL_ERRORS = (
    urllib.error.URLError,
    urllib.error.HTTPError,
    TimeoutError,
    OSError,
    SPARQLWrapperException,
    ValueError,
)


class SparqlQueryError(RuntimeError):
    """Raised when all SPARQL endpoints and retries fail."""

    def __init__(self) -> None:
        super().__init__("All SPARQL endpoints failed after retries.")


def _resolve_timeout(timeout: int | None) -> int:
    if timeout is not None:
        return timeout
    raw = os.getenv("SPARQL_TIMEOUT")
    if not raw:
        return DEFAULT_SPARQL_TIMEOUT
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_SPARQL_TIMEOUT
    return parsed if parsed > 0 else DEFAULT_SPARQL_TIMEOUT


def _order_endpoint_queries(endpoint_queries: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    ordered = list(endpoint_queries)
    override = os.getenv("SPARQL_ENDPOINT", "").strip()
    if not override or not ordered:
        return ordered

    for index, (endpoint, query) in enumerate(ordered):
        if endpoint == override:
            return [(endpoint, query), *ordered[:index], *ordered[index + 1 :]]

    default_query = ordered[-1][1] if "wikidata.org" in override else ordered[0][1]
    return [(override, default_query), *ordered]


def _query_single_endpoint(
    endpoint: str,
    query: str,
    *,
    timeout: int,
    max_retries: int,
    retry_delay: float,
) -> tuple[dict[str, Any] | None, Exception | None]:
    attempts = max(1, max_retries)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            sparql = SPARQLWrapper(endpoint)
            sparql.addCustomHttpHeader("User-Agent", USER_AGENT)
            sparql.setTimeout(timeout)
            sparql.setQuery(query)
            sparql.setReturnFormat(JSON)
            return cast(dict[str, Any], sparql.query().convert()), None
        except _TRANSIENT_SPARQL_ERRORS as exc:
            last_exc = exc
            logger.warning(
                "SPARQL query failed on %s (attempt %d/%d): %s",
                endpoint,
                attempt + 1,
                attempts,
                exc,
            )
            if attempt + 1 < attempts and retry_delay > 0:
                time.sleep(retry_delay * (2**attempt))
    return None, last_exc


def execute_sparql(
    endpoint_queries: Sequence[tuple[str, str]],
    *,
    timeout: int | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> dict[str, Any]:
    """Execute a SPARQL query across ordered endpoints with retries and fallback.

    Parameters
    ----------
    endpoint_queries : Sequence[tuple[str, str]]
        Ordered ``(endpoint_url, query_text)`` pairs to try.
    timeout : int | None
        Request timeout in seconds. Defaults to ``SPARQL_TIMEOUT`` or 45s.
    max_retries : int
        Maximum retry attempts per endpoint.
    retry_delay : float
        Base delay in seconds for exponential backoff between retries.

    Returns
    -------
    dict[str, Any]
        Parsed SPARQL JSON response.
    """
    resolved_timeout = _resolve_timeout(timeout)
    last_exc: Exception | None = None
    for endpoint, query in _order_endpoint_queries(endpoint_queries):
        result, exc = _query_single_endpoint(
            endpoint,
            query,
            timeout=resolved_timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        if result is not None:
            return result
        if exc is not None:
            last_exc = exc
    raise SparqlQueryError() from last_exc


def build_depict_url(smiles: str) -> str:
    """Build the 2D depiction URL for a SMILES string.

    Parameters
    ----------
    smiles : str
        SMILES string of the compound.

    Returns
    -------
    str
        URL pointing to the Cheminformatics Microservice depiction endpoint.
    """
    return (
        f"https://dev.api.naturalproducts.net/latest/depict/2D?"
        f"smiles={urllib.parse.quote(smiles)}&width=300&height=200"
        f"&toolkit=cdk&rotate=0&CIP=false&unicolor=false"
    )


def get_candidate_qids(
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> list[str]:
    """Fetch candidate compound QIDs that have a SMILES and a taxon with an image.

    Parameters
    ----------
    max_retries : int
        Maximum retry attempts per endpoint.
    retry_delay : float
        Base delay in seconds between retries.

    Returns
    -------
    list[str]
        Wikidata QIDs for candidate compounds.
    """
    query = f"""
    {SPARQL_PREFIXES}
    SELECT DISTINCT ?compound WHERE {{
      ?compound wdt:P703 ?taxon ;
                wdt:P233 [] .
      ?taxon wdt:P18 ?image .
    }}
    LIMIT 500000
    """
    raw = execute_sparql(
        [(QLEVER_ENDPOINT, query), (WD_ENDPOINT, query)],
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    results = raw.get("results", {}).get("bindings", [])
    return [row["compound"]["value"].split("/")[-1] for row in results]


def _build_molecule_details_query(qid: str, *, use_scholarly_service: bool) -> str:
    if use_scholarly_service:
        ref_block = """SERVICE <https://query-scholarly.wikidata.org/sparql> {
        ?reference rdfs:label ?referenceLabel . FILTER (lang(?referenceLabel) = "en")
      }"""
    else:
        ref_block = '?reference rdfs:label ?referenceLabel . FILTER (lang(?referenceLabel) = "en")'

    return f"""
    {SPARQL_PREFIXES}
    SELECT ?compoundLabel ?compound ?taxon ?taxonLabel ?reference ?referenceLabel ?smiles ?taxon_image ?kingdom ?kingdomLabel WHERE {{
      VALUES ?compound {{wd:{qid}}}
      OPTIONAL {{ ?compound wdt:P233 ?smiles_c . }}
      OPTIONAL {{ ?compound wdt:P2017 ?smiles_i . }}
      BIND(COALESCE(?smiles_i, ?smiles_c) AS ?smiles)
      FILTER(BOUND(?smiles))
      ?compound p:P703 ?statement .
      ?statement ps:P703 ?taxon ;
                 prov:wasDerivedFrom ?refnode .
      ?refnode pr:P248 ?reference .
      ?taxon wdt:P18 ?taxon_image .
      ?taxon wdt:P171* ?kingdom .
      FILTER(
        ?kingdom IN (
          wd:Q729,   # Animalia
          wd:Q756,   # Plantae
          wd:Q764,   # Fungi
          wd:Q10876  # Bacteria (domain, not kingdom)
        )
      )
      ?kingdom rdfs:label ?kingdomLabel . FILTER (lang(?kingdomLabel) = "en")
      ?compound rdfs:label ?compoundLabel . FILTER (lang(?compoundLabel) = "en")
      ?taxon rdfs:label ?taxonLabel . FILTER (lang(?taxonLabel) = "en")
      {ref_block}
    }}
    LIMIT 10
    """


def get_molecule_details(
    qid: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
) -> dict[str, str] | None:
    """Fetch occurrence, taxon, reference, and structure details for a compound.

    Parameters
    ----------
    qid : str
        Wikidata QID of the compound.
    max_retries : int
        Maximum retry attempts per endpoint.
    retry_delay : float
        Base delay in seconds between retries.

    Returns
    -------
    dict[str, str] | None
        Occurrence details dictionary, or ``None`` if no matching occurrence is found.
    """
    qlever_query = _build_molecule_details_query(qid, use_scholarly_service=False)
    wdqs_query = _build_molecule_details_query(qid, use_scholarly_service=True)
    raw = execute_sparql(
        [(QLEVER_ENDPOINT, qlever_query), (WD_ENDPOINT, wdqs_query)],
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    results = raw.get("results", {}).get("bindings", [])
    if not results:
        return None

    row = secrets.choice(results)

    def extract_val(field: str) -> str:
        return str(row.get(field, {}).get("value", ""))

    def extract_qid(field: str) -> str:
        if "value" in row.get(field, {}):
            return str(row[field]["value"].split("/")[-1])
        return "unknown"

    smiles = extract_val("smiles")
    return {
        "compound": extract_val("compoundLabel"),
        "compound_qid": extract_qid("compound"),
        "taxon": extract_val("taxonLabel"),
        "taxon_qid": extract_qid("taxon"),
        "reference": extract_val("referenceLabel") or "an unknown reference",
        "reference_qid": extract_qid("reference"),
        "smiles": smiles,
        "image_url": build_depict_url(smiles),
        "taxon_image_url": extract_val("taxon_image"),
        "taxon_emoji": {"Q756": "🌿", "Q764": "🍄", "Q729": "🐛", "Q10876": "🦠"}.get(extract_qid("kingdom"), "🧬"),
        "kingdom_label": extract_val("kingdomLabel"),
    }


def _request_json_with_retry(
    url: str,
    *,
    params: dict[str, str] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    timeout: int = 10,
) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT}
    attempts = max(1, max_retries)
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
        except requests.RequestException as exc:
            if attempt + 1 >= attempts:
                raise
            logger.warning("HTTP JSON request failed for %s (attempt %d/%d): %s", url, attempt + 1, attempts, exc)
            if retry_delay > 0:
                time.sleep(retry_delay * (2**attempt))
    raise RuntimeError(url)  # pragma: no cover


def get_revisions(qid: str, since: datetime) -> list[dict[str, Any]]:
    since_utc = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since.astimezone(timezone.utc)
    url = "https://www.wikidata.org/w/api.php"
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": qid,
        "rvprop": "ids|timestamp|user",
        "rvstart": since_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rvlimit": "50",
        "rvdir": "newer",
        "formatversion": "2",
        "format": "json",
    }
    data = _request_json_with_retry(url, params=params, timeout=10)
    pages = data.get("query", {}).get("pages", [])
    revs = cast(list[dict[str, Any]], pages[0]["revisions"]) if pages and "revisions" in pages[0] else []
    if revs:
        parentid = revs[0].get("parentid")
        if isinstance(parentid, int) and parentid > 0:
            return [{"revid": parentid}, *revs]
    return revs


def get_entity_data(qid: str, revid: int) -> dict[str, Any]:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json?revision={revid}"
    data = _request_json_with_retry(url, timeout=10)
    return cast(dict[str, Any], data["entities"][qid])


def get_label_from_revision(qid: str, revid: int) -> str | None:
    entity = get_entity_data(qid, revid)
    val = entity.get("labels", {}).get("en", {}).get("value")
    return val if isinstance(val, str) else None


def get_claim_ids_from_revision(qid: str, revid: int, prop: str) -> set[str]:
    claims = get_entity_data(qid, revid).get("claims", {}).get(prop, [])
    ids: set[str] = set()
    for claim in claims:
        val = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(val, dict) and "id" in val:
            ids.add(val["id"])
    return ids


def get_revision_pairs(qid: str, since: datetime) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    revs = get_revisions(qid, since)
    return list(pairwise(revs))


def compare_revisions_for_change(
    qid: str,
    revisions: list[dict[str, Any]],
    extractor: Callable[[dict[str, Any]], str | None],
    old_val: str,
) -> str | None:
    for i in range(1, len(revisions)):
        old_data: dict[str, Any] = get_entity_data(qid, revisions[i - 1]["revid"])
        new_data: dict[str, Any] = get_entity_data(qid, revisions[i]["revid"])
        old = extractor(old_data)
        new = extractor(new_data)
        if old == old_val and new and new != old:
            user = revisions[i].get("user")
            return str(user) if isinstance(user, str) else None
    return None


def find_p703_removal_editor(qid: str, taxon_qid: str, since: datetime) -> str | None:
    for old_rev, new_rev in get_revision_pairs(qid, since):
        if taxon_qid in get_claim_ids_from_revision(
            qid,
            old_rev["revid"],
            "P703",
        ) and taxon_qid not in get_claim_ids_from_revision(qid, new_rev["revid"], "P703"):
            return str(new_rev["user"])
    return None


def extract_label(data: dict[str, Any]) -> str | None:
    val = data.get("labels", {}).get("en", {}).get("value")
    return val if isinstance(val, str) else None


def get_label_change_editor(qid: str, old_label: str, since: datetime) -> str | None:
    return compare_revisions_for_change(qid, get_revisions(qid, since), extract_label, old_label)


def _extract_first_string_claim(data: dict[str, Any], prop: str) -> str | None:
    for claim in data.get("claims", {}).get(prop, []):
        val = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(val, str):
            return val
    return None


def extract_smiles(data: dict[str, Any]) -> str | None:
    """Extract preferred SMILES (``P2017`` isomeric, else ``P233`` canonical) from entity JSON.

    Parameters
    ----------
    data : dict[str, Any]
        Wikidata entity dictionary from ``Special:EntityData``.

    Returns
    -------
    str | None
        SMILES string if present, else ``None``.
    """
    return _extract_first_string_claim(data, "P2017") or _extract_first_string_claim(data, "P233")


def get_smiles_change_editor(qid: str, old_smiles: str, since: datetime) -> str | None:
    """Find the Wikidata editor who changed a compound's SMILES (``P2017`` or ``P233``).

    Parameters
    ----------
    qid : str
        Compound Wikidata QID.
    old_smiles : str
        Previously recorded SMILES string.
    since : datetime
        Start timestamp for inspecting revisions.

    Returns
    -------
    str | None
        Editor username/IP if found, else ``None``.
    """
    return compare_revisions_for_change(qid, get_revisions(qid, since), extract_smiles, old_smiles)


def fetch_current_smiles(compound_qid: str) -> str:
    """Fetch the current preferred SMILES (``P2017`` else ``P233``) from Wikidata.

    Parameters
    ----------
    compound_qid : str
        Compound Wikidata QID.

    Returns
    -------
    str
        Current SMILES string, or empty string if missing.
    """
    query = f"""
    {SPARQL_PREFIXES}
    SELECT ?smiles WHERE {{
      OPTIONAL {{ wd:{compound_qid} wdt:P2017 ?smiles_i . }}
      OPTIONAL {{ wd:{compound_qid} wdt:P233 ?smiles_c . }}
      BIND(COALESCE(?smiles_i, ?smiles_c) AS ?smiles)
    }}
    LIMIT 1
    """
    raw = execute_sparql([(WD_ENDPOINT, query)])
    bindings = raw.get("results", {}).get("bindings", [])
    if not bindings:
        return ""
    return str(bindings[0].get("smiles", {}).get("value", ""))


def get_reference_label_change_editor(qid: str, old_label: str, since: datetime) -> str | None:
    for old_rev, new_rev in get_revision_pairs(qid, since):
        if (
            (old_val := get_label_from_revision(qid, old_rev["revid"])) == old_label
            and (new_val := get_label_from_revision(qid, new_rev["revid"]))
            and new_val != old_val
        ):
            return str(new_rev["user"])
    return None


def occurrence_still_exists(compound_qid: str, taxon_qid: str) -> bool:
    query = f"{SPARQL_PREFIXES}\nASK {{ wd:{compound_qid} wdt:P703 wd:{taxon_qid} . }}"
    result = execute_sparql([(WD_ENDPOINT, query)])
    return cast(bool, result.get("boolean", False))


def fetch_current_labels(compound_qid: str, taxon_qid: str, reference_qid: str) -> dict[str, str]:
    query = f"""
    {SPARQL_PREFIXES}
    SELECT ?compoundLabel ?taxonLabel ?referenceLabel WHERE {{
      OPTIONAL {{ wd:{compound_qid} rdfs:label ?compoundLabel . FILTER(LANG(?compoundLabel) = "en") }}
      OPTIONAL {{ wd:{taxon_qid} rdfs:label ?taxonLabel . FILTER(LANG(?taxonLabel) = "en") }}
      SERVICE <https://query-scholarly.wikidata.org/sparql> {{
        OPTIONAL {{ wd:{reference_qid} rdfs:label ?referenceLabel . FILTER(LANG(?referenceLabel) = "en") }}
      }}
    }}
    """
    raw = execute_sparql([(WD_ENDPOINT, query)])
    bindings = raw.get("results", {}).get("bindings", [])
    if not bindings:
        return {"compound_label": "", "taxon_label": "", "reference_label": ""}
    row = bindings[0]
    return {
        "compound_label": str(row.get("compoundLabel", {}).get("value", "")),
        "taxon_label": str(row.get("taxonLabel", {}).get("value", "")),
        "reference_label": str(row.get("referenceLabel", {}).get("value", "")),
    }
