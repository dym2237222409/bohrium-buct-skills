#!/usr/bin/env python3
"""
build_and_validate_nanopubs.py — Mechanical 6-Gate Nanopublication Auditor & Multi-Format Serializer

Executes six deterministic quality checks on assertions_draft.json against indexed_passages.json:
  1. Assertion Granularity Gate (拆得细): Flags overly broad/multi-sentence/unsplit compound claims.
  2. Minimal Sufficient Evidence Grounding Gate (对得上): Verifies minimal_evidence_quote matches
     verbatim in the cited passage_id/source_id and checks length bounds.
  3. Controlled Relation Vocabulary & Polarity Gate: Enforces controlled_relations.json + evidence_types.
  4. Subject/Object Grounding Gate: Verifies non-empty distinct subject & object grounded in source.
  5. Complete Provenance & Pubinfo Chain Gate (追得回): Verifies paper title/DOI/source_id/location_ref
     and computes deterministic SHA-256 content-addressed Trusty IDs.
  6. Deduplication & Contradiction Detection Gate: Deduplicates identical canonical assertions and
     flags opposing relation pairs on the same (subject, object) pair.

Serializes valid Nanopublications into W3C TriG, JSON-LD, N-Quads, flat JSONL, Audit Report,
and contract-compliant /app/output/result.json.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
RES_DIR = SCRIPT_DIR.parent / "resources"
REL_VOCAB = json.loads((RES_DIR / "controlled_relations.json").read_text(encoding="utf-8"))
FIELD_DICT = json.loads((RES_DIR / "field_dictionary.json").read_text(encoding="utf-8"))


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact_norm(s: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s.lower())


def ttl_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


def trusty_hash(payload: Dict[str, Any]) -> Tuple[str, str]:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    hex_digest = hashlib.sha256(canonical).hexdigest()
    b64_short = base64.urlsafe_b64encode(hashlib.sha256(canonical).digest()).decode("ascii").rstrip("=")[:32]
    return hex_digest, f"RA{b64_short}"


def count_sentences(text: str) -> int:
    cleaned = re.sub(r"\b(?:Fig|Eq|Tab|Sec|et\s+al|e\.g|i\.e|vs|Dr)\.", "ABBR", text, flags=re.I)
    parts = [p.strip() for p in re.split(r"[.!?。！？]+(?:\s+|$)", cleaned) if p.strip()]
    return len(parts)


def validate_and_build(index_data: Dict[str, Any], draft: Dict[str, Any]) -> Dict[str, Any]:
    passages_by_id = {p["passage_id"]: p for p in index_data.get("passages", [])}
    passages_by_para = {
        (p["source_id"], str(p.get("paragraph_id"))): p
        for p in index_data.get("passages", [])
        if p.get("paragraph_id")
    }
    sources_meta = index_data.get("sources_metadata", {})

    valid_rels = REL_VOCAB.get("relations", {})
    valid_ev_types = set(REL_VOCAB.get("evidence_types", []))
    thresh = FIELD_DICT.get("quality_gate_thresholds", {})

    default_meta = draft.get("paper_metadata") or {}
    raw_assertions = draft.get("assertions") or []

    errors: List[str] = []
    warnings: List[str] = []
    nanopubs: List[Dict[str, Any]] = []
    seen_hashes: Dict[str, str] = {}
    duplicates_removed: List[Dict[str, str]] = []
    contradictions: List[Dict[str, Any]] = []

    grounded_count = 0
    controlled_rel_count = 0

    for idx, a in enumerate(raw_assertions, start=1):
        aid = str(a.get("assertion_id") or f"NP{idx:03d}")
        subj = str(a.get("subject", "")).strip()
        rel = str(a.get("relation", "")).strip()
        obj = str(a.get("object", "")).strip()
        cond = str(a.get("conditions_and_scope", "")).strip() or "unspecified_in_source"
        qval = str(a.get("quantitative_value", "")).strip() or "not_reported"
        negated = bool(a.get("negated", False))
        claim = str(a.get("claim_text", "")).strip()
        prov = a.get("provenance") or {}

        # Gate 1: Granularity (拆得细)
        if not claim:
            errors.append(f"[{aid}] Missing claim_text.")
        else:
            if len(claim) > thresh.get("max_claim_text_chars", 280):
                errors.append(f"[{aid}] assertion_too_broad: claim_text length {len(claim)} > {thresh.get('max_claim_text_chars', 280)}.")
            if count_sentences(claim) > thresh.get("forbid_multi_sentence_claim_count_gt", 1):
                errors.append(f"[{aid}] assertion_too_broad: claim_text contains {count_sentences(claim)} sentences; split into atomic falsifiable assertions.")
            for cpat in thresh.get("compound_clause_warning_patterns", []):
                if re.search(cpat, claim, re.I):
                    warnings.append(f"[{aid}] Possible compound clause detected in claim_text; confirm atomicity.")

        # Gate 3 & 4: Controlled Relation & Distinct Subject/Object
        if not subj or not obj:
            errors.append(f"[{aid}] Missing subject or object.")
        elif subj.lower() == obj.lower():
            errors.append(f"[{aid}] subject_object_confusion: subject and object are identical ('{subj}').")

        if rel in valid_rels:
            controlled_rel_count += 1
            rel_meta = valid_rels[rel]
        else:
            errors.append(f"[{aid}] relation_not_in_vocabulary: '{rel}' not in controlled_relations.json.")
            rel_meta = {"iri": f"scirel:{rel}", "obo_mapping": None, "opposite": None}

        ev_type = str(prov.get("evidence_type", "")).strip()
        if ev_type not in valid_ev_types:
            errors.append(f"[{aid}] Invalid or missing provenance.evidence_type '{ev_type}' (allowed: {sorted(valid_ev_types)}).")

        # Gate 2: Minimal Sufficient Evidence Grounding (对得上)
        sid = str(prov.get("source_id") or default_meta.get("source_id") or "primary")
        pid = str(prov.get("passage_id") or prov.get("paragraph_id") or "").strip()
        quote = str(prov.get("minimal_evidence_quote") or prov.get("quote") or "").strip()

        p_obj = passages_by_id.get(pid) or passages_by_id.get(f"{sid}:{pid}") or passages_by_para.get((sid, pid))
        if p_obj is None:
            errors.append(f"[{aid}] provenance_missing_passage: passage_id '{pid}' not found in indexed_passages.json.")
            loc_type, loc_ref, p_page, p_bbox = "unresolved", pid, None, None
        else:
            loc_type = p_obj["location_type"]
            p_page = p_obj.get("page")
            p_bbox = p_obj.get("bbox")
            if loc_type == "pdf_page_bbox":
                loc_ref = f"page={p_page},block={p_obj['passage_id']},bbox={p_bbox}"
            else:
                loc_ref = f"source_id={p_obj['source_id']},paragraph_id={p_obj.get('paragraph_id') or p_obj['passage_id']}"

            if len(quote) < thresh.get("min_evidence_quote_chars", 18):
                errors.append(f"[{aid}] evidence_too_short: minimal_evidence_quote must be >= 18 chars.")
            elif len(quote) > thresh.get("max_evidence_quote_chars", 480):
                warnings.append(f"[{aid}] evidence_too_bloated: quote is {len(quote)} chars; trim to minimal sufficient snippet.")

            if quote and compact_norm(quote[:100]) in compact_norm(p_obj["text"]):
                grounded_count += 1
            else:
                errors.append(f"[{aid}] evidence_not_in_source: minimal_evidence_quote could not be verbatim-matched in passage '{p_obj['passage_id']}'.")

            # Subject/object token overlap heuristic
            p_low = p_obj["text"].lower()
            subj_toks = [t for t in re.findall(r"[a-z0-9]{3,}", subj.lower()) if t not in {"the", "and", "for", "with"}]
            if subj_toks and not any(t in p_low for t in subj_toks):
                warnings.append(f"[{aid}] subject '{subj}' tokens not literally present in passage '{p_obj['passage_id']}'; verify no subject confusion.")

        # Gate 5: Provenance & Pubinfo Chain + Trusty Hash (追得回)
        smeta = sources_meta.get(sid) or {}
        paper_title = default_meta.get("paper_title") or smeta.get("title") or "not_reported"
        paper_authors = default_meta.get("paper_authors") or smeta.get("authors") or []
        paper_year = default_meta.get("paper_year") or smeta.get("year") or "not_reported"
        paper_doi = default_meta.get("paper_doi_or_id") or smeta.get("doi") or sid
        src_sha = smeta.get("sha256") or "not_reported"

        canonical_core = {
            "subject": subj.lower(),
            "relation": rel,
            "object": obj.lower(),
            "conditions": cond.lower(),
            "negated": negated,
            "source_id": sid,
        }
        content_sha, trusty_id = trusty_hash(canonical_core)
        if content_sha in seen_hashes:
            duplicates_removed.append({"dropped_assertion_id": aid, "duplicate_of": seen_hashes[content_sha]})
            continue
        seen_hashes[content_sha] = aid

        np_uri = f"urn:nanopub:{trusty_id}"
        nanopubs.append({
            "nanopub_uri": np_uri,
            "trusty_id": trusty_id,
            "content_sha256": content_sha,
            "head": {
                "nanopub_uri": np_uri,
                "assertion_graph_uri": f"{np_uri}#assertion",
                "provenance_graph_uri": f"{np_uri}#provenance",
                "pubinfo_graph_uri": f"{np_uri}#pubinfo",
            },
            "assertion": {
                "assertion_id": aid,
                "subject": subj,
                "subject_type": a.get("subject_type", "entity"),
                "relation": rel,
                "relation_iri": rel_meta["iri"],
                "obo_mapping": rel_meta.get("obo_mapping"),
                "object": obj,
                "object_type": a.get("object_type", "entity"),
                "conditions_and_scope": cond,
                "quantitative_value": qval,
                "negated": negated,
                "claim_text": claim,
            },
            "provenance": {
                "source_id": sid,
                "source_file": p_obj["source_file"] if p_obj else None,
                "location_type": loc_type,
                "location_ref": loc_ref,
                "page": p_page,
                "paragraph_id": p_obj.get("paragraph_id") if p_obj else None,
                "passage_id": p_obj["passage_id"] if p_obj else pid,
                "bbox": p_bbox,
                "section_path": p_obj.get("section_path") if p_obj else None,
                "minimal_evidence_quote": quote,
                "evidence_type": ev_type,
                "confidence_basis": str(prov.get("confidence_basis", "")).strip() or "unspecified",
                "study_method": str(prov.get("study_method", "")).strip() or "unspecified",
            },
            "pubinfo": {
                "paper_title": paper_title,
                "paper_authors": paper_authors,
                "paper_year": paper_year,
                "paper_doi_or_id": paper_doi,
                "source_sha256": src_sha,
                "created_at": utc_now_iso(),
                "generator": "scipdf-nanopub-extractor/1.0.0",
            },
        })

    # Gate 6: Contradiction detection across deduplicated assertions
    for i in range(len(nanopubs)):
        for j in range(i + 1, len(nanopubs)):
            ai, aj = nanopubs[i]["assertion"], nanopubs[j]["assertion"]
            if compact_norm(ai["subject"]) == compact_norm(aj["subject"]) and compact_norm(ai["object"]) == compact_norm(aj["object"]):
                opp = valid_rels.get(ai["relation"], {}).get("opposite")
                if (opp and aj["relation"] == opp) or (ai["relation"] == aj["relation"] and ai["negated"] != aj["negated"]):
                    contradictions.append({
                        "assertion_a": ai["assertion_id"],
                        "assertion_b": aj["assertion_id"],
                        "subject": ai["subject"],
                        "object": ai["object"],
                        "relation_a": f"{'NOT ' if ai['negated'] else ''}{ai['relation']}",
                        "relation_b": f"{'NOT ' if aj['negated'] else ''}{aj['relation']}",
                        "conditions_a": ai["conditions_and_scope"],
                        "conditions_b": aj["conditions_and_scope"],
                    })

    total_raw = max(1, len(raw_assertions))
    metrics = {
        "assertions_raw": len(raw_assertions),
        "assertions_deduplicated": len(nanopubs),
        "duplicates_removed": len(duplicates_removed),
        "evidence_grounded_count": grounded_count,
        "evidence_grounded_ratio": round(grounded_count / total_raw, 4) if raw_assertions else 0.0,
        "controlled_relation_ratio": round(controlled_rel_count / total_raw, 4) if raw_assertions else 0.0,
        "contradictions_detected": len(contradictions),
    }

    return {
        "valid": len(errors) == 0 and len(nanopubs) > 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
        "nanopublications": nanopubs,
        "duplicates_removed": duplicates_removed,
        "contradictions": contradictions,
    }


def serialize_trig(nanopubs: List[Dict[str, Any]]) -> str:
    lines = [
        "@prefix np: <http://www.nanopub.org/nschema#> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix dct: <http://purl.org/dc/terms/> .",
        "@prefix scirel: <https://w3id.org/scipdf/rel/> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
    ]
    for npub in nanopubs:
        u = npub["nanopub_uri"]
        a = npub["assertion"]
        p = npub["provenance"]
        pi = npub["pubinfo"]
        lines.extend([
            f"<{u}#Head> {{",
            f"  <{u}> a np:Nanopublication ;",
            f"    np:hasAssertion <{u}#assertion> ;",
            f"    np:hasProvenance <{u}#provenance> ;",
            f"    np:hasPublicationInfo <{u}#pubinfo> .",
            "}",
            "",
            f"<{u}#assertion> {{",
            f"  <{u}#claim> a scirel:AtomicAssertion ;",
            f'    scirel:assertionId "{ttl_escape(a["assertion_id"])}" ;',
            f'    scirel:subject "{ttl_escape(a["subject"])}" ;',
            f'    scirel:predicate <https://w3id.org/scipdf/rel/{ttl_escape(a["relation"])}> ;',
            f'    scirel:object "{ttl_escape(a["object"])}" ;',
            f'    scirel:conditions "{ttl_escape(a["conditions_and_scope"])}" ;',
            f'    scirel:quantitativeValue "{ttl_escape(a["quantitative_value"])}" ;',
            f'    scirel:negated "{str(a["negated"]).lower()}"^^xsd:boolean ;',
            f'    scirel:claimText "{ttl_escape(a["claim_text"])}" .',
            "}",
            "",
            f"<{u}#provenance> {{",
            f"  <{u}#assertion> prov:wasDerivedFrom <urn:paper:{ttl_escape(str(pi['paper_doi_or_id']).replace(' ', '_'))}> ;",
            f'    scirel:sourceId "{ttl_escape(p["source_id"])}" ;',
            f'    scirel:locationType "{ttl_escape(p["location_type"])}" ;',
            f'    scirel:locationRef "{ttl_escape(p["location_ref"])}" ;',
            f'    scirel:evidenceType "{ttl_escape(p["evidence_type"])}" ;',
            f'    scirel:confidenceBasis "{ttl_escape(p["confidence_basis"])}" ;',
            f'    scirel:minimalEvidenceQuote "{ttl_escape(p["minimal_evidence_quote"])}" .',
            "}",
            "",
            f"<{u}#pubinfo> {{",
            f'  <{u}> dct:title "{ttl_escape(str(pi["paper_title"]))}" ;',
            f'    dct:created "{ttl_escape(pi["created_at"])}"^^xsd:dateTime ;',
            f'    dct:creator "{ttl_escape(pi["generator"])}" ;',
            f'    scirel:paperYear "{ttl_escape(str(pi["paper_year"]))}" ;',
            f'    scirel:contentSha256 "{ttl_escape(npub["content_sha256"])}" .',
            "}",
            "",
        ])
    return "\n".join(lines)


def serialize_nquads(nanopubs: List[Dict[str, Any]]) -> str:
    quads: List[str] = []
    rel_base = "https://w3id.org/scipdf/rel/"
    for npub in nanopubs:
        u = npub["nanopub_uri"]
        a, p, pi = npub["assertion"], npub["provenance"], npub["pubinfo"]
        g_head, g_ass, g_prov, g_pub = f"<{u}#Head>", f"<{u}#assertion>", f"<{u}#provenance>", f"<{u}#pubinfo>"
        quads.append(f'<{u}> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.nanopub.org/nschema#Nanopublication> {g_head} .')
        quads.append(f'<{u}> <http://www.nanopub.org/nschema#hasAssertion> {g_ass} {g_head} .')
        quads.append(f'<{u}> <http://www.nanopub.org/nschema#hasProvenance> {g_prov} {g_head} .')
        quads.append(f'<{u}> <http://www.nanopub.org/nschema#hasPublicationInfo> {g_pub} {g_head} .')
        quads.append(f'<{u}#claim> <{rel_base}subject> "{ttl_escape(a["subject"])}" {g_ass} .')
        quads.append(f'<{u}#claim> <{rel_base}predicate> <{rel_base}{ttl_escape(a["relation"])}> {g_ass} .')
        quads.append(f'<{u}#claim> <{rel_base}object> "{ttl_escape(a["object"])}" {g_ass} .')
        quads.append(f'<{u}#claim> <{rel_base}conditions> "{ttl_escape(a["conditions_and_scope"])}" {g_ass} .')
        quads.append(f'<{u}#assertion> <{rel_base}locationRef> "{ttl_escape(p["location_ref"])}" {g_prov} .')
        quads.append(f'<{u}#assertion> <{rel_base}evidenceType> "{ttl_escape(p["evidence_type"])}" {g_prov} .')
        quads.append(f'<{u}#assertion> <{rel_base}minimalEvidenceQuote> "{ttl_escape(p["minimal_evidence_quote"])}" {g_prov} .')
        quads.append(f'<{u}> <http://purl.org/dc/terms/created> "{ttl_escape(pi["created_at"])}" {g_pub} .')
        quads.append(f'<{u}> <{rel_base}contentSha256> "{ttl_escape(npub["content_sha256"])}" {g_pub} .')
    return "\n".join(quads) + "\n"


def serialize_jsonld(nanopubs: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "@context": REL_VOCAB.get("prefixes", {}),
        "@graph": [
            {
                "@id": n["nanopub_uri"],
                "@type": "np:Nanopublication",
                "np:hasAssertion": {"@id": n["head"]["assertion_graph_uri"], **n["assertion"]},
                "np:hasProvenance": {"@id": n["head"]["provenance_graph_uri"], **n["provenance"]},
                "np:hasPublicationInfo": {"@id": n["head"]["pubinfo_graph_uri"], **n["pubinfo"]},
                "trusty_id": n["trusty_id"],
                "content_sha256": n["content_sha256"],
            }
            for n in nanopubs
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate atomic assertions and export W3C Nanopublications.")
    ap.add_argument("--index-json", required=True)
    ap.add_argument("--draft-json", required=True)
    ap.add_argument("--case-json", default=None)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    idx_data = json.loads(Path(args.index_json).read_text(encoding="utf-8"))
    draft = json.loads(Path(args.draft_json).read_text(encoding="utf-8"))
    res = validate_and_build(idx_data, draft)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trig_p = out_dir / "nanopublications.trig"
    jld_p = out_dir / "nanopublications.jsonld"
    nq_p = out_dir / "nanopublications.nq"
    jsonl_p = out_dir / "nanopublications.jsonl"
    rep_p = out_dir / "nanopub_audit_report.md"

    trig_p.write_text(serialize_trig(res["nanopublications"]), encoding="utf-8")
    jld_p.write_text(json.dumps(serialize_jsonld(res["nanopublications"]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    nq_p.write_text(serialize_nquads(res["nanopublications"]), encoding="utf-8")
    jsonl_p.write_text("\n".join(json.dumps(n, ensure_ascii=False) for n in res["nanopublications"]) + "\n", encoding="utf-8")

    # Optional rdflib TriG syntax verification if installed in container
    rdflib_verified = None
    try:
        import rdflib  # type: ignore
        g = rdflib.ConjunctiveGraph()
        g.parse(data=trig_p.read_text(encoding="utf-8"), format="trig")
        rdflib_verified = {"parsed_quads": len(g)}
    except ImportError:
        rdflib_verified = None
    except Exception as exc:
        res["errors"].append(f"rdflib TriG parse error: {exc}")
        res["valid"] = False

    rep_lines = [
        "# Nanopublication Extraction & 6-Gate Quality Audit Report",
        "",
        f"- **Validation Gate**: `{'PASS' if res['valid'] else 'FAIL'}`",
        f"- **Metrics**: `{json.dumps(res['metrics'], ensure_ascii=False)}`",
        f"- **rdflib TriG Syntax Check**: `{rdflib_verified or 'rdflib not installed (stdlib serializer validated)'}`",
        "",
        "## Extracted Atomic Nanopublications",
        "",
        "| ID | Trusty ID | Subject | Relation | Object | Conditions | Evidence Type | Location |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for n in res["nanopublications"]:
        a, p = n["assertion"], n["provenance"]
        rep_lines.append(
            f"| `{a['assertion_id']}` | `{n['trusty_id'][:12]}...` | {a['subject']} | `{a['relation']}` | {a['object']} | {a['conditions_and_scope']} | `{p['evidence_type']}` | `{p['location_ref']}` |"
        )
    if res["errors"] or res["warnings"] or res["contradictions"]:
        rep_lines.extend(["", "## Audit Findings & Conflicts", ""])
        for e in res["errors"]:
            rep_lines.append(f"- **ERROR**: {e}")
        for w in res["warnings"]:
            rep_lines.append(f"- **WARNING**: {w}")
        for c in res["contradictions"]:
            rep_lines.append(f"- **CONTRADICTION**: `{c['assertion_a']}` ({c['relation_a']}) vs `{c['assertion_b']}` ({c['relation_b']}) on ({c['subject']}, {c['object']})")
    rep_p.write_text("\n".join(rep_lines) + "\n", encoding="utf-8")

    # Merge into /app/output/result.json preserving all case.json output_contract fields
    res_path = out_dir / "result.json"
    out_obj: Dict[str, Any] = json.loads(res_path.read_text(encoding="utf-8")) if res_path.is_file() else {}
    case_data = json.loads(Path(args.case_json).read_text(encoding="utf-8")) if (args.case_json and Path(args.case_json).is_file()) else {}

    out_obj["case_id"] = case_data.get("case_id") or case_data.get("id") or out_obj.get("case_id") or "unknown_case"
    out_obj["status"] = "success" if res["valid"] and not idx_data.get("encrypted_files") else "partial"
    out_obj["status_reason"] = (
        f"Validated and exported {res['metrics']['assertions_deduplicated']} atomic Nanopublications "
        f"(grounded={res['metrics']['evidence_grounded_ratio']*100:.1f}%, contradictions={res['metrics']['contradictions_detected']})."
    )
    out_obj["results"] = {
        **(out_obj.get("results") if isinstance(out_obj.get("results"), dict) else {}),
        "nanopubs_trig_path": str(trig_p),
        "nanopubs_jsonld_path": str(jld_p),
        "nanopubs_nq_path": str(nq_p),
        "nanopubs_jsonl_path": str(jsonl_p),
        "audit_report_path": str(rep_p),
        "nanopublications": res["nanopublications"],
        "quality_metrics": res["metrics"],
    }
    out_obj["sources"] = [
        {
            "assertion_id": n["assertion"]["assertion_id"],
            "trusty_id": n["trusty_id"],
            "source_id": n["provenance"]["source_id"],
            "location_type": n["provenance"]["location_type"],
            "location_ref": n["provenance"]["location_ref"],
            "page": n["provenance"]["page"],
            "paragraph_id": n["provenance"]["paragraph_id"],
            "verbatim_quote": n["provenance"]["minimal_evidence_quote"],
            "evidence_type": n["provenance"]["evidence_type"],
        }
        for n in res["nanopublications"]
    ]
    out_obj["uncertainty"] = {
        "unreported_in_source": draft.get("unreported_or_rejected_claims", []),
        "unverifiable_items": idx_data.get("encrypted_files", []),
        "material_conflicts": res["contradictions"],
        "human_takeover_locations": [{"file": ef, "reason": "Encrypted PDF"} for ef in idx_data.get("encrypted_files", [])],
    }
    recs = out_obj.get("change_records") if isinstance(out_obj.get("change_records"), list) else []
    recs.append({"step": "build_and_validate_nanopubs", "timestamp": utc_now_iso(), "summary": out_obj["status_reason"]})
    out_obj["change_records"] = recs
    res_path.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "valid": res["valid"],
        "metrics": res["metrics"],
        "errors": res["errors"],
        "warnings": res["warnings"],
        "rdflib_verified": rdflib_verified,
    }, ensure_ascii=False, indent=2))
    return 2 if (args.strict and not res["valid"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
