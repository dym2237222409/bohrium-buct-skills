#!/usr/bin/env python3
"""
build_and_lock_glossary.py — Terminology Consolidation, Conflict Resolution & Glossary Locking

Merges terminology sources in strict priority order:
  Priority 1: Case/User-provided custom glossary (--custom-glossary, JSON/CSV/TSV)
  Priority 2: Agent-curated paper-specific glossary (--agent-terms-json)
  Priority 3: Paper-defined abbreviations + Seed scientific glossary matches

Maps every locked term across all segments (Title, Abstract, Headings, Body, Captions,
Conclusion) so downstream translation and verification enforce 100% term consistency.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_custom_glossary(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    ext = path.suffix.lower()
    entries: List[Dict[str, str]] = []
    if ext == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if isinstance(data.get("entries"), list):
                for item in data["entries"]:
                    if isinstance(item, dict) and item.get("source_term") and item.get("target_term"):
                        entries.append({
                            "source_term": str(item["source_term"]).strip(),
                            "target_term": str(item["target_term"]).strip(),
                            "rationale": str(item.get("rationale", "Provided by custom glossary")),
                            "drift_aliases": item.get("drift_aliases", []),
                        })
            else:
                for k, v in data.items():
                    if isinstance(v, str):
                        entries.append({"source_term": k.strip(), "target_term": v.strip(), "rationale": "Provided by case glossary"})
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    s = item.get("source_term") or item.get("source") or item.get("en") or item.get("term")
                    t = item.get("target_term") or item.get("target") or item.get("zh") or item.get("translation")
                    if s and t:
                        entries.append({
                            "source_term": str(s).strip(),
                            "target_term": str(t).strip(),
                            "rationale": str(item.get("rationale", "Provided by custom glossary")),
                            "drift_aliases": item.get("drift_aliases", []),
                        })
    elif ext in {".csv", ".tsv"}:
        delim = "\t" if ext == ".tsv" else ","
        with path.open("r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f, delimiter=delim)
            for idx, row in enumerate(reader):
                if len(row) < 2:
                    continue
                if idx == 0 and row[0].strip().lower() in {"source", "source_term", "en", "term", "english"}:
                    continue
                entries.append({
                    "source_term": row[0].strip(),
                    "target_term": row[1].strip(),
                    "rationale": row[2].strip() if len(row) >= 3 else "Provided by tabular glossary",
                })
    return entries


def find_segment_occurrences(source_term: str, segments: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    # Match singular or regular plural ('s' / 'es' / 'ies' for -y)
    stem = source_term.strip()
    if stem.lower().endswith("y") and len(stem) > 3 and stem[-2].lower() not in "aeiou":
        pat = rf"\b(?:{re.escape(stem)}|{re.escape(stem[:-1])}ies)\b"
    else:
        pat = rf"\b{re.escape(stem)}(?:s|es)?\b"
    rx = re.compile(pat, re.I)

    seg_ids: List[str] = []
    sections: List[str] = []
    for s in segments:
        if not s.get("translatable", True):
            continue
        if rx.search(s.get("source_text", "")):
            seg_ids.append(s["seg_id"])
            sp = s.get("section_path", "")
            if sp and sp not in sections:
                sections.append(sp)
    return seg_ids, sections


def build_locked_glossary(
    segments_payload: Dict[str, Any],
    candidates_payload: Dict[str, Any],
    custom_paths: List[Path],
    agent_terms_path: Optional[Path] = None,
) -> Dict[str, Any]:
    segments = segments_payload.get("segments", [])
    merged_by_key: Dict[str, Dict[str, Any]] = {}
    conflicts_resolved: List[Dict[str, Any]] = []

    def upsert(item: Dict[str, Any], origin: str, priority: int) -> None:
        st = str(item["source_term"]).strip()
        tt = str(item["target_term"]).strip()
        if not st or not tt:
            return
        key = st.lower()
        if key in merged_by_key:
            existing = merged_by_key[key]
            if existing["target_term"] != tt:
                conflicts_resolved.append({
                    "source_term": st,
                    "kept_target": existing["target_term"] if existing["priority"] <= priority else tt,
                    "discarded_target": tt if existing["priority"] <= priority else existing["target_term"],
                    "reason": f"Priority {min(existing['priority'], priority)} source overrides priority {max(existing['priority'], priority)}.",
                })
            if priority < existing["priority"]:
                existing.update({"target_term": tt, "origin": origin, "priority": priority, "rationale": item.get("rationale", existing["rationale"])})
            return
        seg_ids, secs = find_segment_occurrences(st, segments)
        if not seg_ids and priority > 1:
            return
        merged_by_key[key] = {
            "source_term": st,
            "canonical_lower": key,
            "target_term": tt,
            "drift_aliases": item.get("drift_aliases", []),
            "origin": origin,
            "priority": priority,
            "locked": True,
            "occurrence_segments": seg_ids,
            "sections_present": secs,
            "rationale": item.get("rationale", ""),
        }

    # Priority 1: Custom / Case glossary
    for cp in custom_paths:
        for ent in load_custom_glossary(cp):
            upsert(ent, f"case_glossary:{cp.name}", 1)

    # Priority 2: Agent-curated domain terms
    polysemy_decisions: List[Dict[str, Any]] = []
    if agent_terms_path and agent_terms_path.is_file():
        adata = json.loads(agent_terms_path.read_text(encoding="utf-8"))
        if isinstance(adata, dict) and isinstance(adata.get("polysemy_decisions"), list):
            polysemy_decisions = adata["polysemy_decisions"]
        for ent in load_custom_glossary(agent_terms_path):
            upsert(ent, "agent_curated_paper_terms", 2)

    # Priority 3: Seed glossary matches found in paper
    for sh in candidates_payload.get("seed_glossary_matches", []):
        upsert(sh, "seed_scientific_glossary", 3)

    entries_list = []
    for idx, (_, val) in enumerate(sorted(merged_by_key.items(), key=lambda kv: (kv[1]["priority"], kv[0])), start=1):
        val_clean = {k: v for k, v in val.items() if k != "priority"}
        val_clean["term_id"] = f"T{idx:03d}"
        entries_list.append(val_clean)

    return {
        "glossary_version": "1.0",
        "source_file": segments_payload.get("source_file"),
        "locked_term_count": len(entries_list),
        "entries": entries_list,
        "paper_abbreviations": candidates_payload.get("paper_abbreviation_definitions", []),
        "unassigned_frequent_phrases": [
            fp for fp in candidates_payload.get("frequent_technical_phrases", [])
            if fp["phrase"].lower() not in merged_by_key
        ],
        "polysemy_alerts": candidates_payload.get("polysemy_and_false_friend_alerts", []),
        "polysemy_decisions": polysemy_decisions,
        "conflicts_resolved": conflicts_resolved,
    }


def render_glossary_markdown(glossary: Dict[str, Any]) -> str:
    lines = [
        "# Locked Scientific Terminology Table (强制锁定术语表)",
        "",
        "| Term ID | Source Term (EN) | Locked Target Term (ZH) | Sections Present | Segment Occurrences | Origin & Rationale |",
        "|---|---|---|---|---|---|",
    ]
    for e in glossary.get("entries", []):
        secs = ", ".join(e.get("sections_present", [])) or "-"
        segs = ", ".join(e.get("occurrence_segments", [])[:8])
        if len(e.get("occurrence_segments", [])) > 8:
            segs += f" (+{len(e['occurrence_segments']) - 8})"
        lines.append(
            f"| `{e['term_id']}` | `{e['source_term']}` | **{e['target_term']}** | {secs} | `{segs}` | {e.get('origin')}: {e.get('rationale', '')} |"
        )
    if glossary.get("paper_abbreviations"):
        lines.extend(["", "## Paper-Defined Abbreviations", ""])
        for ab in glossary["paper_abbreviations"]:
            lines.append(f"- **`{ab['abbreviation']}`**: {ab['full_term']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and lock scientific terminology glossary across segments.")
    parser.add_argument("--segments-json", required=True)
    parser.add_argument("--candidates-json", required=True)
    parser.add_argument("--custom-glossary", action="append", default=[])
    parser.add_argument("--agent-terms-json", default=None)
    parser.add_argument("--out-glossary", required=True)
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args()

    seg_data = json.loads(Path(args.segments_json).read_text(encoding="utf-8"))
    cand_data = json.loads(Path(args.candidates_json).read_text(encoding="utf-8"))
    custom_paths = [Path(p) for p in args.custom_glossary if p]
    agent_p = Path(args.agent_terms_json) if args.agent_terms_json else None

    locked = build_locked_glossary(seg_data, cand_data, custom_paths, agent_p)
    out_p = Path(args.out_glossary)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(locked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_p = Path(args.out_md) if args.out_md else out_p.with_name("glossary_table.md")
    md_p.write_text(render_glossary_markdown(locked), encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "locked_term_count": locked["locked_term_count"],
        "unassigned_frequent_phrases": len(locked["unassigned_frequent_phrases"]),
        "polysemy_alerts": len(locked["polysemy_alerts"]),
        "out_glossary": str(out_p),
        "out_md": str(md_p),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
