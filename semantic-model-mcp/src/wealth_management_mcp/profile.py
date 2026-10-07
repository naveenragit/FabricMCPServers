"""Offline, definition-complete, private-author OSSIE-derived Fabric/DAX catalog.

No query execution, network validation, live binding check, RLS implementation,
or per-user disclosure is performed. Only explicitly mapped metadata is copied;
role expressions/memberships, M queries, source bindings, credentials, extraction
error text and local paths are never serialized. Free-text metadata and overlays
still require author review before disclosure; this is not a secret detector.

Optional overlay (strict JSON or safe YAML, duplicate keys rejected)::

    version: "1.0.0"
    objects:
      datasets/sample/fields/amount:
        description: Reviewed row-level meaning.
        synonyms: [value]
        instructions: Select a reporting period before aggregating.
      metrics/amount_pct:
        description: Reviewed measure meaning.

Canonical paths are ``model/sm_wealth_mgmt_import``, ``datasets/<name>``,
``datasets/<name>/fields/<name>``, ``metrics/<name>`` and
``relationships/<name>``. Only description, synonyms and instructions are
accepted. Unknown paths/keys, empty entries and case-folded duplicate synonyms
fail. Overlays cannot rename objects or modify DAX, native bindings or security.
Relationship descriptions live in POWER_BI.reviewed_description because upstream
Relationship has no description property. Other descriptions use OSSIE fields.

Catalog hashes cover the entire JSON envelope except catalog_sha256, including
source-byte fingerprints, warnings and provenance. No clock or generated timestamp
is used. canonical_json_bytes returns immutable bytes, rejects non-JSON values and
does not mutate its input; it is a project canonicalization, not RFC 8785/JCS.
Source-byte changes (including source export timestamps) change source fingerprints.
validate_catalog proves internal consistency, not authenticity of an untrusted
resealed catalog or DAX syntax/execution. build_catalog additionally copies and
checks formulas against the supplied source text without whitespace rewriting.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry

from .names import NameRegistry, dax_column_reference, dax_measure_reference, normalize_name
from .schemas import BASE_SHA256, PROFILE_ID, PROFILE_VERSION, rules_schema

MODEL_NAME = "sm_wealth_mgmt_import"
_TYPE_NAMES = {2: "String", 6: "Int64", 8: "Double", 9: "DateTime", 10: "Decimal", 11: "Boolean"}
_LOGICAL_TYPES = {
    "String": "String", "Int64": "Integer", "Double": "Float", "DateTime": "DateTime",
    "Decimal": "Decimal", "Boolean": "Boolean", "Binary": "Opaque",
}
_FILTERS = {"Single": "one_direction", "Both": "both_directions"}


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON-only values deterministically to immutable UTF-8 bytes."""
    ancestors: set[int] = set()

    def check(item: Any) -> None:
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) not in (dict, list):
            raise ValueError("Canonical content must contain JSON-only values (no NaN/Infinity)")
        if id(item) in ancestors:
            raise ValueError("Cyclic JSON content")
        ancestors.add(id(item))
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise ValueError("JSON object keys must be strings")
            children = item.values()
        else:
            children = item
        for child in children:
            check(child)
        ancestors.remove(id(item))

    try:
        check(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("Invalid or excessively nested JSON content") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def compute_catalog_sha256(catalog: dict[str, Any]) -> str:
    """Hash all envelope fields except the digest itself; no fields are mutated."""
    return canonical_sha256({key: value for key, value in catalog.items() if key != "catalog_sha256"})


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON/YAML object key")
        result[key] = value
    return result


def _json(text: str | bytes) -> Any:
    def invalid_constant(_: str) -> None:
        raise ValueError("Nonfinite JSON number")

    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=invalid_constant)
        canonical_json_bytes(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Invalid JSON document") from exc


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = _json(raw)
    if type(value) is not dict:
        raise ValueError("Expected a JSON object at the document root")
    return value, hashlib.sha256(raw).hexdigest()


def _local_validator(schema: dict[str, Any]) -> Draft202012Validator:
    def check(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("$ref", "$dynamicRef") and (not isinstance(value, str) or not value.startswith("#")):
                    raise ValueError("Validation schemas may only use document-local references")
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    # An empty registry rejects retrieval; the preflight also forbids external refs.
    return Draft202012Validator(schema, registry=Registry())


def load_base_schema() -> dict[str, Any]:
    """Reconstruct pinned LF source per the manifest and verify upstream bytes.

    Only editor newline conversion is tolerated; no JSON reformatting is hidden.
    """
    raw = files("wealth_management_mcp.schemas").joinpath("ossie-schema.json").read_bytes()
    raw = raw.replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"
    if hashlib.sha256(raw).hexdigest() != BASE_SHA256:
        raise ValueError("Vendored OSSIE schema SHA-256 mismatch")
    return _json(raw)


def load_profile_schema() -> dict[str, Any]:
    """Fresh minimal derivative; see schemas/PROVENANCE.md for the reviewed delta."""
    schema = load_base_schema()
    schema["$id"] = PROFILE_ID
    schema["title"] = "OSSIE-derived Fabric/DAX Profile 1.0.0"
    schema["description"] = "Project-owned derivative of pinned Apache Ossie; not unmodified upstream conformance."
    schema["properties"]["version"]["const"] = PROFILE_VERSION
    schema["properties"]["version"]["description"] = "Project Fabric/DAX profile version"
    schema["$defs"]["Dialect"]["enum"].append("DAX")
    return schema


def load_extension_schema(kind: str) -> dict[str, Any]:
    """Load the strict decoded extension schema (model/dataset/field/metric/relationship)."""
    if kind not in {"model", "dataset", "field", "metric", "relationship"}:
        raise ValueError("Unknown POWER_BI extension kind")
    return {**rules_schema(), "$ref": f"#/$defs/{kind}"}


def _validate(value: Any, validator: Draft202012Validator, label: str) -> None:
    error = next(validator.iter_errors(value), None)
    if error is not None:
        # Do not echo values: validation errors may otherwise disclose formula/overlay text.
        location = "/".join(str(part) for part in error.absolute_path)
        raise ValueError(f"{label}/{location}: schema constraint {error.validator} failed")


def _rule_validator(name: str) -> Draft202012Validator:
    return _local_validator({**rules_schema(), "$ref": f"#/$defs/{name}"})


def _rows(metadata: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = metadata.get(key)
    if not isinstance(value, list) or any(type(row) is not dict for row in value):
        raise ValueError(f"Required metadata inventory missing or malformed: {key}")
    return value


def _text(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"Nonblank string required: {label}")
    return value


def _flag(value: Any, label: str, *, required: bool = False) -> bool | None:
    if value is None and not required:
        return None
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise ValueError(f"Unknown or invalid native Boolean: {label}")


def _import_mode(value: Any) -> None:
    if not (type(value) is int and value == 0 or type(value) is str and value == "Import"):
        raise ValueError("Only evidenced Import storage mode is supported; unknown/other modes fail closed")


def _datatype(native_type: Any) -> str | None:
    if native_type is None:
        return None
    if type(native_type) is int:
        native_type = _TYPE_NAMES.get(native_type)
        if native_type is None:
            raise ValueError("Unsupported native type code")
    if type(native_type) is not str or native_type not in _LOGICAL_TYPES:
        raise ValueError("Unsupported native data type")
    return _LOGICAL_TYPES[native_type]


def _typed(node: dict[str, Any], native_type: Any) -> None:
    datatype = _datatype(native_type)
    if datatype is not None:
        node["datatype"] = datatype


def _expression(expression: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": "DAX", "expression": _text(expression, "DAX definition")}]}


def _dax(node: dict[str, Any]) -> str:
    dialects = node["expression"]["dialects"]
    if len(dialects) != 1 or dialects[0]["dialect"] != "DAX":
        raise ValueError("This author profile requires exactly one native DAX expression per field/metric")
    return _text(dialects[0]["expression"], "DAX definition")


def _set_extension(node: dict[str, Any], payload: dict[str, Any]) -> None:
    node["custom_extensions"] = [{"vendor_name": "POWER_BI", "data": canonical_json_bytes(payload).decode("utf-8")}]


def _payload(node: dict[str, Any]) -> dict[str, Any]:
    extensions = node.get("custom_extensions")
    if not isinstance(extensions, list) or len(extensions) != 1 or extensions[0].get("vendor_name") != "POWER_BI":
        raise ValueError("Exactly one object-local POWER_BI extension is required")
    value = _json(extensions[0]["data"])
    if not isinstance(value, dict):
        raise ValueError("POWER_BI data must encode a JSON object")
    return value


def _trace(source: str, output: str, canonical_path: str, digest: str) -> dict[str, Any]:
    return {"source_pointer": source, "output_pointer": output, "canonical_path": canonical_path,
            "artifact_sha256": digest, "mapping_version": PROFILE_VERSION, "disclosure": "private_author_snapshot"}


def _attach(node: dict[str, Any], kind: str, trace: dict[str, Any], **values: Any) -> None:
    _set_extension(node, {"extension_version": PROFILE_VERSION, "kind": kind, "trace": trace, **values})


def _description(node: dict[str, Any], row: dict[str, Any]) -> None:
    if row.get("Description") is not None:
        text = _text(row["Description"], "native description")
        node["description"] = text


class _OverlayLoader(yaml.SafeLoader):
    """Safe YAML without silent duplicate-key replacement or merge-key expansion."""


def _yaml_mapping(loader: _OverlayLoader, node: yaml.MappingNode) -> dict[str, Any]:
    pairs = []
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if type(key) is not str or key == "<<":
            raise ValueError("Overlay mapping keys must be strings; YAML merges are not supported")
        pairs.append((key, loader.construct_object(value_node, deep=True)))
    return _pairs(pairs)


_OverlayLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def _apply_overlay(path: Path | None, nodes: dict[str, dict[str, Any]]) -> str | None:
    if path is None:
        return None
    raw = path.read_bytes()
    try:
        overlay = _json(raw) if path.suffix.lower() == ".json" else yaml.load(raw, Loader=_OverlayLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid safe-YAML overlay") from exc
    canonical_json_bytes(overlay)
    _validate(overlay, _rule_validator("Overlay"), "overlay")
    for canonical_path, values in overlay["objects"].items():
        if canonical_path not in nodes:
            raise ValueError(f"Unknown overlay canonical path: {canonical_path}")
        node = nodes[canonical_path]
        if "description" in values:
            if canonical_path.startswith("relationships/"):
                payload = _payload(node)
                payload["reviewed_description"] = values["description"]
                _set_extension(node, payload)
            else:
                node["description"] = values["description"]
        context = {key: values[key] for key in ("synonyms", "instructions") if key in values}
        if context:
            node.setdefault("ai_context", {}).update(context)
    return hashlib.sha256(raw).hexdigest()


def _coverage(model: dict[str, Any], *, rls: bool, ols: bool) -> dict[str, Any]:
    fields = [field for dataset in model["datasets"] for field in dataset["fields"]]
    metrics = [_payload(metric) for metric in model["metrics"]]
    active = sum(_payload(relation)["is_active"] for relation in model["relationships"])
    return {
        "scope": "private_author_snapshot", "definition_complete": True,
        "datasets": len(model["datasets"]), "fields": len(fields), "metrics": len(metrics),
        "relationships": len(model["relationships"]), "active_relationships": active,
        "inactive_relationships": len(model["relationships"]) - active,
        "field_datatypes": dict(sorted(Counter(field.get("datatype", "not_extracted") for field in fields).items())),
        "rls_detected": rls, "ols_detected": ols,
        "availability": {
            "hierarchies": "observed_absent", "calculation_groups": "observed_absent",
            "calculated_tables": "observed_absent",
            "measure_types": "available" if metrics and all(m["native"]["data_type"] is not None for m in metrics)
            else "not_extracted",
            "measure_formats": "available" if metrics and all(m["native"]["format_string"] is not None for m in metrics)
            else "not_extracted",
            "relationship_security_filtering": "not_extracted", "rls": "not_extracted", "ols": "not_extracted",
            "live_definition": "not_extracted", "data_freshness": "not_extracted",
        },
    }


def _warnings(model: dict[str, Any], coverage: dict[str, Any]) -> list[str]:
    warnings = [
        "Offline author snapshot only: live workspace/model/report binding and definition are not verified; no DAX was executed.",
        "Fixed private POC author catalog, not a public or per-user view; all permitted callers share the configured backend scope.",
        "RLS/OLS and relationship security filtering are not verified. No per-user security is provided; native engine enforcement remains authoritative.",
        "Catalog hash is not a live-definition check or data-freshness signal. Imported data freshness is not_extracted.",
    ]
    inactive = coverage["inactive_relationships"]
    if inactive:
        warnings.append(f"{inactive} inactive relationships retained; exclude them from default ordinary filter propagation.")
    if coverage["rls_detected"]:
        warnings.append("RLS was detected in the source inventory; role expressions and memberships are intentionally excluded and coverage is unknown.")
    if coverage["ols_detected"]:
        warnings.append("OLS was detected in the source inventory; this private author snapshot is not a caller-authorized metadata view.")
    names = {dataset["name"] for dataset in model["datasets"]}
    if {"fact_transaction", "dim_date"} <= names and not any(
        {relation["from"], relation["to"]} == {"fact_transaction", "dim_date"} for relation in model["relationships"]
    ):
        warnings.append("No extracted transaction-to-date relationship: verify trade/settlement date roles and MTD/QTD semantics in the native model.")
    if coverage["availability"]["measure_types"] != "available" or coverage["availability"]["measure_formats"] != "available":
        warnings.append("Measure output types/formats are not fully extracted; known DAX definitions are retained without guessing result types.")
    return warnings


def build_catalog(
    metadata_path: Path,
    summary_path: Path,
    overlay_path: Path | None = None,
    *,
    # Unset by default; pass the real Fabric identifiers via the CLI or environment.
    model_id: str = "00000000-0000-0000-0000-000000000000",
    workspace_id: str = "00000000-0000-0000-0000-000000000000",
    report_id: str = "00000000-0000-0000-0000-000000000000",
) -> dict[str, Any]:
    """Build and validate a deterministic catalog; fail on unsupported required semantics.

    Accepts the inspect_pbix.py JSON surface. Only Import tables, native columns
    (including extracted calculated-column definitions), and M:1 relationships
    with known activity and Single/Both ordinary filtering are supported.
    Hierarchies/calculation groups/calculated tables require a future mapping.
    Unsupported, absent or errored required inventories fail rather than vanish.
    """
    metadata, metadata_sha = _read_json(metadata_path)
    summary, summary_sha = _read_json(summary_path)
    for source_document in (metadata, summary):
        if source_document.get("extraction_issues") != []:
            raise ValueError("Source extraction issues must be resolved before a definition-complete catalog is built")
    for feature in ("tmschema_hierarchies", "tmschema_levels", "tmschema_calculation_groups",
                    "tmschema_calculation_items", "dax_tables"):
        if _rows(metadata, feature):
            raise ValueError(f"Unsupported feature inventory requires a reviewed mapping: {feature}")
    native_models = _rows(metadata, "tmschema_model")
    if len(native_models) != 1:
        raise ValueError("Exactly one native model is required")
    native_model = native_models[0]
    _import_mode(native_model.get("DefaultMode"))
    binding = {"model_id": model_id, "workspace_id": workspace_id, "report_id": report_id,
               "verification": "not_verified"}
    _validate(binding, _rule_validator("Binding"), "binding")
    registry = NameRegistry()
    table_names = metadata.get("tables")
    if not isinstance(table_names, list) or not table_names:
        raise ValueError("Nonempty logical table inventory required")
    for name in table_names:
        registry.register("datasets", name)
    tables = _rows(metadata, "tmschema_tables")
    columns = _rows(metadata, "tmschema_columns")
    measures = _rows(metadata, "dax_measures")
    relations = _rows(metadata, "relationships")
    partitions = _rows(metadata, "tmschema_partitions")
    table_rows: dict[str, tuple[int, dict[str, Any]]] = {}
    table_ids: set[Any] = set()
    for index, row in enumerate(tables):
        name = registry.canonical_name("datasets", _text(row.get("Name"), "table name"))
        if name in table_rows or row.get("ID") is not None and row["ID"] in table_ids:
            raise ValueError("Duplicate native table or table ID")
        table_rows[name] = (index, row)
        if row.get("ID") is not None:
            table_ids.add(row["ID"])
    if len(table_rows) != len(table_names):
        raise ValueError("Logical and native table inventories disagree")
    by_table: dict[str, list[tuple[int, dict[str, Any], str]]] = {name: [] for name in table_rows}
    partition_map: dict[str, list[dict[str, Any]]] = {name: [] for name in table_rows}
    for row in partitions:
        name = registry.canonical_name("datasets", _text(row.get("TableName"), "partition table name"))
        _import_mode(row.get("Mode"))
        if row.get("TableID") != table_rows[name][1].get("ID"):
            raise ValueError("Partition table ID/native binding mismatch")
        partition_map[name].append({"name": _text(row.get("Name"), "partition name"),
                                    "mode": "Import", "native_mode": row["Mode"], "source_type": row.get("Type")})
    for index, row in enumerate(columns):
        table = registry.canonical_name("datasets", _text(row.get("TableName"), "column table name"))
        if row.get("TableID") != table_rows[table][1].get("ID"):
            raise ValueError("Column table ID/native binding mismatch")
        field_name = registry.register(f"fields/{table}", _text(row.get("Name"), "column name"))
        by_table[table].append((index, row, field_name))
    model: dict[str, Any] = {"name": MODEL_NAME, "datasets": [], "metrics": [], "relationships": []}
    _description(model, native_model)
    nodes = {f"model/{MODEL_NAME}": model}
    source_formulas: dict[str, str] = {}
    for dataset_index, (table, (source_index, row)) in enumerate(sorted(table_rows.items())):
        path = f"datasets/{table}"
        pointer = f"/semantic_model/0/datasets/{dataset_index}"
        dataset: dict[str, Any] = {"name": table, "source": f"powerbi://models/{MODEL_NAME}/tables/{table}", "fields": []}
        _description(dataset, row)
        _attach(dataset, "dataset", _trace(f"/tmschema_tables/{source_index}", pointer, path, metadata_sha),
                binding=binding, native={"name": row["Name"], "id": row.get("ID"), "lineage_tag": row.get("LineageTag"),
                                         "is_hidden": _flag(row.get("IsHidden"), "table visibility"),
                                         "data_category": row.get("DataCategory")},
                storage_mode="Import", partitions=sorted(partition_map[table], key=lambda p: p["name"]),
                source_lineage="not_extracted", key_constraints="not_extracted")
        nodes[path] = dataset
        for field_index, (column_index, column, field_name) in enumerate(sorted(by_table[table], key=lambda c: c[2])):
            column_path = f"{path}/fields/{field_name}"
            column_type = column.get("Type")
            if type(column_type) not in (int, str) or column_type not in (1, 2, "Data", "Calculated"):
                raise ValueError("Unknown or unsupported column kind")
            calculated = column_type in (2, "Calculated")
            formula = column.get("Expression")
            calculation: dict[str, Any] = {"state": "observed_absent"}
            if calculated:
                formula = _text(formula, "calculated column definition")
                calculation = {"state": "available", "expression": formula,
                               "expression_sha256": hashlib.sha256(formula.encode("utf-8")).hexdigest()}
            elif formula is not None:
                raise ValueError("Ordinary column unexpectedly contains a calculation definition")
            field: dict[str, Any] = {"name": field_name, "expression": _expression(dax_column_reference(row["Name"], column["Name"]))}
            _description(field, column)
            _typed(field, column.get("DataType"))
            native = {"name": column["Name"], "table": row["Name"], "id": column.get("ID"),
                      "table_id": column.get("TableID"), "data_type": column.get("DataType"), "column_type": column_type}
            for source, target in (("SourceColumn", "source_column"), ("FormatString", "format_string"),
                                   ("DisplayFolder", "display_folder"), ("LineageTag", "lineage_tag"),
                                   ("DataCategory", "data_category"), ("SummarizeBy", "summarize_by")):
                native[target] = column.get(source)
            for source, target in (("IsNullable", "nullable"), ("IsHidden", "is_hidden"), ("IsKey", "is_key"), ("IsUnique", "is_unique")):
                native[target] = _flag(column.get(source), target)
            _attach(field, "field", _trace(f"/tmschema_columns/{column_index}", f"{pointer}/fields/{field_index}", column_path, metadata_sha),
                    native=native, calculation=calculation, sort_by_column="not_extracted",
                    group_by_columns="not_extracted", time_role="not_extracted")
            dataset["fields"].append(field)
            nodes[column_path] = field
        model["datasets"].append(dataset)
    # A secondary calculated-column inventory must agree, not silently override.
    seen_calculated: set[str] = set()
    for column in _rows(metadata, "dax_columns"):
        table = registry.canonical_name("datasets", _text(column.get("TableName"), "calculated column table name"))
        name = registry.canonical_name(f"fields/{table}", _text(column.get("Name"), "calculated column name"))
        path = f"datasets/{table}/fields/{name}"
        calculation = _payload(nodes[path])["calculation"]
        if path in seen_calculated or calculation.get("expression") != column.get("Expression") or calculation["state"] != "available":
            raise ValueError("Calculated-column inventories disagree")
        seen_calculated.add(path)
    sorted_measures = sorted(enumerate(measures), key=lambda p: normalize_name(_text(p[1].get("Name"), "measure name")))
    for metric_index, (source_index, measure) in enumerate(sorted_measures):
        table = registry.canonical_name("datasets", _text(measure.get("TableName"), "measure table name"))
        name = registry.register("metrics", _text(measure.get("Name"), "measure name"))
        formula = _text(measure.get("Expression"), "native measure definition")
        metric: dict[str, Any] = {"name": name, "expression": _expression(formula)}
        _description(metric, measure)
        _typed(metric, measure.get("DataType"))
        path = f"metrics/{name}"
        source_formulas[path] = formula
        _attach(metric, "metric", _trace(f"/dax_measures/{source_index}", f"/semantic_model/0/metrics/{metric_index}", path, metadata_sha),
                native={"name": measure["Name"], "home_table": registry.native_name("datasets", table),
                        "id": measure.get("ID"), "data_type": measure.get("DataType"), "format_string": measure.get("FormatString"),
                        "display_folder": measure.get("DisplayFolder"), "is_hidden": _flag(measure.get("IsHidden"), "measure visibility"),
                        "lineage_tag": measure.get("LineageTag")},
                invocation_reference=dax_measure_reference(measure["Name"]),
                expression_sha256=hashlib.sha256(formula.encode("utf-8")).hexdigest(),
                definition_state="available", dynamic_format_definition="not_extracted")
        nodes[path] = metric
        model["metrics"].append(metric)
    for index, relation in enumerate(relations):
        if relation.get("Cardinality") != "M:1":
            raise ValueError("Only evidenced M:1 relationships are supported")
        cross_filter = relation.get("CrossFilteringBehavior")
        if cross_filter not in _FILTERS:
            raise ValueError("Unknown ordinary relationship cross-filtering behavior")
        endpoints = {}
        for side in ("from", "to"):
            table = registry.canonical_name("datasets", _text(relation.get(f"{side.title()}TableName"), "relationship table name"))
            column_name = registry.canonical_name(f"fields/{table}", _text(relation.get(f"{side.title()}ColumnName"), "relationship column name"))
            endpoints[side] = (table, column_name)
        generated_name = "rel_" + "_to_".join(f"{table}_{column}" for table, column in endpoints.values())
        name = registry.register("relationships", relation.get("Name") or generated_name)
        edge: dict[str, Any] = {"name": name, "from": endpoints["from"][0], "to": endpoints["to"][0],
                                "from_columns": [endpoints["from"][1]], "to_columns": [endpoints["to"][1]]}
        path = f"relationships/{name}"
        _attach(edge, "relationship", _trace(f"/relationships/{index}", f"/semantic_model/0/relationships/{index}", path, metadata_sha),
                native={"name": relation.get("Name"), "id": relation.get("ID"),
                        "from": {"table": relation["FromTableName"], "columns": [relation["FromColumnName"]]},
                        "to": {"table": relation["ToTableName"], "columns": [relation["ToColumnName"]]},
                        "cardinality": relation["Cardinality"], "cross_filtering_behavior": cross_filter},
                is_active=_flag(relation.get("IsActive"), "relationship activity", required=True),
                from_cardinality="many", to_cardinality="one", cross_filtering_behavior=_FILTERS[cross_filter],
                security_filtering_behavior="not_extracted", join_on_date_behavior="not_extracted",
                rely_on_referential_integrity=_flag(relation.get("RelyOnReferentialIntegrity"), "referential integrity"),
                key_counts={"from": relation.get("FromKeyCount"), "to": relation.get("ToKeyCount")}, reviewed_description=None)
        nodes[path] = edge
        model["relationships"].append(edge)
    coverage = _coverage(model, rls=bool(_rows(metadata, "rls")), ols=bool(_rows(metadata, "ols")))
    for summary_key, key in (("table_count", "datasets"), ("column_count", "fields"),
                             ("measure_count", "metrics"), ("relationship_count", "relationships")):
        if type(summary.get(summary_key)) is not int or summary[summary_key] != coverage[key]:
            raise ValueError(f"Inspection summary coverage mismatch: {summary_key}")
    overlay_sha = _apply_overlay(overlay_path, nodes)
    for path, formula in source_formulas.items():
        if _dax(nodes[path]) != formula:
            raise ValueError("Exact source DAX fidelity check failed")
    base_spec = {key: value["const"] for key, value in rules_schema()["$defs"]["Base"]["properties"].items()}
    provenance = {"base_spec": base_spec, "mapping_version": PROFILE_VERSION,
                  "source_artifacts": {"metadata_sha256": metadata_sha, "summary_sha256": summary_sha,
                                       "pbix_sha256": summary.get("source_sha256"), "overlay_sha256": overlay_sha}}
    _attach(model, "model", _trace("/tmschema_model/0", "/semantic_model/0", f"model/{MODEL_NAME}", metadata_sha),
            profile_id=PROFILE_ID, profile_version=PROFILE_VERSION, base_spec=base_spec, binding=binding,
            native={"name": _text(native_model.get("Name"), "model name"), "culture": native_model.get("Culture"),
                    "compatibility_level": native_model.get("CompatibilityLevel")},
            storage_mode="Import", native_default_mode=native_model["DefaultMode"], coverage=coverage,
            provenance=provenance, audience="fixed_private_poc_author", per_user_security=False)
    catalog = {"profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION,
               "semantic_document": {"version": PROFILE_VERSION, "semantic_model": [model],
                                     "dialects": ["DAX"], "vendors": ["POWER_BI"]},
               "provenance": provenance, "warnings": _warnings(model, coverage), "coverage": coverage}
    catalog["catalog_sha256"] = compute_catalog_sha256(catalog)
    validate_catalog(catalog)
    return catalog


def validate_catalog(catalog: dict[str, Any]) -> None:
    """Reject structural, extension, binding, coverage and formula-hash defects.

    Validation uses only bundled schemas and local references. It neither opens
    provenance source paths nor fetches remote schemas or native Power BI URIs.
    """
    canonical_json_bytes(catalog)
    _validate(catalog, _rule_validator("Catalog"), "catalog")
    document = catalog["semantic_document"]
    _validate(document, _local_validator(load_profile_schema()), "semantic_document")
    if len(document["semantic_model"]) != 1 or document.get("dialects") != ["DAX"] or document.get("vendors") != ["POWER_BI"]:
        raise ValueError("Exactly one model and matching DAX/POWER_BI declarations are required")
    model = document["semantic_model"][0]
    if model["name"] != MODEL_NAME:
        raise ValueError("Unexpected profile model canonical name")
    for key in ("metrics", "relationships"):
        if key not in model:
            raise ValueError(f"Required complete inventory missing: {key}")
    registry = NameRegistry()
    validators = {kind: _rule_validator(kind) for kind in ("model", "dataset", "field", "metric", "relationship")}
    source_pointers: set[str] = set()
    native_ids: dict[str, set[Any]] = {}
    metadata_sha = catalog["provenance"]["source_artifacts"]["metadata_sha256"]

    def inspect(node: dict[str, Any], kind: str, path: str, pointer: str, source_list: str) -> dict[str, Any]:
        payload = _payload(node)
        _validate(payload, validators[kind], path)
        trace = payload["trace"]
        prefix = f"/{source_list}/"
        source = trace["source_pointer"]
        if not source.startswith(prefix) or not source[len(prefix):].isdigit() or source in source_pointers:
            raise ValueError("Invalid or duplicate source object pointer")
        source_pointers.add(source)
        if trace["output_pointer"] != pointer or trace["canonical_path"] != path or trace["artifact_sha256"] != metadata_sha:
            raise ValueError("Object provenance/binding mismatch")
        identity = payload.get("native", {}).get("id")
        if identity is not None:
            ids = native_ids.setdefault(kind, set())
            if identity in ids:
                raise ValueError("Duplicate native object ID")
            ids.add(identity)
        context = node.get("ai_context", {})
        if type(context) is not dict or set(context) - {"instructions", "synonyms", "examples"}:
            raise ValueError("Only reviewed, structured AI context is supported")
        for key in ("instructions",):
            if key in context:
                _text(context[key], key)
        for key in ("synonyms", "examples"):
            if key in context:
                values = [_text(value, key).casefold() for value in context[key]]
                if not values or len(values) != len(set(values)):
                    raise ValueError("Empty or case-folded duplicate AI context entries")
        if "description" in node:
            _text(node["description"], "description")
        return payload

    model_payload = inspect(model, "model", f"model/{MODEL_NAME}", "/semantic_model/0", "tmschema_model")
    if model_payload["provenance"] != catalog["provenance"] or model_payload["coverage"] != catalog["coverage"]:
        raise ValueError("Model and envelope provenance/coverage disagree")
    if model_payload["base_spec"] != catalog["provenance"]["base_spec"]:
        raise ValueError("Model base specification mismatch")
    _import_mode(model_payload["native_default_mode"])
    fields: dict[str, dict[str, dict[str, Any]]] = {}
    table_payloads: dict[str, dict[str, Any]] = {}
    for index, dataset in enumerate(model["datasets"]):
        table = dataset["name"]
        pointer = f"/semantic_model/0/datasets/{index}"
        payload = inspect(dataset, "dataset", f"datasets/{table}", pointer, "tmschema_tables")
        if registry.register("datasets", payload["native"]["name"]) != table:
            raise ValueError("Dataset canonical/native binding mismatch")
        if dataset["source"] != f"powerbi://models/{MODEL_NAME}/tables/{table}" or payload["binding"] != model_payload["binding"]:
            raise ValueError("Dataset source/native model binding mismatch")
        if "primary_key" in dataset or "unique_keys" in dataset:
            raise ValueError("Unverified key constraints may not be promoted in this author profile")
        table_payloads[table] = payload
        fields[table] = {}
        partition_names: set[str] = set()
        for partition in payload["partitions"]:
            _import_mode(partition["native_mode"])
            name = partition["name"].casefold()
            if name in partition_names:
                raise ValueError("Duplicate partition name")
            partition_names.add(name)
        if not isinstance(dataset.get("fields"), list):
            raise ValueError("Dataset field inventory is required")
        for field_index, field in enumerate(dataset["fields"]):
            path = f"datasets/{table}/fields/{field['name']}"
            field_payload = inspect(field, "field", path, f"{pointer}/fields/{field_index}", "tmschema_columns")
            native = field_payload["native"]
            name = registry.register(f"fields/{table}", native["name"])
            if name != field["name"] or native["table"] != payload["native"]["name"] or native["table_id"] != payload["native"]["id"]:
                raise ValueError("Field canonical/native table binding mismatch")
            if _dax(field) != dax_column_reference(native["table"], native["name"]):
                raise ValueError("Field DAX must be the exact native row-context column reference")
            if field.get("datatype") != _datatype(native["data_type"]):
                raise ValueError("Field logical datatype does not match native evidence")
            if "dimension" in field:
                raise ValueError("Temporal role has not been extracted; do not invent dimension annotations")
            calculation = field_payload["calculation"]
            calculated = native["column_type"] in (2, "Calculated")
            if calculated != (calculation["state"] == "available"):
                raise ValueError("Calculated-column definition availability mismatch")
            if calculated and hashlib.sha256(calculation["expression"].encode("utf-8")).hexdigest() != calculation["expression_sha256"]:
                raise ValueError("Calculated-column expression hash mismatch")
            fields[table][name] = native
    for index, metric in enumerate(model["metrics"]):
        payload = inspect(metric, "metric", f"metrics/{metric['name']}", f"/semantic_model/0/metrics/{index}", "dax_measures")
        native = payload["native"]
        if registry.register("metrics", native["name"]) != metric["name"]:
            raise ValueError("Metric canonical/native binding mismatch")
        registry.canonical_name("datasets", native["home_table"])
        if payload["invocation_reference"] != dax_measure_reference(native["name"]):
            raise ValueError("Metric invocation/native binding mismatch")
        formula = _dax(metric)
        if hashlib.sha256(formula.encode("utf-8")).hexdigest() != payload["expression_sha256"]:
            raise ValueError("Metric expression hash mismatch")
        if formula.strip() == payload["invocation_reference"]:
            raise ValueError("A metric invocation is not its native measure definition")
        if metric.get("datatype") != _datatype(native["data_type"]):
            raise ValueError("Metric logical datatype does not match native evidence")
    edges: set[tuple[Any, ...]] = set()
    for index, relation in enumerate(model["relationships"]):
        payload = inspect(relation, "relationship", f"relationships/{relation['name']}", f"/semantic_model/0/relationships/{index}", "relationships")
        if registry.register("relationships", payload["native"]["name"] or relation["name"]) != relation["name"]:
            raise ValueError("Relationship canonical/native binding mismatch")
        if len(relation["from_columns"]) != len(relation["to_columns"]):
            raise ValueError("Relationship key pairs must have equal lengths")
        for side in ("from", "to"):
            table = relation[side]
            columns = relation[f"{side}_columns"]
            if table not in fields or len(columns) != len(set(columns)) or any(column not in fields[table] for column in columns):
                raise ValueError("Dangling or duplicated relationship endpoint")
            expected = {"table": table_payloads[table]["native"]["name"],
                        "columns": [fields[table][column]["name"] for column in columns]}
            if payload["native"][side] != expected:
                raise ValueError("Relationship canonical/native endpoints disagree")
        signature = (relation["from"], tuple(relation["from_columns"]), relation["to"], tuple(relation["to_columns"]))
        if signature in edges:
            raise ValueError("Duplicate relationship column pairs")
        edges.add(signature)
        if relation["from"] == relation["to"] and any(a == b for a, b in zip(relation["from_columns"], relation["to_columns"], strict=True)):
            raise ValueError("A relationship cannot pair a column with itself")
        if payload["cross_filtering_behavior"] != _FILTERS[payload["native"]["cross_filtering_behavior"]]:
            raise ValueError("Relationship ordinary filter behavior mismatch")
    coverage = catalog["coverage"]
    expected_coverage = _coverage(model, rls=coverage["rls_detected"], ols=coverage["ols_detected"])
    if coverage != expected_coverage:
        raise ValueError("Catalog coverage does not match actual first-class object inventory")
    if catalog["warnings"] != _warnings(model, coverage):
        raise ValueError("Required offline/security/inventory warnings do not match the catalog")
    if catalog["catalog_sha256"] != compute_catalog_sha256(catalog):
        raise ValueError("Catalog SHA-256 mismatch")