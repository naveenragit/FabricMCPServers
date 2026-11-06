"""Closed project extension/envelope schemas; all references are document-local.

The upstream core remains unchanged on disk. The small core derivative is made
by ``profile.load_profile_schema``. These additional schemas constrain decoded
POWER_BI payloads; merely validating OSSIE's JSON-encoded string is insufficient.
"""

from __future__ import annotations

from typing import Any

PROFILE_ID = "urn:wealth-management-mcp:schema:ossie-fabric-dax:1.0.0"
PROFILE_VERSION = "1.0.0"
BASE_COMMIT = "28365cd638f3833765c5b940ada5b8cbc65f1c42"
BASE_SHA256 = "22be177612ed665e0af244c586b9c0162f2a3706f8e9b061910f7a8e2a19b8e8"
BASE_URL = f"https://raw.githubusercontent.com/apache/ossie/{BASE_COMMIT}/core-spec/ossie-schema.json"
STATES = ["available", "observed_absent", "not_extracted", "permission_denied", "redacted", "unsupported", "error"]


def closed(properties: dict[str, Any], *, optional: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object", "properties": properties, "additionalProperties": False,
        "required": [key for key in properties if key not in optional],
    }


def ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def rules_schema() -> dict[str, Any]:
    """Return a fresh schema tree so callers cannot poison later validation."""
    text = {"type": "string", "minLength": 1, "pattern": r"\S"}
    nullable_text = {"type": ["string", "null"]}
    nullable_bool = {"type": ["boolean", "null"]}
    count = {"type": "integer", "minimum": 0}
    native_id = {"type": ["integer", "string", "null"]}
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    strings = {"type": "array", "items": text, "uniqueItems": True}
    state = {"enum": STATES}
    version = {"const": PROFILE_VERSION}
    uuid = {"type": "string", "pattern": "^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"}
    definitions: dict[str, Any] = {
        "Base": closed({
            "name": {"const": "Apache Ossie (incubating)"},
            "version": {"const": "0.2.0.dev0"}, "commit": {"const": BASE_COMMIT},
            "schema_sha256": {"const": BASE_SHA256}, "schema_url": {"const": BASE_URL},
            "license": {"const": "Apache-2.0"},
            "notice_url": {"const": f"https://raw.githubusercontent.com/apache/ossie/{BASE_COMMIT}/NOTICE"},
        }),
        "Binding": closed({"model_id": uuid, "workspace_id": uuid, "report_id": uuid,
                           "verification": {"const": "not_verified"}}),
        "Provenance": closed({
            "base_spec": ref("Base"), "mapping_version": version,
            "source_artifacts": closed({
                "metadata_sha256": digest, "summary_sha256": digest,
                "pbix_sha256": {"anyOf": [digest, {"type": "null"}]},
                "overlay_sha256": {"anyOf": [digest, {"type": "null"}]},
            }),
        }),
        "Trace": closed({
            "artifact_sha256": digest, "source_pointer": text, "output_pointer": text,
            "canonical_path": text, "mapping_version": version,
            "disclosure": {"const": "private_author_snapshot"},
        }),
        "Coverage": closed({
            "scope": {"const": "private_author_snapshot"}, "definition_complete": {"const": True},
            "datasets": count, "fields": count, "metrics": count, "relationships": count,
            "active_relationships": count, "inactive_relationships": count,
            "field_datatypes": {"type": "object", "additionalProperties": count},
            "rls_detected": {"type": "boolean"}, "ols_detected": {"type": "boolean"},
            "availability": closed({
                "hierarchies": {"const": "observed_absent"},
                "calculation_groups": {"const": "observed_absent"},
                "calculated_tables": {"const": "observed_absent"},
                "measure_types": state, "measure_formats": state,
                "relationship_security_filtering": {"const": "not_extracted"},
                "rls": {"const": "not_extracted"}, "ols": {"const": "not_extracted"},
                "live_definition": {"const": "not_extracted"}, "data_freshness": {"const": "not_extracted"},
            }),
        }),
        "Calculation": {
            "oneOf": [
                closed({"state": {"const": "observed_absent"}}),
                closed({"state": {"const": "available"}, "expression": text, "expression_sha256": digest}),
            ],
        },
        "Endpoint": closed({"table": text, "columns": {**strings, "minItems": 1}}),
    }
    common = {"extension_version": version, "trace": ref("Trace")}
    definitions["model"] = closed({
        **common, "kind": {"const": "model"}, "profile_id": {"const": PROFILE_ID},
        "profile_version": version, "base_spec": ref("Base"), "binding": ref("Binding"),
        "native": closed({"name": text, "culture": nullable_text, "compatibility_level": native_id}),
        "storage_mode": {"const": "Import"}, "native_default_mode": {"enum": [0, "Import"]},
        "coverage": ref("Coverage"), "provenance": ref("Provenance"),
        "audience": {"const": "fixed_private_poc_author"}, "per_user_security": {"const": False},
    })
    definitions["dataset"] = closed({
        **common, "kind": {"const": "dataset"}, "binding": ref("Binding"),
        "native": closed({"name": text, "id": native_id, "lineage_tag": nullable_text,
                          "is_hidden": nullable_bool, "data_category": nullable_text}),
        "storage_mode": {"const": "Import"},
        "partitions": {"type": "array", "minItems": 1, "items": closed({
            "name": text, "mode": {"const": "Import"}, "native_mode": {"enum": [0, "Import"]},
            "source_type": native_id,
        })},
        "source_lineage": {"const": "not_extracted"}, "key_constraints": {"const": "not_extracted"},
    })
    definitions["field"] = closed({
        **common, "kind": {"const": "field"},
        "native": closed({
            "name": text, "table": text, "id": native_id, "table_id": native_id,
            "data_type": native_id, "column_type": {"enum": [1, 2, "Data", "Calculated"]},
            "source_column": nullable_text, "nullable": nullable_bool, "is_hidden": nullable_bool,
            "is_key": nullable_bool, "is_unique": nullable_bool,
            "format_string": nullable_text, "display_folder": nullable_text,
            "lineage_tag": nullable_text, "data_category": nullable_text, "summarize_by": native_id,
        }),
        "calculation": ref("Calculation"), "sort_by_column": {"const": "not_extracted"},
        "group_by_columns": {"const": "not_extracted"}, "time_role": {"const": "not_extracted"},
    })
    definitions["metric"] = closed({
        **common, "kind": {"const": "metric"},
        "native": closed({
            "name": text, "home_table": text, "id": native_id, "data_type": native_id,
            "format_string": nullable_text, "display_folder": nullable_text,
            "is_hidden": nullable_bool, "lineage_tag": nullable_text,
        }),
        "invocation_reference": text, "expression_sha256": digest,
        "definition_state": {"const": "available"}, "dynamic_format_definition": {"const": "not_extracted"},
    })
    definitions["relationship"] = closed({
        **common, "kind": {"const": "relationship"},
        "native": closed({
            "name": nullable_text, "id": native_id, "from": ref("Endpoint"), "to": ref("Endpoint"),
            "cardinality": {"const": "M:1"}, "cross_filtering_behavior": {"enum": ["Single", "Both"]},
        }),
        "is_active": {"type": "boolean"}, "from_cardinality": {"const": "many"},
        "to_cardinality": {"const": "one"},
        "cross_filtering_behavior": {"enum": ["one_direction", "both_directions"]},
        "security_filtering_behavior": {"const": "not_extracted"},
        "join_on_date_behavior": {"const": "not_extracted"},
        "rely_on_referential_integrity": nullable_bool,
        "key_counts": closed({"from": {"type": ["integer", "null"], "minimum": 0},
                              "to": {"type": ["integer", "null"], "minimum": 0}}),
        "reviewed_description": nullable_text,
    })
    definitions["Catalog"] = closed({
        "profile_id": {"const": PROFILE_ID}, "profile_version": version,
        "semantic_document": {"type": "object"}, "catalog_sha256": digest,
        "provenance": ref("Provenance"), "warnings": {**strings, "minItems": 1}, "coverage": ref("Coverage"),
    })
    definitions["Overlay"] = closed({
        "version": version,
        "objects": {"type": "object", "additionalProperties": {
            **closed({"description": text, "synonyms": {**strings, "minItems": 1}, "instructions": text},
                     optional=("description", "synonyms", "instructions")),
            "minProperties": 1,
        }},
    })
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": definitions}