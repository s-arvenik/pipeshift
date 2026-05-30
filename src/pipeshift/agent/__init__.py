# Copyright 2026 PipeShift Contributors
# SPDX-License-Identifier: Apache-2.0
"""PipeShift Bedrock Agent — Lambda handler for Bedrock Agent action groups."""

import json
import tempfile
import zipfile
import io
import os
from pathlib import Path
from typing import Any, Dict

import boto3

from pipeshift.parser.informatica_xml import parse_file
from pipeshift.generator import generate_dbt_project
from pipeshift.orchestration import generate_step_functions_json
from pipeshift.scorer import classify_mapping, score_mapping, score_repository
from pipeshift.analyzer import build_dependency_graph


s3 = boto3.client("s3")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Bedrock Agent Lambda handler.

    Bedrock Agents invoke this Lambda with an action group event containing:
    - actionGroup: name of the action group
    - function: name of the function to invoke
    - parameters: list of {name, value} dicts
    """
    function = event.get("function", "")
    params = _extract_params(event)

    try:
        if function == "analyze_estate":
            result = _tool_analyze(params)
        elif function == "convert_mappings":
            result = _tool_convert(params)
        elif function == "explain_decision":
            result = _tool_explain(params)
        else:
            result = {"error": f"Unknown function: {function}"}
    except Exception as e:
        result = {"error": str(e)}

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", ""),
            "function": function,
            "functionResponse": {
                "responseBody": {
                    "TEXT": {"body": json.dumps(result, indent=2)}
                }
            },
        },
    }


def _tool_analyze(params: Dict[str, str]) -> Dict[str, Any]:
    """Analyze an Informatica export and return a migration assessment."""
    xml_path = _resolve_input(params.get("s3_uri", ""))

    repo = parse_file(xml_path)
    scoring = score_repository(repo.mappings)

    transform_types: Dict[str, int] = {}
    for m in repo.mappings:
        for t in m.transforms:
            transform_types[t.type.value] = transform_types.get(t.type.value, 0) + 1

    dep_graph = build_dependency_graph(repo)

    return {
        "repository": repo.name,
        "folder": repo.folder,
        "summary": {
            "mappings": len(repo.mappings),
            "transforms": sum(len(m.transforms) for m in repo.mappings),
            "workflows": len(repo.workflows),
            "sources": len(repo.sources),
            "targets": len(repo.targets),
        },
        "complexity": scoring["summary"],
        "effort_estimate": scoring.get("effort_estimate", {}),
        "transform_types": transform_types,
        "dependency_graph": dep_graph,
        "mappings": [
            {
                "name": m.name,
                "description": m.description,
                "transforms": len(m.transforms),
                "score": score_mapping(m),
                "complexity": classify_mapping(m).value,
            }
            for m in repo.mappings
        ],
    }


def _tool_convert(params: Dict[str, str]) -> Dict[str, Any]:
    """Convert Informatica export to dbt project, upload to S3."""
    xml_path = _resolve_input(params.get("s3_uri", ""))
    output_bucket = params.get("output_bucket", os.environ.get("OUTPUT_BUCKET", ""))
    output_prefix = params.get("output_prefix", "pipeshift-output/")

    repo = parse_file(xml_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "dbt_project"
        generated = generate_dbt_project(repo, output_dir)

        # Generate orchestration
        for wf in repo.workflows:
            sf_path = output_dir / "orchestration" / f"{wf.name}.asl.json"
            sf_path.parent.mkdir(parents=True, exist_ok=True)
            sf_path.write_text(generate_step_functions_json(wf))
            generated.append(sf_path)

        # Upload individual files to S3
        uploaded = []
        for f in generated:
            rel = f.relative_to(output_dir)
            key = f"{output_prefix}{rel}"
            s3.upload_file(str(f), output_bucket, key)
            uploaded.append(f"s3://{output_bucket}/{key}")

        # Also create a zip archive
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in generated:
                zf.write(f, f.relative_to(output_dir))
        zip_buffer.seek(0)
        zip_key = f"{output_prefix}dbt_project.zip"
        s3.upload_fileobj(zip_buffer, output_bucket, zip_key)

    return {
        "status": "success",
        "files_generated": len(generated),
        "output_location": f"s3://{output_bucket}/{output_prefix}",
        "zip_archive": f"s3://{output_bucket}/{zip_key}",
        "mappings_converted": [m.name for m in repo.mappings],
        "workflows_converted": [w.name for w in repo.workflows],
    }


def _tool_explain(params: Dict[str, str]) -> Dict[str, Any]:
    """Explain why a mapping received a specific complexity score or flag."""
    xml_path = _resolve_input(params.get("s3_uri", ""))
    mapping_name = params.get("mapping_name", "")

    repo = parse_file(xml_path)
    mapping = next((m for m in repo.mappings if m.name == mapping_name), None)

    if not mapping:
        available = [m.name for m in repo.mappings]
        return {"error": f"Mapping '{mapping_name}' not found. Available: {available}"}

    score = score_mapping(mapping)
    complexity = classify_mapping(mapping).value

    # Build explanation
    transform_breakdown = {}
    for t in mapping.transforms:
        transform_breakdown[t.type.value] = transform_breakdown.get(t.type.value, 0) + 1

    flags = []
    for t in mapping.transforms:
        if t.type.value == "custom":
            flags.append(f"Custom/unsupported transform: {t.name}")
        elif t.type.value == "java":
            flags.append(f"Java transformation (requires manual rewrite): {t.name}")
        elif t.type.value == "stored_procedure":
            flags.append(f"Stored procedure (requires manual migration): {t.name}")

    # Check for low-confidence expressions
    from pipeshift.translator import translate_expression
    low_confidence = []
    for t in mapping.transforms:
        for expr in t.expressions:
            translated = translate_expression(expr.expression)
            if translated == expr.expression and len(expr.expression) > 10:
                low_confidence.append({
                    "field": expr.output_field,
                    "expression": expr.expression,
                    "reason": "Expression passed through untranslated",
                })

    return {
        "mapping": mapping_name,
        "score": score,
        "complexity": complexity,
        "transform_count": len(mapping.transforms),
        "transform_breakdown": transform_breakdown,
        "risk_flags": flags,
        "low_confidence_expressions": low_confidence[:10],
        "explanation": _build_explanation(score, complexity, transform_breakdown, flags),
    }


def _build_explanation(score: float, complexity: str, breakdown: Dict, flags: list) -> str:
    """Generate a human-readable explanation of the complexity assessment."""
    parts = [f"This mapping scored {score} ({complexity})."]

    if complexity == "simple":
        parts.append("It uses standard transforms that map directly to SQL.")
    elif complexity == "medium":
        parts.append("It has moderate complexity with lookups or aggregations that need careful JOIN logic.")
    elif complexity == "complex":
        parts.append("It has high complexity due to multiple chained transforms, routers, or advanced patterns.")
    elif complexity == "manual":
        parts.append("It requires manual intervention due to unsupported transforms.")

    if flags:
        parts.append(f"Risk flags: {len(flags)} transforms need manual review.")

    return " ".join(parts)


def _resolve_input(s3_uri: str) -> Path:
    """Download S3 object to a temp file and return the local path."""
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"Expected s3:// URI, got: {s3_uri}")

    parts = s3_uri.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""

    tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
    s3.download_file(bucket, key, tmp.name)
    return Path(tmp.name)


def _extract_params(event: Dict[str, Any]) -> Dict[str, str]:
    """Extract parameters from Bedrock Agent event format."""
    params = {}
    for p in event.get("parameters", []):
        params[p["name"]] = p.get("value", "")
    return params
