# Implementation conventions

- Python 3.11 only; reuse the parent project virtual environment, never create a nested one.
- Source package: wealth_management_mcp. Snake-case modules/functions, CapWords classes, typed boundaries.
- Four Power BI tool roles come from https://learn.microsoft.com/en-us/power-bi/developer/mcp/remote-mcp-server-tools.
- Actual external wire contracts require discovery; do not fabricate verified Microsoft tool names.
- SDK references: https://github.com/modelcontextprotocol/python-sdk and https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/.
- Semantic definitions use the separately identified OSSIE-derived Fabric/DAX profile, not unmodified upstream conformance.
- No inbound POC auth, no per-caller RLS claims. Loopback HTTP unless operator explicitly confirms restricted network deployment.
- Never log tokens, result rows or raw upstream exception messages. Never change model security or publish cloud resources implicitly.