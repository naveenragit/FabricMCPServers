"""Small synthetic author metadata only; no private model fixture is committed."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator

from wealth_management_mcp.names import NameRegistry, dax_column_reference, normalize_name
from wealth_management_mcp.profile import (
    PROFILE_ID,
    PROFILE_VERSION,
    build_catalog,
    canonical_json_bytes,
    compute_catalog_sha256,
    load_base_schema,
    load_extension_schema,
    load_profile_schema,
    validate_catalog,
)
from wealth_management_mcp.schemas import BASE_SHA256, rules_schema


def synthetic_metadata() -> dict[str, Any]:
    tables = [{"ID": 1, "Name": "dim_item", "Description": None, "IsHidden": 0},
              {"ID": 2, "Name": "fact_event", "Description": None, "IsHidden": 0}]
    columns = []
    for index, (table_id, name, datatype) in enumerate([
        (1, "item_id", 6), (2, "item_id", 6), (2, "value", 8), (2, "event_count", 6),
        (2, "flag", 11), (2, "audit_at", 9), (2, "label", 2),
    ], start=10):
        columns.append({
            "ID": index, "TableID": table_id, "TableName": tables[table_id - 1]["Name"],
            "Name": name, "DataType": datatype, "Type": 1, "IsHidden": 0, "IsNullable": 1,
            "IsKey": 0, "IsUnique": 0, "SourceColumn": name, "Expression": None,
        })
    return {
        "extraction_issues": [], "tables": [row["Name"] for row in tables],
        "tmschema_model": [{"Name": "Model", "Culture": "en-US", "DefaultMode": 0}],
        "tmschema_tables": tables, "tmschema_columns": columns,
        "tmschema_partitions": [{"TableID": row["ID"], "TableName": row["Name"], "Name": row["Name"],
                                 "Mode": 0, "Type": 4} for row in tables],
        "tmschema_hierarchies": [], "tmschema_levels": [], "tmschema_calculation_groups": [],
        "tmschema_calculation_items": [], "dax_tables": [], "dax_columns": [],
        "dax_measures": [{"TableName": "dim_item", "Name": "Value %",
                          "Expression": " DIVIDE(SUM('fact_event'[value]), 2)\n"}],
        "relationships": [{
            "FromTableName": "fact_event", "FromColumnName": "item_id", "ToTableName": "dim_item",
            "ToColumnName": "item_id", "IsActive": 0, "Cardinality": "M:1",
            "CrossFilteringBehavior": "Single", "FromKeyCount": 3, "ToKeyCount": None,
            "RelyOnReferentialIntegrity": 0,
        }],
        "rls": [{"FilterExpression": "PRIVATE_RLS_SENTINEL", "Members": ["PRIVATE_MEMBER_SENTINEL"]}],
        "ols": [], "source_bindings": [{"connection_string": "PRIVATE_CONNECTION_SENTINEL"}],
        "credentials": "PRIVATE_CREDENTIAL_SENTINEL",
    }


def payload(node: dict[str, Any]) -> dict[str, Any]:
    return json.loads(node["custom_extensions"][0]["data"])


def replace_payload(node: dict[str, Any], value: dict[str, Any]) -> None:
    node["custom_extensions"][0]["data"] = json.dumps(value)


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.metadata = synthetic_metadata()

    def build(self, *, overlay: str | None = None, suffix: str = ".yaml") -> dict[str, Any]:
        metadata_path = self.root / "metadata.json"
        summary_path = self.root / "summary.json"
        metadata_path.write_text(json.dumps(self.metadata), encoding="utf-8")
        summary_path.write_text(json.dumps({
            "extraction_issues": [], "table_count": len(self.metadata["tables"]),
            "column_count": len(self.metadata["tmschema_columns"]),
            "measure_count": len(self.metadata["dax_measures"]),
            "relationship_count": len(self.metadata["relationships"]),
            "source_sha256": "a" * 64,
        }), encoding="utf-8")
        overlay_path = None
        if overlay is not None:
            overlay_path = self.root / f"overlay{suffix}"
            overlay_path.write_text(overlay, encoding="utf-8")
        return build_catalog(metadata_path, summary_path, overlay_path)

    def test_complete_deterministic_native_fidelity(self) -> None:
        catalog = self.build()
        self.assertEqual(catalog, self.build())
        before = canonical_json_bytes(catalog)
        validate_catalog(catalog)
        self.assertEqual(before, canonical_json_bytes(catalog))
        self.assertEqual(catalog["profile_id"], PROFILE_ID)
        self.assertEqual(catalog["profile_version"], "1.0.0")
        self.assertEqual(catalog["catalog_sha256"], compute_catalog_sha256(catalog))
        self.assertEqual({key: catalog["coverage"][key] for key in ("datasets", "fields", "metrics", "relationships")},
                         {"datasets": 2, "fields": 7, "metrics": 1, "relationships": 1})
        model = catalog["semantic_document"]["semantic_model"][0]
        metric = model["metrics"][0]
        formula = self.metadata["dax_measures"][0]["Expression"]
        self.assertEqual(metric["expression"]["dialects"], [{"dialect": "DAX", "expression": formula}])
        self.assertEqual(payload(metric)["expression_sha256"], hashlib.sha256(formula.encode()).hexdigest())
        self.assertEqual(payload(metric)["native"]["home_table"], "dim_item")
        self.assertEqual(metric["name"], "value_pct")
        self.assertNotIn("datatype", metric)
        types = catalog["coverage"]["field_datatypes"]
        self.assertEqual(types, {"Integer": 3, "Float": 1, "Boolean": 1, "DateTime": 1, "String": 1})
        for dataset in model["datasets"]:
            self.assertNotIn("primary_key", dataset)
            self.assertNotIn("unique_keys", dataset)
            for field in dataset["fields"]:
                native = payload(field)["native"]
                self.assertEqual(field["expression"]["dialects"][0]["expression"],
                                 dax_column_reference(native["table"], native["name"]))
        relation = payload(model["relationships"][0])
        self.assertIs(relation["is_active"], False)
        self.assertEqual(relation["cross_filtering_behavior"], "one_direction")
        self.assertEqual(relation["security_filtering_behavior"], "not_extracted")
        self.assertEqual(catalog["coverage"]["inactive_relationships"], 1)
        for sentinel in ("PRIVATE_RLS_SENTINEL", "PRIVATE_MEMBER_SENTINEL", "PRIVATE_CONNECTION_SENTINEL",
                         "PRIVATE_CREDENTIAL_SENTINEL"):
            self.assertNotIn(sentinel, before.decode())

    def test_base_pin_and_minimal_derivation(self) -> None:
        base = load_base_schema()
        profile = load_profile_schema()
        self.assertEqual(base["properties"]["version"]["const"], "0.2.0.dev0")
        self.assertNotIn("DAX", base["$defs"]["Dialect"]["enum"])
        self.assertEqual(profile["$id"], PROFILE_ID)
        for key in ("$id", "title", "description"):
            profile[key] = base[key]
        profile["properties"]["version"] = base["properties"]["version"]
        profile["$defs"]["Dialect"]["enum"].remove("DAX")
        self.assertEqual(base, profile)
        self.assertEqual(load_profile_schema()["properties"]["version"]["const"], PROFILE_VERSION)
        self.assertEqual(load_base_schema()["properties"]["version"]["const"], "0.2.0.dev0")
        self.assertEqual(self.build()["provenance"]["base_spec"]["schema_sha256"], BASE_SHA256)
        Draft202012Validator.check_schema(load_profile_schema())
        Draft202012Validator.check_schema(rules_schema())
        self.assertFalse(Draft202012Validator(base).is_valid(self.build()["semantic_document"]))

    def test_schema_uses_package_resource_and_verifies_digest(self) -> None:
        raw = files("wealth_management_mcp.schemas").joinpath("ossie-schema.json").read_bytes()
        expected = load_base_schema()
        resource = Mock(spec=["read_bytes"])
        resource.read_bytes.return_value = raw
        package = Mock(spec=["joinpath"])
        package.joinpath.return_value = resource
        with patch("wealth_management_mcp.profile.files", return_value=package) as locate:
            self.assertEqual(load_base_schema(), expected)
            locate.assert_called_once_with("wealth_management_mcp.schemas")
            package.joinpath.assert_called_once_with("ossie-schema.json")
            resource.read_bytes.return_value = raw + b" "
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                load_base_schema()

    def test_rls_inventory_exposes_only_detection_and_generic_warning(self) -> None:
        original = self.build()
        self.metadata["rls"] = [{
            "RoleName": "PRIVATE_ROLE_SENTINEL",
            "FilterExpression": "'dim_item'[item_id] = PRIVATE_RLS_CHANGED_SENTINEL",
            "Members": ["PRIVATE_MEMBER_CHANGED_SENTINEL"],
        }]
        changed = self.build()
        self.assertTrue(changed["coverage"]["rls_detected"])
        self.assertEqual(changed["coverage"]["availability"]["rls"], "not_extracted")
        self.assertEqual(changed["warnings"], original["warnings"])
        self.assertNotEqual(changed["catalog_sha256"], original["catalog_sha256"])
        serialized = canonical_json_bytes(changed).decode("utf-8")
        for sentinel in ("PRIVATE_ROLE_SENTINEL", "PRIVATE_RLS_CHANGED_SENTINEL", "PRIVATE_MEMBER_CHANGED_SENTINEL"):
            self.assertNotIn(sentinel, serialized)
        self.metadata["rls"] = "PRIVATE_MALFORMED_RLS_SENTINEL"
        with self.assertRaises(ValueError) as error:
            self.build()
        self.assertNotIn("PRIVATE_MALFORMED_RLS_SENTINEL", str(error.exception))

    def test_extensions_are_locally_validated_no_network(self) -> None:
        catalog = self.build()
        model = catalog["semantic_document"]["semantic_model"][0]
        for kind, node in (("model", model), ("dataset", model["datasets"][0]),
                           ("field", model["datasets"][0]["fields"][0]),
                           ("metric", model["metrics"][0]), ("relationship", model["relationships"][0])):
            self.assertTrue(Draft202012Validator(load_extension_schema(kind)).is_valid(payload(node)))
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            validate_catalog(catalog)
        remote = load_profile_schema()
        remote["properties"]["semantic_model"] = {"$ref": "https://invalid.example/schema"}
        with patch("wealth_management_mcp.profile.load_profile_schema", return_value=remote):
            with self.assertRaisesRegex(ValueError, "local"):
                validate_catalog(catalog)

    def test_registry_rejects_collisions_and_preserves_bindings(self) -> None:
        registry = NameRegistry()
        self.assertEqual(registry.register("metrics", "Value %"), "value_pct")
        self.assertEqual(registry.native_name("metrics", "value_pct"), "Value %")
        for native in ("value_pct", "Value %", "VALUE %"):
            with self.assertRaises(ValueError):
                registry.register("metrics", native)
        with self.assertRaises(ValueError):
            registry.canonical_name("metrics", "value_pct")
        self.assertEqual(normalize_name("fact_event"), "fact_event")
        self.assertEqual(dax_column_reference("Owner's data", "a]b"), "'Owner''s data'[a]]b]")

    def test_overlay_is_review_only_and_unknown_paths_fail(self) -> None:
        overlay = json.dumps({"version": "1.0.0", "objects": {
            "metrics/value_pct": {"description": "Reviewed ratio", "synonyms": ["ratio"],
                                  "instructions": "Keep filter context."},
            "datasets/fact_event/fields/value": {"description": "Reviewed row value"},
        }})
        catalog = self.build(overlay=overlay, suffix=".json")
        metric = catalog["semantic_document"]["semantic_model"][0]["metrics"][0]
        self.assertEqual(metric["description"], "Reviewed ratio")
        self.assertEqual(metric["ai_context"]["synonyms"], ["ratio"])
        self.assertEqual(metric["expression"]["dialects"][0]["expression"], self.metadata["dax_measures"][0]["Expression"])
        self.assertNotEqual(catalog["catalog_sha256"], self.build()["catalog_sha256"])
        good_yaml = 'version: "1.0.0"\nobjects:\n  metrics/value_pct:\n    description: Reviewed YAML description\n'
        self.assertEqual(self.build(overlay=good_yaml)["semantic_document"]["semantic_model"][0]["metrics"][0]["description"],
                         "Reviewed YAML description")
        invalid_overlays: list[dict[str, Any]] = [
            {"metrics/missing": {"description": "Bad reference"}},
            {"metrics/value_pct": {"expression": "0"}},
            {"metrics/value_pct": {"synonyms": ["ratio", "RATIO"]}},
            {"metrics/value_pct": {}},
        ]
        for values in invalid_overlays:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.build(overlay=json.dumps({"version": "1.0.0", "objects": values}))
        with self.assertRaises(ValueError):
            self.build(overlay='version: "1.0.0"\nversion: "1.0.0"\nobjects: {}')
        with self.assertRaises(ValueError):
            self.build(overlay='!!python/object/apply:os.system ["should_not_run"]')

    def test_mutations_fail_even_when_resealed(self) -> None:
        original = self.build()
        for mutation in ("formula", "blank", "field_formula", "type", "dangling", "unequal", "kind",
                         "nested_type", "missing_activity", "bad_json", "duplicate", "extra_payload", "coverage", "key"):
            catalog = copy.deepcopy(original)
            model = catalog["semantic_document"]["semantic_model"][0]
            metric = model["metrics"][0]
            field = model["datasets"][1]["fields"][0]
            relation = model["relationships"][0]
            extension = payload(relation)
            if mutation in ("formula", "blank"):
                metric["expression"]["dialects"][0]["expression"] = "0" if mutation == "formula" else " \n"
            elif mutation == "field_formula":
                field["expression"]["dialects"][0]["expression"] = "0"
            elif mutation == "type":
                field["datatype"] = "Decimal"
            elif mutation == "dangling":
                relation["from"] = "missing"
            elif mutation == "unequal":
                relation["from_columns"].append("label")
            elif mutation == "kind":
                extension["kind"] = "metric"
            elif mutation == "nested_type":
                extension["native"]["from"]["columns"] = "item_id"
            elif mutation == "missing_activity":
                del extension["is_active"]
            elif mutation == "extra_payload":
                extension["memberships"] = ["not_allowed"]
            elif mutation == "duplicate":
                model["metrics"].append(copy.deepcopy(metric))
            elif mutation == "coverage":
                catalog["coverage"]["fields"] = 999
            elif mutation == "key":
                model["datasets"][0]["primary_key"] = ["item_id"]
            replace_payload(relation, extension)
            if mutation == "bad_json":
                relation["custom_extensions"][0]["data"] = "{invalid"
            catalog["catalog_sha256"] = compute_catalog_sha256(catalog)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_catalog(catalog)

    def test_unsupported_or_unknown_required_features_fail(self) -> None:
        cases = [
            ("tmschema_model", "DefaultMode", None), ("tmschema_partitions", "Mode", 1),
            ("relationships", "Cardinality", "M:M"), ("relationships", "CrossFilteringBehavior", "Unknown"),
            ("relationships", "IsActive", "false"), ("tmschema_columns", "Type", 999),
            ("tmschema_columns", "DataType", 999), ("dax_measures", "Expression", None),
        ]
        for inventory, key, value in cases:
            self.metadata = synthetic_metadata()
            self.metadata[inventory][0][key] = value
            with self.subTest(inventory=inventory, key=key), self.assertRaises(ValueError):
                self.build()
        self.metadata = synthetic_metadata()
        self.metadata["tmschema_calculation_groups"].append({"Name": "Synthetic unsupported group"})
        with self.assertRaises(ValueError):
            self.build()
        self.metadata = synthetic_metadata()
        self.metadata["dax_measures"].append({"Name": "value_pct", "TableName": "fact_event", "Expression": "1"})
        with self.assertRaises(ValueError):
            self.build()

    def test_calculated_columns_keep_materialized_reference_and_exact_definition(self) -> None:
        column = self.metadata["tmschema_columns"][2]
        column["Type"] = 2
        column["Expression"] = " 1 + 2\n"
        self.metadata["dax_columns"] = [{"TableName": column["TableName"], "Name": column["Name"],
                                         "Expression": column["Expression"]}]
        catalog = self.build()
        fields = catalog["semantic_document"]["semantic_model"][0]["datasets"][1]["fields"]
        field = next(field for field in fields if field["name"] == "value")
        self.assertEqual(payload(field)["calculation"]["expression"], " 1 + 2\n")
        self.assertEqual(field["expression"]["dialects"][0]["expression"], "'fact_event'[value]")
        self.metadata["dax_columns"][0]["Expression"] = "4"
        with self.assertRaises(ValueError):
            self.build()

    def test_json_only_and_tampered_digest(self) -> None:
        for value in (float("nan"), float("inf"), {1: "bad key"}, {"set": {1}}, (1, 2)):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                canonical_json_bytes(value)
        self.assertEqual(canonical_json_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}')
        cyclic: list[Any] = []
        cyclic.append(cyclic)
        with self.assertRaises(ValueError):
            canonical_json_bytes(cyclic)
        catalog = self.build()
        catalog["catalog_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            validate_catalog(catalog)

    def test_summary_mismatch_and_duplicate_json_keys_fail(self) -> None:
        self.build()
        summary_path = self.root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["measure_count"] += 1
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            build_catalog(self.root / "metadata.json", summary_path)
        (self.root / "metadata.json").write_text('{"tables": [], "tables": []}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            build_catalog(self.root / "metadata.json", summary_path)


if __name__ == "__main__":
    unittest.main()