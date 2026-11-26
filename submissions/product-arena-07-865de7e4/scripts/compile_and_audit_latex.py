#!/usr/bin/env python3
"""Multi-pass LaTeX compiler, full log root-cause parser, PNG previewer, diff & manifest generator.

Pure Python stdlib (Python 3.8+). Never fabricates PDFs/PNGs if TeX/preview tools are absent.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from inspect_latex_project import probe_environment, run_static_audit, safe_read_text


SKILL_ROOT = Path(__file__).resolve().parent.parent
AUX_EXTS = {".aux", ".log", ".out", ".toc", ".lof", ".lot", ".bbl", ".blg", ".bcf", ".run.xml", ".fls", ".fdb_latexmk", ".synctex.gz", ".nav", ".snm"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_compile_logs(log_text: str, blg_text: str, taxonomy: Dict[str, Any]) -> Dict[str, Any]:
    """Parse entire .log and .blg files into deduplicated root-cause diagnostics."""
    diagnostics: List[Dict[str, Any]] = []
    seen = set()
    lines = log_text.splitlines()
    categories = taxonomy.get("error_categories", [])

    def classify(msg: str) -> Tuple[str, str, bool]:
        for cat in categories:
            for pat in cat.get("patterns", []):
                if re.search(pat, msg, re.IGNORECASE):
                    return cat["category"], cat["fix_hint"], bool(cat.get("is_cascade_symptom", False))
        return "other_latex_diagnostic", "Inspect the reported file and line context in the .log file.", False

    # 1. Parse -file-line-error style: ./main.tex:42: Error message
    fle_re = re.compile(r"^(\.?[/\\]?[^:\s][^:]*\.(?:tex|sty|cls|bbl)):(\d+):\s*(.+)$")
    for idx, line in enumerate(lines):
        m = fle_re.match(line.strip())
        if m:
            f, ln, msg = m.group(1), int(m.group(2)), m.group(3).strip()
            cat, hint, is_casc = classify(msg)
            key = (f, ln, cat, msg[:80])
            if key not in seen:
                seen.add(key)
                diagnostics.append(
                    {
                        "severity": "error",
                        "file": f,
                        "line": ln,
                        "category": cat,
                        "message": msg,
                        "fix_hint": hint,
                        "is_cascade": is_casc,
                    }
                )
            continue

        # 2. Classic ! Error followed by l.<num>
        if line.startswith("! "):
            msg = line[2:].strip()
            ln = None
            for ahead in lines[idx + 1 : idx + 6]:
                lm = re.match(r"^l\.(\d+)\s", ahead.strip())
                if lm:
                    ln = int(lm.group(1))
                    break
            cat, hint, is_casc = classify(msg)
            key = ("<log>", ln, cat, msg[:80])
            if key not in seen:
                seen.add(key)
                diagnostics.append(
                    {
                        "severity": "error",
                        "file": "<current_tex>",
                        "line": ln,
                        "category": cat,
                        "message": msg,
                        "fix_hint": hint,
                        "is_cascade": is_casc,
                    }
                )

    # 3. Warnings: Undefined citations, references, multiply-defined labels, Overfull \hbox, rerun
    warn_re = re.compile(
        r"((?:LaTeX|Package\s+[A-Za-z0-9_-]+)\s+Warning:[^\n]+|Overfull \\hbox \([0-9.]+pt too wide\)[^\n]*)"
    )
    overfull_over_10pt = 0
    unresolved_cites = 0
    unresolved_refs = 0
    rerun_needed = False

    for m in warn_re.finditer(log_text):
        wmsg = re.sub(r"\s+", " ", m.group(1)).strip()
        cat, hint, is_casc = classify(wmsg)
        if cat == "rerun_required":
            rerun_needed = True
        if "Citation `" in wmsg and "undefined" in wmsg:
            unresolved_cites += 1
        if "Reference `" in wmsg and "undefined" in wmsg:
            unresolved_refs += 1
        ov = re.search(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", wmsg)
        if ov and float(ov.group(1)) > 10.0:
            overfull_over_10pt += 1
        lm = re.search(r"input line (\d+)", wmsg)
        ln = int(lm.group(1)) if lm else None
        key = ("warn", ln, cat, wmsg[:90])
        if key not in seen:
            seen.add(key)
            diagnostics.append(
                {
                    "severity": "warning",
                    "file": "<log>",
                    "line": ln,
                    "category": cat,
                    "message": wmsg,
                    "fix_hint": hint,
                    "is_cascade": is_casc,
                }
            )

    # 4. BibTeX / Biber .blg errors
    for bline in blg_text.splitlines():
        if re.search(r"(I didn't find a database entry|Warning--|ERROR -|WARN -|error message)", bline, re.IGNORECASE):
            cat, hint, is_casc = classify(bline)
            sev = "error" if "error" in bline.lower() else "warning"
            key = ("blg", None, cat, bline.strip()[:90])
            if key not in seen:
                seen.add(key)
                diagnostics.append(
                    {
                        "severity": sev,
                        "file": "<bib_log>",
                        "line": None,
                        "category": cat,
                        "message": bline.strip(),
                        "fix_hint": hint,
                        "is_cascade": is_casc,
                    }
                )

    fatal_errors = sum(1 for d in diagnostics if d["severity"] == "error")
    warnings = sum(1 for d in diagnostics if d["severity"] == "warning")
    return {
        "diagnostics": diagnostics,
        "fatal_error_count": fatal_errors,
        "warning_count": warnings,
        "unresolved_citations": unresolved_cites,
        "unresolved_references": unresolved_refs,
        "overfull_hbox_over_10pt": overfull_over_10pt,
        "rerun_needed": rerun_needed,
    }


def render_pdf_previews(pdf_path: Path, preview_dir: Path, env_tools: Dict[str, Any]) -> Tuple[List[str], Optional[str]]:
    """Render real PNG page previews from compiled PDF using available system tools."""
    preview_dir.mkdir(parents=True, exist_ok=True)
    # 1. Prefer pdftoppm
    if env_tools.get("pdftoppm", {}).get("available"):
        prefix = preview_dir / "page"
        proc = subprocess.run(
            ["pdftoppm", "-png", "-r", "150", str(pdf_path), str(prefix)],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if proc.returncode == 0:
            imgs = sorted(p.relative_to(preview_dir.parent).as_posix() for p in preview_dir.glob("page*.png"))
            if imgs:
                return imgs, None
    # 2. Try PyMuPDF (fitz)
    if env_tools.get("pymupdf_fitz", {}).get("available"):
        try:
            import fitz  # type: ignore

            doc = fitz.open(str(pdf_path))
            imgs = []
            for idx, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=150)
                out_img = preview_dir / f"page-{idx:02d}.png"
                pix.save(str(out_img))
                imgs.append(out_img.relative_to(preview_dir.parent).as_posix())
            doc.close()
            if imgs:
                return imgs, None
        except Exception:
            pass
    # 3. Try Ghostscript
    if env_tools.get("gs", {}).get("available"):
        out_pat = str(preview_dir / "page-%02d.png")
        proc = subprocess.run(
            ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m", "-r150", f"-sOutputFile={out_pat}", str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if proc.returncode == 0:
            imgs = sorted(p.relative_to(preview_dir.parent).as_posix() for p in preview_dir.glob("page*.png"))
            if imgs:
                return imgs, None
    return [], "PDF compiled, but no PDF-to-PNG renderer (pdftoppm/pymupdf/gs) is available in this environment."


def compute_unified_diff(orig_dir: Path, work_dir: Path) -> Tuple[str, List[Dict[str, str]]]:
    diffs: List[str] = []
    changes: List[Dict[str, str]] = []
    source_exts = {".tex", ".bib", ".cls", ".sty"}
    work_files = {p.relative_to(work_dir).as_posix(): p for p in work_dir.rglob("*") if p.is_file() and p.suffix.lower() in source_exts}
    orig_files = {p.relative_to(orig_dir).as_posix(): p for p in orig_dir.rglob("*") if p.is_file() and p.suffix.lower() in source_exts}

    for rel in sorted(set(work_files) | set(orig_files)):
        o_lines = safe_read_text(orig_files[rel]).splitlines(keepends=True) if rel in orig_files else []
        w_lines = safe_read_text(work_files[rel]).splitlines(keepends=True) if rel in work_files else []
        if o_lines != w_lines:
            d = "".join(difflib.unified_diff(o_lines, w_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
            diffs.append(d)
            changes.append(
                {
                    "file": rel,
                    "change_type": "modified" if rel in orig_files and rel in work_files else ("added" if rel in work_files else "removed"),
                    "summary": f"Updated {rel} ({len(o_lines)} -> {len(w_lines)} lines).",
                }
            )
    return "\n".join(diffs), changes


def run_multipass_compile(
    work_dir: Path,
    main_tex_rel: str,
    engine: str,
    bib_tool: Optional[str],
    taxonomy: Dict[str, Any],
) -> Dict[str, Any]:
    main_path = work_dir / main_tex_rel
    stem = main_path.stem
    cwd = main_path.parent
    passes: List[Dict[str, Any]] = []

    def _run(cmd: List[str], label: str) -> Dict[str, Any]:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=180, check=False)
        rec = {"step": label, "command": " ".join(cmd), "returncode": proc.returncode}
        passes.append(rec)
        return rec

    tex_cmd = [engine, "-interaction=nonstopmode", "-file-line-error", main_path.name]

    # Pass 1
    _run(tex_cmd, "pass_1")

    # Optional BibTeX / Biber pass
    if bib_tool and shutil.which(bib_tool):
        if bib_tool == "biber" and (cwd / f"{stem}.bcf").exists():
            _run(["biber", stem], "bib_biber")
        elif bib_tool == "bibtex" and (cwd / f"{stem}.aux").exists():
            aux_txt = safe_read_text(cwd / f"{stem}.aux")
            if "\\bibdata" in aux_txt or "\\citation" in aux_txt:
                _run(["bibtex", stem], "bib_bibtex")

    # Convergence passes (up to 3 additional passes)
    parsed = {"diagnostics": [], "fatal_error_count": 0, "warning_count": 0, "unresolved_citations": 0, "unresolved_references": 0, "overfull_hbox_over_10pt": 0, "rerun_needed": True}
    converged = False
    for pnum in (2, 3, 4):
        _run(tex_cmd, f"pass_{pnum}")
        log_txt = safe_read_text(cwd / f"{stem}.log")
        blg_txt = safe_read_text(cwd / f"{stem}.blg")
        parsed = parse_compile_logs(log_txt, blg_txt, taxonomy)
        if not parsed["rerun_needed"]:
            converged = True
            break

    pdf_path = cwd / f"{stem}.pdf"
    return {
        "passes": passes,
        "converged": converged,
        "pdf_path": pdf_path if pdf_path.exists() else None,
        "log_summary": parsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile LaTeX project, audit logs, render previews, and generate manifest.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--work-project", required=True)
    parser.add_argument("--inspection", required=False)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--engine", default="auto", choices=["auto", "pdflatex", "xelatex", "lualatex"])
    parser.add_argument("--main-tex", default=None)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    work_dir = Path(args.work_project)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rules = json.loads((SKILL_ROOT / "resources" / "template_and_package_rules.json").read_text(encoding="utf-8"))
    taxonomy = json.loads((SKILL_ROOT / "resources" / "latex_error_taxonomy.json").read_text(encoding="utf-8"))

    insp_data: Dict[str, Any] = {}
    if args.inspection and Path(args.inspection).exists():
        insp_data = json.loads(safe_read_text(Path(args.inspection)))
    elif (input_dir / "case.json").exists():
        try:
            c = json.loads(safe_read_text(input_dir / "case.json"))
            insp_data = {"case_id": c.get("case_id", "unknown-case"), "output_contract": c.get("output_contract") or {}}
        except Exception:
            pass

    env_tools = probe_environment()
    static_after = run_static_audit(work_dir, rules)
    main_tex_rel = args.main_tex or static_after.get("main_tex")

    chosen_engine = args.engine if args.engine != "auto" else static_after.get("recommended_engine", "pdflatex")
    if not env_tools.get(chosen_engine, {}).get("available"):
        # Fallback only if another compatible engine exists
        for cand in ("xelatex", "pdflatex", "lualatex"):
            if env_tools.get(cand, {}).get("available"):
                chosen_engine = cand
                break
        else:
            chosen_engine = None

    bib_tool = static_after.get("recommended_bib_tool")
    if bib_tool and not env_tools.get(bib_tool, {}).get("available"):
        bib_tool = None

    uncertainties: List[Dict[str, str]] = []
    compile_result: Dict[str, Any] = {
        "passes": [],
        "converged": False,
        "pdf_path": None,
        "log_summary": {
            "diagnostics": [],
            "fatal_error_count": 0,
            "warning_count": 0,
            "unresolved_citations": 0,
            "unresolved_references": 0,
            "overfull_hbox_over_10pt": 0,
            "rerun_needed": False,
        },
    }

    if not main_tex_rel:
        uncertainties.append({"item": "No main .tex file found in work project.", "action_needed": "Specify --main-tex."})
    elif not chosen_engine:
        uncertainties.append(
            {
                "item": "No TeX compilation binary (pdflatex/xelatex/lualatex) is installed in this environment; PDF compilation skipped honestly without fabrication.",
                "action_needed": "Run reproduce_build.sh in an environment with TeX Live/MiKTeX installed.",
            }
        )
    else:
        compile_result = run_multipass_compile(work_dir, main_tex_rel, chosen_engine, bib_tool, taxonomy)

    # Copy clean editable sources + PDF to output_dir
    output_hashes: Dict[str, str] = {}
    for p in sorted(work_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() in AUX_EXTS or p.name.endswith(".synctex.gz"):
            continue
        rel = p.relative_to(work_dir)
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
        output_hashes[rel.as_posix()] = sha256_file(dest)

    compiled_pdf_rel: Optional[str] = None
    preview_images: List[str] = []
    if compile_result["pdf_path"] and compile_result["pdf_path"].exists():
        pdf_rel = compile_result["pdf_path"].relative_to(work_dir).as_posix()
        compiled_pdf_rel = pdf_rel
        preview_images, preview_warn = render_pdf_previews(out_dir / pdf_rel, out_dir / "previews", env_tools)
        if preview_warn:
            uncertainties.append({"item": preview_warn, "action_needed": "Inspect compiled PDF directly or install poppler/pdftoppm."})

    # Compute unified diff against original /input
    diff_text, change_records = compute_unified_diff(input_dir, work_dir)
    (out_dir / "changes.diff").write_text(diff_text or "# No source differences.\n", encoding="utf-8")

    # Generate reproducible build script
    repro_engine = chosen_engine or static_after.get("recommended_engine", "pdflatex")
    repro_bib = static_after.get("recommended_bib_tool")
    stem = Path(main_tex_rel).stem if main_tex_rel else "main"
    repro_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"# Reproducible LaTeX build script generated by scipdf-latex-builder-auditor",
        f"# Engine: {repro_engine} | Bib tool: {repro_bib or 'none'}",
        f"{repro_engine} -interaction=nonstopmode -file-line-error {main_tex_rel or 'main.tex'}",
    ]
    if repro_bib:
        repro_lines.append(f"{repro_bib} {stem}")
    repro_lines.extend(
        [
            f"{repro_engine} -interaction=nonstopmode -file-line-error {main_tex_rel or 'main.tex'}",
            f"{repro_engine} -interaction=nonstopmode -file-line-error {main_tex_rel or 'main.tex'}",
            "",
        ]
    )
    (out_dir / "reproduce_build.sh").write_text("\n".join(repro_lines), encoding="utf-8")

    log_sum = compile_result["log_summary"]
    build_manifest = {
        "case_id": insp_data.get("case_id", "unknown-case"),
        "main_tex": main_tex_rel,
        "engine_used": chosen_engine,
        "engine_version": env_tools.get(chosen_engine, {}).get("version") if chosen_engine else None,
        "bib_tool_used": bib_tool,
        "packages_detected": static_after.get("packages_loaded", []),
        "compile_passes": compile_result["passes"],
        "converged": compile_result["converged"],
        "fatal_error_count": log_sum["fatal_error_count"],
        "warning_count": log_sum["warning_count"],
        "diagnostics": log_sum["diagnostics"],
        "static_audit": {
            "static_error_count": static_after["static_error_count"],
            "static_warning_count": static_after["static_warning_count"],
            "issues": static_after["static_issues"],
        },
        "preview_images": preview_images,
        "output_hashes_sha256": output_hashes,
    }
    (out_dir / "build_manifest.json").write_text(json.dumps(build_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # Audit Report Markdown
    rep_lines = [
        "# LaTeX Static Audit & Reproducible Build Report",
        "",
        f"- **Main TeX**: `{main_tex_rel}`",
        f"- **Engine Used**: `{chosen_engine}` ({build_manifest['engine_version'] or 'N/A'})",
        f"- **Compiled PDF**: `{compiled_pdf_rel or 'Not produced'}`",
        f"- **Converged**: `{compile_result['converged']}`",
        f"- **Static Errors / Warnings**: `{static_after['static_error_count']} / {static_after['static_warning_count']}`",
        f"- **Compile Fatal Errors / Warnings**: `{log_sum['fatal_error_count']} / {log_sum['warning_count']}`",
        f"- **Unresolved Citations / References**: `{log_sum['unresolved_citations']} / {log_sum['unresolved_references']}`",
        f"- **Overfull \\hbox > 10pt**: `{log_sum['overfull_hbox_over_10pt']}`",
        f"- **Rendered PNG Previews**: `{len(preview_images)}`",
        "",
        "## Static Audit Findings",
    ]
    if static_after["static_issues"]:
        for it in static_after["static_issues"]:
            rep_lines.append(f"- `[{it['severity'].upper()}]` `{it['file']}:{it['line']}` ({it['category']}): {it['message']}")
    else:
        rep_lines.append("- None (0 static errors/warnings).")

    rep_lines.extend(["", "## Compile Log Root-Cause Diagnostics"])
    if log_sum["diagnostics"]:
        for d in log_sum["diagnostics"]:
            tag = "CASCADE" if d["is_cascade"] else "ROOT"
            rep_lines.append(
                f"- `[{d['severity'].upper()}][{tag}]` `{d['file']}:{d['line']}` ({d['category']}): {d['message']} -> *Fix*: {d['fix_hint']}"
            )
    else:
        rep_lines.append("- No compile diagnostics recorded.")
    rep_lines.append("")
    (out_dir / "audit_report.md").write_text("\n".join(rep_lines), encoding="utf-8")

    # Honest overall status
    compiled_ok = bool(compiled_pdf_rel and log_sum["fatal_error_count"] == 0 and compile_result["converged"])
    clean_static = static_after["static_error_count"] == 0
    if compiled_ok and clean_static and log_sum["unresolved_citations"] == 0 and log_sum["unresolved_references"] == 0:
        status = "completed"
    else:
        status = "partial"

    for it in static_after["static_issues"]:
        if it["severity"] == "error":
            uncertainties.append({"item": f"{it['file']}:{it['line']} {it['message']}", "action_needed": f"Fix {it['category']}"})
    for d in log_sum["diagnostics"]:
        if d["severity"] == "error":
            uncertainties.append({"item": f"{d['file']}:{d['line']} {d['message']}", "action_needed": d["fix_hint"]})

    sources = [{"source_file": f} for f in static_after["tex_files"] + static_after["bib_files"] + static_after["cls_files"] + static_after["sty_files"]]

    result: Dict[str, Any] = {
        "case_id": insp_data.get("case_id", "unknown-case"),
        "status": status,
        "skill_name": "scipdf-latex-builder-auditor",
        "deliverables": {
            "main_tex": main_tex_rel,
            "compiled_pdf": compiled_pdf_rel,
            "preview_images": preview_images,
            "changes_diff": "changes.diff",
            "build_manifest_json": "build_manifest.json",
            "reproduce_script": "reproduce_build.sh",
            "audit_report_md": "audit_report.md",
        },
        "summary": {
            "engine_used": chosen_engine,
            "compiled_successfully": compiled_ok,
            "converged": compile_result["converged"],
            "static_errors": static_after["static_error_count"],
            "compile_fatal_errors": log_sum["fatal_error_count"],
            "unresolved_citations": log_sum["unresolved_citations"],
            "unresolved_references": log_sum["unresolved_references"],
            "overfull_hbox_over_10pt": log_sum["overfull_hbox_over_10pt"],
        },
        "sources": sources,
        "uncertainty": uncertainties,
        "change_records": change_records,
    }

    contract = insp_data.get("output_contract") or {}
    req_fields = contract.get("required_fields") if isinstance(contract, dict) else None
    if isinstance(req_fields, list):
        for field in req_fields:
            if isinstance(field, str) and field not in result:
                result[field] = None

    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": status,
                "engine_used": chosen_engine,
                "compiled_successfully": compiled_ok,
                "static_errors": static_after["static_error_count"],
                "compile_fatal_errors": log_sum["fatal_error_count"],
                "output_dir": str(out_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0 if (clean_static and log_sum["fatal_error_count"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
