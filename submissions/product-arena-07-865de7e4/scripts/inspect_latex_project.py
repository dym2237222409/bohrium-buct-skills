#!/usr/bin/env python3
"""Inspect LaTeX project tree, probe TeX toolchain, and run pre-compile static audit.

Pure Python stdlib (Python 3.8+).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


SKILL_ROOT = Path(__file__).resolve().parent.parent


def safe_read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return ""


def strip_tex_comments(line: str) -> str:
    """Remove unescaped % comments from a single TeX line."""
    i = 0
    while i < len(line):
        if line[i] == "%":
            # Count preceding backslashes
            bs = 0
            j = i - 1
            while j >= 0 and line[j] == "\\":
                bs += 1
                j -= 1
            if bs % 2 == 0:
                return line[:i]
        i += 1
    return line


def clean_tex(text: str) -> str:
    return "\n".join(strip_tex_comments(ln) for ln in text.splitlines())


def probe_tool(cmd: str) -> Dict[str, Any]:
    exe = shutil.which(cmd)
    if not exe:
        return {"available": False, "path": None, "version": None}
    ver = None
    for flag in ("--version", "-version", "-v"):
        try:
            proc = subprocess.run([exe, flag], capture_output=True, text=True, timeout=5, check=False)
            out = (proc.stdout or proc.stderr or "").strip()
            if out:
                ver = out.splitlines()[0][:140]
                break
        except Exception:
            continue
    return {"available": True, "path": exe, "version": ver}


def probe_environment() -> Dict[str, Any]:
    tools = {}
    for name in ("pdflatex", "xelatex", "lualatex", "bibtex", "biber", "latexmk", "kpsewhich", "pdftoppm", "gs", "pdftotext"):
        tools[name] = probe_tool(name)
    fitz_ok = False
    try:
        import fitz  # type: ignore  # noqa: F401

        fitz_ok = True
    except Exception:
        pass
    tools["pymupdf_fitz"] = {"available": fitz_ok, "path": "python-module" if fitz_ok else None, "version": None}
    return tools


def parse_bib_keys(bib_text: str) -> Set[str]:
    keys: Set[str] = set()
    for m in re.finditer(r"@([A-Za-z]+)\s*\{\s*([^,\s]+)\s*,", bib_text):
        if m.group(1).lower() not in {"comment", "preamble", "string"}:
            keys.add(m.group(2).strip())
    return keys


def count_tabular_columns(spec: str) -> Optional[int]:
    """Parse a LaTeX tabular column specification into total column count."""
    # Expand *{N}{sub} iteratively
    s = spec.strip()
    for _ in range(5):
        m = re.search(r"\*\{(\d+)\}\{([^{}]*)\}", s)
        if not m:
            break
        s = s[: m.start()] + (m.group(2) * int(m.group(1))) + s[m.end() :]

    # Strip @{...}, !{...}, >{...}, <{...}
    def strip_arg_prefixes(raw: str) -> str:
        out: List[str] = []
        i = 0
        while i < len(raw):
            if raw[i] in "@!><" and i + 1 < len(raw) and raw[i + 1] == "{":
                i += 2
                depth = 1
                while i < len(raw) and depth > 0:
                    if raw[i] == "{":
                        depth += 1
                    elif raw[i] == "}":
                        depth -= 1
                    i += 1
                continue
            out.append(raw[i])
            i += 1
        return "".join(out)

    s = strip_arg_prefixes(s)
    # Count column tokens: l, c, r, X, S, L, C, R, p{...}, m{...}, b{...}
    cols = 0
    i = 0
    while i < len(s):
        ch = s[i]
        if ch in "lcrXSLCR":
            cols += 1
            i += 1
        elif ch in "pmb" and i + 1 < len(s) and s[i + 1] == "{":
            cols += 1
            i += 2
            depth = 1
            while i < len(s) and depth > 0:
                if s[i] == "{":
                    depth += 1
                elif s[i] == "}":
                    depth -= 1
                i += 1
        else:
            i += 1
    return cols if cols > 0 else None


def check_environments(lines: List[str], rel_path: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    stack: List[Tuple[str, int]] = []
    token_re = re.compile(r"\\(begin|end)\{([^}]+)\}")
    for lineno, raw in enumerate(lines, start=1):
        line = strip_tex_comments(raw)
        for m in token_re.finditer(line):
            kind, env = m.group(1), m.group(2).strip()
            if kind == "begin":
                stack.append((env, lineno))
            else:
                if not stack:
                    issues.append(
                        {
                            "category": "environment_mismatch",
                            "severity": "error",
                            "file": rel_path,
                            "line": lineno,
                            "message": f"Unexpected \\end{{{env}}} with no matching \\begin{{{env}}}.",
                        }
                    )
                elif stack[-1][0] == env:
                    stack.pop()
                else:
                    # Search stack for matching env to avoid cascade
                    matched_idx = None
                    for idx in range(len(stack) - 1, -1, -1):
                        if stack[idx][0] == env:
                            matched_idx = idx
                            break
                    if matched_idx is not None:
                        for unclosed_env, unclosed_line in reversed(stack[matched_idx + 1 :]):
                            issues.append(
                                {
                                    "category": "environment_mismatch",
                                    "severity": "error",
                                    "file": rel_path,
                                    "line": unclosed_line,
                                    "message": f"Unclosed \\begin{{{unclosed_env}}} (ended prematurely by \\end{{{env}}} on line {lineno}).",
                                }
                            )
                        stack = stack[:matched_idx]
                    else:
                        top_env, top_line = stack[-1]
                        issues.append(
                            {
                                "category": "environment_mismatch",
                                "severity": "error",
                                "file": rel_path,
                                "line": lineno,
                                "message": f"\\begin{{{top_env}}} on line {top_line} ended by mismatched \\end{{{env}}}.",
                            }
                        )
    for env, lineno in stack:
        issues.append(
            {
                "category": "environment_mismatch",
                "severity": "error",
                "file": rel_path,
                "line": lineno,
                "message": f"Unclosed \\begin{{{env}}} reaching end of file.",
            }
        )
    return issues


def check_tabular_columns(text: str, rel_path: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    # Match \begin{tabular}{spec} or \begin{tabularx}{width}{spec}
    tab_re = re.compile(
        r"\\begin\{(tabular\*?|tabularx|longtable)\}(?:\[[^\]]*\])?(?:\{[^}]*\})?\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
        re.MULTILINE,
    )
    for m in tab_re.finditer(text):
        env_name = m.group(1)
        spec = m.group(2)
        if env_name in {"tabularx", "tabular*"}:
            # Already matched optional width via (?:\{[^}]*\})?; verify spec looks like cols
            pass
        expected_cols = count_tabular_columns(spec)
        if not expected_cols:
            continue
        end_pat = re.compile(r"\\end\{" + re.escape(env_name) + r"\}")
        end_m = end_pat.search(text, m.end())
        if not end_m:
            continue
        body = text[m.end() : end_m.start()]
        start_line = text[: m.end()].count("\n") + 1
        # Split rows on \\ (ignoring \hline/\toprule/\midrule/\bottomrule-only fragments)
        raw_rows = re.split(r"\\\\(?:\s*\[[^\]]*\])?", body)
        cur_line = start_line
        for r in raw_rows:
            cleaned = "\n".join(strip_tex_comments(ln) for ln in r.splitlines())
            # Remove rule commands
            stripped = re.sub(
                r"\\(?:toprule|midrule|bottomrule|hline|cline\{[^}]*\}|cmidrule(?:\([^)]*\))?\{[^}]*\}|endhead|endfirsthead|endfoot|endlastfoot|caption\{[^}]*\}|label\{[^}]*\})",
                "",
                cleaned,
            ).strip()
            if stripped and "&" in stripped:
                # Count unescaped &
                amps = len(re.findall(r"(?<!\\)&", stripped))
                extra_span = 0
                for mc in re.finditer(r"\\multicolumn\{(\d+)\}", stripped):
                    span = int(mc.group(1))
                    if span > 1:
                        extra_span += span - 1
                actual_cols = amps + 1 + extra_span
                if actual_cols != expected_cols:
                    issues.append(
                        {
                            "category": "table_column_or_overflow",
                            "severity": "error",
                            "file": rel_path,
                            "line": cur_line,
                            "message": f"Tabular '{env_name}' expects {expected_cols} columns ('{spec}'), but row has {actual_cols} columns.",
                        }
                    )
            cur_line += r.count("\n")
    return issues


def check_packages_and_rules(text: str, rel_path: str, rules: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
    cleaned = clean_tex(text)
    pkg_positions: Dict[str, Tuple[int, int]] = {}
    ordered_pkgs: List[str] = []
    for lineno, line in enumerate(cleaned.splitlines(), start=1):
        for m in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}", line):
            for p in m.group(1).split(","):
                pname = p.strip()
                if pname:
                    ordered_pkgs.append(pname)
                    pkg_positions.setdefault(pname, (len(ordered_pkgs), lineno))

    issues: List[Dict[str, Any]] = []
    for rule in rules.get("package_order_rules", []):
        first, second = rule["first"], rule["second"]
        if first in pkg_positions and second in pkg_positions:
            pos1, _ = pkg_positions[first]
            pos2, line2 = pkg_positions[second]
            if pos1 > pos2:
                issues.append(
                    {
                        "category": "package_order_conflict",
                        "severity": rule.get("severity", "warning"),
                        "file": rel_path,
                        "line": line2,
                        "message": f"Package order rule '{rule['rule_id']}': '{first}' loaded after '{second}'. {rule['reason']}",
                    }
                )
    for hack in rules.get("discouraged_table_hacks", []):
        if hack in cleaned:
            issues.append(
                {
                    "category": "table_column_or_overflow",
                    "severity": "warning",
                    "file": rel_path,
                    "line": 1,
                    "message": f"Discouraged table scaling hack '{hack}' found; prefer tabularx/p{{...}}/booktabs.",
                }
            )
    return ordered_pkgs, issues


def run_static_audit(project_dir: Path, rules: Dict[str, Any]) -> Dict[str, Any]:
    tex_files = sorted(project_dir.rglob("*.tex"))
    bib_files = sorted(project_dir.rglob("*.bib"))
    cls_files = sorted(project_dir.rglob("*.cls"))
    sty_files = sorted(project_dir.rglob("*.sty"))

    bib_keys: Set[str] = set()
    for bf in bib_files:
        bib_keys.update(parse_bib_keys(safe_read_text(bf)))

    # Score candidate main .tex files
    main_tex: Optional[Path] = None
    best_score = -1
    for tf in tex_files:
        txt = clean_tex(safe_read_text(tf))
        score = 0
        if "\\documentclass" in txt:
            score += 10
        if "\\begin{document}" in txt:
            score += 10
        if tf.name.lower() in {"main.tex", "paper.tex", "manuscript.tex", "article.tex"}:
            score += 3
        if score > best_score:
            best_score = score
            main_tex = tf

    all_issues: List[Dict[str, Any]] = []
    all_labels: List[Tuple[str, str, int]] = []
    all_refs: List[Tuple[str, str, int]] = []
    all_cites: List[Tuple[str, str, int]] = []
    all_graphics: List[Tuple[str, str, int]] = []
    all_packages: List[str] = []
    has_cjk = False
    needs_xelatex = False
    needs_biber = False

    for tf in tex_files:
        rel = tf.relative_to(project_dir).as_posix()
        raw = safe_read_text(tf)
        cleaned = clean_tex(raw)
        lines = raw.splitlines()

        if re.search(r"[\u4e00-\u9fff]", cleaned):
            has_cjk = True
        if any(ind in cleaned for ind in rules.get("xelatex_indicators", [])):
            needs_xelatex = True
        if any(ind in cleaned for ind in rules.get("biber_indicators", [])):
            needs_biber = True

        all_issues.extend(check_environments(lines, rel))
        all_issues.extend(check_tabular_columns(raw, rel))
        pkgs, pkg_issues = check_packages_and_rules(raw, rel, rules)
        all_packages.extend(pkgs)
        all_issues.extend(pkg_issues)

        for lineno, rline in enumerate(lines, start=1):
            cline = strip_tex_comments(rline)
            for m in re.finditer(r"\\label\{([^}]+)\}", cline):
                all_labels.append((m.group(1).strip(), rel, lineno))
            for m in re.finditer(r"\\(?:eq|page|auto|c|C)?ref\*?\{([^}]+)\}", cline):
                for rkey in m.group(1).split(","):
                    if rkey.strip():
                        all_refs.append((rkey.strip(), rel, lineno))
            for m in re.finditer(r"\\(?:[a-zA-Z]*cite[a-zA-Z*]*)(?:\[[^\]]*\])*\{([^}]+)\}", cline):
                for ckey in m.group(1).split(","):
                    if ckey.strip():
                        all_cites.append((ckey.strip(), rel, lineno))
            for m in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", cline):
                all_graphics.append((m.group(1).strip(), rel, lineno))

    # Duplicate labels
    label_counts = Counter(lbl for lbl, _, _ in all_labels)
    defined_labels = set(label_counts.keys())
    for lbl, cnt in label_counts.items():
        if cnt > 1:
            locs = [f"{f}:{ln}" for l2, f, ln in all_labels if l2 == lbl]
            all_issues.append(
                {
                    "category": "undefined_reference_or_duplicate_label",
                    "severity": "error",
                    "file": locs[0].split(":")[0],
                    "line": int(locs[0].split(":")[1]),
                    "message": f"Duplicate \\label{{{lbl}}} defined {cnt} times at {', '.join(locs)}.",
                }
            )

    # Undefined refs
    for rkey, rel, lineno in all_refs:
        if rkey not in defined_labels:
            all_issues.append(
                {
                    "category": "undefined_reference_or_duplicate_label",
                    "severity": "error",
                    "file": rel,
                    "line": lineno,
                    "message": f"Undefined reference '\\ref{{{rkey}}}' has no matching \\label.",
                }
            )

    # Undefined citations (when .bib exists or bibitem absent)
    bibitem_keys: Set[str] = set()
    for tf in tex_files:
        for m in re.finditer(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", clean_tex(safe_read_text(tf))):
            bibitem_keys.add(m.group(1).strip())
    known_cite_keys = bib_keys | bibitem_keys
    for ckey, rel, lineno in all_cites:
        if known_cite_keys and ckey not in known_cite_keys:
            all_issues.append(
                {
                    "category": "citation_or_bib_error",
                    "severity": "error",
                    "file": rel,
                    "line": lineno,
                    "message": f"Undefined citation key '{ckey}' not found in .bib or \\bibitem entries.",
                }
            )

    # Missing graphic assets
    for gpath, rel, lineno in all_graphics:
        candidates = [project_dir / gpath]
        if not Path(gpath).suffix:
            for ext in (".pdf", ".png", ".jpg", ".jpeg", ".eps"):
                candidates.append(project_dir / f"{gpath}{ext}")
        if not any(c.exists() for c in candidates):
            # Also search by basename inside project_dir
            stem = Path(gpath).name
            if not any(p.name == stem or p.stem == stem for p in project_dir.rglob("*") if p.is_file()):
                all_issues.append(
                    {
                        "category": "missing_figure_asset",
                        "severity": "warning",
                        "file": rel,
                        "line": lineno,
                        "message": f"Graphic asset '{gpath}' referenced by \\includegraphics was not found in project directory.",
                    }
                )

    recommended_engine = "xelatex" if (needs_xelatex or has_cjk) else "pdflatex"
    recommended_bib = "biber" if needs_biber else ("bibtex" if bib_files else None)

    return {
        "main_tex": main_tex.relative_to(project_dir).as_posix() if main_tex else None,
        "tex_files": [p.relative_to(project_dir).as_posix() for p in tex_files],
        "bib_files": [p.relative_to(project_dir).as_posix() for p in bib_files],
        "cls_files": [p.relative_to(project_dir).as_posix() for p in cls_files],
        "sty_files": [p.relative_to(project_dir).as_posix() for p in sty_files],
        "packages_loaded": sorted(set(all_packages)),
        "has_cjk": has_cjk,
        "recommended_engine": recommended_engine,
        "recommended_bib_tool": recommended_bib,
        "bib_key_count": len(known_cite_keys),
        "label_count": len(defined_labels),
        "static_issues": all_issues,
        "static_error_count": sum(1 for i in all_issues if i["severity"] == "error"),
        "static_warning_count": sum(1 for i in all_issues if i["severity"] == "warning"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect LaTeX project, probe toolchain, and run static audit.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"ERROR: input directory not found: {input_dir}", file=sys.stderr)
        return 2

    rules = json.loads((SKILL_ROOT / "resources" / "template_and_package_rules.json").read_text(encoding="utf-8"))
    case_data: Dict[str, Any] = {}
    if (input_dir / "case.json").exists():
        try:
            case_data = json.loads(safe_read_text(input_dir / "case.json"))
        except Exception:
            pass

    env_tools = probe_environment()
    static_audit = run_static_audit(input_dir, rules)

    report = {
        "case_id": str(case_data.get("case_id") or case_data.get("id") or "unknown-case"),
        "output_contract": case_data.get("output_contract") or {},
        "environment": env_tools,
        "project": static_audit,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "case_id": report["case_id"],
                "main_tex": static_audit["main_tex"],
                "recommended_engine": static_audit["recommended_engine"],
                "static_errors": static_audit["static_error_count"],
                "static_warnings": static_audit["static_warning_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
