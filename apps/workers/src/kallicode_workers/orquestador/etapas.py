"""Ejecución de las etapas de triage, design y build del orquestador."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContentSettings
from kallicode_core.adjuntos import cliente_blob
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, todos, uno
from kallicode_core.grafo import GrafoCliente, grafo_cliente
from kallicode_core.llm import ejecutar_paso
from kallicode_core.llm.cliente import ResultadoPaso
from kallicode_core.llm.tiers import Tier
from kallicode_core.logging import log
from kallicode_core.pipeline import EstadoJob

from .cliente_interno import ClienteInterno


async def ejecutar_triage(
    job: dict[str, Any],
    cliente: ClienteInterno,
    *,
    intento: int,
    grafo: GrafoCliente | None = None,
) -> None:
    """Ejecuta los tres pasos de triage y publica el resultado agregado.

    En el Sprint 4:
      - triage.classify usa los datos del ticket.
      - triage.dedup recibe candidatos vacíos (la búsqueda vectorial por
        similitud no existe todavía en la capa de vectores; llega en un
        sprint posterior).
      - triage.impact recibe un subgrafo vacío del GrafoClienteDoble; la
        implementación real sobre el esquema cm llega en el Sprint 10
        (KC-BE-10-6).
    """
    tenant_id: str = job["tenant_id"]
    job_id: str = job["id"]
    ticket_id: str = job["ticket_id"]
    payload: dict = job.get("contexto") or {}

    # El endpoint POST /tickets/{numero}/triage-result espera el número
    # legible del ticket (KC-####), no su id interno (tk_...). El job
    # guarda ticket_id, pero no el numero: lo consultamos del ticket.
    async with sesion_sistema() as db:
        ticket_row = await uno(
            db,
            "SELECT numero FROM core.tickets WHERE id = :id AND tenant_id = :t",
            {"id": ticket_id, "t": tenant_id},
        )
    numero = ticket_row["numero"] if ticket_row else ticket_id

    # --- paso 1: classify ---
    r_classify = await ejecutar_paso(
        "triage.classify",
        {
            "ticket_id": numero,
            "titulo": payload.get("titulo", ""),
            "origen": payload.get("origen", "desconocido"),
            "descripcion": payload.get("descripcion", ""),
            "adjuntos_transcritos": [],
        },
    )
    await cliente.publicar_paso(
        job_id=job_id,
        tenant_id=tenant_id,
        paso=_paso_dict("triage", r_classify),
        intento=intento,
    )

    # --- paso 2: dedup (candidatos vacíos en Sprint 4) ---
    r_dedup = await ejecutar_paso(
        "triage.dedup",
        {
            "ticket": r_classify.salida,
            "candidatos": [],
        },
    )
    await cliente.publicar_paso(
        job_id=job_id,
        tenant_id=tenant_id,
        paso=_paso_dict("triage", r_dedup),
        intento=intento,
    )

    # --- paso 3: impact (subgrafo vía GrafoClienteDoble en Sprint 4) ---
    gc: GrafoCliente = grafo or grafo_cliente()
    sub = await gc.subgrafo_ticket(tenant_id, numero)
    r_impact = await ejecutar_paso(
        "triage.impact",
        {
            "ticket": {
                "classify": r_classify.salida,
                "dedup": r_dedup.salida,
            },
            "subgrafo": {
                "nodes": list(sub.nodos),
                "edges": list(sub.aristas),
                "inbound_deps": [],
            },
            "incidentes": [],
        },
    )
    await cliente.publicar_paso(
        job_id=job_id,
        tenant_id=tenant_id,
        paso=_paso_dict("triage", r_impact),
        intento=intento,
    )

    # --- publicación agregada: triage-result ---
    await cliente.publicar_triage(
        numero=numero,
        tenant_id=tenant_id,
        resultado=_triage_result(r_classify, r_dedup, r_impact),
        intento=intento,
    )

    log.info(
        "orquestador.triage_completado",
        job_id=job_id,
        tenant_id=tenant_id,
        tipo=r_classify.salida.get("type"),
        prioridad=r_impact.salida.get("priority"),
        complejidad=r_impact.salida.get("estimated_complexity"),
    )


def _paso_dict(
    etapa: str,
    r: ResultadoPaso,
    *,
    paso_id: str | None = None,
    entrada_ref: str | None = None,
    salida_ref: str | None = None,
) -> dict[str, Any]:
    """Convierte un ResultadoPaso en el payload de StepIn del API interna.

    El campo `version_plantilla` del StepIn se rellena con `r.version`, que
    es el hash canónico de 12 caracteres de la plantilla ejecutada
    (calculado en plantillas.py). `reintentos` cuenta los intentos fallidos
    previos al éxito final.
    """
    return {
        "etapa": etapa,
        "paso": paso_id or r.paso_id,
        "version_plantilla": r.version,
        "modelo": r.modelo,
        "tier": r.tier.value,
        "confianza": r.confianza,
        "validacion": r.validacion,
        "reintentos": len(r.intentos),
        "tokens_in": r.tokens_in,
        "tokens_out": r.tokens_out,
        "duracion_ms": r.duracion_ms,
        # Triage conserva referencias temporales; Design y Build las sustituyen
        # por referencias Blob al persistir sus contratos completos.
        "entrada_ref": entrada_ref or f"mem://{r.paso_id}/in",
        "salida_ref": salida_ref or f"mem://{r.paso_id}/out",
    }


# Mapeo del `type` del catálogo LLM al `tipo` del CHECK de core.tickets
# (bug | mejora | funcionalidad | seguridad).
_TIPO_MODELO_A_ESQUEMA = {
    "bug": "bug",
    "improvement": "mejora",
    "feature": "funcionalidad",
    "requirement": "funcionalidad",
}


def _triage_result(
    r_classify: ResultadoPaso,
    r_dedup: ResultadoPaso,
    r_impact: ResultadoPaso,
) -> dict[str, Any]:
    """Construye el payload de TriageResultIn a partir de los tres pasos.

    Los nombres de las claves coinciden con el JSON Schema de cada plantilla
    (en inglés) y se traducen a los nombres del contrato interno del API
    (en español) en este único punto.
    """
    c = r_classify.salida
    d = r_dedup.salida
    i = r_impact.salida

    tipo_modelo = c.get("type", "bug")
    tipo_esquema = _TIPO_MODELO_A_ESQUEMA.get(tipo_modelo, "bug")

    return {
        "tipo": tipo_esquema,
        "prioridad": i.get("priority", "P3"),
        "duplicado_de": d.get("duplicate_of"),
        "relacionados": list(d.get("related_to") or []),
        "impacto": {
            "modulos": list(i.get("affected_modules") or []),
            "radio": i.get("impact_radius", "low"),
            "riesgo_regresion": i.get("regression_risk"),
            "razonamiento": i.get("reasoning_summary_en"),
        },
        "complejidad": i.get("estimated_complexity", "medium"),
        # La plantilla triage.impact produce `recommended_tier_downstream`
        # (en ese orden). Antes leíamos `tier_recommended_downstream`, que
        # siempre devolvía el default "flash" y perdía el tier real.
        "tier_recomendado": i.get("recommended_tier_downstream", "flash"),
    }


def _grafo_dict(subgrafo: Any) -> dict[str, Any]:
    nodos = list(subgrafo.nodos)
    aristas = list(subgrafo.aristas)
    return {
        "nodes": nodos,
        "edges": aristas,
        "inbound_deps": [arista for arista in aristas if arista.get("direction") == "inbound"],
    }


async def _cargar_ticket(job: dict[str, Any]) -> dict[str, Any]:
    async with sesion_sistema() as db:
        ticket = await uno(
            db,
            """
            SELECT id, numero, tipo, titulo, descripcion, prioridad_pipeline,
                   impacto, complejidad, pr_url
              FROM core.tickets
             WHERE id = :ticket_id AND tenant_id = :tenant_id
            """,
            {"ticket_id": job["ticket_id"], "tenant_id": job["tenant_id"]},
        )
    if ticket is None:
        raise ValueError(f"No se encontró el ticket {job['ticket_id']} del job {job['id']}.")
    return dict(ticket)


async def _ejecutar_y_publicar(
    paso_id: str,
    entrada: dict[str, Any],
    *,
    job: dict[str, Any],
    cliente: ClienteInterno,
    etapa: str,
    intento: int,
    registro_id: str | None = None,
    tier_inicial: Tier | None = None,
) -> ResultadoPaso:
    resultado = await ejecutar_paso(
        paso_id,
        entrada,
        tier_inicial=tier_inicial,
        tenant_id=job["tenant_id"],
        ticket_id=job["ticket_id"],
    )
    clave_paso = registro_id or paso_id
    entrada_ref = await _subir_artefacto(
        job,
        etapa=etapa,
        intento=intento,
        paso=clave_paso,
        nombre="entrada.json",
        contenido=json.dumps(entrada, ensure_ascii=False, default=str),
        content_type="application/json",
    )
    salida_ref = await _subir_artefacto(
        job,
        etapa=etapa,
        intento=intento,
        paso=clave_paso,
        nombre="salida.json",
        contenido=json.dumps(resultado.salida, ensure_ascii=False, default=str),
        content_type="application/json",
    )
    await cliente.publicar_paso(
        job_id=job["id"],
        tenant_id=job["tenant_id"],
        paso=_paso_dict(
            etapa,
            resultado,
            paso_id=registro_id,
            entrada_ref=entrada_ref,
            salida_ref=salida_ref,
        ),
        intento=intento,
    )
    return resultado


async def _subir_artefacto(
    job: dict[str, Any],
    *,
    etapa: str,
    intento: int,
    paso: str,
    nombre: str,
    contenido: str,
    content_type: str,
) -> str:
    settings = get_settings()
    tenant = quote(str(job["tenant_id"]), safe="")
    job_id = quote(str(job["id"]), safe="")
    paso_path = quote(paso, safe=".")
    ref = f"pipeline/{tenant}/{job_id}/{etapa}/{intento}/{paso_path}/{nombre}"
    async with cliente_blob() as cliente:
        contenedor = cliente.get_container_client(settings.blob_container_evidencia)
        if settings.env == "local":
            try:
                await contenedor.create_container()
            except ResourceExistsError:
                pass
        objeto = contenedor.get_blob_client(ref)
        await objeto.upload_blob(
            contenido.encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
    return ref


async def _leer_artefacto(ref: str) -> str:
    if ref.startswith("mem://"):
        raise ValueError(f"No se puede recuperar el artefacto temporal {ref!r}.")
    settings = get_settings()
    async with cliente_blob() as cliente:
        objeto = cliente.get_blob_client(settings.blob_container_evidencia, ref)
        descarga = await objeto.download_blob()
        contenido = await descarga.readall()
    return contenido.decode("utf-8")


def _riesgo_alternativa(riesgos: list[str]) -> str:
    if len(riesgos) >= 3:
        return "alto"
    if riesgos:
        return "medio"
    return "bajo"


async def ejecutar_diseno(
    job: dict[str, Any],
    cliente: ClienteInterno,
    *,
    intento: int,
    grafo: GrafoCliente | None = None,
) -> None:
    """Genera y publica una spec completa, una vez, para el intento de diseño."""
    ticket = await _cargar_ticket(job)
    contexto_job = job.get("contexto") or {}
    if not isinstance(contexto_job, dict):
        raise TypeError("El contexto del job debe ser un objeto JSON.")

    gc = grafo or grafo_cliente()
    subgrafo = await gc.subgrafo_ticket(job["tenant_id"], ticket["numero"])
    grafo_ctx = _grafo_dict(subgrafo)
    ticket_triado = {
        "ticket_id": ticket["numero"],
        "type": ticket["tipo"],
        "title": ticket["titulo"],
        "description": ticket["descripcion"],
        "priority": ticket["prioridad_pipeline"],
        "impact": ticket["impacto"],
        "complexity": ticket["complejidad"],
    }
    contexto = {
        clave: contexto_job.get(clave, [])
        for clave in ("contracts", "schemas", "adrs", "docs", "incidents")
    }
    contexto["subgrafo"] = grafo_ctx

    digest = await _ejecutar_y_publicar(
        "design.digest",
        {"ticket_triado": ticket_triado, "contexto": contexto},
        job=job,
        cliente=cliente,
        etapa="design",
        intento=intento,
    )
    alternativas = await _ejecutar_y_publicar(
        "design.alternatives",
        {"design_brief": digest.salida},
        job=job,
        cliente=cliente,
        etapa="design",
        intento=intento,
    )
    decision = await _ejecutar_y_publicar(
        "design.adversarial_review",
        {
            "alternativas": alternativas.salida,
            "brief": digest.salida,
            "subgrafo_dependencias": grafo_ctx,
        },
        job=job,
        cliente=cliente,
        etapa="design",
        intento=intento,
    )
    compilada = await _ejecutar_y_publicar(
        "design.compile",
        {"decision": decision.salida, "brief": digest.salida},
        job=job,
        cliente=cliente,
        etapa="design",
        intento=intento,
    )

    alternativas_modelo = alternativas.salida.get("alternatives", [])
    elegido = decision.salida.get("chosen_id")
    if not 2 <= len(alternativas_modelo) <= 3:
        raise ValueError("El diseño debe proponer entre dos y tres alternativas.")
    ids_alternativas = {a.get("id") for a in alternativas_modelo}
    if len(ids_alternativas) != len(alternativas_modelo):
        raise ValueError("El diseño generó identificadores de alternativa duplicados.")
    if not elegido or elegido not in ids_alternativas:
        raise ValueError("La revisión de diseño eligió una alternativa inexistente.")

    alternativas_api = [
        {
            "id": alternativa["id"],
            "titulo": alternativa["approach_en"],
            "descripcion": "; ".join(alternativa.get("risks", [])),
            "riesgo": _riesgo_alternativa(alternativa.get("risks", [])),
            "recomendada": alternativa["id"] == elegido,
        }
        for alternativa in alternativas_modelo
    ]
    criterios_api = []
    for indice, criterio in enumerate(compilada.salida.get("acceptance_criteria", []), start=1):
        criterios_api.append(
            {
                "id": f"AC-{indice}",
                "given": criterio["given"],
                "when": criterio["when"],
                "then": criterio["then"],
            }
        )
    if not criterios_api:
        raise ValueError("design.compile no generó criterios de aceptación GWT.")

    scope_paths = compilada.salida["scope_paths"]
    if not scope_paths or any(
        _ruta_segura(scope) is None or scope.startswith("infra/secrets") for scope in scope_paths
    ):
        raise ValueError("design.compile propuso scope_paths vacíos o no permitidos.")

    spec_md_ref = await _subir_artefacto(
        job,
        etapa="design",
        intento=intento,
        paso="spec",
        nombre="spec.md",
        contenido=compilada.salida["spec_md"],
        content_type="text/markdown",
    )
    await cliente.publicar_spec(
        job_id=job["id"],
        numero=ticket["numero"],
        tenant_id=job["tenant_id"],
        intento=intento,
        spec={
            "spec_md_ref": spec_md_ref,
            "alternativas": alternativas_api,
            "criterios": criterios_api,
            "scope_paths": scope_paths,
            "adr_borrador": decision.salida.get("adr_draft"),
        },
    )
    log.info(
        "orquestador.design_completado",
        job_id=job["id"],
        tenant_id=job["tenant_id"],
        alternativas=len(alternativas_api),
        criterios=len(criterios_api),
    )


def _ruta_segura(ruta: str) -> str | None:
    normalizada = ruta.replace("\\", "/")
    path = PurePosixPath(normalizada)
    if (
        not normalizada
        or path.is_absolute()
        or path == PurePosixPath(".")
        or normalizada.startswith("//")
        or (len(normalizada) >= 2 and normalizada[1] == ":")
        or ".." in path.parts
    ):
        return None
    return str(path)


def _ruta_en_scope(ruta: str, scope_paths: list[str]) -> bool:
    objetivo = _ruta_segura(ruta)
    if objetivo is None:
        return False
    for scope in scope_paths:
        patron = _ruta_segura(scope.rstrip("/"))
        if patron is None:
            continue
        if fnmatchcase(objetivo, patron) or objetivo == patron or objetivo.startswith(patron + "/"):
            return True
    return False


def _validar_diff_scope(
    diff: str,
    archivo_planificado: str,
    scope_paths: list[str],
) -> str | None:
    """Devuelve el motivo de replan si el diff no cambia solo el archivo permitido."""
    archivo = _ruta_segura(archivo_planificado)
    if archivo is None or not _ruta_en_scope(archivo_planificado, scope_paths):
        return "The planned file is outside the approved scope_paths."

    rutas: list[str] = []
    for linea in diff.splitlines():
        if not linea.startswith(("--- ", "+++ ")):
            continue
        ruta = linea[4:].split("\t", 1)[0].strip()
        if ruta == "/dev/null":
            continue
        if ruta.startswith(("a/", "b/")):
            ruta = ruta[2:]
        ruta_normalizada = _ruta_segura(ruta)
        if ruta_normalizada is None or not _ruta_en_scope(ruta_normalizada, scope_paths):
            return "The generated diff changes a path outside the approved scope_paths."
        if ruta_normalizada != archivo:
            return "The generated diff changes a file other than the planned file."
        rutas.append(ruta_normalizada)

    if not rutas:
        return "The generated diff has no valid unified-diff file headers."
    return None


def _contar_loc(diff: str) -> tuple[int, int]:
    añadidas = eliminadas = 0
    for linea in diff.splitlines():
        if linea.startswith(("+++", "---")):
            continue
        if linea.startswith("+"):
            añadidas += 1
        elif linea.startswith("-"):
            eliminadas += 1
    return añadidas, eliminadas


async def _cargar_spec_aprobada(job: dict[str, Any], ticket_id: str) -> dict[str, Any]:
    async with sesion_sistema() as db:
        spec = await uno(
            db,
            """
            SELECT id, spec_md_ref, scope_paths, plan_tests, migraciones, adr_borrador
              FROM core.specs
             WHERE tenant_id = :tenant_id AND ticket_id = :ticket_id
               AND estado = 'aprobada'
             ORDER BY version DESC
             LIMIT 1
            """,
            {"tenant_id": job["tenant_id"], "ticket_id": ticket_id},
        )
        if spec is None:
            raise ValueError(f"El ticket {ticket_id} no tiene una spec aprobada para Build.")
        alternativas = await todos(
            db,
            """
            SELECT alt_id AS id, titulo, descripcion, riesgo, recomendada
              FROM core.spec_alternatives
             WHERE tenant_id = :tenant_id AND spec_id = :spec_id
             ORDER BY alt_id
            """,
            {"tenant_id": job["tenant_id"], "spec_id": spec["id"]},
        )
        criterios = await todos(
            db,
            """
            SELECT criterio_id AS id, given_txt AS given, when_txt AS "when",
                   then_txt AS then
              FROM core.spec_criteria
             WHERE tenant_id = :tenant_id AND spec_id = :spec_id
             ORDER BY criterio_id
            """,
            {"tenant_id": job["tenant_id"], "spec_id": spec["id"]},
        )
    return {**dict(spec), "alternativas": alternativas, "criterios": criterios}


async def ejecutar_build(
    job: dict[str, Any],
    cliente: ClienteInterno,
    *,
    intento: int,
    grafo: GrafoCliente | None = None,
) -> dict[str, Any] | None:
    """Planifica y genera diffs bajo scope_paths; los diffs fuera de alcance piden replan."""
    ticket = await _cargar_ticket(job)
    spec = await _cargar_spec_aprobada(job, ticket["id"])
    scope_paths = list(spec["scope_paths"] or [])
    contexto_job = job.get("contexto") or {}
    if not isinstance(contexto_job, dict):
        raise TypeError("El contexto del job debe ser un objeto JSON.")

    gc = grafo or grafo_cliente()
    subgrafo = await gc.subgrafo_ticket(job["tenant_id"], ticket["numero"])
    grafo_ctx = _grafo_dict(subgrafo)
    spec_md = contexto_job.get("spec_md")
    if not spec_md:
        spec_md = await _leer_artefacto(spec["spec_md_ref"])
    spec_entrada = {
        "spec_md_ref": spec["spec_md_ref"],
        "spec_md": spec_md,
        "acceptance_criteria": spec["criterios"],
        "alternatives": spec["alternativas"],
        "adr_draft": spec["adr_borrador"],
    }
    plan = await _ejecutar_y_publicar(
        "build.plan",
        {
            "spec": spec_entrada,
            "contexto_archivos": {
                "dependencias": contexto_job.get("contexto_archivos", {}),
                "subgrafo": grafo_ctx,
                "scope_paths": scope_paths,
            },
        },
        job=job,
        cliente=cliente,
        etapa="build",
        intento=intento,
    )
    pasos = plan.salida.get("steps", [])
    if not pasos:
        raise ValueError("build.plan no generó ningún paso de cambios.")
    for paso in pasos:
        if not _ruta_en_scope(paso["file"], scope_paths):
            raise ValueError(
                f"build.plan propuso {paso['file']!r}, fuera de scope_paths; se requiere replan."
            )

    archivos = contexto_job.get("archivos", {})
    if not isinstance(archivos, dict):
        archivos = {}
    diffs: list[tuple[str, str]] = []
    for indice, paso in enumerate(pasos, start=1):
        if paso["file"] not in archivos:
            raise ValueError(
                f"No hay contenido de {paso['file']!r} en contexto.archivos para generar su diff."
            )
        contenido_archivo = archivos[paso["file"]]
        if not isinstance(contenido_archivo, str):
            raise TypeError(f"El contenido de {paso['file']!r} debe ser texto.")
        callers = {"nodes": grafo_ctx["nodes"], "edges": grafo_ctx["edges"]}
        resultado_diff = await _ejecutar_y_publicar(
            "build.diff",
            {
                "plan_step": paso,
                "contenido_archivo": contenido_archivo,
                "callers": callers,
            },
            job=job,
            cliente=cliente,
            etapa="build",
            intento=intento,
            registro_id=f"build.diff.{indice}",
            tier_inicial=Tier(paso["complexity"]),
        )
        salida_diff = resultado_diff.salida
        motivo_replan = salida_diff.get("replan_reason_en", "")
        if salida_diff.get("needs_replan"):
            return {
                "needs_replan": True,
                "replan_reason_en": motivo_replan or "The diff requested a replan.",
            }
        diff = salida_diff.get("diff", "")
        motivo_replan = _validar_diff_scope(diff, paso["file"], scope_paths)
        if motivo_replan:
            salida_diff["needs_replan"] = True
            salida_diff["replan_reason_en"] = motivo_replan
            await _subir_artefacto(
                job,
                etapa="build",
                intento=intento,
                paso=f"build.diff.{indice}",
                nombre="salida.json",
                contenido=json.dumps(salida_diff, ensure_ascii=False, default=str),
                content_type="application/json",
            )
            return {"needs_replan": True, "replan_reason_en": motivo_replan}
        diffs.append((paso["file"], diff))

    diff_completo = "\n".join(f"# {ruta}\n{diff}" for ruta, diff in diffs)
    selfreview = await _ejecutar_y_publicar(
        "build.selfreview",
        {
            "spec": spec_entrada,
            "diff_completo": diff_completo,
            "resultados_verificacion": contexto_job.get("resultados_verificacion", {}),
        },
        job=job,
        cliente=cliente,
        etapa="build",
        intento=intento,
    )
    salida_revision = selfreview.salida
    if salida_revision.get("verdict") != "ready_for_qa" or salida_revision.get("blockers"):
        return {
            "needs_replan": True,
            "replan_reason_en": "; ".join(salida_revision.get("blockers", []))
            or "Self-review requested another build iteration.",
        }

    añadidas = eliminadas = 0
    tests = 0
    for ruta, diff in diffs:
        altas, bajas = _contar_loc(diff)
        añadidas += altas
        eliminadas += bajas
        if "test" in PurePosixPath(ruta).parts or PurePosixPath(ruta).name.startswith("test_"):
            tests += altas + bajas

    pr_url = contexto_job.get("pr_url") or ticket.get("pr_url")
    if pr_url:
        parsed = urlparse(pr_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("El job contiene una pr_url que no es una URL HTTP(S) válida.")
        await cliente.publicar_loc(
            job_id=job["id"],
            tenant_id=job["tenant_id"],
            intento=intento,
            loc={
                "ticket_id": ticket["numero"],
                "pr_url": pr_url,
                "anadidas": añadidas,
                "eliminadas": eliminadas,
                "netas": añadidas - eliminadas,
                "tests": tests,
            },
        )
    else:
        log.info(
            "orquestador.build_loc_pendiente_pr",
            job_id=job["id"],
            tenant_id=job["tenant_id"],
            anadidas=añadidas,
            eliminadas=eliminadas,
        )

    log.info(
        "orquestador.build_completado",
        job_id=job["id"],
        tenant_id=job["tenant_id"],
        archivos=len(diffs),
        anadidas=añadidas,
        eliminadas=eliminadas,
        pr_url=bool(pr_url),
    )
    return None


# Mapa etapa → función. Los endpoints de resultado avanzan Triage y Design;
# el bucle solo transiciona Build una vez que self-review termina.
ETAPAS: dict[EstadoJob, Callable[..., Awaitable[Any]]] = {
    EstadoJob.TRIAGE: ejecutar_triage,
    EstadoJob.DESIGN: ejecutar_diseno,
    EstadoJob.BUILD: ejecutar_build,
}
