"""Resolve the Power BI private-link namespaces so private routing can be verified.

The Power BI tenant private endpoint creates records in ``privatelink.analysis.windows.net``,
``privatelink.pbidedicated.windows.net`` and ``privatelink.prod.powerquery.microsoft.com``.
``api.powerbi.com`` itself is a global front door and is not part of those zones, so it is
reported separately rather than implied to be private.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
from typing import Any

#: Namespaces covered by the Microsoft.PowerBI/privateLinkServicesForPowerBI 'tenant' endpoint.
PRIVATE_LINK_ZONES = (
    "privatelink.analysis.windows.net",
    "privatelink.pbidedicated.windows.net",
    "privatelink.prod.powerquery.microsoft.com",
)

#: Hostnames the server actually contacts, checked for private versus public resolution.
PROBE_HOSTS = (
    "api.powerbi.com",
    "login.microsoftonline.com",
)


def _classify(addresses: list[str]) -> str:
    if not addresses:
        return "unresolved"
    parsed = [ipaddress.ip_address(item) for item in addresses]
    if all(item.is_private for item in parsed):
        return "private"
    if any(item.is_private for item in parsed):
        return "mixed"
    return "public"


def _resolve(host: str) -> dict[str, Any]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        return {"host": host, "addresses": [], "routing": "unresolved", "error": error.strerror}
    addresses = sorted({str(info[4][0]) for info in infos})
    return {"host": host, "addresses": addresses, "routing": _classify(addresses)}


async def network_report(tenant_id: str = "") -> dict[str, Any]:
    """Report how the Power BI endpoints resolve from this host."""
    hosts = list(PROBE_HOSTS)
    if tenant_id:
        # The tenant-specific endpoint published once Azure Private Link is enabled.
        hosts.append(f"{tenant_id.replace('-', '')}-api.analysis.windows.net")
    results = await asyncio.gather(*(asyncio.to_thread(_resolve, host) for host in hosts))
    private = [item for item in results if item["routing"] == "private"]
    return {
        "private_link_zones": list(PRIVATE_LINK_ZONES),
        "resolutions": list(results),
        "private_link_active": bool(private),
        "note": (
            "'private' means the name resolved to a VNet address through the private endpoint. "
            "api.powerbi.com is a global front door and is expected to stay public; the tenant "
            "cluster under analysis.windows.net is the namespace the private link covers."
        ),
    }


async def identity_report(settings: Any) -> dict[str, Any]:
    """Report upstream status for the Power BI and Fabric endpoints, so failures can be told apart.

    Returns status codes and non-secret token claims only: never the token or model data.
    Without this the server's sanitized tool errors cannot distinguish a rejected token from
    a missing workspace role, nor a Power BI-only rejection from a global one.
    """
    import httpx2
    from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

    from .backend import POWER_BI_SCOPE

    report: dict[str, Any] = {"credential_mode": settings.credential_mode, "token_acquired": False}
    credential: Any = (
        AzureCliCredential(tenant_id=settings.tenant_id, process_timeout=20)
        if settings.credential_mode == "azure_cli"
        else ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    )
    try:
        async with credential:
            try:
                token = await credential.get_token(POWER_BI_SCOPE)
            except Exception as error:
                report["token_error"] = type(error).__name__
                return report
            report["token_acquired"] = True
            report["token_claims"] = _claims(token.token)
            probes = {
                "power_bi_workspace": f"https://api.powerbi.com/v1.0/myorg/groups/{settings.workspace_id}",
                "fabric_workspace": f"https://api.fabric.microsoft.com/v1/workspaces/{settings.workspace_id}",
                "power_bi_dataset": (
                    f"https://api.powerbi.com/v1.0/myorg/groups/{settings.workspace_id}"
                    f"/datasets/{settings.semantic_model_id}"
                ),
                "fabric_semantic_model": (
                    f"https://api.fabric.microsoft.com/v1/workspaces/{settings.workspace_id}"
                    f"/semanticModels/{settings.semantic_model_id}"
                ),
            }
            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token.token}", "Accept-Encoding": "identity"},
                timeout=httpx2.Timeout(30),
                follow_redirects=False,
            ) as client:
                for name, url in probes.items():
                    try:
                        response = await client.get(url)
                    except Exception as error:
                        report[name] = {"transport_error": type(error).__name__}
                        continue
                    entry: dict[str, Any] = {"http_status": response.status_code}
                    if response.status_code >= 400:
                        entry["error"] = response.text[:300]
                        entry["hint"] = _hint(response.status_code)
                    report[name] = entry

                # Execute Queries is gated separately from ordinary metadata reads.
                # executeDaxQueries is the newer Fabric-capacity endpoint; probe both
                # because they authorize through different paths.
                for name, path, body in (
                    (
                        "execute_queries",
                        "executeQueries",
                        {"queries": [{"query": 'EVALUATE ROW("probe", 1)'}]},
                    ),
                    (
                        "execute_dax_queries",
                        "executeDaxQueries",
                        {"query": 'EVALUATE ROW("probe", 1)'},
                    ),
                ):
                    url = (
                        f"https://api.powerbi.com/v1.0/myorg/groups/{settings.workspace_id}"
                        f"/datasets/{settings.semantic_model_id}/{path}"
                    )
                    try:
                        response = await client.post(url, json=body)
                        entry = {"http_status": response.status_code}
                        if response.status_code >= 400:
                            entry["error"] = response.text[:400]
                            entry["hint"] = _execute_hint(response.status_code)
                        report[name] = entry
                    except Exception as error:
                        report[name] = {"transport_error": type(error).__name__}
            return report
    except Exception as error:
        report["transport_error"] = type(error).__name__
        return report


def _claims(token: str) -> dict[str, Any]:
    """Decode non-secret identity claims from the JWT payload; signature is not verified."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {"decoded": False}
    return {
        "decoded": True,
        "aud": data.get("aud"),
        "tid": data.get("tid"),
        "oid": data.get("oid"),
        "appid": data.get("appid") or data.get("azp"),
        "idtyp": data.get("idtyp"),
    }


def _execute_hint(status: int) -> str:
    if status == 401:
        return (
            "Metadata reads succeed but Execute Queries does not, so this is gated "
            "separately: check the 'Dataset Execute Queries REST API' tenant setting "
            "(Tenant settings > Developer settings) and its security-group scope."
        )
    if status == 403:
        return "Needs Build permission on the semantic model, or workspace Contributor or higher."
    return "Unexpected upstream status; inspect error."


def _hint(status: int) -> str:
    if status == 401:
        return (
            "Token rejected before item permissions were evaluated. Typically the Fabric "
            "tenant setting 'Service principals can use Fabric APIs' (Tenant settings > "
            "Developer settings) is off or not scoped to a group containing this identity."
        )
    if status == 403:
        return "Token accepted but the identity lacks access to this workspace or item."
    return "Unexpected upstream status; inspect error."
