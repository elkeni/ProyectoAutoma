#!/usr/bin/env bash
set -euo pipefail

# Hook PostToolUse. Valida solo archivos Python y el pyproject recién escritos.
payload="$(cat)"
project_root="${CLAUDE_PROJECT_DIR:-$(pwd)}"

if command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
else
  echo "ERROR: verificar_python.sh necesita Python." >&2
  exit 2
fi

HOOK_PAYLOAD="$payload" "$python_bin" - "$project_root" <<'PY'
from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tokenize
import tomllib
from typing import Any


def fail(message: str) -> None:
    print(f"VALIDACIÓN FALLIDA: {message}", file=sys.stderr)
    raise SystemExit(2)


try:
    event = json.loads(os.environ.get("HOOK_PAYLOAD", ""))
except json.JSONDecodeError:
    fail("evento JSON inválido")

root = Path(sys.argv[1]).expanduser().resolve(strict=False)
tool_input = event.get("tool_input") or {}


def collect_paths(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"file_path", "path", "notebook_path", "target_file"}:
                if isinstance(item, str):
                    found.append(item)
                elif isinstance(item, list):
                    found.extend(entry for entry in item if isinstance(entry, str))
            elif isinstance(item, (dict, list)):
                found.extend(collect_paths(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_paths(item))
    return found


targets: list[Path] = []
for raw_path in collect_paths(tool_input):
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve(strict=False)
    if path.exists() and path.is_file() and path not in targets:
        targets.append(path)

for path in targets:
    if path.suffix.lower() == ".py":
        try:
            with tokenize.open(path) as source_file:
                ast.parse(source_file.read(), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            fail(f"{path}: {exc}")

        # Ruff es obligatorio en el entorno dev, pero el hook sigue siendo útil en
        # un bootstrap sin dependencias instaladas.
        if importlib.util.find_spec("ruff") is not None:
            result = subprocess.run(
                [sys.executable, "-m", "ruff", "check", str(path)],
                check=False,
            )
            if result.returncode != 0:
                fail(f"Ruff rechazó {path}")

    if path.name == "pyproject.toml":
        try:
            with path.open("rb") as toml_file:
                data = tomllib.load(toml_file)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            fail(f"pyproject.toml no es TOML válido: {exc}")

        commands = data.get("tool", {}).get("sagale", {}).get("commands", {})
        required = {"setup", "phase1", "test", "lint", "format_check", "types", "syntax"}
        missing = sorted(required.difference(commands))
        if missing:
            fail(f"faltan comandos canónicos en [tool.sagale.commands]: {missing}")
        invalid = sorted(
            key for key in required if not isinstance(commands.get(key), str) or not commands[key]
        )
        if invalid:
            fail(f"comandos canónicos vacíos o no textuales: {invalid}")

raise SystemExit(0)
PY
