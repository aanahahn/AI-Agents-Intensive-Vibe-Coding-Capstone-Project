"""
Compare NIH funding vs publication volume across genomics technologies.
Identifies technologies with increasing funding but relatively low publication output.

Skips clinical trials (not needed for this analysis) to keep runtime manageable.
"""
import pandas as pd
from research_agent import (
    fetch_nih_projects, fetch_pubmed,
    analyze_nih, analyze_pubmed,
    _rate_limit
)

technologies = {
    "long-read sequencing": "long-read sequencing OR nanopore OR PacBio",
    "single-cell genomics": "single-cell genomics OR single-cell RNA-seq OR scRNA-seq",
    "spatial transcriptomics": "spatial transcriptomics OR spatial genomics OR MERFISH",
    "CRISPR screens": "CRISPR screen OR CRISPR knockout OR CRISPRa OR CRISPRi",
    "epigenomics": "epigenomics OR ATAC-seq OR ChIP-seq OR DNA methylation",
    "liquid biopsy": "liquid biopsy OR circulating tumor DNA OR ctDNA",
    "multi-omics": "multi-omics OR integrated genomics OR proteogenomics",
    "AI genomics": "machine learning genomics OR deep learning genomics OR AI genomics",
}

years = [2022, 2023, 2024, 2025]

rows = []
for tech, query in technologies.items():
    for yr in years:
        projects = fetch_nih_projects(query, yr, max_records=100)
        articles = fetch_pubmed(query, yr, max_records=100)
        nih = analyze_nih(projects)
        pm = analyze_pubmed(articles)
        nc = nih.get("count", 0)
        nf = nih.get("total_funding", 0)
        pc = pm.get("count", 0)
        rows.append({
            "technology": tech,
            "year": yr,
            "nih_count": nc,
            "nih_funding": nf,
            "nih_avg_funding": nih.get("avg_funding", 0),
            "pub_count": pc,
        })
        print(f"  {tech:25s} ({yr}): NIH={nc:>4d}  funding=${nf:>10,.0f}  PubMed={pc:>4d}")

print("\n" + "=" * 130)
print(f"{'Technology':30s} {'Metric':18s} {'2022':>12s} {'2023':>12s} {'2024':>12s} {'2025':>12s} {'Trend':>8s}")
print("=" * 130)

df = pd.DataFrame(rows)

for tech in technologies:
    t = df[df.technology == tech].sort_values("year").reset_index(drop=True)
    if len(t) < 4:
        continue
    fvals = t.nih_funding.values
    pvals = t.pub_count.values
    ft = "UP" if fvals[-1] > fvals[0] else ("down" if fvals[-1] < fvals[0] else "flat")
    pt = "UP" if pvals[-1] > pvals[0] else ("down" if pvals[-1] < pvals[0] else "flat")
    print(f"{tech:30s} {'NIH funding ($)':18s} {fvals[0]:>12,.0f} {fvals[1]:>12,.0f} {fvals[2]:>12,.0f} {fvals[3]:>12,.0f} {ft:>8s}")
    print(f"{tech:30s} {'PubMed articles':18s} {pvals[0]:>12d} {pvals[1]:>12d} {pvals[2]:>12d} {pvals[3]:>12d} {pt:>8s}")
    print()

print()
print("=" * 95)
print("  ANALYSIS: Increasing NIH funding + relatively low publication volume")
print("=" * 95)

candidates = []
for tech in technologies:
    t = df[df.technology == tech]
    avg_fund = t.nih_funding.mean()
    avg_pub = t.pub_count.mean()
    f0 = t[t.year == t.year.min()].nih_funding.values[0]
    f1 = t[t.year == t.year.max()].nih_funding.values[0]
    fund_up = f1 > f0
    if fund_up and avg_fund > 0:
        pubs_per_m = avg_pub / (avg_fund / 1_000_000)
        candidates.append((tech, avg_fund, avg_pub, pubs_per_m, f0, f1))

candidates.sort(key=lambda x: x[3])

print(f"{'Technology':30s} {'Avg Fund':>14s} {'Fund 2022':>14s} {'Fund 2025':>14s} {'Avg Pubs':>10s} {'Pubs/$1M':>10s}")
print("-" * 95)
for tech, af, ap, ppm, f0, f1 in candidates:
    print(f"{tech:30s} ${af:>10,.0f} ${f0:>10,.0f} ${f1:>10,.0f} {ap:>8.0f} {ppm:>8.1f}")

print("\n--- Top candidates (lowest pubs per $1M NIH funding, upward funding trend) ---\n")
for i, (tech, af, ap, ppm, f0, f1) in enumerate(candidates[:4], 1):
    fund_change = ((f1 - f0) / f0 * 100) if f0 else 0
    print(f"{i}. {tech}")
    print(f"   NIH funding: ${f0:,.0f} (2022) -> ${f1:,.0f} (2025)  ({fund_change:+.0f}%)")
    print(f"   Avg annual articles: {ap:.0f}")
    print(f"   Publications per $1M funding: {ppm:.1f}")
    print()

print("=" * 95)
print("  INTERPRETATION")
print("=" * 95)
print("""
Technologies with few publications relative to their NIH funding -- especially
when funding is growing -- represent potential \"under-published\" or emerging
areas. Low pubs/$1M can mean: (1) the field is capital-intensive (large
consortia, equipment), (2) lag between funding and publication, or (3) it is
truly underexplored relative to investment. These are candidates for
researchers looking for high-impact, less crowded niches.
""")
