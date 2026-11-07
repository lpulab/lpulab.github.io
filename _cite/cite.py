"""
cite process to convert sources and metasources into full citations
"""

import traceback
import sys
from importlib import import_module
from pathlib import Path
from dotenv import load_dotenv
from util import *


# load environment variables
load_dotenv()


# save errors/warnings for reporting at end
errors = []
warnings = []

# output citations file
output_file = "_data/citations.yaml"

# link patterns used to build a bare-bones citation when Manubot fails
fallback_links = {
    "doi": "https://doi.org/{}",
    "pubmed": "https://pubmed.ncbi.nlm.nih.gov/{}",
    "pmid": "https://pubmed.ncbi.nlm.nih.gov/{}",
    "pmc": "https://www.ncbi.nlm.nih.gov/pmc/articles/{}",
    "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/{}",
    "arxiv": "https://arxiv.org/abs/{}",
    "url": "{}",
}


def fallback_citation(_id):
    """
    minimal citation built from the source id alone,
    last resort when no full citation can be generated
    """
    kind, _, value = _id.partition(":")
    pattern = fallback_links.get(kind.strip().lower())
    link = pattern.format(value.strip()) if pattern and value.strip() else ""
    return {"title": _id, "link": link}


def first_text(value):
    """
    CSL fields can be a string or a list of strings; return one clean string
    """
    if isinstance(value, list):
        value = next((v for v in value if v), "")
    return " ".join(str(value or "").split())


def cite_doi_directly(_id):
    """
    second attempt for DOIs: ask doi.org directly for the citation data (CSL JSON),
    independent of Manubot and of the template's date handling.
    returns a citation dict, or {} if the id isn't a DOI or the lookup fails
    """
    kind, _, doi = _id.partition(":")
    doi = doi.strip()
    if kind.strip().lower() != "doi" or not doi:
        return {}

    try:
        import requests

        response = requests.get(
            f"https://doi.org/{doi}",
            headers={"Accept": "application/vnd.citationstyles.csl+json"},
            timeout=30,
        )
        response.raise_for_status()
        csl = response.json()
        if not isinstance(csl, dict) or not first_text(csl.get("title")):
            raise Exception("doi.org returned no title")
    except Exception as e:
        log(f"Direct DOI lookup failed: {e}", indent=3, level="WARNING")
        return {}

    # authors, as "Given Family"
    authors = []
    for author in csl.get("author") or []:
        name = " ".join(
            filter(None, [first_text(author.get("given")), first_text(author.get("family"))])
        )
        authors.append(name or first_text(author.get("literal")) or first_text(author.get("name")))
    authors = [author for author in authors if author]

    # date as YYYY-MM-DD; a missing month or day becomes 01
    date = ""
    try:
        parts = [int(p) for p in csl["issued"]["date-parts"][0] if p is not None]
    except Exception:
        parts = []
    if parts:
        from datetime import date as make_date

        year, month, day = (parts + [1, 1])[:3]
        try:
            date = make_date(year, month, day).isoformat()
        except ValueError:
            date = f"{year:04d}-01-01"

    return {
        "title": first_text(csl.get("title")),
        "authors": authors,
        "publisher": first_text(csl.get("container-title"))
        or first_text(csl.get("publisher"))
        or first_text(csl.get("collection-title")),
        "date": date,
        "link": f"https://doi.org/{doi}",
    }


log()

log("Compiling sources")

# compiled list of sources
sources = []

# in-order list of plugins to run
plugins = ["google-scholar", "pubmed", "orcid", "sources"]

