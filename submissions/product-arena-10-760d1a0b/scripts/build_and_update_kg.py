#!/usr/bin/env python3
"""
build_and_update_kg.py — Evidence-Grounded KG Builder, Anti-False-Merge Aligner & Incremental Updater

Features:
1. Grounding Gate (抽得准 / 查得到): Verifies every node and edge cites valid (source_id,
   paragraph_id) and verbatim_quote present in corpus_index.json (never fabricates PDF pages!).
2. Anti-False-Merge Alignment & Incremental Update (对得齐 / 增量更新):
   - Supports --merge-into existing_kg.json, preserving all existing node IDs (E0001..) and
     edge IDs (R0001..) when adding Paper 2!
   - Merges ONLY shared non-instance concepts (is_paper_specific_instance=false) with matching
     canonical_name/aliases; strictly separates paper-specific physical samples, model instances,
     cohorts, and numeric metrics (is_paper_specific_instance=true) and logs both merged and
     non-merged decisions in entity_alignment_log!
   - Verifies true cross-paper evidence requires >= 2 distinct source_id AND distinct content hashes.
3. Conflict Detection (连得对): Automatically flags opposing directed relations (OUTPERFORMS vs
   UNDERPERFORMS, SUPPORTS vs CONTRADICTS, INCREASES vs DECREASES) on shared entities/concepts.
4. Multi-Format Export + Real Visual Preview:
   - knowledge_graph.json, knowledge_graph.jsonld, knowledge_graph.graphml,
     knowledge_graph.cypher, knowledge_graph.mmd, kg_preview.svg (+ kg_preview.png if
     matplotlib is installed), and kg_audit_report.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA = json.loads((SCRIPT_DIR.parent / "resources" / "kg_schema.json").read_text(encoding="utf-8"))


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s.lower())


def quote_matches(quote: str, passage_text: str) -> bool:
    if not quote:
        return False
    p_norm = norm_key(passage_text)
    if not p_norm:
        return False
    segs = [s.strip() for s in re.split(r"\.{3,}|…|\[\.\.\.\]", quote) if len(norm_key(s)) >= 10]
    if segs:
        return all(norm_key(s)[:90] in p_norm for s in segs)
    return norm_key(quote[:90]) in p_norm


def build_or_update(
    corpus_index: Dict[str, Any],
    draft: Dict[str, Any],
    existing_kg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    passages_by_uid = {p["uid"]: p for p in corpus_index.get("passages", [])}
    passages_by_pair = {(p["source_id"], str(p.get("paragraph_id"))): p for p in corpus_index.get("passages", [])}
    corpus_sources = corpus_index.get("sources", {})

    valid_etypes = set(SCHEMA.get("entity_types", []))
    valid_rtypes = SCHEMA.get("relation_types", {})
    valid_attrs = set(SCHEMA.get("attribution_types", []))
    valid_strengths = set(SCHEMA.get("evidence_strength_levels", []))

    # Load existing state for ID-preserving incremental updates
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[str, Dict[str, Any]] = {}
    alignment_log: List[Dict[str, Any]] = []
    if existing_kg:
        for n in existing_kg.get("nodes", []):
            nodes[n["node_id"]] = json.loads(json.dumps(n))
        for e in existing_kg.get("edges", []):
            edges[e["edge_id"]] = json.loads(json.dumps(e))
        alignment_log = list(existing_kg.get("entity_alignment_log", []))

    next_node_num = max([int(nid[1:]) for nid in nodes if nid.startswith("E") and nid[1:].isdigit()] + [0]) + 1
    next_edge_num = max([int(eid[1:]) for eid in edges if eid.startswith("R") and eid[1:].isdigit()] + [0]) + 1

    preserved_node_ids = sorted(nodes.keys())
    inc_log: Dict[str, Any] = {
        "mode": "incremental_update" if existing_kg else "initial_build",
        "timestamp": utc_now_iso(),
        "nodes_preserved_count": len(preserved_node_ids),
        "nodes_added": [],
        "nodes_merged_into_existing": [],
        "nodes_kept_separate": [],
        "edges_added": [],
        "conflicts_detected": [],
    }

    errors: List[str] = []
    warnings: List[str] = []

    def verify_provenance_list(provs: List[Dict[str, Any]], label: str) -> List[Dict[str, Any]]:
        verified: List[Dict[str, Any]] = []
        if not provs:
            errors.append(f"[{label}] Missing provenance list; every entity/relation must bind to source evidence.")
            return verified
        for pi, pr in enumerate(provs):
            sid = str(pr.get("source_id", "")).strip()
            pid = str(pr.get("paragraph_id") or pr.get("passage_id") or "").strip()
            quote = str(pr.get("verbatim_quote") or pr.get("quote") or "").strip()
            attr = str(pr.get("attribution", "author_direct")).strip()
            if attr not in valid_attrs:
                warnings.append(f"[{label}] provenance[{pi}] attribution '{attr}' not in {sorted(valid_attrs)}.")
            p_obj = passages_by_uid.get(f"{sid}:{pid}") or passages_by_uid.get(pid) or passages_by_pair.get((sid, pid))
            if p_obj is None:
                errors.append(f"[{label}] provenance[{pi}] unresolved (source_id='{sid}', paragraph_id='{pid}') in corpus_index.json.")
                continue
            if not quote_matches(quote, p_obj["text"]):
                errors.append(f"[{label}] provenance[{pi}] verbatim_quote not found in passage '{p_obj['uid']}'.")
                continue
            verified.append({
                "source_id": p_obj["source_id"],
                "source_content_sha256": (corpus_sources.get(p_obj["source_id"]) or {}).get("content_sha256"),
                "paragraph_id": p_obj["paragraph_id"],
                "location_type": p_obj["location_type"],
                "page": p_obj.get("page"),  # Stays None for JATS corpora!
                "section": p_obj.get("section"),
                "verbatim_quote": quote,
                "attribution": attr,
            })
        return verified

    # Build lookup index of non-instance conceptual nodes eligible for safe merge
    concept_key_to_nid: Dict[str, str] = {}
    for nid, n in nodes.items():
        if not n.get("is_paper_specific_instance", False):
            for nm in [n["canonical_name"], *n.get("aliases", [])]:
                k = norm_key(nm)
                if k:
                    concept_key_to_nid[k] = nid

    temp_to_final_nid: Dict[str, str] = {}

    for ent in draft.get("entities", []):
        tid = str(ent.get("temp_id") or ent.get("canonical_name"))
        cname = str(ent.get("canonical_name", "")).strip()
        etype = str(ent.get("entity_type", "")).strip()
        aliases = [str(a).strip() for a in ent.get("aliases", []) if str(a).strip()]
        is_inst = bool(ent.get("is_paper_specific_instance", etype in {"Material_or_Sample", "Dataset_or_Cohort", "Claim_or_Conclusion"}))

        if not cname:
            errors.append(f"Entity '{tid}' has empty canonical_name.")
            continue
        if etype not in valid_etypes:
            errors.append(f"Entity '{cname}' has invalid entity_type '{etype}' (allowed: {sorted(valid_etypes)}).")
        v_provs = verify_provenance_list(ent.get("provenance", []), f"entity:{cname}")

        # Safe concept alignment vs strict physical-instance separation
        matched_nid: Optional[str] = None
        if not is_inst:
            for nm in [cname, *aliases]:
                k = norm_key(nm)
                if k in concept_key_to_nid:
                    matched_nid = concept_key_to_nid[k]
                    break

        if matched_nid is not None:
            ex = nodes[matched_nid]
            for al in [cname, *aliases]:
                if al != ex["canonical_name"] and al not in ex["aliases"]:
                    ex["aliases"].append(al)
            ex["provenance"].extend(v_provs)
            ex["source_ids"] = sorted(set(ex["source_ids"] + [p["source_id"] for p in v_provs]))
            ex["distinct_content_hashes"] = sorted(set(
                ex.get("distinct_content_hashes", []) + [p["source_content_sha256"] for p in v_provs if p.get("source_content_sha256")]
            ))
            ex["is_true_cross_paper"] = len(ex["source_ids"]) >= 2 and len(ex["distinct_content_hashes"]) >= 2
            temp_to_final_nid[tid] = matched_nid
            inc_log["nodes_merged_into_existing"].append({"incoming_name": cname, "merged_into": matched_nid, "canonical_name": ex["canonical_name"]})
        else:
            nid = f"E{next_node_num:04d}"
            next_node_num += 1
            sids = sorted({p["source_id"] for p in v_provs})
            hashes = sorted({p["source_content_sha256"] for p in v_provs if p.get("source_content_sha256")})
            nodes[nid] = {
                "node_id": nid,
                "canonical_name": cname,
                "entity_type": etype,
                "aliases": aliases,
                "is_paper_specific_instance": is_inst,
                "conditions_summary": str(ent.get("conditions_summary", "")).strip(),
                "source_ids": sids,
                "distinct_content_hashes": hashes,
                "is_true_cross_paper": len(sids) >= 2 and len(hashes) >= 2,
                "provenance": v_provs,
            }
            temp_to_final_nid[tid] = nid
            inc_log["nodes_added"].append({"node_id": nid, "canonical_name": cname, "entity_type": etype, "is_paper_specific_instance": is_inst})
            if not is_inst:
                for nm in [cname, *aliases]:
                    k = norm_key(nm)
                    if k:
                        concept_key_to_nid[k] = nid
            elif any(norm_key(nm) in concept_key_to_nid for nm in [cname, *aliases]):
                inc_log["nodes_kept_separate"].append({"node_id": nid, "canonical_name": cname, "reason": "Paper-specific sample/instance kept separate from general concept."})

    # Record explicit alignment & do-not-merge decisions
    for ad in draft.get("alignment_decisions", []):
        if ad not in alignment_log:
            alignment_log.append(ad)

    # Validate & add directed typed relations
    for rel in draft.get("relations", []):
        s_ref = str(rel.get("source_temp_id") or rel.get("source_node_id") or "")
        t_ref = str(rel.get("target_temp_id") or rel.get("target_node_id") or "")
        rtype = str(rel.get("relation_type", "")).strip()
        strength = str(rel.get("evidence_strength", "strong_direct")).strip()

        s_nid = temp_to_final_nid.get(s_ref) or (s_ref if s_ref in nodes else None)
        t_nid = temp_to_final_nid.get(t_ref) or (t_ref if t_ref in nodes else None)
        if not s_nid or not t_nid:
            errors.append(f"Relation ({s_ref} -[{rtype}]-> {t_ref}) references unknown node ID.")
            continue
        if rtype not in valid_rtypes:
            errors.append(f"Relation '{rtype}' not in kg_schema.json (allowed: {sorted(valid_rtypes.keys())}).")
        if strength not in valid_strengths:
            warnings.append(f"Relation evidence_strength '{strength}' not in {sorted(valid_strengths)}.")
        v_provs = verify_provenance_list(rel.get("provenance", []), f"edge:{s_nid}-{rtype}-{t_nid}")

        eid = f"R{next_edge_num:04d}"
        next_edge_num += 1
        edges[eid] = {
            "edge_id": eid,
            "source_node_id": s_nid,
            "relation_type": rtype,
            "target_node_id": t_nid,
            "conditions": str(rel.get("conditions", "")).strip() or "unspecified_in_source",
            "quantitative_detail": str(rel.get("quantitative_detail", "")).strip() or "not_reported",
            "evidence_strength": strength,
            "source_ids": sorted({p["source_id"] for p in v_provs}),
            "provenance": v_provs,
        }
        inc_log["edges_added"].append(eid)

    # Detect opposing/conflicting edges across all edges
    edge_list = list(edges.values())
    conflicts: List[Dict[str, Any]] = []
    for i in range(len(edge_list)):
        for j in range(i + 1, len(edge_list)):
            ei, ej = edge_list[i], edge_list[j]
            if ei["source_node_id"] == ej["source_node_id"] and ei["target_node_id"] == ej["target_node_id"]:
                opp = valid_rtypes.get(ei["relation_type"], {}).get("opposite")
                if opp and ej["relation_type"] == opp:
                    c_rec = {
                        "edge_a": ei["edge_id"],
                        "edge_b": ej["edge_id"],
                        "source_node": nodes[ei["source_node_id"]]["canonical_name"],
                        "target_node": nodes[ei["target_node_id"]]["canonical_name"],
                        "relation_a": ei["relation_type"],
                        "relation_b": ej["relation_type"],
                        "conditions_a": ei["conditions"],
                        "conditions_b": ej["conditions"],
                        "sources_a": ei["source_ids"],
                        "sources_b": ej["source_ids"],
                    }
                    conflicts.append(c_rec)
    inc_log["conflicts_detected"] = conflicts

    kg_obj = {
        "schema_version": "scipdf-kg/1.0",
        "updated_at": utc_now_iso(),
        "corpus_sources": corpus_sources,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "true_cross_paper_concept_nodes": sum(1 for n in nodes.values() if n.get("is_true_cross_paper")),
            "paper_specific_instance_nodes": sum(1 for n in nodes.values() if n.get("is_paper_specific_instance")),
            "distinct_sources_represented": sorted({sid for n in nodes.values() for sid in n.get("source_ids", [])}),
            "alignment_decisions_recorded": len(alignment_log),
            "conflicts_detected": len(conflicts),
        },
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "entity_alignment_log": alignment_log,
        "conflicts": conflicts,
        "incremental_update_history": (existing_kg.get("incremental_update_history", []) if existing_kg else []) + [inc_log],
    }
    return {"valid": len(errors) == 0 and len(nodes) > 0, "errors": errors, "warnings": warnings, "kg": kg_obj}


def export_graphml(kg: Dict[str, Any]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '  <key id="label" for="node" attr.name="canonical_name" attr.type="string"/>',
        '  <key id="etype" for="node" attr.name="entity_type" attr.type="string"/>',
        '  <key id="sources" for="node" attr.name="source_ids" attr.type="string"/>',
        '  <key id="rtype" for="edge" attr.name="relation_type" attr.type="string"/>',
        '  <key id="cond" for="edge" attr.name="conditions" attr.type="string"/>',
        '  <graph id="LiteratureKG" edgedefault="directed">',
    ]
    for n in kg["nodes"]:
        lines.extend([
            f'    <node id="{html.escape(n["node_id"])}">',
            f'      <data key="label">{html.escape(n["canonical_name"])}</data>',
            f'      <data key="etype">{html.escape(n["entity_type"])}</data>',
            f'      <data key="sources">{html.escape(",".join(n.get("source_ids", [])))}</data>',
            "    </node>",
        ])
    for e in kg["edges"]:
        lines.extend([
            f'    <edge id="{html.escape(e["edge_id"])}" source="{html.escape(e["source_node_id"])}" target="{html.escape(e["target_node_id"])}">',
            f'      <data key="rtype">{html.escape(e["relation_type"])}</data>',
            f'      <data key="cond">{html.escape(e["conditions"])}</data>',
            "    </edge>",
        ])
    lines.extend(["  </graph>", "</graphml>"])
    return "\n".join(lines) + "\n"


def export_cypher(kg: Dict[str, Any]) -> str:
    lines = ["// Auto-generated Neo4j Cypher import script for Literature Knowledge Graph"]
    for n in kg["nodes"]:
        lbl = re.sub(r"[^A-Za-z0-9_]", "_", n["entity_type"]) or "Entity"
        name_esc = n["canonical_name"].replace("'", "\\'")
        sids = ",".join(n.get("source_ids", []))
        lines.append(f"MERGE (n:{lbl} {{node_id: '{n['node_id']}'}}) SET n.name = '{name_esc}', n.sources = '{sids}';")
    for e in kg["edges"]:
        rel = re.sub(r"[^A-Z0-9_]", "_", e["relation_type"].upper()) or "RELATED_TO"
        cond_esc = e["conditions"].replace("'", "\\'")
        lines.append(
            f"MATCH (a {{node_id: '{e['source_node_id']}'}}), (b {{node_id: '{e['target_node_id']}'}}) "
            f"MERGE (a)-[r:{rel} {{edge_id: '{e['edge_id']}'}}]->(b) SET r.conditions = '{cond_esc}';"
        )
    return "\n".join(lines) + "\n"


def export_mermaid(kg: Dict[str, Any]) -> str:
    lines = ["flowchart LR"]
    for n in kg["nodes"]:
        lbl = n["canonical_name"].replace('"', "'")[:48]
        sids = ",".join(n.get("source_ids", []))
        lines.append(f'  {n["node_id"]}["{lbl} ({n["entity_type"]} | {sids})"]')
    for e in kg["edges"]:
        lines.append(f'  {e["source_node_id"]} -->|"{e["relation_type"]}"| {e["target_node_id"]}')
    return "\n".join(lines) + "\n"


def export_svg_preview(kg: Dict[str, Any]) -> str:
    """Renders a clean circular-layout vector SVG preview of the Knowledge Graph (zero external deps)."""
    w, h = 960, 640
    cx, cy, radius = w / 2.0, h / 2.0, 220.0
    palette = {
        "Concept_Term": "#7c3aed",
        "Material_or_Sample": "#2563eb",
        "Method_or_Model": "#0891b2",
        "Claim_or_Conclusion": "#059669",
        "Metric_or_Property": "#d97706",
        "Condition_or_Regime": "#dc2626",
    }
    n_list = kg["nodes"]
    pos: Dict[str, Tuple[float, float]] = {}
    count = max(1, len(n_list))
    for i, n in enumerate(n_list):
        ang = 2.0 * math.pi * i / count - math.pi / 2.0
        pos[n["node_id"]] = (round(cx + radius * math.cos(ang), 1), round(cy + radius * math.sin(ang), 1))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="background:#0f172a;font-family:sans-serif">',
        '  <defs><marker id="arrow" markerWidth="10" markerHeight="7" refX="26" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="#94a3b8"/></marker></defs>',
        f'  <text x="24" y="34" fill="#f8fafc" font-size="18" font-weight="bold">Literature Knowledge Graph Preview ({len(n_list)} Nodes, {len(kg["edges"])} Edges)</text>',
    ]
    for e in kg["edges"]:
        x1, y1 = pos.get(e["source_node_id"], (cx, cy))
        x2, y2 = pos.get(e["target_node_id"], (cx, cy))
        mx, my = round((x1 + x2) / 2.0, 1), round((y1 + y2) / 2.0 - 6.0, 1)
        color = "#ef4444" if e["relation_type"] == "CONTRADICTS" else "#64748b"
        parts.append(f'  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2" marker-end="url(#arrow)"/>')
        parts.append(f'  <text x="{mx}" y="{my}" fill="#cbd5e1" font-size="10" text-anchor="middle">{html.escape(e["relation_type"])}</text>')
    for n in n_list:
        x, y = pos[n["node_id"]]
        fill = palette.get(n["entity_type"], "#475569")
        lbl = html.escape(n["canonical_name"][:28])
        sids = html.escape(",".join(n.get("source_ids", [])))
        parts.append(f'  <circle cx="{x}" cy="{y}" r="22" fill="{fill}" stroke="#e2e8f0" stroke-width="2"/>')
        parts.append(f'  <text x="{x}" y="{y+4}" fill="#ffffff" font-size="10" text-anchor="middle">{html.escape(n["node_id"])}</text>')
        parts.append(f'  <text x="{x}" y="{y+38}" fill="#f1f5f9" font-size="11" text-anchor="middle">{lbl} [{sids}]</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build or incrementally update evidence-grounded Literature KG.")
    ap.add_argument("--corpus-index", required=True)
    ap.add_argument("--draft-json", required=True)
    ap.add_argument("--merge-into", default=None, help="Existing knowledge_graph.json for ID-preserving incremental update.")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    corpus = json.loads(Path(args.corpus_index).read_text(encoding="utf-8"))
    draft = json.loads(Path(args.draft_json).read_text(encoding="utf-8"))
    existing = json.loads(Path(args.merge_into).read_text(encoding="utf-8")) if (args.merge_into and Path(args.merge_into).is_file()) else None

    res = build_or_update(corpus, draft, existing)
    kg = res["kg"]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "knowledge_graph.json").write_text(json.dumps(kg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "knowledge_graph.jsonld").write_text(json.dumps({
        "@context": {"scikg": "https://w3id.org/scipdf/kg/", "prov": "http://www.w3.org/ns/prov#"},
        "@graph": kg["nodes"] + kg["edges"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "knowledge_graph.graphml").write_text(export_graphml(kg), encoding="utf-8")
    (out_dir / "knowledge_graph.cypher").write_text(export_cypher(kg), encoding="utf-8")
    (out_dir / "knowledge_graph.mmd").write_text(export_mermaid(kg), encoding="utf-8")
    (out_dir / "kg_preview.svg").write_text(export_svg_preview(kg), encoding="utf-8")

    # Render real PNG preview via multi-backend fallback: matplotlib -> Pillow -> cairosvg -> rsvg-convert
    png_rendered = False
    png_path = out_dir / "kg_preview.png"
    svg_path = out_dir / "kg_preview.svg"
    try:
        import matplotlib  # type: ignore
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
        fig, ax = plt.subplots(figsize=(10, 6.8), dpi=150)
        n_list = kg["nodes"]
        cnt = max(1, len(n_list))
        pos = {n["node_id"]: (math.cos(2 * math.pi * i / cnt), math.sin(2 * math.pi * i / cnt)) for i, n in enumerate(n_list)}
        for e in kg["edges"]:
            x1, y1 = pos[e["source_node_id"]]
            x2, y2 = pos[e["target_node_id"]]
            ec = "#ef4444" if e["relation_type"] == "CONTRADICTS" else "#64748b"
            ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="->", color=ec, lw=1.4))
            ax.text((x1 + x2) / 2, (y1 + y2) / 2, e["relation_type"], fontsize=7, ha="center", color="#1e293b")
        for n in n_list:
            x, y = pos[n["node_id"]]
            ax.scatter([x], [y], s=440, zorder=3)
            ax.text(x, y - 0.14, f"{n['node_id']}: {n['canonical_name'][:26]}", fontsize=8, ha="center")
        ax.axis("off")
        ax.set_title(f"Literature Knowledge Graph ({len(n_list)} nodes, {len(kg['edges'])} edges)")
        fig.tight_layout()
        fig.savefig(str(png_path))
        plt.close(fig)
        png_rendered = png_path.is_file()
    except Exception:
        png_rendered = False

    if not png_rendered:
        try:
            import cairosvg  # type: ignore
            cairosvg.svg2png(url=str(svg_path), write_to=str(png_path))
            png_rendered = png_path.is_file()
        except Exception:
            pass

    if not png_rendered:
        try:
            import shutil as _shutil, subprocess as _sub
            if _shutil.which("rsvg-convert"):
                _sub.run(["rsvg-convert", "-o", str(png_path), str(svg_path)], check=False, timeout=20)
                png_rendered = png_path.is_file()
            elif _shutil.which("inkscape"):
                _sub.run(["inkscape", str(svg_path), "--export-type=png", f"--export-filename={png_path}"], check=False, timeout=30)
                png_rendered = png_path.is_file()
        except Exception:
            pass

    if not png_rendered:
        try:
            from PIL import Image, ImageDraw  # type: ignore
            w, h = 960, 640
            img = Image.new("RGB", (w, h), (15, 23, 42))
            draw = ImageDraw.Draw(img)
            n_list = kg["nodes"]
            cnt = max(1, len(n_list))
            cx, cy, rad = w / 2.0, h / 2.0, 220.0
            pos = {n["node_id"]: (cx + rad * math.cos(2 * math.pi * i / cnt), cy + rad * math.sin(2 * math.pi * i / cnt)) for i, n in enumerate(n_list)}
            draw.text((20, 16), f"Literature KG Preview ({len(n_list)} nodes, {len(kg['edges'])} edges)", fill=(248, 250, 252))
            for e in kg["edges"]:
                x1, y1 = pos[e["source_node_id"]]
                x2, y2 = pos[e["target_node_id"]]
                col = (239, 68, 68) if e["relation_type"] == "CONTRADICTS" else (148, 163, 184)
                draw.line([(x1, y1), (x2, y2)], fill=col, width=2)
                draw.text(((x1 + x2) / 2, (y1 + y2) / 2 - 8), e["relation_type"], fill=(203, 213, 225))
            for n in n_list:
                x, y = pos[n["node_id"]]
                draw.ellipse([x - 18, y - 18, x + 18, y + 18], fill=(37, 99, 235), outline=(226, 232, 240), width=2)
                draw.text((x - 14, y - 6), n["node_id"], fill=(255, 255, 255))
                draw.text((x - 45, y + 22), n["canonical_name"][:22], fill=(241, 245, 249))
            img.save(str(png_path))
            png_rendered = png_path.is_file()
        except Exception:
            png_rendered = False

    audit_lines = [
        "# Literature Knowledge Graph Alignment & Grounding Audit Report",
        "",
        f"- **Validation Gate**: `{'PASS' if res['valid'] else 'FAIL'}`",
        f"- **Graph Summary**: `{json.dumps(kg['summary'], ensure_ascii=False)}`",
        f"- **Visual Previews**: `kg_preview.svg`{' + `kg_preview.png`' if png_rendered else ''}",
        "",
        "## Entity Alignment & Do-Not-Merge Decisions",
        "",
    ]
    for ad in kg["entity_alignment_log"]:
        audit_lines.append(f"- **`{ad.get('decision')}`** ({ad.get('candidate_names')}): {ad.get('rationale')}")
    if kg["conflicts"]:
        audit_lines.extend(["", "## Detected Cross-Paper / Regime Conflicts", ""])
        for c in kg["conflicts"]:
            audit_lines.append(f"- `{c['edge_a']}` ({c['relation_a']}, {c['sources_a']}) vs `{c['edge_b']}` ({c['relation_b']}, {c['sources_b']}) on `{c['source_node']}` -> `{c['target_node']}`")
    (out_dir / "kg_audit_report.md").write_text("\n".join(audit_lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "valid": res["valid"],
        "summary": kg["summary"],
        "png_rendered": png_rendered,
        "errors": res["errors"],
        "warnings": res["warnings"],
    }, ensure_ascii=False, indent=2))
    return 2 if (args.strict and not res["valid"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
