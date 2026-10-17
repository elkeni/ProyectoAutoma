"""Validador autocontenido de los artefactos entregables de la FASE 1.

No necesita dependencias externas. Toma los BPMN como fuente de verdad y falla ante
desfases de conteo, IDs, HITL, conexiones n8n o archivos de gobernanza inválidos.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BPMN_DIR = ROOT / "DIAGRAMAS" / "editables"
N8N_DIR = ROOT / "n8n_workflows"
NS = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
FLOW_NODE_TAGS = {
    "startEvent",
    "endEvent",
    "serviceTask",
    "userTask",
    "exclusiveGateway",
    "eventBasedGateway",
    "intermediateCatchEvent",
    "boundaryEvent",
}
HITL_IDS = {
    "p1_incidente",
    "p2_revision_fallo",
    "p2_legal",
    "p2_subsanacion",
    "p2_archivar",
    "p3_revision",
    "p3_firma",
}
BPMN_FILES = {
    "ASIS": ("01_ASIS_minuta_reconstruido.bpmn", "D3_Process", 28),
    "P01": ("02_TOBE_P01_registro.bpmn", "D4_Process", 22),
    "P02": ("03_TOBE_P02_analisis.bpmn", "D5_Process", 22),
    "P03": ("04_TOBE_P03_cierre.bpmn", "D6_Process", 14),
}


class ValidationError(RuntimeError):
    """Error acumulable de coherencia de FASE 1."""


errors: list[str] = []
checks = 0


def require(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        errors.append(message)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"No se pudo leer {path.relative_to(ROOT)}: {exc}")
        return ""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        errors.append(f"JSON inválido en {path.relative_to(ROOT)}: {exc}")
        return {}
    require(isinstance(value, dict), f"{path.relative_to(ROOT)} debe contener un objeto JSON")
    return value if isinstance(value, dict) else {}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def audit_bpmn() -> dict[str, list[str]]:
    ids_by_process: dict[str, list[str]] = {}
    for label, (filename, process_id, expected_count) in BPMN_FILES.items():
        path = BPMN_DIR / filename
        require(path.is_file(), f"Falta BPMN: {path.relative_to(ROOT)}")
        if not path.is_file():
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            errors.append(f"XML inválido en {filename}: {exc}")
            continue
        process = root.find(f"bpmn:process[@id='{process_id}']", NS)
        require(process is not None, f"{filename} no contiene el proceso {process_id}")
        if process is None:
            continue
        ids = [
            child.attrib["id"]
            for child in process
            if local_name(child.tag) in FLOW_NODE_TAGS and "id" in child.attrib
        ]
        require(
            len(ids) == expected_count,
            f"{label}: se esperaban {expected_count} nodos; hay {len(ids)}",
        )
        require(len(ids) == len(set(ids)), f"{label}: hay IDs de nodo duplicados")
        ids_by_process[label] = ids

        known_ids = {element.attrib["id"] for element in root.iter() if "id" in element.attrib}
        for flow in process.findall("bpmn:sequenceFlow", NS):
            source = flow.attrib.get("sourceRef", "")
            target = flow.attrib.get("targetRef", "")
            require(source in known_ids, f"{label}: sourceRef inexistente {source}")
            require(target in known_ids, f"{label}: targetRef inexistente {target}")

    tobe_ids = [node for label in ("P01", "P02", "P03") for node in ids_by_process.get(label, [])]
    require(len(ids_by_process.get("ASIS", [])) == 28, "El AS-IS debe contener 28 nodos")
    require(len(tobe_ids) == 58, "Los TO-BE deben contener 58 nodos (22 + 22 + 14)")

    actual_hitl: set[str] = set()
    for label in ("P01", "P02", "P03"):
        filename, process_id, _ = BPMN_FILES[label]
        root = ET.parse(BPMN_DIR / filename).getroot()
        process = root.find(f"bpmn:process[@id='{process_id}']", NS)
        if process is not None:
            actual_hitl.update(task.attrib["id"] for task in process.findall("bpmn:userTask", NS))
    require(
        actual_hitl == HITL_IDS, f"HITL reales {sorted(actual_hitl)}; esperados {sorted(HITL_IDS)}"
    )

    asis_root = ET.parse(BPMN_DIR / BPMN_FILES["ASIS"][0]).getroot()
    conditions = {
        event.attrib.get("id"): "".join(event.itertext()).strip()
        for event in asis_root.findall(".//bpmn:intermediateCatchEvent", NS)
    }
    require(
        "turnoDisponible" in conditions.get("as_turno", ""), "as_turno debe usar turnoDisponible"
    )
    require(
        "titularDisponible" in conditions.get("as_titular", ""),
        "as_titular debe usar titularDisponible",
    )
    timers = {
        event.attrib.get("id"): "".join(event.itertext()).strip()
        for event in asis_root.findall(".//bpmn:intermediateCatchEvent", NS)
    }
    require("P5D" in timers.get("as_plazo_recaudos", ""), "as_plazo_recaudos debe contener P5D")
    require("PT72H" in timers.get("as_plazo_cliente", ""), "as_plazo_cliente debe contener PT72H")
    return ids_by_process


def audit_workflow(path: Path, required_ids: list[str], expected_name: str) -> dict[str, Any]:
    workflow = load_json(path)
    require(workflow.get("name") == expected_name, f"Nombre de workflow incorrecto en {path.name}")
    nodes = workflow.get("nodes")
    connections = workflow.get("connections")
    require(
        isinstance(nodes, list) and bool(nodes), f"{path.name}: nodes debe ser una lista no vacía"
    )
    require(isinstance(connections, dict), f"{path.name}: connections debe ser un objeto")
    if not isinstance(nodes, list) or not isinstance(connections, dict):
        return workflow

    names = [node.get("name") for node in nodes if isinstance(node, dict)]
    node_ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    require(
        all(isinstance(name, str) and name for name in names), f"{path.name}: hay nodos sin nombre"
    )
    require(len(names) == len(set(names)), f"{path.name}: hay nombres de nodo duplicados")
    require(
        all(isinstance(node_id, str) and node_id for node_id in node_ids),
        f"{path.name}: hay nodos sin UUID",
    )
    require(len(node_ids) == len(set(node_ids)), f"{path.name}: hay UUID de nodo duplicados")
    known = set(names)

    for source, connection in connections.items():
        require(source in known, f"{path.name}: conexión parte de nodo inexistente {source}")
        outputs = connection.get("main") if isinstance(connection, dict) else None
        require(isinstance(outputs, list), f"{path.name}: conexión main inválida en {source}")
        if not isinstance(outputs, list):
            continue
        for output in outputs:
            require(isinstance(output, list), f"{path.name}: salida inválida en {source}")
            if not isinstance(output, list):
                continue
            for edge in output:
                target = edge.get("node") if isinstance(edge, dict) else None
                require(target in known, f"{path.name}: destino inexistente {source} -> {target}")

    searchable = json.dumps(nodes, ensure_ascii=False)
    for bpmn_id in required_ids:
        require(
            re.search(rf"(?<![A-Za-z0-9_]){re.escape(bpmn_id)}(?![A-Za-z0-9_])", searchable)
            is not None,
            f"{path.name}: falta el ID BPMN {bpmn_id}",
        )

    webhooks = [node for node in nodes if node.get("type") == "n8n-nodes-base.webhook"]
    require(len(webhooks) == 1, f"{path.name}: debe contener un único Webhook inicial")
    if webhooks:
        require(
            webhooks[0].get("parameters", {}).get("responseMode") == "responseNode",
            f"{path.name}: Webhook no usa responseNode",
        )

    for node in nodes:
        if node.get("onError") == "continueErrorOutput":
            outputs = connections.get(node.get("name"), {}).get("main", [])
            require(
                len(outputs) > 1 and bool(outputs[1]),
                f"{path.name}: {node.get('name')} no conecta su salida de error",
            )

    node_by_prefix = {str(node.get("name", "")).split(" · ", 1)[0]: node for node in nodes}
    for hitl_id in HITL_IDS.intersection(required_ids):
        node = node_by_prefix.get(hitl_id)
        require(node is not None, f"{path.name}: falta nodo HITL {hitl_id}")
        if node is not None:
            require(
                node.get("type") == "n8n-nodes-base.wait", f"{path.name}: {hitl_id} debe ser Wait"
            )
            require(
                node.get("parameters", {}).get("resume") == "webhook",
                f"{path.name}: {hitl_id} no reanuda por webhook",
            )

    wait_seconds = sorted(
        node.get("parameters", {}).get("amount")
        for node in nodes
        if node.get("type") == "n8n-nodes-base.wait"
        and node.get("parameters", {}).get("resume") == "timeInterval"
        and node.get("parameters", {}).get("unit") == "seconds"
    )
    require(
        wait_seconds == [2, 4, 8],
        f"{path.name}: backoff materializado debe ser [2, 4, 8], no {wait_seconds}",
    )

    p2_wait = next(
        (node for node in nodes if str(node.get("name", "")).startswith("p2_esperar")), None
    )
    require(p2_wait is not None, f"{path.name}: falta p2_esperar")
    if p2_wait:
        params = p2_wait.get("parameters", {})
        require(params.get("limitWaitTime") is True, f"{path.name}: p2_esperar no limita espera")
        require(
            (params.get("resumeAmount"), params.get("resumeUnit")) == (5, "days"),
            f"{path.name}: p2_esperar no implementa P5D",
        )

    require(
        "credentials" not in searchable.lower(),
        f"{path.name}: no debe exportar IDs de credenciales",
    )
    return workflow


def audit_governance() -> None:
    required = [
        ROOT / "CLAUDE.md",
        ROOT / "AGENTS.md",
        ROOT / "LEEME_entorno.md",
        ROOT / ".claude" / "settings.json",
        ROOT / ".claude" / "hooks" / "proteger.sh",
        ROOT / ".claude" / "hooks" / "verificar_python.sh",
        ROOT / ".mcp.json",
        ROOT / "pyproject.toml",
    ]
    for path in required:
        require(path.is_file(), f"Falta archivo de gobernanza {path.relative_to(ROOT)}")
    skills = sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
    require(len(skills) == 5, f"Se esperaban 5 skills de gobernanza; hay {len(skills)}")

    settings = load_json(ROOT / ".claude" / "settings.json")
    mcp = load_json(ROOT / ".mcp.json")
    require("hooks" in settings, ".claude/settings.json debe registrar hooks")
    require("sagale-mcp" in mcp.get("mcpServers", {}), ".mcp.json debe declarar sagale-mcp")

    try:
        with (ROOT / "pyproject.toml").open("rb") as source:
            project = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"pyproject.toml inválido: {exc}")
        return
    commands = project.get("tool", {}).get("sagale", {}).get("commands", {})
    require(
        commands.get("phase1") == "python scripts/validar_fase1.py",
        "pyproject.toml debe registrar el comando phase1",
    )

    combined = "\n".join(read_text(ROOT / name) for name in ("CLAUDE.md", "AGENTS.md"))
    for token in (
        "get_llm()",
        "expediente_id",
        "Pydantic",
        "interrupt()",
        "Ley 29733",
        "SHA-256",
        "P5D",
        "PT72H",
        "58 nodos",
        "7 puntos HITL",
    ):
        require(token in combined, f"Gobernanza: falta el control {token}")


def audit_portal(all_bpmn_ids: list[str]) -> None:
    path = ROOT / "docs" / "catalogo_tutorial.html"
    require(path.is_file(), "Falta docs/catalogo_tutorial.html")
    html = read_text(path)
    for bpmn_id in all_bpmn_ids:
        require(
            re.search(rf"(?<![A-Za-z0-9_]){re.escape(bpmn_id)}(?![A-Za-z0-9_])", html) is not None,
            f"Portal: falta el ID BPMN {bpmn_id}",
        )
    for marker in (
        "28 → 58",
        "58</strong>",
        "puntos HITL verificados",
        "prefers-reduced-motion",
        "skip-link",
    ):
        require(marker in html, f"Portal: falta marcador {marker}")
    require("28 → 57" not in html, "Portal conserva el conteo obsoleto 28 → 57")
    require("D6_Process · 13 nodos" not in html, "Portal conserva el conteo obsoleto P03=13")


def main() -> int:
    ids_by_process = audit_bpmn()
    p1_p2 = ids_by_process.get("P01", []) + ids_by_process.get("P02", [])
    all_tobe = p1_p2 + ids_by_process.get("P03", [])
    audit_workflow(
        N8N_DIR / "01_TOBE1_flujo_rpa_y_analisis_actualizado.json",
        p1_p2,
        "01_TOBE1_flujo_rpa_y_analisis_actualizado",
    )
    workflow2 = audit_workflow(
        N8N_DIR / "02_TOBE2_flujo_multiagentico_completo.json",
        all_tobe,
        "02_TOBE2_flujo_multiagentico_completo",
    )

    nodes2 = workflow2.get("nodes", []) if isinstance(workflow2, dict) else []
    types2 = {node.get("type") for node in nodes2 if isinstance(node, dict)}
    searchable2 = json.dumps(nodes2, ensure_ascii=False)
    require("n8n-nodes-base.postgres" in types2, "TO-BE 2 debe contener BD relacional PostgreSQL")
    for token in ("Memoria", "Orquestador", "A0", "A1", "A2", "A3", "WhatsApp"):
        require(token in searchable2, f"TO-BE 2: falta {token}")
    p3_wait = next(
        (node for node in nodes2 if str(node.get("name", "")).startswith("p3_esperar")), None
    )
    require(p3_wait is not None, "TO-BE 2: falta p3_esperar")
    if p3_wait:
        params = p3_wait.get("parameters", {})
        require(
            (params.get("resumeAmount"), params.get("resumeUnit")) == (72, "hours"),
            "TO-BE 2: p3_esperar no implementa PT72H",
        )

    audit_governance()
    all_ids = ids_by_process.get("ASIS", []) + all_tobe
    audit_portal(all_ids)

    if errors:
        print(
            f"FASE 1 inválida: {len(errors)} error(es), {checks} comprobaciones.", file=sys.stderr
        )
        for index, error in enumerate(errors, start=1):
            print(f"  {index}. {error}", file=sys.stderr)
        return 1
    print(f"FASE 1 válida: {checks} comprobaciones superadas.")
    print("BPMN: AS-IS=28, P01=22, P02=22, P03=14; TO-BE=58; HITL=7.")
    print("n8n: 2 workflows conectados, sin credenciales exportadas y con cobertura BPMN completa.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
