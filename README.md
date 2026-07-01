# AI Research Agent — NIH Funding, Clinical Trials & PubMed

An AI-powered research agent that queries **three major biomedical databases** — NIH RePORTER, ClinicalTrials.gov, and PubMed — then produces a structured findings report (.txt) and a Tableau-ready dataset (.csv).

Built for the **Kaggle x Google AI Agents: Intensive Vibe Coding Capstone Project**.

---

## Features

- **Multi-source search** — Queries NIH funding data, clinical trial records, and PubMed publications in a single run
- **No API keys required** — All three APIs are free and open (NIH, ClinicalTrials.gov, PubMed E-utilities)
- **Flexible input** — Single year (2025), year range ("2022-2025"), or custom list ([2022, 2023, 2024])
- **CLI mode** — Pass arguments for batch/scripting use
- **Interactive mode** — Run with no arguments for a guided prompt
- **Python API** — Import as a module: `from research_agent import run_pipeline`
- **Kaggle / Colab compatible** — Auto-installs missing dependencies, strips Jupyter kernel args
- **Rate-limit safe** — 1-second delay between API calls to respect terms of use
- **Tableau-ready output** — CSV exports with consistent schema across all three sources
- **Cited evidence** — PubMed results include formatted citations with PMID and DOI

---

## Output

Every run produces exactly **two files**:

| File | Format | Contents |
|---|---|---|
| `{interest}_{year}_findings.txt` | Plain text | 4 sections: NIH funding analysis, clinical trial status/phases, publication journals/authors, cited evidence |
| `{interest}_{year}_tableau_data.csv` | CSV | One row per record (project / study / article) across all 3 sources, with consistent columns: Source, Interest, Year, ID, Title, Funding, Organization, PI, ActivityCode, Link |

---

## Quick Start

### 1. Install dependencies

```bash
pip install requests pandas
```

The agent auto-installs these if missing (no extra step needed in Kaggle/Colab).

### 2. Run the agent

#### Interactive mode (guided prompts)

```bash
python research_agent.py
```

You will be asked to:
- Select or type a research interest
- Enter a year or year range (e.g. `2025` or `2022-2025`)
- Choose the maximum number of records per source

#### CLI mode (scriptable)

```bash
# Pre-defined interest areas: genomics, cell therapy, personalized medicine
python research_agent.py --interest genomics --year 2025

# Year range
python research_agent.py --interest "cell therapy" --year 2022-2025

# Custom query (use --interest custom)
python research_agent.py --interest custom --query "mRNA vaccine" --year 2024

# Increase records per source
python research_agent.py --interest genomics --year 2025 --max 200

# Specify output directory
python research_agent.py --interest genomics --year 2025 --outdir ./reports
```

#### Python API (notebooks / scripts)

```python
from research_agent import run_pipeline

# Single year
result = run_pipeline(interest="genomics", year=2025, max_per_source=50)

# Year range
result = run_pipeline(interest="cell therapy", year="2022-2025")

# Custom query
result = run_pipeline(
    interest="custom",
    year=2025,
    query="CRISPR AND gene editing",
    max_per_source=100,
)

print(result["report_path"])  # path to .txt findings
print(result["csv_path"])     # path to .csv Tableau data
```

---

## Usage Examples

### Example 1: What are the latest funded projects in genomics?

```bash
python research_agent.py --interest genomics --year 2025 --max 20
```

Outputs: `genomics_2025_findings.txt` + `genomics_2025_tableau_data.csv`

### Example 2: What cell therapies entered trials from 2022–2025?

```bash
python research_agent.py --interest "cell therapy" --year 2022-2025 --max 50
```

Outputs: `cell_therapy_2022-2025_findings.txt` + `cell_therapy_2022-2025_tableau_data.csv`

### Example 3: Cross-technology trend analysis

The companion script `analyze_trends.py` compares NIH funding vs. publication volume across 8 genomics technologies over 4 years, identifying which areas have rising investment but low publication output:

```bash
python analyze_trends.py
```

This script uses `research_agent` as a Python module to loop over technologies and years, computing aggregate stats and ranking by publications per $1M funding.

---

## Environment Support

| Environment | How to run |
|---|---|
| **Local CLI** | `python research_agent.py ...` |
| **Kaggle Notebook** | Copy `research_agent.py` into the notebook's working directory, then import or `%run` |
| **Google Colab** | Upload `research_agent.py` to the Colab runtime, then import or `%run` |
| **Jupyter Notebook** | Same as Colab — auto-strips kernel args |
| **Any Python 3.8+** | Compatible — only needs `requests` and `pandas` |

---

## How It Works

```
User Input (CLI / Interactive / Python API)
        │
        ▼
┌─────────────────┐
│   NIH RePORTER   │  POST /v2/projects/search  (fiscal year, keyword, orgs)
│   API v2         │  → Returns funded research projects with award amounts
└────────┬────────┘
         │
┌─────────────────┐
│ ClinicalTrials   │  GET /api/v2/studies  (condition, status, phase)
│   .gov API v2    │  → Returns clinical study records with phase/status
└────────┬────────┘
         │
┌─────────────────┐
│   PubMed         │  E-utilities: esearch + esummary (chunked at 100 IDs)
│   E-utilities    │  → Returns publication metadata with authors/DOI
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Analysis       │  Aggregates: total funding, top orgs, status/phase
│   Engine         │  distributions, top journals, avg authors, citations
└────────┬────────┘
         │
         ▼
┌──────────────────────────┐
│  Output                 │
│  ├─ {interest}_findings.txt   (narrative report)
│  └─ {interest}_tableau_data.csv  (structured data for Tableau)
└──────────────────────────┘
```

### Data Sources

| Source | API | Endpoint | Rate Limit | Auth |
|---|---|---|---|---|
| NIH RePORTER | v2 | `POST https://api.reporter.nih.gov/v2/projects/search` | ~1 req/s | None |
| ClinicalTrials.gov | v2 | `GET https://clinicaltrials.gov/api/v2/studies` | ~50 req/min | None |
| PubMed (E-utilities) | esearch/esummary | `GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi` | ~3 req/s | None |
| | | `GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi` | | |

---

## File Reference

| File | Description |
|---|---|
| `research_agent.py` | Main agent — fetches, analyzes, and reports from all 3 data sources |
| `analyze_trends.py` | Example cross-technology analysis script using `research_agent` as a module |
| `requirements.txt` | Runtime dependencies (`requests`, `pandas`) |

---

## Notes

- All APIs are public and **do not require authentication**.
- PubMed IDs are fetched in chunks of 100 to avoid HTTP 414 (URI too long) errors.
- The NIH `include_active_projects` flag includes currently active grants that may span multiple fiscal years. For year-specific snapshots, set `include_active_projects=False` in the code.
- Pre-defined interest areas (`genomics`, `cell therapy`, `personalized medicine`) include expanded Boolean queries for better recall. Use `--interest custom --query "your terms"` for full control.
