#!/usr/bin/env python3
"""Extract line-anchored experimental facts, environment specs, and cross-file parameter conflicts.

Pure Python stdlib (Python 3.8+).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SKILL_ROOT = Path(__file__).resolve().parent.parent
SCANNABLE_EXTS = {
    ".yaml", ".yml", ".json", ".jsonl", ".toml", ".ini", ".cfg",
    ".py", ".sh", ".bash", ".log", ".out", ".txt", ".md", ".tex", ".csv", ".tsv",
}


def safe_read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return ""


def normalize_value(val: str) -> str:
    s = val.strip().strip("'\"` ,;)")
    # Try numeric normalization so 1e-4 and 0.0001 compare equal
    try:
        if re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", s):
            num = float(s)
            if num.is_integer() and "e" not in s.lower() and "." not in s:
                return str(int(num))
            return f"{num:.10g}"
    except Exception:
        pass
    return s.lower()


def build_alias_lookup(patterns_cfg: Dict[str, Any]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for canonical, aliases in patterns_cfg.get("canonical_parameter_aliases", {}).items():
        lookup[canonical.lower()] = canonical
        for a in aliases:
            lookup[a.lower()] = canonical
    return lookup


def extract_json_kv(obj: Any, rel_path: str, alias_lookup: Dict[str, str], facts: List[Dict[str, Any]], prefix: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            k_str = str(k)
            full_k = f"{prefix}.{k_str}" if prefix else k_str
            if isinstance(v, (str, int, float, bool)) and v is not None:
                canonical = alias_lookup.get(k_str.lower(), k_str.lower())
                facts.append(
                    {
                        "raw_key": full_k,
                        "canonical_key": canonical,
                        "value": str(v).strip(),
                        "normalized_value": normalize_value(str(v)),
                        "source_file": rel_path,
                        "line": None,
                        "locator": f"{rel_path}:{full_k}",
                    }
                )
            elif isinstance(v, list) and v and all(isinstance(x, (str, int, float)) for x in v) and len(v) <= 20:
                val_str = ", ".join(str(x) for x in v)
                canonical = alias_lookup.get(k_str.lower(), k_str.lower())
                if canonical in set(alias_lookup.values()):
                    facts.append(
                        {
                            "raw_key": full_k,
                            "canonical_key": canonical,
                            "value": val_str,
                            "normalized_value": normalize_value(val_str),
                            "source_file": rel_path,
                            "line": None,
                            "locator": f"{rel_path}:{full_k}",
                        }
                    )
            else:
                extract_json_kv(v, rel_path, alias_lookup, facts, full_k)
    elif isinstance(obj, list):
        for idx, item in enumerate(obj[:50]):
            extract_json_kv(item, rel_path, alias_lookup, facts, f"{prefix}[{idx}]")


def extract_line_kv(text: str, rel_path: str, alias_lookup: Dict[str, str]) -> List[Dict[str, Any]]:
    facts: List[Dict[str, Any]] = []
    # 1. Key: value or key = value (YAML, INI, TOML, Python assignments, logs)
    kv_re = re.compile(
        r"(?:^|[\s,(])([A-Za-z_][A-Za-z0-9_.-]{1,36})\s*(?::|=)\s*([A-Za-z0-9_.+/\-eE]+(?:\s*(?:%|°C|℃|K|bar|MPa|ms|s|min|h|GB|MB))?)"
    )
    # 2. CLI flags: --lr 3e-4 or --batch_size=32 or --seeds 42,43,44
    cli_re = re.compile(r"--([A-Za-z][A-Za-z0-9_-]{1,32})(?:\s+|=)([A-Za-z0-9_.,+-]+)")

    known_aliases = set(alias_lookup.keys())
    is_config_file = Path(rel_path).suffix.lower() in {".yaml", ".yml", ".ini", ".cfg", ".toml"}

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue

        # CLI flags first
        for m in cli_re.finditer(line):
            raw_k = m.group(1).replace("-", "_")
            raw_v = m.group(2).strip().rstrip(",;)")
            if raw_k.lower() in known_aliases:
                canonical = alias_lookup[raw_k.lower()]
                facts.append(
                    {
                        "raw_key": f"--{m.group(1)}",
                        "canonical_key": canonical,
                        "value": raw_v,
                        "normalized_value": normalize_value(raw_v),
                        "source_file": rel_path,
                        "line": lineno,
                        "locator": f"{rel_path}:{lineno}",
                    }
                )

        # Standard key: val / key = val
        for m in kv_re.finditer(line):
            raw_k = m.group(1).strip()
            raw_v = m.group(2).strip().rstrip(",;)")
            if raw_v.lower() in {"true", "false", "none", "null"} and not is_config_file:
                continue
            key_low = raw_k.lower().split(".")[-1]
            if key_low in known_aliases or (is_config_file and len(raw_v) <= 40 and not raw_v.startswith("http")):
                canonical = alias_lookup.get(key_low, key_low)
                facts.append(
                    {
                        "raw_key": raw_k,
                        "canonical_key": canonical,
                        "value": raw_v,
                        "normalized_value": normalize_value(raw_v),
                        "source_file": rel_path,
                        "line": lineno,
                        "locator": f"{rel_path}:{lineno}",
                    }
                )
    return facts


def extract_env_and_cli(text: str, rel_path: str, env_patterns: Dict[str, List[str]]) -> Dict[str, List[Dict[str, str]]]:
    found: Dict[str, List[Dict[str, str]]] = {k: [] for k in env_patterns.keys()}
    for cat, pats in env_patterns.items():
        seen = set()
        for pat in pats:
            for m in re.finditer(pat, text, re.IGNORECASE):
                val = m.group(0).strip()
                if val and val not in seen:
                    seen.add(val)
                    found[cat].append({"value": val, "source_file": rel_path})
    return found


def detect_conflicts(facts: List[Dict[str, Any]], canonical_keys_of_interest: set) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for f in facts:
        ck = f["canonical_key"]
        if ck in canonical_keys_of_interest:
            grouped[ck][f["normalized_value"]].append(f)

    conflicts: List[Dict[str, Any]] = []
    for ck, val_buckets in sorted(grouped.items()):
        if len(val_buckets) > 1:
            entries = []
            for norm_v, items in val_buckets.items():
                entries.append(
                    {
                        "normalized_value": norm_v,
                        "raw_values": sorted({it["value"] for it in items}),
                        "locators": sorted({it["locator"] for it in items}),
                    }
                )
            conflicts.append(
                {
                    "canonical_key": ck,
                    "distinct_value_count": len(val_buckets),
                    "entries": entries,
                }
            )
    return conflicts


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract experimental facts & cross-file conflicts from /input.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"ERROR: input directory not found: {input_dir}", file=sys.stderr)
        return 2

    patterns_cfg = json.loads((SKILL_ROOT / "resources" / "config_extraction_patterns.json").read_text(encoding="utf-8"))
    alias_lookup = build_alias_lookup(patterns_cfg)
    canonical_set = set(alias_lookup.values())

    case_data: Dict[str, Any] = {}
    task_md = ""
    warnings: List[str] = []

    if (input_dir / "case.json").exists():
        try:
            case_data = json.loads(safe_read_text(input_dir / "case.json"))
        except Exception as exc:
            warnings.append(f"Failed to parse case.json: {exc}")
    else:
        warnings.append("case.json not found; using fallback defaults.")

    if (input_dir / "task.md").exists():
        task_md = safe_read_text(input_dir / "task.md")

    files_indexed: List[Dict[str, Any]] = []
    all_facts: List[Dict[str, Any]] = []
    env_agg: Dict[str, List[Dict[str, str]]] = {k: [] for k in patterns_cfg.get("environment_patterns", {}).keys()}
    all_numeric_tokens = set()

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(input_dir).as_posix()
        ext = path.suffix.lower()
        files_indexed.append({"path": rel, "ext": ext, "size_bytes": path.stat().st_size})
        if ext not in SCANNABLE_EXTS:
            continue
        text = safe_read_text(path)
        if path.name in {"case.json", "task.md"}:
            continue

        # Collect numeric tokens for grounding
        for nm in re.findall(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text):
            all_numeric_tokens.add(nm)
            all_numeric_tokens.add(normalize_value(nm))

        if ext == ".json":
            try:
                extract_json_kv(json.loads(text), rel, alias_lookup, all_facts)
            except Exception as exc:
                warnings.append(f"JSON parse failed for {rel}: {exc}")
        elif ext == ".jsonl":
            for line in text.splitlines():
                if line.strip():
                    try:
                        extract_json_kv(json.loads(line), rel, alias_lookup, all_facts)
                    except Exception:
                        pass
        else:
            all_facts.extend(extract_line_kv(text, rel, alias_lookup))

        env_found = extract_env_and_cli(text, rel, patterns_cfg.get("environment_patterns", {}))
        for cat, items in env_found.items():
            env_agg[cat].extend(items)

    conflicts = detect_conflicts(all_facts, canonical_set)

    # Identify missing hints
    found_keys = {f["canonical_key"] for f in all_facts}
    missing_hints: List[str] = []
    if "seed" not in found_keys:
        missing_hints.append("random_seed not detected in configs/logs -> verify or query author")
    if not env_agg.get("hardware"):
        missing_hints.append("hardware (GPU/CPU) spec not detected -> verify or query author")
    if not env_agg.get("software"):
        missing_hints.append("software/framework versions not detected -> verify or query author")
    if not env_agg.get("dataset_version"):
        missing_hints.append("explicit dataset version tag not detected -> verify or query author")
    if not env_agg.get("cli_command"):
        missing_hints.append("runnable CLI command not detected -> verify or query author")

    lang = case_data.get("language")
    if not lang:
        zh_n = len(re.findall(r"[\u4e00-\u9fa5]", task_md))
        en_n = len(re.findall(r"\b[A-Za-z]{3,}\b", task_md))
        lang = "zh" if zh_n > en_n else "en"

    output = {
        "case_id": str(case_data.get("case_id") or case_data.get("id") or "unknown-case"),
        "language": lang,
        "output_contract": case_data.get("output_contract") or {},
        "files_indexed": files_indexed,
        "parameter_facts": all_facts,
        "cross_file_conflicts": conflicts,
        "environment_and_hardware": env_agg,
        "missing_element_hints": missing_hints,
        "all_numeric_tokens": sorted(all_numeric_tokens)[:1500],
        "input_warnings": warnings,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "case_id": output["case_id"],
                "files_indexed": len(files_indexed),
                "parameter_facts": len(all_facts),
                "cross_file_conflicts": len(conflicts),
                "missing_hints": len(missing_hints),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
