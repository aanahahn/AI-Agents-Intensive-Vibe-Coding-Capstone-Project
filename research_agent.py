#!/usr/bin/env python3
"""
AI Research Agent: NIH Funding + Clinical Trials + PubMed Metadata
=================================================================
Fetches, filters, analyzes, and reports on biomedical research data from:
  - NIH RePORTER API v2  (funding/projects)
  - ClinicalTrials.gov API v2 (clinical studies)
  - PubMed E-utilities API (publications)

Works in: CLI, Kaggle, Colab, Jupyter, any Python 3.8+ environment.
Outputs:  findings .txt report + Tableau-ready .csv.

Quick start (notebook):
  from research_agent import run_pipeline
  run_pipeline(interest="genomics", year=2025, max_per_source=20)

Quick start (CLI):
  python research_agent.py --interest genomics --year 2025
"""

import csv
import os
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# Self-install missing deps (critical for Kaggle/Colab)
# ---------------------------------------------------------------------------
_INSTALLED = False
for _pkg in ("requests", "pandas"):
    try:
        __import__(_pkg)
    except ImportError:
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", _pkg]
        )
        print(f"  Installed {_pkg}")
_INSTALLED = True

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NIH_API = "https://api.reporter.nih.gov/v2/projects/search"
CT_API_BASE = "https://clinicaltrials.gov/api/v2"
PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

REQUEST_INTERVAL = 1.0
MAX_RESULTS_PER_SOURCE = 100
USER_AGENT = "NIH-Research-Agent/1.0 (mailto:user@example.com)"

