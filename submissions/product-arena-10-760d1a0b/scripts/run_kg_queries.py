#!/usr/bin/env python3
"""
run_kg_queries.py — Executable Subgraph Query Engine & Contract Result Finalizer

Executes >= 3 real queries over knowledge_graph.json (neighborhood subgraph, cross-paper
concept comparison with condition separation, conflict audit, shortest evidence path,
and honest 'unverifiable_not_connected' reporting for unsupported queries), writes
/app/output/kg_queries_report.md, and finalizes /app/output/result.json preserving all
original /input/case.json output_contract fields.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def match_nodes(nodes: List[Dict[str, Any]], q: str) -> List[Dict[str, Any]]:
    ql = q.strip().lower()
    hits = []
    for n in nodes:
        hay = " ".join([n["node_id"], n["canonical_name"], *n.get("aliases", [])]).lower()
        if ql in hay:
            hits.append(n)
    return hits


def execute_query(kg: Dict[str, Any], qspec: Dict[str, Any]) -> Dict[str, Any]:
    nodes = kg.get("nodes", [])
    edges = kg.get("edges", [])
    nodes_by_id = {n["node_id"]: n for n in nodes}
    qid = str(qspec.get("query_id", "Q"))
    kind = str(qspec.get("kind", "entity_neighborhood"))

    if kind == "entity_neighborhood":
        eq = str(qspec.get("entity_query", ""))
        seeds = match_nodes(nodes, eq)
        if not seeds:
            return {"query_id": qid, "kind": kind, "description": qspec.get("description", ""), "status": "not_found_in_corpus", "subgraph": {"nodes": [], "edges": []}, "evidence": []}
        seed_ids = {s["node_id"] for s in seeds}
        rel_edges = [e for e in edges if e["source_node_id"] in seed_ids or e["target_node_id"] in seed_ids]
        sub_nids = set(seed_ids)
        for e in rel_edges:
            sub_nids.add(e["source_node_id"])
            sub_nids.add(e["target_node_id"])
        sub_nodes = [nodes_by_id[nid] for nid in sorted(sub_nids) if nid in nodes_by_id]
        ev = [pr for e in rel_edges for pr in e.get("provenance", [])]
        return {
            "query_id": qid, "kind": kind, "description": qspec.get("description", ""),
            "status": "answered",
            "matched_seed_nodes": [s["node_id"] for s in seeds],
            "subgraph": {"nodes": sub_nodes, "edges": rel_edges},
            "evidence": ev,
        }

    if kind == "cross_paper_comparison":
        min_src = int(qspec.get("min_distinct_sources", 2))
        shared = [n for n in nodes if n.get("is_true_cross_paper") and len(n.get("source_ids", [])) >= min_src]
        shared_ids = {n["node_id"] for n in shared}
        linked_edges = [e for e in edges if e["source_node_id"] in shared_ids or e["target_node_id"] in shared_ids]
        sub_nids = set(shared_ids)
        for e in linked_edges:
            sub_nids.add(e["source_node_id"])
            sub_nids.add(e["target_node_id"])
        return {
            "query_id": qid, "kind": kind, "description": qspec.get("description", ""),
            "status": "answered" if shared else "no_cross_paper_overlap_found",
            "shared_concept_nodes": [n["canonical_name"] for n in shared],
            "subgraph": {"nodes": [nodes_by_id[nid] for nid in sorted(sub_nids)], "edges": linked_edges},
            "evidence": [pr for e in linked_edges for pr in e.get("provenance", [])],
        }

    if kind == "conflict_and_condition_audit":
        confs = kg.get("conflicts", [])
        c_eids = {c["edge_a"] for c in confs} | {c["edge_b"] for c in confs}
        c_edges = [e for e in edges if e["edge_id"] in c_eids]
        sub_nids = {e["source_node_id"] for e in c_edges} | {e["target_node_id"] for e in c_edges}
        return {
            "query_id": qid, "kind": kind, "description": qspec.get("description", ""),
            "status": "answered",
            "conflict_count": len(confs),
            "conflicts": confs,
            "subgraph": {"nodes": [nodes_by_id[nid] for nid in sorted(sub_nids)], "edges": c_edges},
            "evidence": [pr for e in c_edges for pr in e.get("provenance", [])],
        }

    if kind == "shortest_evidence_path":
        src_hits = match_nodes(nodes, str(qspec.get("from_entity", "")))
        dst_hits = match_nodes(nodes, str(qspec.get("to_entity", "")))
        if not src_hits or not dst_hits:
            return {
                "query_id": qid, "kind": kind, "description": qspec.get("description", ""),
                "status": "unverifiable_not_connected",
                "reason": "One or both queried entities do not exist in the grounded corpus graph.",
                "subgraph": {"nodes": [], "edges": []},
                "evidence": [],
            }
        adj: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        for e in edges:
            adj[e["source_node_id"]].append(e)
            adj[e["target_node_id"]].append(e)
        dst_set = {d["node_id"] for d in dst_hits}
        q_bfs: collections.deque[Any] = collections.deque([(s["node_id"], [s["node_id"]], []) for s in src_hits])
        visited = {s["node_id"] for s in src_hits}
        while q_bfs:
            cur, n_path, e_path = q_bfs.popleft()
            if cur in dst_set:
                return {
                    "query_id": qid, "kind": kind, "description": qspec.get("description", ""),
                    "status": "answered",
                    "path_node_ids": n_path,
                    "subgraph": {"nodes": [nodes_by_id[nid] for nid in n_path], "edges": e_path},
                    "evidence": [pr for e in e_path for pr in e.get("provenance", [])],
                }
            for e in adj[cur]:
                nxt = e["target_node_id"] if e["source_node_id"] == cur else e["source_node_id"]
                if nxt not in visited:
                    visited.add(nxt)
                    q_bfs.append((nxt, n_path + [nxt], e_path + [e]))
        return {
            "query_id": qid, "kind": kind, "description": qspec.get("description", ""),
            "status": "unverifiable_not_connected",
            "reason": "No verified evidence path connects the queried entities in the corpus.",
            "subgraph": {"nodes": [], "edges": []},
            "evidence": [],
        }

    return {"query_id": qid, "kind": kind, "status": "unsupported_query_kind", "subgraph": {"nodes": [], "edges": []}, "evidence": []}


def build_auto_queries(kg: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes = kg.get("nodes", [])
    edges = kg.get("edges", [])
    deg: Dict[str, int] = collections.Counter()
    for e in edges:
        deg[e["source_node_id"]] += 1
        deg[e["target_node_id"]] += 1
    ranked = sorted(nodes, key=lambda n: (-deg[n["node_id"]], n["node_id"]))
    queries: List[Dict[str, Any]] = []
    if ranked:
        hub = ranked[0]
        queries.append({
            "query_id": "Q1_HUB_NEIGHBORHOOD",
            "kind": "entity_neighborhood",
            "entity_query": hub["canonical_name"],
            "description": f"1-hop evidence-grounded neighborhood of highest-degree hub '{hub['canonical_name']}'.",
        })
    queries.append({
        "query_id": "Q2_CROSS_PAPER_OVERLAP",
        "kind": "cross_paper_comparison",
        "min_distinct_sources": 2,
        "description": "Compare aligned conceptual nodes corroborated across >= 2 distinct papers.",
    })
    queries.append({
        "query_id": "Q3_CONFLICT_CONDITION_AUDIT",
        "kind": "conflict_and_condition_audit",
        "description": "Audit opposing directed relations and compare holding conditions across papers/regimes.",
    })
    if len(ranked) >= 2:
        queries.append({
            "query_id": "Q4_EVIDENCE_PATH",
            "kind": "shortest_evidence_path",
            "from_entity": ranked[0]["canonical_name"],
            "to_entity": ranked[-1]["canonical_name"],
            "description": f"Shortest verified evidence path between '{ranked[0]['canonical_name']}' and '{ranked[-1]['canonical_name']}'.",
        })
    queries.append({
        "query_id": "Q5_UNSUPPORTED_BOUNDARY",
        "kind": "shortest_evidence_path",
        "from_entity": ranked[0]["canonical_name"] if ranked else "unknown",
        "to_entity": "__NON_EXISTENT_UNREPORTED_ENTITY__",
        "description": "Negative boundary check verifying honest 'unverifiable_not_connected' reporting for absent entities.",
    })
    return queries


def main() -> int:
    ap = argparse.ArgumentParser(description="Run executable Knowledge Graph queries and finalize result.json.")
    ap.add_argument("--kg-json", required=True)
    ap.add_argument("--queries-json", default=None, help="Optional queries JSON; if omitted or --auto-queries is set, generates >= 4 grounded queries automatically.")
    ap.add_argument("--auto-queries", action="store_true")
    ap.add_argument("--case-json", default=None)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    kg = json.loads(Path(args.kg_json).read_text(encoding="utf-8"))
    qlist: List[Dict[str, Any]] = []
    if args.queries_json and Path(args.queries_json).is_file():
        qdata = json.loads(Path(args.queries_json).read_text(encoding="utf-8"))
        qlist = qdata.get("queries", qdata if isinstance(qdata, list) else [])
    if args.auto_queries or len(qlist) < 3:
        existing_ids = {q.get("query_id") for q in qlist}
        for aq in build_auto_queries(kg):
            if aq["query_id"] not in existing_ids:
                qlist.append(aq)

    results = [execute_query(kg, qs) for qs in qlist]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rep_lines = ["# Executed Knowledge Graph Queries & Subgraph Evidence Report", ""]
    for qr in results:
        rep_lines.extend([
            f"## [{qr['query_id']}] {qr['kind']} — Status: `{qr['status']}`",
            f"- **Description**: {qr.get('description', '-')}",
            f"- **Result Subgraph**: {len(qr['subgraph']['nodes'])} node(s), {len(qr['subgraph']['edges'])} edge(s)",
        ])
        if qr.get("reason"):
            rep_lines.append(f"- **Boundary / Reason**: {qr['reason']}")
        for e in qr["subgraph"]["edges"][:10]:
            rep_lines.append(f"  - Edge `{e['edge_id']}`: `{e['source_node_id']}` -[`{e['relation_type']}`]-> `{e['target_node_id']}` (Conditions: {e['conditions']})")
        for ev in qr["evidence"][:8]:
            rep_lines.append(f"  - Evidence (`{ev['source_id']}:{ev['paragraph_id']}`): “{ev['verbatim_quote'][:160]}”")
        rep_lines.append("")
    qrep_p = out_dir / "kg_queries_report.md"
    qrep_p.write_text("\n".join(rep_lines), encoding="utf-8")

    # Update /app/output/result.json preserving all original output_contract keys
    res_p = out_dir / "result.json"
    out_obj: Dict[str, Any] = json.loads(res_p.read_text(encoding="utf-8")) if res_p.is_file() else {}
    case_data = json.loads(Path(args.case_json).read_text(encoding="utf-8")) if (args.case_json and Path(args.case_json).is_file()) else {}

    out_obj["case_id"] = case_data.get("case_id") or case_data.get("id") or out_obj.get("case_id") or "unknown_case"
    out_obj["status"] = "success" if len(results) >= 3 and kg["summary"]["node_count"] > 0 else "partial"
    out_obj["status_reason"] = (
        f"Built grounded KG ({kg['summary']['node_count']} nodes, {kg['summary']['edge_count']} edges across "
        f"{len(kg['summary']['distinct_sources_represented'])} source(s)) and executed {len(results)} queries."
    )
    out_obj["results"] = {
        **(out_obj.get("results") if isinstance(out_obj.get("results"), dict) else {}),
        "kg_json_path": str(out_dir / "knowledge_graph.json"),
        "kg_jsonld_path": str(out_dir / "knowledge_graph.jsonld"),
        "kg_graphml_path": str(out_dir / "knowledge_graph.graphml"),
        "kg_cypher_path": str(out_dir / "knowledge_graph.cypher"),
        "kg_mermaid_path": str(out_dir / "knowledge_graph.mmd"),
        "kg_preview_svg_path": str(out_dir / "kg_preview.svg"),
        "kg_preview_png_path": str(out_dir / "kg_preview.png") if (out_dir / "kg_preview.png").is_file() else None,
        "queries_report_path": str(qrep_p),
        "audit_report_path": str(out_dir / "kg_audit_report.md"),
        "graph_summary": kg["summary"],
        "entity_alignment_log": kg["entity_alignment_log"],
        "incremental_update_history": kg.get("incremental_update_history", []),
        "query_results": results,
    }
    out_obj["sources"] = [
        {
            "entity_or_edge_id": e["edge_id"],
            "source_id": pr["source_id"],
            "paragraph_id": pr["paragraph_id"],
            "location_type": pr["location_type"],
            "page": pr.get("page"),
            "verbatim_quote": pr["verbatim_quote"],
            "attribution": pr.get("attribution"),
        }
        for e in kg["edges"] for pr in e.get("provenance", [])
    ]
    unver_qs = [qr for qr in results if qr["status"] in {"unverifiable_not_connected", "not_found_in_corpus"}]
    out_obj["uncertainty"] = {
        "unreported_in_source": (out_obj.get("uncertainty") or {}).get("unreported_in_source", []),
        "unverifiable_queries_or_relations": [{"query_id": q["query_id"], "status": q["status"], "reason": q.get("reason", "")} for q in unver_qs],
        "material_conflicts": kg.get("conflicts", []),
        "human_takeover_locations": [],
    }
    recs = out_obj.get("change_records") if isinstance(out_obj.get("change_records"), list) else []
    recs.append({"step": "run_kg_queries", "timestamp": utc_now_iso(), "summary": out_obj["status_reason"]})
    out_obj["change_records"] = recs
    res_p.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": out_obj["status"],
        "queries_executed": len(results),
        "query_statuses": {r["query_id"]: r["status"] for r in results},
        "result_json": str(res_p),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
