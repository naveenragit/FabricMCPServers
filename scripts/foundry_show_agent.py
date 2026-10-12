"""Print the latest version definition of an existing agent (read-only)."""

import json
import os
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential

for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=AzureCliCredential())
name = sys.argv[1]
agent = project.agents.get(name)
print(json.dumps(agent.as_dict(), indent=2, default=str)[:6000])