PREDEFINED_QUERIES = {
    "genomics": "genomics OR genome OR sequencing OR GWAS OR CRISPR",
    "cell therapy": "cell therapy OR CAR-T OR stem cell OR immunotherapy",
    "personalized medicine": (
        "personalized medicine OR precision medicine OR pharmacogenomics"
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rate_limit():
    time.sleep(REQUEST_INTERVAL)


def _parse_years(year_input):
    """Accept int, str like '2022-2025', or tuple/list of ints.
    Returns (year_list, display_string)."""
    if year_input is None:
        y = datetime.now().year
        return [y], str(y)
    if isinstance(year_input, (list, tuple)):
        ylist = sorted([int(y) for y in year_input if y])
        if not ylist:
            ylist = [datetime.now().year]
        return ylist, f"{ylist[0]}-{ylist[-1]}" if len(ylist) > 1 else str(ylist[0])
    s = str(year_input).strip()
    if "-" in s:
        parts = s.split("-")
        try:
            start, end = int(parts[0]), int(parts[1])
            ylist = list(range(start, end + 1))
            return ylist, f"{start}-{end}"
        except ValueError:
            pass
    try:
        y = int(s)
        return [y], str(y)
    except ValueError:
        y = datetime.now().year
        return [y], str(y)


def _improve_query(text):
    """Convert a natural-language question into keyword search terms.
    Strips question prefixes, filler words, and time references."""
    import re
    q = text.strip().rstrip("?")
    # Remove leading question phrases: "Which genomics technologies..." -> "genomics technologies..."
    q = re.sub(
        r"^(which|what|how|why|where|when)\s+(are|is|do|does|did|have|has|can|could|would|should)\s+",
        "", q, flags=re.IGNORECASE,
    )
    q = re.sub(r"^(which|what|how|why|where|when)\s+", "", q, flags=re.IGNORECASE)
    q = re.sub(r"^(can|could|would|should|does|do|did|are|is)\s+", "", q, flags=re.IGNORECASE)
    # Remove time-range boilerplate: "over the last five years", "2020-2025", etc.
    q = re.sub(
        r"\b(over the past|over the last|in the last|during the|in the past)\s+\w+\b",
        "", q, flags=re.IGNORECASE,
    )
    # Remove comparative/superlative framing
    q = re.sub(
        r"\b(the most|the least|the fastest|the highest|the lowest|the largest|the smallest|the best|the worst)\b",
        "", q, flags=re.IGNORECASE,
    )
    # Remove meta-verbs and request words
    q = re.sub(
        r"\b(experienced|received|shown|demonstrated|exhibited|please|tell me|show me|list|find|give me|get|have been|has been|are being|were being)\b",
        "", q, flags=re.IGNORECASE,
    )
    # Remove trailing year patterns (already handled by --year / _parse_years)
    q = re.sub(r"\b\d{4}[\s-]+\d{4}\b", "", q)
    q = re.sub(r"\b\d{4}\b", "", q)
    # Remove common stopwords, keep 2+ char words (for acronyms like AI, RNA)
    words = re.findall(r"\b[a-zA-Z]{2,}\b", q)
    STOP = {
        "the", "and", "for", "are", "has", "had", "but", "not", "you",
        "all", "can", "its", "over", "last", "have", "that", "which",
        "what", "how", "why", "where", "when", "with", "from", "been",
        "were", "they", "their", "them", "this", "that", "these", "those",
        "also", "than", "then", "each", "much", "more", "most", "some",
        "any", "into", "about", "could", "would", "should", "does", "did",
        "after", "before", "between", "through", "during", "without",
        "years", "year", "five", "past", "role", "new",
        "to", "in", "of", "at", "on", "by", "up", "no", "if", "or",
        "as", "an", "be", "it", "is", "we", "he", "she", "do",
    }
    words = [w for w in words if w.lower() not in STOP]
    if not words:
        return text

    # Check if the query maps to a predefined interest area (by keyword overlap)
    known_areas = {k.lower(): v for k, v in PREDEFINED_QUERIES.items()}
    combined = " ".join(words).lower()
    for keyword, predefined_query in known_areas.items():
        kw_parts = keyword.lower().split()
        if any(p in combined for p in kw_parts):
            return predefined_query
    return " ".join(words)


def _parse_query_terms(query):
    """Split a Boolean OR query into individual keyword terms."""
    return [t.strip() for t in query.split(" OR ") if t.strip()]


def _matching_terms(terms, *text_fields):
    """Return list of query terms found across any of the given text fields.
    Matches on word boundaries for multi-word terms, uses substring for single words."""
    if not terms:
        return []
    import re
    combined = " ".join(t.lower() for t in text_fields if t)
    matched = []
    for t in terms:
        pattern = r"\b" + re.escape(t.lower()) + r"\b"
        if re.search(pattern, combined):
            matched.append(t)
    return matched


def _keyword_stats(terms, records, text_getter):
    """Count how many records match each query term.
    text_getter is a function that returns the text to search per record."""
    counts = {t: 0 for t in terms}
    for rec in records:
        text = text_getter(rec).lower() if text_getter(rec) else ""
        for t in terms:
            if t.lower() in text:
                counts[t] = counts.get(t, 0) + 1
    return counts


def _dump_json(data, path):
    """Save data as formatted JSON."""
    import json
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)


def _load_json(path):
    """Load data from a JSON file if it exists."""
    import json
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _safe_get(url, params=None, headers=None, method="GET", json_payload=None):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    try:
        if method.upper() == "POST" and json_payload:
            resp = requests.post(url, json=json_payload, headers=hdrs, timeout=60)
        else:
            resp = requests.get(url, params=params, headers=hdrs, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as exc:
        print(f"  [WARN] API call failed: {url} — {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# 1. NIH RePORTER
# ---------------------------------------------------------------------------
def fetch_nih_projects(interest, years, max_records=MAX_RESULTS_PER_SOURCE):
    """Query NIH RePORTER v2 for projects matching interest + years (list)."""
    ylist = _parse_years(years)[0]
    label = _parse_years(years)[1]
    print(f"  Fetching NIH projects for '{interest}' ({label}) ...")
    payload = {
        "criteria": {
            "fiscal_years": ylist,
            "advanced_text_search": {
                "operator": "or",
                "search_field": "projecttitle,terms",
                "search_text": interest,
            },
            "include_active_projects": True,
            "exclude_subprojects": True,
        },
        "include_fields": [
            "ApplId", "FiscalYear", "ProjectNum", "ProjectTitle",
            "AwardAmount", "Organization", "PrincipalInvestigators",
            "ActivityCode", "AbstractText", "PhrText",
        ],
        "offset": 0,
        "limit": min(max_records, 500),
        "sort_field": "project_start_date",
        "sort_order": "desc",
    }
    data = _safe_get(NIH_API, method="POST", json_payload=payload)
    if not data:
        return []
    _rate_limit()
    if isinstance(data, list):
        return data[:max_records]
    return (data.get("results") or [])[:max_records]


# ---------------------------------------------------------------------------
# 2. ClinicalTrials.gov
# ---------------------------------------------------------------------------
def fetch_clinical_trials(interest, years, max_records=MAX_RESULTS_PER_SOURCE):
    """Search ClinicalTrials.gov v2 for studies matching condition + years."""
    ylist = _parse_years(years)[0]
    label = _parse_years(years)[1]
    print(f"  Fetching clinical trials for '{interest}' ({label}) ...")
    params = {
        "query.term": interest,
        "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING,COMPLETED",
        "pageSize": min(max_records, 1000),
        "format": "json",
    }
    data = _safe_get(f"{CT_API_BASE}/studies", params=params)
    if not data:
        return []
    _rate_limit()
    studies = data.get("studies") or []
    year_strs = set(str(y) for y in ylist)
    filtered = []
    for s in studies:
        proto = s.get("protocolSection") or {}
        status_mod = proto.get("statusModule") or {}
        start_date = status_mod.get("startDate") or {}
        if isinstance(start_date, dict):
            raw = start_date.get("date") or ""
        else:
            raw = str(start_date)
        if raw and raw[:4] in year_strs:
            filtered.append(s)
        elif not raw:
            filtered.append(s)
    return filtered[:max_records]


# ---------------------------------------------------------------------------
# 3. PubMed
# ---------------------------------------------------------------------------
def fetch_pubmed(interest, years, max_records=MAX_RESULTS_PER_SOURCE):
    """Search PubMed via E-utilities, then fetch summaries."""
    ylist, ylabel = _parse_years(years)
    print(f"  Fetching PubMed articles for '{interest}' ({ylabel}) ...")
    if len(ylist) > 1:
        date_term = f"{ylist[0]}:{ylist[-1]}[pdat]"
    else:
        date_term = f"{ylist[0]}[pdat]"
    search_params = {
        "db": "pubmed",
        "term": f"({interest}) AND {date_term}",
        "retmax": min(max_records, 10000),
        "retmode": "json",
        "sort": "relevance",
    }
    search_data = _safe_get(PUBMED_ESEARCH, params=search_params)
    if not search_data:
        return []
    _rate_limit()
    id_list = (search_data.get("esearchresult") or {}).get("idlist") or []
    if not id_list:
        return []
    ids = id_list[:max_records]

    # Chunk IDs to avoid 414 URI Too Long errors on esummary
    CHUNK = 100
    articles = []
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        summary_data = _safe_get(
            PUBMED_ESUMMARY,
            params={"db": "pubmed", "id": ",".join(chunk), "retmode": "json"},
        )
        if not summary_data:
            continue
        _rate_limit()
        result_map = summary_data.get("result") or {}
        for uid in chunk:
            art = result_map.get(uid)
            if art and art.get("uid"):
                articles.append(art)
    return articles


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def analyze_nih(projects):
    """Analyze NIH project data."""
    if not projects:
        return {"count": 0}
    total_award = 0
    award_counts = []
    orgs = {}
    activity_counts = {}
    for p in projects:
        amt = p.get("award_amount") or 0
        award_counts.append(amt)
        total_award += amt
        org = p.get("organization") or {}
        org_name = org.get("org_name") or "Unknown"
        orgs[org_name] = orgs.get(org_name, 0) + 1
        act = p.get("activity_code") or "Unknown"
        activity_counts[act] = activity_counts.get(act, 0) + 1
    return {
        "count": len(projects),
        "total_funding": total_award,
        "avg_funding": round(total_award / len(projects), 2) if projects else 0,
        "max_funding": max(award_counts) if award_counts else 0,
        "top_orgs": sorted(orgs.items(), key=lambda x: -x[1])[:5],
        "activity_codes": sorted(activity_counts.items(), key=lambda x: -x[1])[:5],
    }


def analyze_trials(trials):
    """Analyze clinical trials data."""
    if not trials:
        return {"count": 0}
    statuses = {}
    phases = {}
    sponsors = {}
    conditions = {}
    for t in trials:
        proto = t.get("protocolSection") or {}
        sm = proto.get("statusModule") or {}
        ds = proto.get("designModule") or {}
        sp = proto.get("sponsorCollaboratorsModule") or {}
        st = sm.get("overallStatus") or "Unknown"
        statuses[st] = statuses.get(st, 0) + 1
        for ph in (ds.get("phases") or []):
            phases[ph] = phases.get(ph, 0) + 1
        lead = sp.get("leadSponsor") or {}
        sp_name = lead.get("name") or "Unknown"
        sponsors[sp_name] = sponsors.get(sp_name, 0) + 1
        cond_mod = proto.get("conditionsModule") or {}
        for cond in (cond_mod.get("conditions") or []):
            conditions[cond] = conditions.get(cond, 0) + 1
    return {
        "count": len(trials),
        "statuses": sorted(statuses.items(), key=lambda x: -x[1])[:5],
        "phases": sorted(phases.items(), key=lambda x: -x[1])[:5],
        "top_sponsors": sorted(sponsors.items(), key=lambda x: -x[1])[:5],
        "top_conditions": sorted(conditions.items(), key=lambda x: -x[1])[:5],
    }


def analyze_pubmed(articles):
    """Analyze PubMed publication data."""
    if not articles:
        return {"count": 0}
    journals = {}
    years = {}
    authors_count = []
    for a in articles:
        src = a.get("source") or "Unknown"
        journals[src] = journals.get(src, 0) + 1
        y = (a.get("pubdate") or "")[:4]
        if y:
            years[y] = years.get(y, 0) + 1
        auths = a.get("authors") or []
        authors_count.append(len(auths))
    return {
        "count": len(articles),
        "top_journals": sorted(journals.items(), key=lambda x: -x[1])[:5],
        "pub_years": sorted(years.items(), key=lambda x: -x[1], reverse=True)[:5],
        "avg_authors": round(sum(authors_count) / len(authors_count), 1)
        if authors_count else 0,
    }


def generate_citations(articles):
    """Generate formatted citations from PubMed articles."""
    citations = []
    for a in articles:
        title = (a.get("title") or "").rstrip(".")
        authors = a.get("authors") or []
        author_list = []
        for au in authors[:5]:
            name = au.get("name") or ""
            if name:
                author_list.append(name)
        author_str = ", ".join(author_list)
        if len(authors) > 5:
            author_str += " et al."
        journal = a.get("source") or ""
        year = (a.get("pubdate") or "")[:4]
        pmid = a.get("uid") or ""
        doi = ""
        for aid in (a.get("articleids") or []):
            if aid.get("idtype") == "doi":
                doi = aid.get("value") or ""
        cite = f"{author_str} ({year}). {title}. {journal}. PMID: {pmid}"
        if doi:
            cite += f". DOI: {doi}"
        citations.append(cite)
    return citations


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def _add_keyword_table(lines, terms, nih_stats, ct_stats, pm_stats):
    """Append a formatted keyword-match table to lines."""
    if not terms:
        return
    lines.append("  Query keyword breakdown (records matching each term):")
    lines.append(f"  {'Term':30s} {'NIH':>6s} {'Trials':>8s} {'PubMed':>8s}")
    lines.append(f"  {'-'*30} {'-'*6} {'-'*8} {'-'*8}")
    for t in terms:
        n = nih_stats.get(t, 0) if nih_stats else 0
        c = ct_stats.get(t, 0) if ct_stats else 0
        p = pm_stats.get(t, 0) if pm_stats else 0
        lines.append(f"  {t:30s} {n:>6d} {c:>8d} {p:>8d}")
    lines.append("")


def write_report(out_dir, interest, year_label, query, terms,
                 nih_analysis, ct_analysis, pm_analysis, citations,
                 nih_keyword_stats=None, ct_keyword_stats=None,
                 pm_keyword_stats=None):
    """Write the findings report to a .txt file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_name = interest.replace(" ", "_").replace("/", "_")[:60]
    lines = [
        "=" * 72,
        "  NIH-CLINICALTRIALS-PUBMED RESEARCH AGENT \u2014 FINDINGS REPORT",
        "=" * 72,
        f"  Generated : {timestamp}",
        f"  Interest  : {interest}",
        f"  Year(s)   : {year_label}",
        f"  Query     : {query}",
        "=" * 72,
        "",
    ]
    if terms:
        _add_keyword_table(lines, terms, nih_keyword_stats or {},
                           ct_keyword_stats or {}, pm_keyword_stats or {})

    # Section 1: NIH
    lines.append("-" * 72)
    lines.append("  SECTION 1: NIH FUNDING (RePORTER)")
    lines.append("-" * 72)
    nc = nih_analysis.get("count", 0)
    lines.append(f"  Projects found : {nc}")
    if nc > 0:
        lines.append(f"  Total funding  : ${nih_analysis.get('total_funding', 0):,.0f}")
        lines.append(f"  Avg funding    : ${nih_analysis.get('avg_funding', 0):,.0f}")
        lines.append(f"  Max award      : ${nih_analysis.get('max_funding', 0):,.0f}")
        lines.append("  Top organizations:")
        for name, cnt in nih_analysis.get("top_orgs", []):
            lines.append(f"    - {name} ({cnt} projects)")
        lines.append("  Top activity codes:")
        for code, cnt in nih_analysis.get("activity_codes", []):
            lines.append(f"    - {code} ({cnt} projects)")
    lines.append("")

    # Section 2: Clinical Trials
    lines.append("-" * 72)
    lines.append("  SECTION 2: CLINICAL TRIALS (ClinicalTrials.gov)")
    lines.append("-" * 72)
    tc = ct_analysis.get("count", 0)
    lines.append(f"  Studies found : {tc}")
    if tc > 0:
        lines.append("  By status:")
        for s, cnt in ct_analysis.get("statuses", []):
            lines.append(f"    - {s}: {cnt}")
        lines.append("  By phase:")
        for ph, cnt in ct_analysis.get("phases", []):
            lines.append(f"    - {ph}: {cnt}")
        lines.append("  Top sponsors:")
        for sp, cnt in ct_analysis.get("top_sponsors", []):
            lines.append(f"    - {sp} ({cnt} studies)")
        lines.append("  Top conditions:")
        for cond, cnt in ct_analysis.get("top_conditions", []):
            lines.append(f"    - {cond} ({cnt} studies)")
    lines.append("")

    # Section 3: PubMed
    lines.append("-" * 72)
    lines.append("  SECTION 3: PUBLICATIONS (PubMed)")
    lines.append("-" * 72)
    pc = pm_analysis.get("count", 0)
    lines.append(f"  Articles found : {pc}")
    if pc > 0:
        lines.append("  Top journals:")
        for j, cnt in pm_analysis.get("top_journals", []):
            lines.append(f"    - {j} ({cnt} articles)")
        lines.append(f"  Avg authors/article : {pm_analysis.get('avg_authors', 0)}")
    lines.append("")

    # Section 4: Citations
    if citations:
        lines.append("-" * 72)
        lines.append("  SECTION 4: CITED EVIDENCE")
        lines.append("-" * 72)
        for i, cite in enumerate(citations, 1):
            lines.append(f"  [{i}] {cite}")
        lines.append("")

    # Tableau instructions
    lines.append("-" * 72)
    lines.append("  TABLEAU VISUALIZATION")
    lines.append("-" * 72)
    lines.append("  Three CSVs exported alongside this report (one per source):")
    lines.append(f"    NIH:  {safe_name}_{year_label}_nih.csv")
    lines.append(f"    Trials: {safe_name}_{year_label}_trials.csv")
    lines.append(f"    Pubs: {safe_name}_{year_label}_pubmed.csv")
    lines.append("  Open in Tableau Desktop/Public:")
    lines.append("    1. Connect -> Text/CSV -> select a CSV")
    lines.append("    2. Drag fields onto sheets to build charts")
    lines.append("  Suggested visualizations:")
    lines.append("    - NIH:    Bar chart: funding by organization or activity code")
    lines.append("    - Trials: Pie chart of status; bar chart of phases")
    lines.append("    - Pubs:   Bar chart of articles by journal")
    lines.append("=" * 72)

    report_path = os.path.join(out_dir, f"{safe_name}_{year_label}_findings.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n  Report written: {report_path}")
    return report_path


def _write_csv(path, fieldnames, rows):
    """Write rows to a CSV file with headers."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  CSV exported: {path}")


def write_tableau_csv(out_dir, interest, year_label, query, terms,
                      projects, trials, articles):
    """Write three per-source Tableau-friendly CSVs with query keyword + per-row keyword tags."""
    safe_name = interest.replace(" ", "_").replace("/", "_")[:60]
    base_cols = ["Query", "Keywords", "Interest", "Year", "ID", "Title", "Link"]

    # --- NIH ---
    nih_rows = []
    for p in projects:
        kw = _matching_terms(terms,
                             p.get("project_title"),
                             p.get("abstract_text"),
                             p.get("phr_text"))
        org = p.get("organization") or {}
        pis = p.get("principalInvestigators") or []
        pi_names = "; ".join(pi.get("fullName", "") for pi in pis[:3])
        appl_id = p.get("appl_id") or ""
        nih_rows.append({
            "Query": query,
            "Keywords": "; ".join(kw) if kw else "(broad match)",
            "Interest": interest,
            "Year": year_label,
            "ID": str(appl_id),
            "Title": (p.get("project_title") or "")[:200],
            "Link": f"https://reporter.nih.gov/project-details/{appl_id}",
            "Funding": p.get("award_amount") or 0,
            "Organization": org.get("org_name") or "",
            "PI": pi_names,
            "ActivityCode": p.get("activity_code") or "",
        })
    nih_path = os.path.join(out_dir, f"{safe_name}_{year_label}_nih.csv")
    _write_csv(nih_path, base_cols + ["Funding", "Organization", "PI", "ActivityCode"], nih_rows)

    # --- Trials ---
    trial_rows = []
    for t in trials:
        proto = t.get("protocolSection") or {}
        ident = proto.get("identificationModule") or {}
        status = proto.get("statusModule") or {}
        start_date = status.get("startDate") or {}
        trial_year = (start_date.get("date", "")[:4]
                      if isinstance(start_date, dict) else str(start_date)[:4])
        nct_id = ident.get("nctId") or ""
        ds = proto.get("designModule") or {}
        cond_mod = proto.get("conditionsModule") or {}
        title = (ident.get("briefTitle") or ident.get("officialTitle") or "")
        conditions = "; ".join(cond_mod.get("conditions") or [])
        kw = _matching_terms(terms, title, conditions)
        trial_rows.append({
            "Query": query,
            "Keywords": "; ".join(kw) if kw else "(broad match)",
            "Interest": interest,
            "Year": trial_year or year_label,
            "ID": nct_id,
            "Title": title[:200],
            "Link": f"https://clinicaltrials.gov/study/{nct_id}",
            "Status": status.get("overallStatus") or "",
            "Phase": "; ".join(ds.get("phases") or []),
            "Conditions": conditions,
        })
    trial_path = os.path.join(out_dir, f"{safe_name}_{year_label}_trials.csv")
    _write_csv(trial_path, base_cols + ["Status", "Phase", "Conditions"], trial_rows)

    # --- PubMed ---
    pub_rows = []
    for a in articles:
        title = a.get("title") or ""
        kw = _matching_terms(terms, title)
        pmid = a.get("uid") or ""
        pub_rows.append({
            "Query": query,
            "Keywords": "; ".join(kw) if kw else "(broad match)",
            "Interest": interest,
            "Year": (a.get("pubdate") or "")[:4] or year_label,
            "ID": pmid,
            "Title": title[:200],
            "Link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "Journal": a.get("source") or "",
            "Authors": "; ".join(
                au.get("name", "") for au in (a.get("authors") or [])[:5]
            ),
            "DOI": next(
                (aid.get("value", "") for aid in (a.get("articleids") or [])
                 if aid.get("idtype") == "doi"),
                "",
            ),
        })
    pub_path = os.path.join(out_dir, f"{safe_name}_{year_label}_pubmed.csv")
    _write_csv(pub_path, base_cols + ["Journal", "Authors", "DOI"], pub_rows)

    return {"nih": nih_path, "trials": trial_path, "pubmed": pub_path}


# ---------------------------------------------------------------------------
# Main pipeline (public API for notebooks & CLI)
# ---------------------------------------------------------------------------
def run_pipeline(interest="genomics", year=2025, query=None,
                 out_dir=".", max_per_source=MAX_RESULTS_PER_SOURCE,
                 use_cache=True):
    """
    Execute the full research pipeline.

    Parameters
    ----------
    interest : str
        Label for the research area (used in filenames).
    year : int, str, or tuple/list of ints
        Single year (2025), range ("2022-2025"), or list ([2022,2023,2024]).
    query : str or None
        Search query string. If None and interest is a known key, the
        predefined query is used; otherwise ``interest`` is used as the query.
    out_dir : str
        Output directory for report + CSV.
    max_per_source : int
        Max records to fetch per data source.
    use_cache : bool
        If True (default), saves raw API responses as JSON files and reuses
        them on subsequent runs with the same interest/year/query.

    Returns
    -------
    dict with keys: report_path, csv_path, nih_count, trials_count, pubmed_count
    """
    known = {k.lower(): v for k, v in PREDEFINED_QUERIES.items()}
    if query is None:
        query = known.get(interest.lower(), interest)
    query = _improve_query(query)

    ylist, ylabel = _parse_years(year)

    print(f"\n{'='*60}")
    print(f"  RESEARCH AGENT PIPELINE")
    print(f"  Interest : {interest}")
    print(f"  Years    : {ylabel}")
    print(f"  Query    : {query}")
    print(f"{'='*60}")

    os.makedirs(out_dir, exist_ok=True)
    safe_name = interest.replace(" ", "_").replace("/", "_")[:60]

    # ---- Cached fetching ----
    def _fetch_or_load(source, fetch_fn):
        cache_path = os.path.join(out_dir, f".cache_{safe_name}_{ylabel}_{source}.json")
        if use_cache:
            cached = _load_json(cache_path)
            if cached is not None:
                print(f"  Using cached {source} data ({len(cached)} records)")
                return cached
        data = fetch_fn()
        if use_cache and data is not None:
            _dump_json(data, cache_path)
        return data or []

    projects = _fetch_or_load("nih", lambda: fetch_nih_projects(query, ylist, max_per_source))
    trials = _fetch_or_load("trials", lambda: fetch_clinical_trials(query, ylist, max_per_source))
    articles = _fetch_or_load("pubmed", lambda: fetch_pubmed(query, ylist, max_per_source))

    all_empty = not projects and not trials and not articles
    if all_empty:
        print("  [WARN] No results from any source. Try using fewer/simpler keywords.")
    print("\n  Analyzing data ...")
    nih_analysis = analyze_nih(projects)
    ct_analysis = analyze_trials(trials)
    pm_analysis = analyze_pubmed(articles)
    citations = generate_citations(articles)

    os.makedirs(out_dir, exist_ok=True)

    terms = _parse_query_terms(query)

    def _nih_text(p):
        return " ".join(filter(None, [
            p.get("project_title"), p.get("abstract_text"), p.get("phr_text"),
        ]))

    def _trial_text(t):
        proto = t.get("protocolSection") or {}
        ident = proto.get("identificationModule") or {}
        cond = proto.get("conditionsModule") or {}
        return "{} {}".format(
            ident.get("briefTitle") or ident.get("officialTitle") or "",
            " ".join(cond.get("conditions") or []),
        )

    nih_kw = _keyword_stats(terms, projects, _nih_text)
    ct_kw = _keyword_stats(terms, trials, _trial_text)
    pm_kw = _keyword_stats(terms, articles, lambda a: a.get("title") or "")
    pm_kw = _keyword_stats(terms, articles,
                           lambda a: a.get("title") or "")

    report_path = write_report(out_dir, interest, ylabel, query, terms,
                                nih_analysis, ct_analysis, pm_analysis,
                                citations,
                                nih_keyword_stats=nih_kw,
                                ct_keyword_stats=ct_kw,
                                pm_keyword_stats=pm_kw)
    csv_files = write_tableau_csv(out_dir, interest, ylabel, query, terms,
                                   projects, trials, articles)

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  NIH projects      : {len(projects)}")
    print(f"  Clinical trials   : {len(trials)}")
    print(f"  PubMed articles   : {len(articles)}")
    print(f"  Report            : {report_path}")
    print(f"  CSVs              :")
    print(f"    NIH:   {csv_files['nih']}")
    print(f"    Trials:{csv_files['trials']}")
    print(f"    Pubs:  {csv_files['pubmed']}")
    print(f"{'='*60}")

    return {
        "report_path": report_path,
        "csv_files": csv_files,
        "nih_count": len(projects),
        "trials_count": len(trials),
        "pubmed_count": len(articles),
    }


# ---------------------------------------------------------------------------
# Interactive mode  (notebook & terminal)
# ---------------------------------------------------------------------------
def interactive_mode():
    """Run the agent interactively: ask the user for their research question."""
    print()
    print("=" * 60)
    print("  NIH-CLINICALTRIALS-PUBMED RESEARCH AGENT")
    print("  Interactive mode")
    print("=" * 60)
    print()
    print("  I can search across three databases:")
    print("    1. NIH RePORTER  — funded research projects & grants")
    print("    2. ClinicalTrials.gov — clinical studies worldwide")
    print("    3. PubMed — 35M+ biomedical publications")
    print()

    # --- Ask for research interest ---
    print("  Pre-defined interest areas:")
    for i, k in enumerate(PREDEFINED_QUERIES, 1):
        print(f"    {i}. {k}")
    print("    Or type your own query.")
    print()
    raw = input("  What would you like to research? ").strip()
    if not raw:
        print("  Using default: genomics")
        raw = "genomics"

    # Check if it matches a number (predefined choice) or a keyword
    if raw.isdigit():
        idx = int(raw) - 1
        keys = list(PREDEFINED_QUERIES)
        if 0 <= idx < len(keys):
            interest_label = keys[idx]
            search_query = PREDEFINED_QUERIES[keys[idx]]
        else:
            interest_label = raw
            search_query = raw
    elif raw.lower() in [k.lower() for k in PREDEFINED_QUERIES]:
        # Match by name
        for k in PREDEFINED_QUERIES:
            if k.lower() == raw.lower():
                interest_label = k
                search_query = PREDEFINED_QUERIES[k]
                break
    else:
        interest_label = raw
        search_query = _improve_query(raw)
        if search_query != raw:
            print(f"  (converted to keywords: {search_query})")

    # --- Ask for year ---
    print()
    yr_raw = input(f"  Year(s) (e.g. 2025 or 2022-2025) [{datetime.now().year}]: ").strip()
    if not yr_raw:
        year = datetime.now().year
    else:
        year = yr_raw

    # --- Ask for record count ---
    print()
    max_raw = input(f"  Max records per source (1-500) [{MAX_RESULTS_PER_SOURCE}]: ").strip()
    try:
        max_per = min(int(max_raw), 500) if max_raw else MAX_RESULTS_PER_SOURCE
    except ValueError:
        max_per = MAX_RESULTS_PER_SOURCE

    print()
    run_pipeline(
        interest=interest_label,
        year=year,
        query=search_query,
        out_dir=".",
        max_per_source=max_per,
    )


# ---------------------------------------------------------------------------
# Entry points: CLI mode (with args) or interactive mode (no args)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    # Strip Jupyter/Colab kernel args (e.g. -f /path/to/kernel.json)
    _cleaned_argv = [sys.argv[0]]
    _skip = False
    for _arg in sys.argv[1:]:
        if _skip:
            _skip = False
            continue
        if _arg == "-f":
            _skip = True
            continue
        _cleaned_argv.append(_arg)
    sys.argv = _cleaned_argv

    has_user_args = len(sys.argv) > 1
    if not has_user_args:
        interactive_mode()
    else:
        parser = argparse.ArgumentParser(
            description="NIH + ClinicalTrials + PubMed Research Agent",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=(
                "Interactive mode (no args):\n"
                "  python research_agent.py\n"
                "CLI mode (with args):\n"
                "  python research_agent.py --interest genomics --year 2025\n"
                '  python research_agent.py --interest "cell therapy" --year 2024\n'
                '  python research_agent.py --interest custom --query "mRNA vaccine"\n'
            ),
        )
        parser.add_argument(
            "--interest", "-i",
            choices=list(PREDEFINED_QUERIES) + ["custom"],
            default="genomics",
            help="Pre-defined interest area or 'custom' for free-form query",
        )
        parser.add_argument("--query", "-q",
                            help="Custom search query (required when --interest=custom)")
        parser.add_argument("--year", "-y", type=str,
                            default=str(datetime.now().year),
                            help="Year or range (e.g. 2025 or 2022-2025)")
        parser.add_argument("--outdir", "-o", default=".",
                            help="Output directory for report + CSV")
        parser.add_argument("--max", "-m", type=int,
                            default=MAX_RESULTS_PER_SOURCE,
                            help="Max records per source")
        parser.add_argument("--no-cache", "--nocache",
                            action="store_false", dest="use_cache",
                            help="Skip cached API responses and re-fetch")

        args = parser.parse_args()

        if args.interest == "custom":
            if not args.query:
                print("ERROR: --query is required when --interest=custom",
                      file=sys.stderr)
                sys.exit(1)
            interest_label = args.query
            search_query = args.query
        else:
            interest_label = args.interest
            search_query = PREDEFINED_QUERIES[args.interest]

        run_pipeline(
            interest=interest_label,
            year=args.year,
            query=search_query,
            out_dir=args.outdir,
            max_per_source=args.max,
            use_cache=args.use_cache,
        )
