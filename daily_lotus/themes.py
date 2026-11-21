from dataclasses import dataclass
from datetime import date, datetime, timezone

_SPARQL_PREFIXES = """
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX ps: <http://www.wikidata.org/prop/statement/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX pr: <http://www.wikidata.org/prop/reference/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
""".strip()


class UnknownThemeError(ValueError):
    """Raised when an unrecognized theme slug is requested."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"Unknown theme: {slug}")


@dataclass(frozen=True)
class Theme:
    """Configuration describing a thematic LOTUS daily post."""

    slug: str
    name: str
    header: str
    hashtag: str
    description: str
    weekday: int | None = None
    candidate_limit: int = 5000
    taxon_ancestor_qid: str | None = None
    chemical_class_qid: str | None = None
    min_mass: float | None = None
    recent_years: int | None = None


THEMES: dict[str, Theme] = {
    "alkaloid": Theme(
        slug="alkaloid",
        name="Monday's Alkaloid",
        header="📣 Monday's Alkaloid",
        hashtag="#MondayAlkaloid",
        description="Alkaloids (Q70702) with documented biological occurrences",
        weekday=0,
        chemical_class_qid="Q70702",
    ),
    "insect": Theme(
        slug="insect",
        name="Tuesday's Insect Compound",
        header="📣 Tuesday's Insect Compound",
        hashtag="#InsectTuesday",
        description="Natural products isolated from insects (Q1390)",
        weekday=1,
        taxon_ancestor_qid="Q1390",
    ),
    "big-fat-np": Theme(
        slug="big-fat-np",
        name="Wednesday's Big Fat NP",
        header="📣 Wednesday's Big Fat Natural Product",
        hashtag="#BigFatNP",
        description="High-molecular-weight natural products (mass >= 800 Da)",
        weekday=2,
        min_mass=800.0,
    ),
    "freshest": Theme(
        slug="freshest",
        name="Thursday's Freshest LOTUS Occurrence",
        header="📣 Thursday's Freshest LOTUS Occurrence",
        hashtag="#FreshestNP",
        description="Occurrences backed by recent publications",
        weekday=3,
        candidate_limit=500,
        recent_years=2,
    ),
    "plant": Theme(
        slug="plant",
        name="Plant Natural Product",
        header="📣 Plant Natural Product of the Day",
        hashtag="#PlantNP",
        description="Natural products isolated from plants (Q756)",
        taxon_ancestor_qid="Q756",
    ),
    "fungi": Theme(
        slug="fungi",
        name="Fungal Natural Product",
        header="📣 Fungal Natural Product of the Day",
        hashtag="#FungiNP",
        description="Natural products isolated from fungi (Q764)",
        taxon_ancestor_qid="Q764",
    ),
    "bacteria": Theme(
        slug="bacteria",
        name="Bacterial Natural Product",
        header="📣 Bacterial Natural Product of the Day",
        hashtag="#BacteriaNP",
        description="Natural products isolated from bacteria (Q10876)",
        taxon_ancestor_qid="Q10876",
    ),
    "animal": Theme(
        slug="animal",
        name="Animal Natural Product",
        header="📣 Animal Natural Product of the Day",
        hashtag="#AnimalNP",
        description="Natural products isolated from animals (Q729)",
        taxon_ancestor_qid="Q729",
    ),
}


def get_theme(slug: str) -> Theme:
    """Look up a theme by slug.

    Parameters
    ----------
    slug : str
        Theme identifier (case-insensitive).

    Returns
    -------
    Theme
        Matching theme configuration.
    """
    normalized = slug.strip().lower()
    if normalized not in THEMES:
        raise UnknownThemeError(slug)
    return THEMES[normalized]


def get_theme_for_date(target_date: date | None = None) -> Theme | None:
    """Return the scheduled theme for ``target_date`` (Monday-Thursday), or ``None``.

    Parameters
    ----------
    target_date : date | None
        Date to check. Defaults to the current UTC date.

    Returns
    -------
    Theme | None
        Matching scheduled theme, or ``None`` on Friday through Sunday.
    """
    resolved = target_date if target_date is not None else datetime.now(tz=timezone.utc).date()
    weekday = resolved.weekday()
    for theme in THEMES.values():
        if theme.weekday == weekday:
            return theme
    return None


def list_themes() -> list[Theme]:
    """Return all registered themes.

    Returns
    -------
    list[Theme]
        List of configured themes.
    """
    return list(THEMES.values())


def resolve_reference_year_cutoff(
    theme: Theme,
    *,
    reference_year_cutoff: int | None = None,
    reference_date: date | None = None,
) -> int | None:
    """Resolve the minimum publication year for recency-filtered themes."""
    if theme.recent_years is None:
        return None
    if reference_year_cutoff is not None:
        return reference_year_cutoff
    resolved_date = reference_date if reference_date is not None else datetime.now(tz=timezone.utc).date()
    return resolved_date.year - theme.recent_years


def _build_freshest_candidate_query(
    theme: Theme,
    cutoff_year: int,
    *,
    use_scholarly_service: bool,
) -> str:
    cutoff_literal = f'"{cutoff_year}-01-01T00:00:00Z"^^xsd:dateTime'
    if use_scholarly_service:
        return f"""
        {_SPARQL_PREFIXES}
        SELECT DISTINCT ?compound WHERE {{
          SERVICE <https://query-scholarly.wikidata.org/sparql> {{
            SELECT ?reference ?pub_date WHERE {{
              ?reference wdt:P577 ?pub_date .
              FILTER(?pub_date >= {cutoff_literal})
            }}
            ORDER BY DESC(?pub_date)
            LIMIT 2000
          }}
          ?compound p:P703 ?stmt ;
                    wdt:P233 [] .
          ?stmt ps:P703 ?taxon ;
                prov:wasDerivedFrom/pr:P248 ?reference .
          ?taxon wdt:P18 ?image .
        }}
        LIMIT {theme.candidate_limit}
        """

    return f"""
    {_SPARQL_PREFIXES}
    SELECT DISTINCT ?compound WHERE {{
      ?compound wdt:P233 [] ;
                p:P703 ?stmt .
      ?stmt ps:P703 ?taxon ;
            prov:wasDerivedFrom/pr:P248 ?reference .
      ?taxon wdt:P18 ?image .
      ?reference wdt:P577 ?pub_date .
      FILTER(?pub_date >= {cutoff_literal})
    }}
    ORDER BY DESC(?pub_date)
    LIMIT {theme.candidate_limit}
    """


def build_theme_candidate_query(
    theme: Theme,
    *,
    use_scholarly_service: bool = False,
    reference_year_cutoff: int | None = None,
) -> str:
    """Build the SPARQL candidate selection query for ``theme``.

    Parameters
    ----------
    theme : Theme
        Target theme definition.
    use_scholarly_service : bool
        When ``True``, wrap publication-date filtering in WDQS's scholarly subgraph ``SERVICE``.
    reference_year_cutoff : int | None
        Optional explicit minimum publication year for recency themes.

    Returns
    -------
    str
        SPARQL ``SELECT DISTINCT ?compound`` query string.
    """
    cutoff = resolve_reference_year_cutoff(theme, reference_year_cutoff=reference_year_cutoff)
    if cutoff is not None:
        return _build_freshest_candidate_query(theme, cutoff, use_scholarly_service=use_scholarly_service)

    clauses: list[str] = []
    if theme.chemical_class_qid:
        clauses.append(f"?compound (wdt:P31|wdt:P279)/wdt:P279* wd:{theme.chemical_class_qid} .")
    if theme.taxon_ancestor_qid:
        clauses.append(f"?taxon wdt:P171* wd:{theme.taxon_ancestor_qid} .")
    if theme.min_mass is not None:
        clauses.append(f"?compound wdt:P2067 ?mass . FILTER(?mass >= {theme.min_mass:g})")

    extra = "\n      ".join(clauses)
    return f"""
    {_SPARQL_PREFIXES}
    SELECT DISTINCT ?compound WHERE {{
      ?compound wdt:P703 ?taxon ;
                wdt:P233 [] .
      ?taxon wdt:P18 ?image .
      {extra}
    }}
    LIMIT {theme.candidate_limit}
    """


def build_theme_details_clause(
    theme: Theme,
    *,
    use_scholarly_service: bool = False,
    reference_year_cutoff: int | None = None,
) -> str:
    """Build extra SPARQL triple/filter patterns so details match ``theme``.

    Parameters
    ----------
    theme : Theme
        Active theme definition.
    use_scholarly_service : bool
        When ``True``, query ``P577`` via WDQS's scholarly subgraph ``SERVICE``.
    reference_year_cutoff : int | None
        Optional explicit minimum publication year for recency themes.

    Returns
    -------
    str
        SPARQL pattern snippet to insert inside ``get_molecule_details``.
    """
    clauses: list[str] = []
    if theme.chemical_class_qid:
        clauses.append(f"?compound (wdt:P31|wdt:P279)/wdt:P279* wd:{theme.chemical_class_qid} .")
    if theme.taxon_ancestor_qid:
        clauses.append(f"?taxon wdt:P171* wd:{theme.taxon_ancestor_qid} .")
    if theme.min_mass is not None:
        clauses.append(f"?compound wdt:P2067 ?mass . FILTER(?mass >= {theme.min_mass:g})")

    cutoff = resolve_reference_year_cutoff(theme, reference_year_cutoff=reference_year_cutoff)
    if cutoff is not None:
        date_filter = f'?reference wdt:P577 ?pub_date . FILTER(?pub_date >= "{cutoff}-01-01T00:00:00Z"^^xsd:dateTime)'
        if use_scholarly_service:
            clauses.append(f"SERVICE <https://query-scholarly.wikidata.org/sparql> {{\n        {date_filter}\n      }}")
        else:
            clauses.append(date_filter)

    return "\n      ".join(clauses)
