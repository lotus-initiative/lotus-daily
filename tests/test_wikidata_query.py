from __future__ import annotations

from typing import Any

from daily_lotus import themes, wikidata_query

_SAMPLE_PAYLOAD: dict[str, Any] = {
    "results": {
        "bindings": [
            {
                "compound": {"value": "http://www.wikidata.org/entity/Q60235"},
                "compoundLabel": {"value": "caffeine"},
                "taxon": {"value": "http://www.wikidata.org/entity/Q101815"},
                "taxonLabel": {"value": "Camellia sinensis"},
                "reference": {"value": "http://www.wikidata.org/entity/Q104247570"},
                "referenceLabel": {"value": "Example paper"},
                "smiles": {"value": "Cn1c(=O)c2c(ncn2C)n(C)c1=O"},
                "taxon_image": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/Csinensis.jpg"},
                "kingdom": {"value": "http://www.wikidata.org/entity/Q756"},
                "kingdomLabel": {"value": "plant"},
            },
        ],
    },
}


class _FakeQueryResult:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def convert(self) -> dict[str, Any]:
        return self._result


def _install_wdqs_fallback_wrapper(monkeypatch: Any) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    class FakeSPARQLWrapper:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint
            self.query_text = ""

        def addCustomHttpHeader(self, name: str, value: str) -> None:
            pass

        def setTimeout(self, timeout: int) -> None:
            pass

        def setQuery(self, query: str) -> None:
            self.query_text = query

        def setReturnFormat(self, fmt: str) -> None:
            pass

        def query(self) -> _FakeQueryResult:
            calls.append((self.endpoint, self.query_text))
            if self.endpoint == wikidata_query.QLEVER_ENDPOINT:
                raise TimeoutError
            return _FakeQueryResult(_SAMPLE_PAYLOAD)

    monkeypatch.setattr(wikidata_query, "SPARQLWrapper", FakeSPARQLWrapper)
    return calls


def test_uses_wdqs_v2_endpoints_in_queries(monkeypatch: Any) -> None:
    monkeypatch.delenv("SPARQL_ENDPOINT", raising=False)
    assert wikidata_query.WD_ENDPOINT == "https://query-next.wikidata.org/sparql"
    assert wikidata_query.WD_SCHOLARLY_ENDPOINT == "https://query-scholarly-next.wikidata.org/sparql"

    calls = _install_wdqs_fallback_wrapper(monkeypatch)
    freshest = themes.get_theme("freshest")
    details = wikidata_query.get_molecule_details("Q60235", max_retries=1, retry_delay=0.0)
    labels = wikidata_query.fetch_current_labels("Q60235", "Q101815", "Q104247570")
    candidates = wikidata_query.get_candidate_qids(theme=freshest, max_retries=1, retry_delay=0.0)
    themed_details = wikidata_query.get_molecule_details(
        "Q60235",
        theme=freshest,
        max_retries=1,
        retry_delay=0.0,
    )

    assert details is not None
    assert labels["reference_label"] == "Example paper"
    assert candidates == ["Q60235"]
    assert themed_details is not None

    qlever_queries = [query for endpoint, query in calls if endpoint == wikidata_query.QLEVER_ENDPOINT]
    wd_calls = [(endpoint, query) for endpoint, query in calls if endpoint != wikidata_query.QLEVER_ENDPOINT]

    assert len(qlever_queries) == 3
    for query in qlever_queries:
        assert "SERVICE <" not in query
        assert "https://query.wikidata.org/sparql" not in query
        assert "https://query-scholarly.wikidata.org/sparql" not in query

    assert len(wd_calls) == 4
    for endpoint, query in wd_calls:
        assert endpoint == "https://query-next.wikidata.org/sparql"
        assert "https://query-scholarly-next.wikidata.org/sparql" in query
        assert "https://query-scholarly.wikidata.org/sparql" not in query
        assert "https://query.wikidata.org/sparql" not in query
