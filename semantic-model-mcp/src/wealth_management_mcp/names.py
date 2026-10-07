"""Deterministic public identifiers; native DAX bindings are never rewritten."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


def normalize_name(native_name: str) -> str:
    """NFKC/casefold, percent -> pct, separators -> underscore; reject empty names.

    This is an alias, not a reversible encoding. Every use must go through a
    registry so e.g. ``Margin %`` and ``margin_pct`` cannot silently coalesce.
    Existing ASCII snake_case names (including dim_/fact_) remain unchanged.
    """
    if not isinstance(native_name, str) or not native_name.strip():
        raise ValueError("Native name must be a nonblank string")
    if any(unicodedata.category(char) == "Cc" for char in native_name):
        raise ValueError("Control characters are not allowed in native names")
    name = unicodedata.normalize("NFKC", native_name).casefold().replace("%", "_pct_")
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    if not name:
        raise ValueError("Native name has no supported canonical characters")
    return f"n_{name}" if name[0].isdigit() else name


@dataclass
class NameRegistry:
    """Collision-rejecting namespaces with lossless native-name lookup.

    Table/metric/relationship names have separate model-wide namespaces; fields
    use one namespace per table. Duplicate registrations are errors, even when
    the original spelling is identical. No order-dependent numeric suffixes.
    """

    _names: dict[str, dict[str, str]] = field(default_factory=dict)

    def register(self, namespace: str, native_name: str) -> str:
        canonical = normalize_name(native_name)
        names = self._names.setdefault(namespace, {})
        if canonical in names:
            raise ValueError(f"Duplicate name or normalization collision in {namespace}: {canonical}")
        names[canonical] = native_name
        return canonical

    def native_name(self, namespace: str, canonical: str) -> str:
        try:
            return self._names[namespace][canonical]
        except KeyError:
            raise ValueError(f"Unknown canonical reference in {namespace}: {canonical}") from None

    def canonical_name(self, namespace: str, native_name: str) -> str:
        canonical = normalize_name(native_name)
        if self.native_name(namespace, canonical) != native_name:
            raise ValueError(f"Native binding mismatch in {namespace}: {canonical}")
        return canonical


def dax_measure_reference(native_name: str) -> str:
    """Quote a native measure/column identifier, not arbitrary DAX code."""
    normalize_name(native_name)
    return "[" + native_name.replace("]", "]]") + "]"


def dax_column_reference(native_table: str, native_column: str) -> str:
    """Return a row-context column reference with escaped native identifiers."""
    normalize_name(native_table)
    return "'" + native_table.replace("'", "''") + "'" + dax_measure_reference(native_column)