# loop through plugins
for plugin in plugins:
    # convert into path object
    plugin = Path(f"plugins/{plugin}.py")

    log(f"Running {plugin.stem} plugin")

    # get all data files to process with current plugin
    files = Path.cwd().glob(f"_data/{plugin.stem}*.*")
    files = list(filter(lambda p: p.suffix in [".yaml", ".yml", ".json"], files))

    log(f"Found {len(files)} {plugin.stem}* data file(s)", indent=1)

    # loop through data files
    for file in files:
        log(f"Processing data file {file.name}", indent=1)

        # load data from file
        try:
            data = load_data(file)
            # check if file in correct format
            if not list_of_dicts(data):
                raise Exception(f"{file.name} data file not a list of dicts")
        except Exception as e:
            log(e, indent=2, level="ERROR")
            errors.append(e)
            continue

        # loop through data entries
        for index, entry in enumerate(data):
            log(f"Processing entry {index + 1} of {len(data)}, {label(entry)}", level=2)

            # run plugin on data entry to expand into multiple sources
            try:
                expanded = import_module(f"plugins.{plugin.stem}").main(entry)
                # check that plugin returned correct format
                if not list_of_dicts(expanded):
                    raise Exception(f"{plugin.stem} plugin didn't return list of dicts")
            # catch any plugin error
            except Exception as e:
                # log detailed pre-formatted/colored trace
                print(traceback.format_exc())
                # log high-level error
                log(e, indent=3, level="ERROR")
                errors.append(e)
                continue

            # loop through sources
            for source in expanded:
                if plugin.stem != "sources":
                    log(label(source), level=3)

                # include meta info about source
                source["plugin"] = plugin.name
                source["file"] = file.name

                # add source to compiled list
                sources.append(source)

            if plugin.stem != "sources":
                log(f"{len(expanded)} source(s)", indent=3)


log("Merging sources by id")

# merge sources with matching (non-blank) ids
for a in range(0, len(sources)):
    a_id = get_safe(sources, f"{a}.id", "")
    if not a_id:
        continue
    for b in range(a + 1, len(sources)):
        b_id = get_safe(sources, f"{b}.id", "")
        if b_id == a_id:
            log(f"Found duplicate {b_id}", indent=2)
            sources[a].update(sources[b])
            sources[b] = {}
sources = [entry for entry in sources if entry]


log(f"{len(sources)} total source(s) to cite")


log()

log("Generating citations")

# list of new citations
citations = []


# loop through compiled sources
for index, source in enumerate(sources):
    log(f"Processing source {index + 1} of {len(sources)}, {label(source)}")

    # if explicitly flagged, remove/ignore entry
    if get_safe(source, "remove", False) == True:
        continue

    # new citation data for source
    citation = {}

    # source id
    _id = get_safe(source, "id", "").strip()

    # manubot doesn't work without an id
    if _id:
        log("Using Manubot to generate citation", indent=1)

        try:
            # run manubot and set citation
            citation = cite_with_manubot(_id)

        # if manubot cannot cite source, never drop it: try a direct DOI lookup,
        # then fall back to a placeholder built from the id.
        # (fields from the input source still override either one below)
        except Exception as e:
            plugin = get_safe(source, "plugin", "")
            file = get_safe(source, "file", "")
            log(e, indent=3, level="WARNING")

            citation = cite_doi_directly(_id)
            if citation:
                log("Used direct DOI lookup instead", indent=3)
                warnings.append(
                    f"Manubot step failed for source {_id} ({e}), used direct DOI lookup instead (from {file} with {plugin})"
                )
            else:
                citation = fallback_citation(_id)
                warnings.append(
                    f"Could not generate full citation for source {_id} (from {file} with {plugin}), kept with limited details"
                )

    # preserve fields from input source, overriding existing fields
    citation.update(source)

    # ensure date in proper format for correct date sorting
    if get_safe(citation, "date", ""):
        citation["date"] = format_date(get_safe(citation, "date", ""))

    # add new citation to list
    citations.append(citation)


log()

log("Saving updated citations")


# save new citations
try:
    save_data(output_file, citations)
except Exception as e:
    log(e, level="ERROR")
    errors.append(e)


log()


# exit at end, so user can see all errors/warnings in one run
if len(warnings):
    log(f"{len(warnings)} warning(s) occurred above", level="WARNING")
    for warning in warnings:
        log(warning, indent=1, level="WARNING")

if len(errors):
    log(f"{len(errors)} error(s) occurred above", level="ERROR")
    for error in errors:
        log(error, indent=1, level="ERROR")
    log()
    exit(1)

else:
    log("All done!", level="SUCCESS")

log()
