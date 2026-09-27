"""Catálogo de llamadas LLM del pipeline — LA documentación viva de cada paso.

Cada entrada define un paso de cadena de prompts (§3 del Documento Técnico):
    id            identificador estable (etapa.paso) que se registra en llm_steps.
    version       versión de la plantilla (hash determinista SHA-256).
    version_hash  primeros 12 caracteres del SHA-256 canónico (KC-FAB-03-2).
    descripcion   qué hace el paso, en español.
    tier          tier por defecto: flash | pro | fable.
    escalable     si aplica el escalado automático flash→pro→fable.
    entrada       documentación de los campos que rellena el orquestador.
    salida        JSON Schema contra el que se valida la respuesta del modelo
                  (con additionalProperties: False estricto en raíz y sub-objetos).
    system        prompt de sistema en inglés (§3.1: los prompts operativos
                  corren en inglés; la salida al usuario se localiza después).

Regla de asignación de tiers:
    flash: pasos de clasificación/extracción/dedup/digestión/redacción.
    pro:   diseño, planes de cambio, diffs complejos, diagnósticos.
    fable: revisión adversarial, explotabilidad, anomalías de deploy y
           orquestación (decisiones que cruzan etapas).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .tiers import Tier


def calcular_hash_plantilla(system_prompt: str, schema: dict[str, Any]) -> str:
    """Calcula el hash canónico SHA-256 (12 caracteres) del prompt y su esquema."""
    payload = {
        "system": system_prompt.strip(),
        "schema": schema,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


PLANTILLAS: dict[str, dict[str, Any]] = {
    # ----------------------------------------------------------------- TRIAGE
    "triage.classify": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Normaliza y clasifica el ticket entrante: tipo, síntomas, "
        "superficies afectadas y urgencia. No propone soluciones.",
        "entrada": {
            "ticket_id": "KC-####",
            "titulo": "str",
            "origen": "str",
            "descripcion": "str",
            "adjuntos_transcritos": "list[str]",
        },
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "summary_en", "symptoms", "confidence"],
            "properties": {
                "type": {"enum": ["bug", "improvement", "feature", "requirement"]},
                "summary_en": {"type": "string"},
                "symptoms": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "symptom": {"type": "string"},
                            "context": {"type": "string"},
                        },
                    },
                },
                "affected_surfaces": {"type": "array", "items": {"type": "string"}},
                "urgency_signals": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        "system": (
            "You are the Triage agent of the Kallicode factory. Step 1 of 3: "
            "normalize and classify the incoming ticket. Do NOT propose solutions. "
            "Extract only what the ticket states or shows; never invent symptoms. "
            "Return ONLY valid JSON conforming to the output schema."
        ),
    },
    "triage.dedup": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Decide si el ticket duplica uno histórico. Recibe los k vecinos "
        "por similitud vectorial (pgvector); similitud alta NO basta: "
        "deben coincidir síntoma raíz y módulo.",
        "entrada": {
            "ticket": "salida de triage.classify",
            "candidatos": "list[{ticket_id, similitud, resumen, modulo, resolucion}]",
        },
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["duplicate_of", "related_to"],
            "properties": {
                "duplicate_of": {"type": ["string", "null"]},
                "related_to": {"type": "array", "items": {"type": "string"}},
                "rationale_en": {"type": "string"},
                "reopen_recommended": {"type": "boolean"},
            },
        },
        "system": (
            "Step 2 of 3: decide whether this ticket duplicates an existing one. "
            "A high similarity score alone is NOT sufficient: mark duplicate_of only "
            "if root symptom AND module match. Related but distinct goes to related_to. "
            "Return ONLY valid JSON."
        ),
    },
    "triage.impact": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Mide el radio de impacto sobre el subgrafo de CodeMapping y fija "
        "prioridad P1–P4, complejidad y tier recomendado para las etapas "
        "siguientes. Escala a PRO si el subgrafo supera el umbral o hay "
        "historial de incidentes en los módulos tocados.",
        "entrada": {
            "ticket": "classify+dedup",
            "subgrafo": "{nodes, edges, inbound_deps}",
            "incidentes": "historial de los módulos tocados",
        },
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "affected_modules",
                "impact_radius",
                "priority",
                "estimated_complexity",
                "recommended_tier_downstream",
            ],
            "properties": {
                "affected_modules": {"type": "array", "items": {"type": "string"}},
                "impact_radius": {"enum": ["low", "medium", "high"]},
                "regression_risk": {"type": "number", "minimum": 0, "maximum": 1},
                "priority": {"enum": ["P1", "P2", "P3", "P4"]},
                "estimated_complexity": {"enum": ["trivial", "small", "medium", "large"]},
                "recommended_tier_downstream": {"enum": ["flash", "pro", "fable"]},
                "reasoning_summary_en": {"type": "string"},
            },
        },
        "system": (
            "Step 3 of 3: assess system impact using the CodeMapping subgraph. "
            "Reason ONLY over the provided nodes and edges. Weigh incident history "
            "heavily. Your complexity estimate selects the model tier downstream. "
            "Return ONLY valid JSON."
        ),
    },
    # ----------------------------------------------------------------- DESIGN
    "design.digest": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Comprime el contexto recuperado (contratos, esquemas, ADRs, docs) "
        "en un brief de diseño. Preserva nombres exactos; marca toda "
        "restricción dura.",
        "entrada": {
            "ticket_triado": "...",
            "contexto": "{contracts, schemas, adrs, docs, incidents}",
        },
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["design_brief_en", "hard_constraints"],
            "properties": {
                "design_brief_en": {"type": "string"},
                "hard_constraints": {"type": "array", "items": {"type": "string"}},
                "relevant_entities": {"type": "array", "items": {"type": "string"}},
                "known_pitfalls": {"type": "array", "items": {"type": "string"}},
            },
        },
        "system": (
            "You prepare the design brief for the architect. Compress the retrieved "
            "context into only what is relevant to THIS ticket. Do not design. "
            "Preserve exact names. Flag every hard constraint explicitly. JSON only."
        ),
    },
    "design.alternatives": {
        "tier": Tier.PRO,
        "escalable": True,
        "descripcion": "Genera 2–3 alternativas de solución genuinamente distintas con "
        "cambios por módulo, impacto en datos, riesgos y tamaño. No elige "
        "ganadora (eso es la revisión adversarial).",
        "entrada": {"design_brief": "salida de design.digest"},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["alternatives"],
            "properties": {
                "alternatives": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "approach_en", "risks", "est_size"],
                        "properties": {
                            "id": {"type": "string"},
                            "approach_en": {"type": "string"},
                            "risks": {"type": "array", "items": {"type": "string"}},
                            "est_size": {"enum": ["small", "medium", "large"]},
                        },
                    },
                }
            },
        },
        "system": (
            "You are the design architect of the Kallicode factory. Produce 2-3 "
            "genuinely different solution alternatives. Do NOT pick a winner. "
            "Do NOT write production code. Respect every hard constraint. JSON only."
        ),
    },
    "design.adversarial_review": {
        "tier": Tier.FABLE,
        "escalable": False,
        "descripcion": "Revisión adversarial: ataca cada alternativa (acoplamientos ocultos, "
        "riesgo de regresión, coste operativo), elige UNA y justifica también "
        "los rechazos como borrador de ADR. Tier FABLE fijo: es el paso de "
        "máximo criterio del diseño.",
        "entrada": {
            "alternativas": "design.alternatives",
            "brief": "design.digest",
            "subgrafo_dependencias": "CodeMapping",
        },
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["chosen_id", "decision_rationale_en", "adr_draft"],
            "properties": {
                "chosen_id": {"type": "string"},
                "critique_per_alternative": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "alt_id": {"type": "string"},
                            "critique": {"type": "string"},
                        },
                    },
                },
                "decision_rationale_en": {"type": "string"},
                "adr_draft": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "context": {"type": "string"},
                        "decision": {"type": "string"},
                        "consequences": {"type": "string"},
                    },
                },
                "open_questions_for_approver": {"type": "array", "items": {"type": "string"}},
            },
        },
        "system": (
            "You are now the reviewing architect. Attack each alternative: violated "
            "constraints, hidden coupling, regression risk, operational cost, what "
            "breaks at scale. Choose ONE and justify the choice AND the rejections "
            "as an ADR draft. JSON only."
        ),
    },
    "design.compile": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Compila la decisión en los artefactos de spec: narrativa Markdown y "
        "bloque estructurado (criterios AC-n verificables, scope_paths como "
        "frontera dura, plan de tests, migraciones).",
        "entrada": {"decision": "design.adversarial_review", "brief": "design.digest"},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["spec_md", "acceptance_criteria", "scope_paths"],
            "properties": {
                "spec_md": {"type": "string"},
                "acceptance_criteria": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "given", "when", "then"],
                        "properties": {
                            "id": {"type": "string"},
                            "given": {"type": "string"},
                            "when": {"type": "string"},
                            "then": {"type": "string"},
                        },
                    },
                },
                "scope_paths": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "test_plan": {"type": "array", "items": {"type": "string"}},
                "migration_steps": {"type": "array", "items": {"type": "string"}},
            },
        },
        "system": (
            "Compile the approved design into the spec artifacts. Acceptance criteria "
            "must be independently verifiable. scope_paths is a HARD boundary: Build "
            "cannot edit outside it. JSON only."
        ),
    },
    # ------------------------------------------------------------------ BUILD
    "build.plan": {
        "tier": Tier.PRO,
        "escalable": True,
        "descripcion": "Plan de cambios archivo por archivo dentro de scope_paths, con "
        "orden de edición, dependencias afectadas y complejidad por archivo "
        "(que decide el tier del diff). Planifica migraciones con rollback.",
        "entrada": {
            "spec": "spec.md + criterios",
            "contexto_archivos": "dependencias/usos por archivo",
        },
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["steps"],
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["order", "file", "change_summary_en", "complexity"],
                        "properties": {
                            "order": {"type": "integer"},
                            "file": {"type": "string"},
                            "change_summary_en": {"type": "string"},
                            "complexity": {"enum": ["flash", "pro"]},
                        },
                    },
                },
                "migrations": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
            },
        },
        "system": (
            "You are the Build agent. Produce a file-by-file change plan strictly "
            "inside scope_paths. Tag each file's complexity (flash|pro) to select the "
            "diff model tier. Plan migrations and rollback. No code yet. JSON only."
        ),
    },
    "build.diff": {
        "tier": Tier.PRO,
        "escalable": True,
        "descripcion": "Genera UN diff atómico para un archivo del plan siguiendo las "
        "convenciones del cliente. Si necesita tocar otro archivo devuelve "
        "needs_replan (no improvisa). El tier real lo fija el plan "
        "(boilerplate=flash, complejo=pro).",
        "entrada": {"plan_step": "...", "contenido_archivo": "...", "callers": "del grafo"},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["diff"],
            "properties": {
                "diff": {"type": "string"},
                "rationale_en": {"type": "string"},
                "tests_added_or_updated": {"type": "array", "items": {"type": "string"}},
                "needs_replan": {"type": "boolean"},
                "replan_reason_en": {"type": "string"},
            },
        },
        "system": (
            "Implement exactly ONE step of the approved plan as a unified diff. "
            "Follow client conventions verbatim. Touch ONLY the given file; if another "
            "file is needed, STOP and return needs_replan. JSON only."
        ),
    },
    "build.diagnose": {
        "tier": Tier.PRO,
        "escalable": True,
        "descripcion": "Diagnostica un fallo de verificación determinista (build/lint/tests) "
        "tras aplicar un diff: ¿diff erróneo, test desactualizado por la spec, "
        "o fallo preexistente? Nunca debilita un test para que pase.",
        "entrada": {"check": "...", "log": "extracto", "diff": "...", "criterios": "AC-n"},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cause"],
            "properties": {
                "cause": {"enum": ["diff_error", "outdated_test", "preexisting"]},
                "evidence_en": {"type": "string"},
                "corrective_diff": {"type": ["string", "null"]},
                "escalate": {"type": "boolean"},
            },
        },
        "system": (
            "Deterministic verification failed after applying a diff. Determine the "
            "cause and the minimal corrective action. Never weaken a test just to make "
            "it pass. JSON only."
        ),
    },
    "build.selfreview": {
        "tier": Tier.FABLE,
        "escalable": False,
        "descripcion": "Auto-revisión hostil del diff combinado contra la spec antes del PR: "
        "criterios sin cubrir, scope creep, violaciones de convención, cambios "
        "de comportamiento ocultos. FABLE fijo: es el último control antes de QA.",
        "entrada": {"spec": "...", "diff_completo": "...", "resultados_verificacion": "..."},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "criteria_coverage"],
            "properties": {
                "blockers": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "criteria_coverage": {"type": "array", "items": {"type": "string"}},
                "verdict": {"enum": ["ready_for_qa", "back_to_step_2"]},
            },
        },
        "system": (
            "You did not write this code. Review the FULL combined diff against the "
            "spec as a hostile senior reviewer. Verdict ready_for_qa requires zero "
            "blockers. JSON only."
        ),
    },
    # --------------------------------------------------------------------- QA
    "qa.derive_cases": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Deriva casos ejecutables desde los criterios AC-n (NUNCA desde el "
        "diff: QA no confía en Build): happy path, bordes y failure modes "
        "del historial de incidentes.",
        "entrada": {
            "criterios": "spec.acceptance_criteria",
            "fixtures": "...",
            "incidentes": "...",
        },
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cases"],
            "properties": {
                "cases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["criterion_id", "name", "steps", "expected", "kind"],
                        "properties": {
                            "criterion_id": {"type": "string"},
                            "name": {"type": "string"},
                            "steps": {"type": "array", "items": {"type": "string"}},
                            "expected": {"type": "string"},
                            "kind": {"enum": ["happy_path", "edge_case", "failure_mode"]},
                        },
                    },
                }
            },
        },
        "system": (
            "You are the QA agent. Derive executable test cases from the acceptance "
            "criteria ONLY — do not read the PR diff; your independence from Build is "
            "the point. JSON only."
        ),
    },
    "qa.results_digest": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Digiere las corridas (CI del cliente + regresión dirigida + flujos "
        "headless) en la matriz estructurada; copia fallos literalmente y "
        "separa flaky de fallos duros.",
        "entrada": {"ci_output": "...", "regression_output": "...", "flow_results": "..."},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["matrix", "suites_summary"],
            "properties": {
                "matrix": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "test_id": {"type": "string"},
                            "status": {"enum": ["pass", "fail", "flaky", "skipped"]},
                            "raw_failure": {"type": ["string", "null"]},
                        },
                    },
                },
                "suites_summary": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "passed": {"type": "integer"},
                        "failed": {"type": "integer"},
                        "skipped": {"type": "integer"},
                    },
                },
                "flaky": {"type": "array", "items": {"type": "string"}},
            },
        },
        "system": (
            "Summarize raw test outputs into a structured result matrix. Copy failure "
            "messages verbatim. Flag flaky separately from hard failures. JSON only."
        ),
    },
    "qa.visual_check": {
        "tier": Tier.PRO,
        "escalable": True,
        "descripcion": "Checkpoint de visión: verifica que lo VISIBLE en la evidencia "
        "(screenshot/frames) satisface el criterio — un test verde con "
        "píxeles equivocados es un fallo. (Multimodal: usa PRO con visión; "
        "escalable a FABLE.)",
        "entrada": {"criterio": "...", "estado_esperado": "...", "evidencia": "imagen(es)"},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["criterion_id", "visual_verdict"],
            "properties": {
                "criterion_id": {"type": "string"},
                "visual_verdict": {"enum": ["pass", "fail"]},
                "observed_en": {"type": "string"},
                "evidence_region": {"type": "string"},
            },
        },
        "system": (
            "You verify visual evidence. Check that what is VISIBLE satisfies the "
            "acceptance criterion — correct data, correct state, no error artifacts. "
            "A passing test with wrong pixels is a failure. JSON only."
        ),
    },
    "qa.failure_diagnosis": {
        "tier": Tier.PRO,
        "escalable": True,
        "descripcion": "Diagnóstico de fallo de test con el grafo de dependencias e "
        "historial: regresión del diff, test desactualizado o preexistente. "
        "Su veredicto enruta el job (back_to_build / update_test / escalate).",
        "entrada": {
            "fallo": "case_id + extracto",
            "diff_hunks": "...",
            "grafo": "deps + incidentes",
        },
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["cause", "action"],
            "properties": {
                "cause": {"enum": ["regression", "outdated_test", "preexisting"]},
                "evidence_en": {"type": "string"},
                "action": {"enum": ["back_to_build", "update_test", "escalate"]},
                "confidence": {"type": "number"},
            },
        },
        "system": (
            "A test failed. Determine the root cause using the dependency graph and "
            "incident history. Your verdict routes the job. JSON only."
        ),
    },
    # --------------------------------------------------------------- SECURITY
    "security.normalize": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Normaliza y deduplica los hallazgos crudos de los escáneres "
        "deterministas (SAST/SCA/secretos) al esquema interno. No juzga "
        "explotabilidad ni inventa hallazgos.",
        "entrada": {"sast": "json semgrep", "sca": "json trivy", "secrets": "json gitleaks"},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["findings"],
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "rule", "file", "raw_severity", "category"],
                        "properties": {
                            "id": {"type": "string"},
                            "rule": {"type": "string"},
                            "file": {"type": "string"},
                            "raw_severity": {"type": "string"},
                            "category": {"type": "string"},
                        },
                    },
                }
            },
        },
        "system": (
            "Normalize raw scanner findings to the internal schema, merge duplicates "
            "across scanners, map severities. Do NOT judge exploitability. Do NOT "
            "invent findings. JSON only."
        ),
    },
    "security.exploitability": {
        "tier": Tier.FABLE,
        "escalable": False,
        "descripcion": "Evalúa UN hallazgo en ESTE sistema con el contexto de flujo de datos "
        "y alcanzabilidad del grafo: ¿falso positivo?, ¿alcanzable desde "
        "entrada de usuario?, ¿qué expone?, severidad contextual y fix mínimo. "
        "FABLE fijo: razonamiento sobre data-flow donde equivocarse es caro.",
        "entrada": {"hallazgo": "...", "grafo": "{callers, data_flow, exposure, mitigations}"},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["finding_id", "verdict", "contextual_severity"],
            "properties": {
                "finding_id": {"type": "string"},
                "verdict": {"enum": ["confirmed", "false_positive"]},
                "reachability_trace_en": {"type": "string"},
                "contextual_severity": {"enum": ["critical", "high", "medium", "low"]},
                "proposed_fix_diff": {"type": ["string", "null"]},
                "blocks_deploy": {"type": "boolean"},
            },
        },
        "system": (
            "Assess ONE finding in the context of THIS system using graph data-flow "
            "and reachability. Justify false positives with the code path. Propose the "
            "MINIMAL fix. JSON only."
        ),
    },
    "security.report": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Compila el veredicto del cambio y redacta tickets proactivos para "
        "deuda fuera del alcance del PR. Todo descarte lleva su justificación "
        "literal (registro de compliance).",
        "entrada": {"veredictos": "security.exploitability[]", "nocturno": "hallazgos proactivos"},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["change_verdict"],
            "properties": {
                "change_verdict": {"enum": ["approved", "blocked"]},
                "blocking_findings": {"type": "array", "items": {"type": "string"}},
                "dismissed": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "finding_id": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                    },
                },
                "proactive_tickets": {"type": "array", "items": {"type": "string"}},
            },
        },
        "system": (
            "Compile the security verdict and draft proactive tickets. Every dismissed "
            "finding must carry its justification verbatim. JSON only."
        ),
    },
    # ----------------------------------------------------------------- DEPLOY
    "deploy.preflight_digest": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Verifica en los registros de base las precondiciones de deploy "
        "(tres firmas, security approved, migraciones con rollback, pipeline "
        "disponible). Cualquier faltante => no_go.",
        "entrada": {"gate_records": "...", "security_verdict": "...", "pipeline_status": "..."},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict"],
            "properties": {
                "verdict": {"enum": ["go", "no_go"]},
                "missing": {"type": "array", "items": {"type": "string"}},
                "rollback_plan_ref": {"type": ["string", "null"]},
            },
        },
        "system": (
            "Verify every precondition for deployment from the operational records. "
            "Any missing item means no_go. JSON only."
        ),
    },
    "deploy.anomaly": {
        "tier": Tier.FABLE,
        "escalable": False,
        "descripcion": "Una métrica post-deploy degradó: correlaciona la degradación con el "
        "contenido del cambio vía subgrafo y recomienda keep|rollback (la "
        "decisión final es del humano de guardia). FABLE fijo: correlación "
        "causal compleja bajo presión de tiempo.",
        "entrada": {"metrica": "{nombre, valor, baseline}", "logs": "extracto", "subgrafo": "..."},
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["probable_cause_en", "recommendation"],
            "properties": {
                "probable_cause_en": {"type": "string"},
                "confidence": {"type": "number"},
                "recommendation": {"enum": ["keep", "rollback"]},
                "rationale_en": {"type": "string"},
            },
        },
        "system": (
            "A post-deploy metric degraded. Correlate the degradation with the change "
            "content using the dependency subgraph. Recommend keep or rollback — the "
            "human on-call decides. JSON only."
        ),
    },
    "deploy.changelog": {
        "tier": Tier.FLASH,
        "escalable": True,
        "descripcion": "Cierra el circuito: changelog y documento del cambio (inglés) + "
        "mensaje al reporter en su idioma, en lenguaje llano, con enlaces a "
        "la evidencia visual.",
        "entrada": {
            "ticket": "...",
            "spec_ref": "...",
            "deploy_result": "...",
            "idioma": "es|en|pt",
        },
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["changelog_en", "reporter_message_localized"],
            "properties": {
                "changelog_en": {"type": "string"},
                "change_document_en": {"type": "string"},
                "reporter_message_localized": {"type": "string"},
            },
        },
        "system": (
            "Close the loop. Produce the changelog (English) and the reporter "
            "notification in the client language, plain words, no internal jargon. "
            "JSON only."
        ),
    },
    # ------------------------------------------------------------ ORQUESTADOR
    "orchestrator.route": {
        "tier": Tier.FABLE,
        "escalable": False,
        "descripcion": "Decisión de orquestación no trivial: adónde devolver un job tras un "
        "fallo ambiguo, cómo priorizar líneas ante empates con dependencias "
        "cruzadas, o si un caso amerita escalado humano inmediato. FABLE "
        "fijo: es el paso de orquestación (razonamiento sobre el pipeline "
        "entero, no sobre una etapa).",
        "entrada": {
            "situacion": "descripción estructurada",
            "estado_jobs": "...",
            "historial": "transiciones e iteraciones",
        },
        "salida": {
            "type": "object",
            "additionalProperties": False,
            "required": ["decision", "rationale_en"],
            "properties": {
                "decision": {"type": "object", "additionalProperties": False},
                "rationale_en": {"type": "string"},
                "confidence": {"type": "number"},
                "escalate_to_human": {"type": "boolean"},
            },
        },
        "system": (
            "You are the pipeline orchestrator of the Kallicode factory. Decide the "
            "next action for the described situation considering the whole pipeline "
            "state, gate integrity and iteration limits. If ambiguity remains, "
            "escalate to a human. JSON only."
        ),
    },
}


# Inyección automática y determinista de version y version_hash (KC-FAB-03-2)
for _p in PLANTILLAS.values():
    _v_hash = calcular_hash_plantilla(_p["system"], _p["salida"])
    _p["version_hash"] = _v_hash
    _p["version"] = _v_hash


def plantilla(paso_id: str) -> dict[str, Any]:
    """Obtiene una plantilla del catálogo o lanza KeyError con mensaje claro."""
    if paso_id not in PLANTILLAS:
        raise KeyError(f"Paso LLM desconocido: {paso_id}. Registrados: {sorted(PLANTILLAS)}")
    return PLANTILLAS[paso_id]
