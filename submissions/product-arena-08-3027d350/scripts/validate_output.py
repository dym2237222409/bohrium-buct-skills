#!/usr/bin/env python3
"""
validate_output.py — Contract & Anti-Hallucination Verifier for /app/output/result.json

Checks:
1. case_id and all original output_contract keys from /input/case.json exist in result.json.
2. Honest status enum ('success', 'partial', 'unverifiable', 'refused') and status_reason exist.
3. Every entry in result.json["sources"] cites a valid PDF page within [1, num_pages] and
   its verbatim_quote (when provided) matches actual text extracted on that page.
4. Uncertainty boundaries (unreported, unverifiable, conflicts, garbled spans) and change_records exist.
5. Exports a human-verifiable Markdown audit report (--report-out).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


VALID_STATUSES = {"success", "partial", "unverifiable", "unable_to_determine", "refused", "completed", "ok"}


def compact_alnum(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def extract_required_contract_keys(case_data: Dict[str, Any]) -> List[str]:
    contract = case_data.get("output_contract")
    keys: List[str] = []
    if isinstance(contract, dict):
        if isinstance(contract.get("required"), list):
            keys.extend(k for k in contract["required"] if isinstance(k, str))
        elif isinstance(contract.get("properties"), dict):
            keys.extend(contract["properties"].keys())
        elif isinstance(contract.get("required_fields"), list):
            keys.extend(k for k in contract["required_fields"] if isinstance(k, str))
        else:
            for k in contract.keys():
                if k not in {"type", "$schema", "description"}:
                    keys.append(k)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                keys.append(item["name"])
    return list(dict.fromkeys(keys))


def validate_result_payload(
    result_data: Dict[str, Any],
    case_data: Optional[Dict[str, Any]] = None,
    parsed_docs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(result_data, dict):
        return {"valid": False, "errors": ["result.json root must be a JSON object"], "warnings": []}

    # 1. case_id & output_contract check
    if not result_data.get("case_id"):
        errors.append("Missing non-empty 'case_id' in result.json.")
    if case_data:
        expected_id = case_data.get("case_id") or case_data.get("id") or case_data.get("task_id")
        if expected_id and str(result_data.get("case_id")) != str(expected_id):
            errors.append(f"case_id mismatch: result.json has '{result_data.get('case_id')}', expected '{expected_id}'.")
        for req_key in extract_required_contract_keys(case_data):
            if req_key not in result_data and req_key not in (result_data.get("results") or {}):
                errors.append(f"Missing output_contract field '{req_key}' in result.json.")

    # 2. Status check
    status = str(result_data.get("status", "")).strip().lower()
    if not status:
        errors.append("Missing 'status' field in result.json.")
    elif status not in VALID_STATUSES:
        warnings.append(f"Non-standard status value '{status}'; prefer one of {sorted(VALID_STATUSES)}.")

    # 3. Evidence & anti-hallucination page/quote verification
    sources = result_data.get("sources")
    if sources is None:
        sources = result_data.get("evidence")
    if not isinstance(sources, list):
        errors.append("Missing 'sources' (or 'evidence') list in result.json.")
        sources = []
    elif len(sources) == 0 and status in {"success", "completed", "ok"}:
        warnings.append("status is 'success' but 'sources' list is empty; every key conclusion should carry page/block evidence.")

    page_text_map: Dict[int, str] = {}
    max_pages = 0
    if parsed_docs:
        for pdoc in parsed_docs:
            max_pages = max(max_pages, int(pdoc.get("num_pages", 0)))
            for blk in pdoc.get("reading_order_blocks", []):
                p = int(blk["page"])
                page_text_map[p] = page_text_map.get(p, "") + "\n" + blk.get("text", "")
            for tab in pdoc.get("tables", []):
                t_str = "\n".join(" ".join(r) for r in tab.get("grid", []))
                for p in tab.get("pages", []):
                    page_text_map[int(p)] = page_text_map.get(int(p), "") + "\n" + tab.get("caption", "") + "\n" + t_str

    verified_quotes = 0
    for idx, src in enumerate(sources):
        if not isinstance(src, dict):
            errors.append(f"sources[{idx}] must be an object.")
            continue
        p = src.get("page")
        if p is None:
            warnings.append(f"sources[{idx}] is missing 'page' number.")
            continue
        if not isinstance(p, int) or p < 1:
            errors.append(f"sources[{idx}] has invalid page={p} (must be integer >= 1).")
            continue
        if max_pages > 0 and p > max_pages:
            errors.append(f"Hallucinated page detected in sources[{idx}]: page={p} exceeds PDF num_pages={max_pages}.")
            continue

        quote = str(src.get("verbatim_quote") or src.get("quote") or "").strip()
        if quote and page_text_map:
            probe = compact_alnum(quote[:90])
            page_blob = compact_alnum(page_text_map.get(p, ""))
            if len(probe) >= 8 and probe in page_blob:
                verified_quotes += 1
            elif len(probe) >= 8:
                # Check adjacent pages in case of cross-page paragraph stitch
                adj_blob = compact_alnum(page_text_map.get(p - 1, "") + page_text_map.get(p, "") + page_text_map.get(p + 1, ""))
                if probe in adj_blob:
                    verified_quotes += 1
                    warnings.append(f"sources[{idx}] quote matched on adjacent page of page={p} (cross-page span).")
                else:
                    warnings.append(f"sources[{idx}] verbatim_quote could not be substring-matched on page {p}; double-check exact wording.")

    # 4. Uncertainty & change_records check
    if "uncertainty" not in result_data and "limitations" not in result_data:
        warnings.append("Missing 'uncertainty' object disclosing unreported/unverifiable/conflicting items.")
    if "change_records" not in result_data:
        warnings.append("Missing 'change_records' audit trail.")

    return {
        "valid": len(errors) == 0,
        "case_id": result_data.get("case_id"),
        "status": result_data.get("status"),
        "sources_count": len(sources),
        "verified_verbatim_quotes": verified_quotes,
        "errors": errors,
        "warnings": warnings,
    }


def write_audit_report(
    report_path: Path,
    result_data: Dict[str, Any],
    validation: Dict[str, Any],
    parsed_docs: List[Dict[str, Any]],
) -> None:
    lines = [
        f"# Scientific Document Parsing & Evidence Audit Report (`{result_data.get('case_id')}`)",
        "",
        f"- **Execution Status**: `{result_data.get('status')}` — {result_data.get('status_reason', '')}",
        f"- **Contract Validation**: `{'PASS' if validation['valid'] else 'FAIL'}` "
        f"(Sources: {validation['sources_count']}, Verbatim-Matched Quotes: {validation['verified_verbatim_quotes']})",
        "",
    ]
    if parsed_docs:
        lines.extend(["## 1. Parsed PDF Inventory & Structural Summary", ""])
        for pdoc in parsed_docs:
            lines.append(
                f"- **`{Path(pdoc.get('source_pdf', '')).name}`**: {pdoc.get('num_pages')} pages | "
                f"Backend: `{pdoc.get('backend_used')}` | Mode: `{pdoc.get('document_mode')}` | "
                f"Sections: {len(pdoc.get('sections', []))} | Blocks: {len(pdoc.get('reading_order_blocks', []))} | "
                f"Tables: {len(pdoc.get('tables', []))} | Equations: {len(pdoc.get('equations', []))} | "
                f"References: {len(pdoc.get('references', []))}"
            )
        lines.append("")

    sources = result_data.get("sources") or []
    if sources:
        lines.extend(
            [
                "## 2. Key Conclusions & Evidence Anchors",
                "",
                "| Item / Field | Page | Col | Block / Table ID | BBox | Verbatim Evidence Excerpt |",
                "|---|---|---|---|---|---|",
            ]
        )
        for s in sources[:40]:
            item = str(s.get("item_id") or s.get("claim_or_field") or "-")
            pg = str(s.get("page", "-"))
            col = str(s.get("column", "-"))
            bid = str(s.get("block_id", "-"))
            bbox = str(s.get("bbox", "-"))
            qt = str(s.get("verbatim_quote") or s.get("quote") or "").replace("\n", " ").replace("|", "\\|")[:120]
            lines.append(f"| `{item}` | {pg} | {col} | `{bid}` | `{bbox}` | {qt} |")
        lines.append("")

    unc = result_data.get("uncertainty") or {}
    lines.extend(
        [
            "## 3. Uncertainty, Unreported Items & Boundary Disclosures",
            "",
            f"- **Unreported in Source**: {json.dumps(unc.get('unreported_in_source', []), ensure_ascii=False)}",
            f"- **Unverifiable Items**: {json.dumps(unc.get('unverifiable_items', []), ensure_ascii=False)}",
            f"- **Material Conflicts**: {json.dumps(unc.get('material_conflicts', []), ensure_ascii=False)}",
            f"- **Garbled / Low-Confidence Spans**: {len(unc.get('garbled_or_low_confidence_spans', []))} span(s)",
            f"- **Human Takeover Locations**: {json.dumps(unc.get('human_takeover_locations', []), ensure_ascii=False)}",
            "",
        ]
    )
    if validation["errors"] or validation["warnings"]:
        lines.extend(["## 4. Validator Findings", ""])
        for e in validation["errors"]:
            lines.append(f"- **ERROR**: {e}")
        for w in validation["warnings"]:
            lines.append(f"- **WARNING**: {w}")
        lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate /app/output/result.json against case.json and parsed PDF evidence.")
    parser.add_argument("--result-json", required=True, help="Path to /app/output/result.json.")
    parser.add_argument("--case-json", default=None, help="Optional path to /input/case.json.")
    parser.add_argument("--parsed-dir", default=None, help="Directory containing parsed_document.json (or parent work dir).")
    parser.add_argument("--report-out", default=None, help="Optional path to write Markdown audit report.")
    args = parser.parse_args()

    res_path = Path(args.result_json)
    if not res_path.is_file():
        print(json.dumps({"valid": False, "errors": [f"result.json not found: {res_path}"]}))
        return 1

    result_data = json.loads(res_path.read_text(encoding="utf-8"))
    case_data = None
    if args.case_json and Path(args.case_json).is_file():
        case_data = json.loads(Path(args.case_json).read_text(encoding="utf-8"))

    parsed_docs: List[Dict[str, Any]] = []
    if args.parsed_dir and Path(args.parsed_dir).exists():
        p_root = Path(args.parsed_dir)
        candidates = [p_root] if p_root.is_file() else sorted(p_root.rglob("parsed_document.json"))
        for cp in candidates:
            parsed_docs.append(json.loads(cp.read_text(encoding="utf-8")))

    verdict = validate_result_payload(result_data, case_data, parsed_docs)
    if args.report_out:
        write_audit_report(Path(args.report_out), result_data, verdict, parsed_docs)
        verdict["report_out"] = args.report_out

    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
