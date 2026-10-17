#!/usr/bin/env bash
set -euo pipefail

# Hook PreToolUse. Recibe el evento JSON de Claude Code por stdin.
payload="$(cat)"
project_root="${CLAUDE_PROJECT_DIR:-$(pwd)}"

if command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
else
  echo "BLOQUEADO: proteger.sh necesita Python para inspeccionar la operación." >&2
  exit 2
fi

HOOK_PAYLOAD="$payload" "$python_bin" - "$project_root" <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any


def block(reason: str) -> None:
    print(f"BLOQUEADO por gobernanza: {reason}", file=sys.stderr)
    raise SystemExit(2)


try:
    event = json.loads(os.environ.get("HOOK_PAYLOAD", ""))
except json.JSONDecodeError:
    block("el hook no recibió un evento JSON válido; se aplica cierre seguro")

root = Path(sys.argv[1]).expanduser().resolve(strict=False)
diagrams = (root / "DIAGRAMAS").resolve(strict=False)
git_metadata = (root / ".git").resolve(strict=False)
tool_name = str(event.get("tool_name", ""))
tool_input = event.get("tool_input") or {}


def is_within(path: Path, parent: Path) -> bool:
    try:
        left = os.path.normcase(str(path.resolve(strict=False)))
        right = os.path.normcase(str(parent.resolve(strict=False)))
        return os.path.commonpath([left, right]) == right
    except ValueError:
        return False


def iter_target_paths(value: Any) -> list[str]:
    targets: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"file_path", "path", "notebook_path", "target_file"}:
                if isinstance(item, str):
                    targets.append(item)
                elif isinstance(item, list):
                    targets.extend(entry for entry in item if isinstance(entry, str))
            elif isinstance(item, (dict, list)):
                targets.extend(iter_target_paths(item))
    elif isinstance(value, list):
        for item in value:
            targets.extend(iter_target_paths(item))
    return targets


def is_secret_path(path: Path) -> bool:
    lowered = [part.lower() for part in path.parts]
    name = path.name.lower()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if name in {"credentials.json", "service-account.json", "secrets.json"}:
        return True
    if any(part in {"secrets", ".secrets"} for part in lowered):
        return True
    return bool(re.search(r"(?:credential|service[-_]?account).+\.json$", name))


for raw_path in iter_target_paths(tool_input):
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=False)
    if not is_within(candidate, root):
        block(f"escritura fuera del proyecto: {raw_path}")
    if is_within(candidate, diagrams):
        block(f"DIAGRAMAS es fuente de verdad de solo lectura: {raw_path}")
    if is_within(candidate, git_metadata):
        block(f"no se modifica metadata interna de Git directamente: {raw_path}")
    if is_secret_path(candidate):
        block(f"no se escriben secretos o credenciales: {raw_path}")

# Los comandos de shell no exponen siempre un file_path estructurado. Se bloquean
# mutaciones evidentes cuando apuntan a destinos protegidos; las lecturas continúan.
if tool_name.lower() in {"bash", "powershell"}:
    command = str(tool_input.get("command", ""))
    normalized = command.replace("\\", "/").lower()
    mutation = re.search(
        r"(?:\b(?:rm|mv|cp|install|truncate|tee)\b|"
        r"\bsed\s+-i\b|\bperl\s+-pi\b|\bapply_patch\b|"
        r"\bgit\s+(?:checkout|restore|clean|reset|apply)\b|"
        r"\b(?:set-content|out-file|remove-item|move-item|copy-item)\b|"
        r"(?:^|[^<])>{1,2})",
        normalized,
    )
    diagram_reference = re.search(
        r"(?:^|[/\s'\"`])(?:\./)?diagramas(?:/|$|[\s'\"`])", normalized
    )
    git_reference = re.search(
        r"(?:^|[/\s'\"`])(?:\./)?\.git(?:/|$|[\s'\"`])", normalized
    )
    if mutation and diagram_reference:
        block("el comando intenta mutar DIAGRAMAS")
    if mutation and git_reference:
        block("el comando intenta mutar .git directamente")
    secret_reference = re.search(
        r"(?:^|[/\s'\"])(?:\.env(?:\.[^/\s'\"]+)?|secrets?|credentials\.json)(?:$|[/\s'\"])",
        normalized,
    )
    if mutation and secret_reference:
        block("el comando intenta escribir un archivo de secretos")

raise SystemExit(0)
PY
