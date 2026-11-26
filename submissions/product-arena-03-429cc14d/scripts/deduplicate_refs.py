#!/usr/bin/env python3
"""
deduplicate_refs.py — Explainable 4-tier bibliography deduplication,
field enrichment, anti-false-merge guardrail, and conflict auditor
for scipdf-citation-manager. Zero external dependencies (Python 3.9+ stdlib).
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RULES = SCRIPT_DIR.parent / "resources" / "dedup_rules.json"


def norm_title(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_similarity(t1: str, t2: str) -> float:
    n1, n2 = norm_title(t1), norm_title(t2)
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0
    return round(difflib.SequenceMatcher(None, n1, n2).ratio(), 4)


def extract_surnames(authors: List[str]) -> Set[str]:
    surnames: Set[str] = set()
    for a in authors:
        a_s = a.strip()
        if not a_s:
            continue
        if re.search(r"[\u4e00-\u9fff]", a_s):
            surnames.add(a_s.lower())
        elif "," in a_s:
            surnames.add(a_s.split(",", 1)[0].strip().lower())
        else:
            parts = a_s.split()
            if parts:
                surnames.add(parts[-1].strip().lower())
    return surnames


def author_overlap(a1: List[str], a2: List[str]) -> float:
    s1, s2 = extract_surnames(a1), extract_surnames(a2)
    if not s1 or not s2:
        return 0.0
    inter = len(s1 & s2)
    denom = min(len(s1), len(s2))
    return round(inter / denom, 4) if denom else 0.0


def first_author_surname(authors: List[str]) -> str:
    if not authors:
        return ""
    a = authors[0].strip()
    if re.search(r"[\u4e00-\u9fff]", a):
        return a.lower()
    if "," in a:
        return a.split(",", 1)[0].strip().lower()
    parts = a.split()
    return parts[-1].strip().lower() if parts else ""


def is_preprint_record(rec: Dict[str, Any], indicators: List[str]) -> bool:
    if rec.get("entry_type") == "preprint":
        return True
    hay = f"{rec.get('venue', '')} {rec.get('doi', '')} {rec.get('url', '')} {rec.get('arxiv_id', '')}".lower()
    return any(ind in hay for ind in indicators) or bool(rec.get("arxiv_id") and not rec.get("doi"))


def is_preprint_doi(doi: str) -> bool:
    d = doi.lower()
    return d.startswith("10.48550/arxiv") or d.startswith("10.1101/") or d.startswith("10.26434/chemrxiv")


def version_rank(rec: Dict[str, Any], pref_order: List[str], indicators: List[str]) -> Tuple[int, int, int]:
    etype = "preprint" if is_preprint_record(rec, indicators) else rec.get("entry_type", "misc")
    try:
        rank = pref_order.index(etype)
    except ValueError:
        rank = len(pref_order)
    has_non_preprint_doi = 0 if (rec.get("doi") and not is_preprint_doi(rec["doi"])) else 1
    filled_count = sum(1 for k in ("authors", "title", "year", "venue", "volume", "issue", "pages", "doi", "abstract") if rec.get(k))
    return (rank, has_non_preprint_doi, -filled_count)


def evaluate_pair(
    r1: Dict[str, Any],
    r2: Dict[str, Any],
    rules: Dict[str, Any],
) -> Tuple[str, str, Dict[str, Any]]:
    th = rules.get("thresholds", {})
    indicators = rules.get("preprint_venue_indicators", [])

    d1, d2 = r1.get("doi", ""), r2.get("doi", "")
    a1_id, a2_id = r1.get("arxiv_id", ""), r2.get("arxiv_id", "")
    t_sim = max(
        title_similarity(r1.get("title", ""), r2.get("title", "")),
        title_similarity(r1.get("title_en", ""), r2.get("title_en", "")),
        title_similarity(r1.get("title_zh", ""), r2.get("title_zh", "")),
    )
    a_ov = author_overlap(r1.get("authors", []), r2.get("authors", []))
    y1 = int(r1["year"]) if str(r1.get("year", "")).isdigit() else None
    y2 = int(r2["year"]) if str(r2.get("year", "")).isdigit() else None
    y_diff = abs(y1 - y2) if (y1 is not None and y2 is not None) else 0

    diag = {"title_similarity": t_sim, "author_overlap": a_ov, "year_diff": y_diff}

    # Tier 1: Exact Persistent ID
    if d1 and d2 and d1 == d2:
        return "MERGE", "Tier1_ExactDOI", diag
    if a1_id and a2_id and a1_id == a2_id:
        return "MERGE", "Tier1_ExactArXivID", diag

    # Anti-False-Merge Guard 1: Two distinct valid non-preprint DOIs
    if d1 and d2 and d1 != d2 and not is_preprint_doi(d1) and not is_preprint_doi(d2):
        return "KEEP_SEPARATE", "Guard_DistinctValidDOIs", diag

    # Tier 2: Preprint <-> Published Version
    p1 = is_preprint_record(r1, indicators)
    p2 = is_preprint_record(r2, indicators)
    if (p1 != p2) and t_sim >= float(th.get("preprint_published_title_similarity", 0.90)) and a_ov >= float(th.get("author_overlap_min", 0.50)) and y_diff <= int(th.get("preprint_max_year_diff", 2)):
        return "MERGE", "Tier2_PreprintPublishedMatch", diag

    # Tier 3: Cross-DB / Same-Work High Title Match
    if t_sim >= float(th.get("cross_db_title_similarity", 0.92)) and a_ov >= float(th.get("author_overlap_min", 0.50)) and y_diff <= int(th.get("cross_db_max_year_diff", 1)):
        return "MERGE", "Tier3_CrossDBTitleAuthorMatch", diag

    # Anti-False-Merge Guard 2: Same first author + same year, distinct title
    fa1, fa2 = first_author_surname(r1.get("authors", [])), first_author_surname(r2.get("authors", []))
    if fa1 and fa2 and fa1 == fa2 and y1 is not None and y2 is not None and y1 == y2 and t_sim < float(th.get("same_author_same_year_distinct_title_ceiling", 0.75)):
        return "KEEP_SEPARATE", "Guard_SameAuthorSameYearDistinctWork", diag

    return "NO_MATCH", "DistinctRecords", diag


def merge_cluster(
    cluster_idx: int,
    members: List[Dict[str, Any]],
    reasons: List[Dict[str, Any]],
    rules: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    pref_order = rules.get("version_preference_order", [])
    indicators = rules.get("preprint_venue_indicators", [])
    ordered = sorted(members, key=lambda r: version_rank(r, pref_order, indicators))
    primary = dict(ordered[0])
    ref_id = f"REF{cluster_idx:03d}"

    field_provenance: Dict[str, str] = {}
    field_conflicts: List[Dict[str, Any]] = []
    merged_versions: List[Dict[str, Any]] = []

    enrich_fields = ("title_zh", "title_en", "volume", "issue", "pages", "doi", "arxiv_id", "url", "abstract", "venue", "year")
    for f in enrich_fields:
        if primary.get(f):
            field_provenance[f] = primary["raw_id"]

    for other in ordered[1:]:
        merged_versions.append({
            "raw_id": other["raw_id"],
            "cite_key": other["cite_key"],
            "source_file": other["source_file"],
            "entry_type": other["entry_type"],
            "year": other.get("year", ""),
            "doi": other.get("doi", ""),
            "arxiv_id": other.get("arxiv_id", ""),
        })
        for f in enrich_fields:
            pval = str(primary.get(f) or "").strip()
            oval = str(other.get(f) or "").strip()
            if not oval:
                continue
            if not pval:
                primary[f] = oval
                field_provenance[f] = other["raw_id"]
            elif f in ("year", "pages", "volume") and pval != oval:
                # Expected year difference for preprint vs published is informational, not a fatal conflict
                is_preprint_pair = is_preprint_record(other, indicators) and not is_preprint_record(primary, indicators)
                if not (is_preprint_pair and f == "year"):
                    field_conflicts.append({
                        "ref_id": ref_id,
                        "field": f,
                        "canonical_value": pval,
                        "canonical_source": primary["raw_id"],
                        "conflicting_value": oval,
                        "conflicting_source": other["raw_id"],
                    })

    if not primary.get("authors"):
        for other in ordered[1:]:
            if other.get("authors"):
                primary["authors"] = other["authors"]
                field_provenance["authors"] = other["raw_id"]
                break

    missing = [fld for fld in ("authors", "title", "year", "venue") if not primary.get(fld)]
    canonical = {
        "ref_id": ref_id,
        "canonical_raw_id": primary["raw_id"],
        "cite_keys": [m["cite_key"] for m in ordered],
        "merged_raw_ids": [m["raw_id"] for m in ordered],
        "source_files": sorted(set(m["source_file"] for m in ordered)),
        "entry_type": primary["entry_type"],
        "language": primary["language"],
        "authors": primary["authors"],
        "title": primary["title"],
        "title_zh": primary.get("title_zh", ""),
        "title_en": primary.get("title_en", ""),
        "year": primary.get("year", ""),
        "venue": primary.get("venue", ""),
        "volume": primary.get("volume", ""),
        "issue": primary.get("issue", ""),
        "pages": primary.get("pages", ""),
        "doi": primary.get("doi", ""),
        "arxiv_id": primary.get("arxiv_id", ""),
        "url": primary.get("url", ""),
        "abstract": primary.get("abstract", ""),
        "missing_fields": missing,
        "field_provenance": field_provenance,
        "merged_versions": merged_versions,
        "merge_rationale": reasons,
    }
    return canonical, field_conflicts


def main() -> int:
    parser = argparse.ArgumentParser(description="Explainable multi-tier bibliography deduplication.")
    parser.add_argument("--parsed", type=Path, required=True, help="Path to parsed_case.json")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES, help="Path to dedup_rules.json")
    parser.add_argument("--out", type=Path, required=True, help="Output deduped_refs.json")
    args = parser.parse_args()

    parsed = json.loads(args.parsed.read_text(encoding="utf-8-sig"))
    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    raw_refs: List[Dict[str, Any]] = parsed.get("raw_references", [])

    n = len(raw_refs)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    merge_edges: List[Dict[str, Any]] = []
    kept_separate: List[Dict[str, Any]] = []

    for i in range(n):
        for j in range(i + 1, n):
            decision, rationale, diag = evaluate_pair(raw_refs[i], raw_refs[j], rules)
            if decision == "MERGE":
                union(i, j)
                merge_edges.append({
                    "raw_id_a": raw_refs[i]["raw_id"],
                    "raw_id_b": raw_refs[j]["raw_id"],
                    "rationale": rationale,
                    **diag,
                })
            elif decision == "KEEP_SEPARATE":
                kept_separate.append({
                    "raw_id_a": raw_refs[i]["raw_id"],
                    "raw_id_b": raw_refs[j]["raw_id"],
                    "title_a": raw_refs[i].get("title", ""),
                    "title_b": raw_refs[j].get("title", ""),
                    "guard_reason": rationale,
                    **diag,
                })

    clusters: Dict[int, List[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    deduped_refs: List[Dict[str, Any]] = []
    all_conflicts: List[Dict[str, Any]] = []
    raw_to_ref: Dict[str, str] = {}
    key_to_ref: Dict[str, str] = {}

    for c_idx, (_, idxs) in enumerate(sorted(clusters.items()), start=1):
        members = [raw_refs[i] for i in idxs]
        member_ids = {m["raw_id"] for m in members}
        c_reasons = [e for e in merge_edges if e["raw_id_a"] in member_ids and e["raw_id_b"] in member_ids]
        canonical, conflicts = merge_cluster(c_idx, members, c_reasons, rules)
        deduped_refs.append(canonical)
        all_conflicts.extend(conflicts)
        for m in members:
            raw_to_ref[m["raw_id"]] = canonical["ref_id"]
            if m.get("cite_key"):
                key_to_ref[m["cite_key"]] = canonical["ref_id"]

    merged_groups = [r for r in deduped_refs if len(r["merged_raw_ids"]) > 1]
    output = {
        "case_id": parsed.get("case_id", "local-citation-case"),
        "raw_count": n,
        "deduplicated_count": len(deduped_refs),
        "merged_duplicate_groups": len(merged_groups),
        "kept_separate_audit": kept_separate,
        "field_conflicts": all_conflicts,
        "raw_id_to_ref_id": raw_to_ref,
        "cite_key_to_ref_id": key_to_ref,
        "references": deduped_refs,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "raw_count": n,
        "deduplicated_count": len(deduped_refs),
        "merged_duplicate_groups": len(merged_groups),
        "kept_separate_guarded": len(kept_separate),
        "field_conflicts": len(all_conflicts),
        "out": str(args.out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
