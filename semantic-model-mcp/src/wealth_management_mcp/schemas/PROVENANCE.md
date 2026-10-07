# OSSIE-derived Fabric/DAX Profile 1.0.0

This is a project-owned derivative, not Apache-endorsed DAX support and not
conformance to the unmodified OSSIE schema. Power BI remains the DAX authority.

## Verified upstream source

The schema is from Apache Ossie (incubating), commit
28365cd638f3833765c5b940ada5b8cbc65f1c42, core specification 0.2.0.dev0.
The complete source URL and independently verified upstream SHA-256 are recorded
in source-manifest.json. LICENSE and NOTICE reproduce upstream licensing and
attribution. The manifest includes their verified source hashes and URLs.

The source was fetched and its **11325 original bytes** verified against
22be177612ed665e0af244c586b9c0162f2a3706f8e9b061910f7a8e2a19b8e8.
The Windows editor changes text line endings and can omit the final newline.
Therefore this is a **manifest-verified source archive**, not a claim that the
physical checkout bytes always equal upstream. The loader reconstructs LF line
endings and one terminal LF, verifies the pinned digest, and only then parses.
All other bytes must match; even JSON-equivalent reformatting is rejected.
No network access or arbitrary source locator resolution occurs at validation.

## Explicit derivative changes

`load_profile_schema()` obtains a fresh verified base copy, then performs only:

1. Replace `$id` with `urn:wealth-management-mcp:schema:ossie-fabric-dax:1.0.0`.
2. Replace title/description to identify the project derivative prominently.
3. Replace `properties.version.const` with `1.0.0` and describe it as the profile version.
4. Append `DAX` to `$defs.Dialect.enum`; retain all original dialects.

No unmodified-base `allOf` is used to attempt to widen an enum. All original
object closure, fields, datatypes and expression nesting are retained.

The separate Python schema factory in this directory supplies closed, local-only
schemas for the envelope and each **decoded** object-local POWER_BI payload.
It is new project code, not an alteration of the archived base. Required kinds
are model, dataset, field, metric and relationship, all extension version 1.0.0.
The outer `custom_extensions[].data` stays a JSON string. Semantic validation
additionally checks names, native bindings, coverage, source/output pointers,
required warnings, expressions and hashes. The private author projection is
not a per-user security/disclosure policy or a model-definition signature.

## Deliberate scope

Only evidenced Import storage, ordinary/calculated columns with known kinds,
and M:1 relationships with known Single/Both filtering are accepted. Hierarchies,
calculation groups and calculated tables require a future reviewed mapping.
Single ordinary filtering travels from the one-side `to` to many-side `from`.
Inactive relationships remain first-class entries but are excluded from the
default graph. Security filtering is independently **not_extracted**.

Null native properties mean no value was supplied by this extraction. Field
types remain native (Double becomes Float, not Decimal); measure types can be
unknown. Positive column key flags are retained as evidence, but no primary or
unique constraint is promoted without independently verified constraint semantics.
There is no automatic date-role inference or DAX parsing/evaluation. Reviewed
free text must not contain secrets. Source M queries, security expressions,
memberships and credentials are not projected. There is no live/authenticated
metadata parity, data-freshness guarantee, query result, or per-user security.

The module docstring documents strict overlay paths and shape. Catalog hashing
includes the envelope excluding its own hash, with source artifact byte hashes;
it uses no generated timestamp. Re-exported source bytes may change provenance
even when the model objects are unchanged.