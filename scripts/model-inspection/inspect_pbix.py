"""Inspect local PBIX metadata without decoding rows or executing DAX.

Outputs are private planning evidence under the ignored artifacts directory.
PBIXRay is a third-party, read-only parser, not a Microsoft-supported model engine.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from pbixray import PBIXRay


def decode_text(value: bytes) -> str:
    """Handle the mixed UTF-8/UTF-16 encodings used inside this PBIX."""
    if value.startswith((b"\xff\xfe", b"\xfe\xff")):
        return value.decode("utf-16")
    return value.decode("utf-16-le" if value[1:2] == b"\x00" else "utf-8-sig")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def collect_references(value: Any, references: set[tuple[str, str, str]]) -> None:
    if isinstance(value, dict):
        for kind in ("Column", "Measure", "Hierarchy", "HierarchyLevel"):
            reference = value.get(kind)
            if isinstance(reference, dict):
                entity = reference.get("Expression", {}).get("SourceRef", {}).get("Entity")
                name = reference.get("Property") or reference.get("Hierarchy")
                if entity and name:
                    references.add((kind, entity, name))
        for child in value.values():
            collect_references(child, references)
    elif isinstance(value, list):
        for child in value:
            collect_references(child, references)


def inspect_report(source: Path) -> dict[str, Any]:
    with ZipFile(source) as archive:
        names = archive.namelist()
        pages = []
        references: set[tuple[str, str, str]] = set()
        visual_types: dict[str, int] = {}
        for name in names:
            if re.fullmatch(r"Report/definition/pages/[^/]+/page.json", name):
                page = json.loads(decode_text(archive.read(name)))
                prefix = name.removesuffix("page.json") + "visuals/"
                visuals = [entry for entry in names if entry.startswith(prefix) and entry.endswith("/visual.json")]
                pages.append({"name": page.get("name"), "display_name": page.get("displayName"),
                              "visibility": page.get("visibility", "default"), "visual_count": len(visuals)})
                collect_references(page.get("filterConfig", {}), references)
                for visual_name in visuals:
                    visual = json.loads(decode_text(archive.read(visual_name)))
                    visual_type = visual.get("visual", {}).get("visualType", "unknown")
                    visual_types[visual_type] = visual_types.get(visual_type, 0) + 1
                    collect_references(visual, references)
        copilot = {}
        for name in names:
            if name.startswith("Copilot/") and not name.endswith("version.json"):
                text = decode_text(archive.read(name))
                copilot[name] = json.loads(text) if name.endswith(".json") else text
        return {
            "archive_entries": [{"name": entry.filename, "size_bytes": entry.file_size} for entry in archive.infolist()],
            "connections": json.loads(decode_text(archive.read("Connections"))) if "Connections" in names else {},
            "pages": pages,
            "visual_types": visual_types,
            "references": [{"kind": kind, "table": table, "name": name} for kind, table, name in sorted(references)],
            "copilot": copilot,
        }


def inspect_model(source: Path) -> dict[str, Any]:
    # Explicit selection excludes connection strings, role memberships, and raw M.
    selections = {
        "tmschema_model": ["Name", "Description", "Culture", "DefaultMode", "CompatibilityLevel"],
        "tmschema_tables": ["ID", "Name", "Description", "IsHidden", "DataCategory", "LineageTag"],
        "tmschema_columns": ["ID", "TableID", "TableName", "Name", "ExplicitName", "InferredName", "DataType", "ExplicitDataType", "InferredDataType", "Type", "IsHidden", "IsKey", "IsUnique", "IsNullable", "Description", "FormatString", "SummarizeBy", "SortByColumnID", "SourceColumn", "Expression", "DisplayFolder", "DataCategory", "LineageTag"],
        "tmschema_partitions": ["TableID", "TableName", "Name", "Mode", "Type", "State"],
        "tmschema_hierarchies": ["ID", "TableID", "TableName", "Name", "Description", "IsHidden"],
        "tmschema_levels": ["HierarchyID", "Name", "ColumnID", "Ordinal"],
        "tmschema_calculation_groups": None,
        "tmschema_calculation_items": None,
        "dax_measures": None,
        "dax_columns": None,
        "dax_tables": None,
        "schema": None,
        "relationships": None,
        "rls": None,
        "ols": None,
    }
    output: dict[str, Any] = {"extraction_issues": [], "property_columns": {}, "internal_metadata_excluded": {}}
    with PBIXRay(str(source), on_disk=True) as model:
        output["tables"] = [str(name) for name in model.tables]
        for property_name, allowed_columns in selections.items():
            try:
                frame = getattr(model, property_name)
                output["property_columns"][property_name] = list(frame.columns)
                if property_name in {"tmschema_columns", "tmschema_partitions"}:
                    # Raw endpoints also include H$/R$ storage structures. These
                    # are not logical tables and must never enter the MCP schema.
                    raw_count = len(frame)
                    frame = frame.loc[frame["TableName"].isin(output["tables"])]
                    output["internal_metadata_excluded"][property_name] = raw_count - len(frame)
                if allowed_columns is not None:
                    frame = frame.loc[:, [column for column in allowed_columns if column in frame.columns]]
                output[property_name] = json.loads(frame.to_json(orient="records", date_format="iso"))
            except Exception as error:
                output["extraction_issues"].append({"property": property_name, "error_type": type(error).__name__, "message": str(error)})
                output[property_name] = None
        try:
            output["source_bindings"] = []
            for row in model.power_query.to_dict(orient="records"):
                expression = str(row.get("Expression", ""))
                output["source_bindings"].append({
                    "table": row.get("TableName"),
                    "m_sha256": hashlib.sha256(expression.encode("utf-8")).hexdigest(),
                    "schema_items": [list(pair) for pair in re.findall(r'Schema\s*=\s*"([^"]+)"\s*,\s*Item\s*=\s*"([^"]+)"', expression)],
                    "source_kinds": sorted(set(re.findall(r"\b(Sql\.Database|Sql\.Databases|Lakehouse\.Contents|Warehouse\.Contents|Excel\.Workbook|Csv\.Document|Table\.FromRows)\s*\(", expression))),
                })
        except Exception as error:
            output["extraction_issues"].append({"property": "source_bindings", "error_type": type(error).__name__, "message": str(error)})
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/pbix-inspection"))
    arguments = parser.parse_args()
    source = arguments.source.resolve(strict=True)
    arguments.output.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    print("Reading archive and report metadata...", flush=True)
    report = inspect_report(source)
    print("Reading model metadata only; no row extraction or query execution...", flush=True)
    model = inspect_model(source)
    columns = model.get("schema") or []
    tables = model.get("tmschema_tables") or []
    measures = model.get("dax_measures") or []
    relationships = model.get("relationships") or []
    summary = {
        "inspected_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": source.name,
        "source_size_bytes": source.stat().st_size,
        "source_sha256": source_hash,
        "python_version": sys.version,
        "pbixray_version": importlib.metadata.version("pbixray"),
        "row_data_extracted": False,
        "dax_executed": False,
        "extraction_issues": model["extraction_issues"],
        "internal_metadata_excluded": model["internal_metadata_excluded"],
        "table_count": len(model["tables"]),
        "column_count": len(columns),
        "measure_count": len(measures),
        "relationship_count": len(relationships),
        "page_count": len(report["pages"]),
        "visual_count": sum(page["visual_count"] for page in report["pages"]),
        "tables": [{"name": name,
                    "column_count": sum(column.get("TableName") == name for column in columns),
                    "measure_count": sum(measure.get("TableName") == name for measure in measures),
                    "description": next((table.get("Description") for table in tables if table.get("Name") == name), None)}
                   for name in model["tables"]],
    }
    write_json(arguments.output / "model-metadata.json", model)
    write_json(arguments.output / "report-metadata.json", report)
    write_json(arguments.output / "inspection-summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()