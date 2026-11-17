#!/usr/bin/env python3
"""Ensayo integral de retención y minimización de datos (KC-DB-10-6).

Recorre de punta a punta el ciclo de vida del dato con las piezas REALES de los tickets 10-1, 10-2 y 10-3:

    datos históricos -> simulacro -> purga sin export (la auditoría se bloquea) -> export WORM verificado
    -> DROP de la partición antigua con ancla -> TTL de idempotency_keys -> minimización de PII
    -> validación final de integridad (los 26 chequeos) y re-verificación externa de los exports

El paso del tiempo se simula con el parámetro ``p_ahora`` de las funciones (no se espera ni se cambia el reloj):
se calcula una fecha ficticia posterior al plazo de las particiones del mes en curso, de modo que el mes en curso
queda vencido y el siguiente no. Los eventos de auditoría del mes en curso nacen por el disparador real de sellado;
los del mes siguiente se sellan con la propia ``audit.calcular_sello`` (el disparador fija creado_en = now()).

Todo lo que toca la base ocurre en UNA transacción que SIEMPRE se revierte: no deja datos ni cambia la política de
la auditoría. Lo único que persiste es el informe y los archivos del export, que son la evidencia.

Qué NO ensaya (se dice en el informe): el disparo real de pg_cron (se comprueba que los trabajos están definidos y
llaman a las funciones que aquí se ejecutan) ni almacenamiento inmutable real.

Uso:  KC_DATABASE_URL_MIGRATIONS=postgresql://... python db/tools/ensayo_retencion.py --destino ./evidencia
Código de salida: 0 si todos los pasos pasan, 1 si alguno falla.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import stat
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

GENESIS = "0" * 64
DIAS_RETENCION = (
    90  # el plazo de plataforma de webhook_inbox y notifications; se aplica también a la auditoría
)
POR = "ensayo_retencion"


def _cargar_worm() -> ModuleType:
    ruta = Path(__file__).resolve().parent / "worm_auditoria.py"
    spec = importlib.util.spec_from_file_location("worm_auditoria", ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["worm_auditoria"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


worm = _cargar_worm()


class Informe:
    def __init__(self) -> None:
        self.pasos: list[dict[str, Any]] = []
        self.datos: dict[str, Any] = {}

    def paso(self, nombre: str, ok: bool, detalle: Any = None) -> bool:
        self.pasos.append({"paso": nombre, "ok": bool(ok), "detalle": detalle})
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(p["ok"] for p in self.pasos)

    def markdown(self) -> str:
        lineas = [
            "# Ensayo integral de retención (KC-DB-10-6)",
            "",
            f"Resultado: **{'APROBADO' if self.ok else 'FALLIDO'}** ({sum(p['ok'] for p in self.pasos)}/{len(self.pasos)} pasos)",
            "",
            "| # | Paso | Resultado | Detalle |",
            "|---|---|---|---|",
        ]
        for i, p in enumerate(self.pasos, 1):
            detalle = (
                json.dumps(p["detalle"], ensure_ascii=False, default=str)
                if p["detalle"] is not None
                else ""
            )
            lineas.append(
                f"| {i} | {p['paso']} | {'OK' if p['ok'] else 'FALLA'} | {detalle.replace('|', '/')} |"
            )
        return "\n".join(lineas) + "\n"


# ---------------------------------------------------------------------------------------------------------
# Consultas auxiliares
# ---------------------------------------------------------------------------------------------------------
def _uno(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    fila = conn.execute(sql, params).fetchone()
    return fila[0] if fila else None


def _problemas(conn: Any) -> dict[str, int]:
    """Chequeos de core.validar_integridad() con problemas (vacío = todo limpio) y el total de chequeos."""
    filas = conn.execute("SELECT chequeo, problemas FROM core.validar_integridad()").fetchall()
    return {str(c): int(p) for c, p in filas}


def _existe(conn: Any, tabla: str) -> bool:
    return bool(_uno(conn, "SELECT to_regclass(%s) IS NOT NULL", (tabla,)))


def _motor(conn: Any, ahora: dt.datetime, simular: bool) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT entidad, objeto, accion, filas, motivo FROM core.purgar_retencion(%s, %s)",
        (ahora, simular),
    )
    return [
        dict(zip(("entidad", "objeto", "accion", "filas", "motivo"), r, strict=True))
        for r in cur.fetchall()
    ]


def _particiones_audit(conn: Any) -> list[str]:
    """Particiones por rango de audit.audit_events, de la más antigua a la más reciente (sin DEFAULT)."""
    filas = conn.execute(
        "SELECT n.nspname || '.' || c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE i.inhparent = 'audit.audit_events'::regclass "
        "AND pg_get_expr(c.relpartbound, c.oid) <> 'DEFAULT' "
        "ORDER BY (audit.limites_particion((n.nspname || '.' || c.relname)::regclass)).desde"
    ).fetchall()
    return [str(f[0]) for f in filas]


# ---------------------------------------------------------------------------------------------------------
# Datos históricos
# ---------------------------------------------------------------------------------------------------------
def _evento_actual(conn: Any, tenant: str, texto: str) -> None:
    """Evento de auditoría por la vía normal: el disparador de sellado fija creado_en = now() y encadena."""
    conn.execute(
        "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, sello_previo, sello, ip, user_agent, request_id) "
        "VALUES (%s, 'sistema', 'ensayo_retencion', %s, %s, %s, '10.0.0.7', 'ensayo/1', %s)",
        (tenant, texto, GENESIS, "a" * 64, f"req-{uuid.uuid4().hex[:8]}"),
    )


def _evento_futuro(conn: Any, tenant: str, cuando: dt.datetime, texto: str) -> None:
    """Evento de una partición posterior, encadenado a la cabeza actual. Sin disparadores (replica) y sellado
    con audit.calcular_sello, como hace el disparador. Es la única forma de tener eventos «del futuro»."""
    cabeza = conn.execute(
        "SELECT ultimo_sello, eventos FROM audit.chain_heads WHERE tenant_id = %s FOR UPDATE",
        (tenant,),
    ).fetchone()
    assert cabeza is not None, "el tenant debe tener ya una cadena"
    conn.execute("SET LOCAL session_replication_role = replica")
    try:
        eid = _uno(
            conn,
            "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, sello_previo, sello, creado_en, "
            "version_sello, ip, user_agent, request_id) VALUES (%s, 'sistema', 'ensayo_retencion', %s, %s, %s, %s, 2, "
            "'10.0.0.7', 'ensayo/1', %s) RETURNING id",
            (tenant, texto, cabeza[0], "a" * 64, cuando, f"req-{uuid.uuid4().hex[:8]}"),
        )
        sello = _uno(
            conn,
            "UPDATE audit.audit_events e SET sello = audit.calcular_sello(e) WHERE e.id = %s RETURNING sello",
            (eid,),
        )
        conn.execute(
            "UPDATE audit.chain_heads SET ultimo_sello = %s, eventos = eventos + 1 WHERE tenant_id = %s",
            (sello, tenant),
        )
    finally:
        conn.execute("SET LOCAL session_replication_role = origin")


def _sembrar(
    conn: Any, inf: Informe, ahora: dt.datetime, mes: dt.datetime, mes_sig: dt.datetime
) -> dict[str, Any]:
    s = uuid.uuid4().hex[:8]
    a, b = f"org_ens_a_{s}", f"org_ens_b_{s}"
    for org in (a, b):
        conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (org, org))
        conn.execute(
            "INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el) "
            "VALUES (%s, %s, 'growth', current_date - 5, current_date + 25)",
            (f"sub_{org}", org),
        )
        conn.execute(
            "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'U')",
            (f"us_{org}", org, f"{org}@ensayo.dev"),
        )
    conn.execute(
        "INSERT INTO core.connections (id, tenant_id, categoria, proveedor) VALUES (%s, %s, 'repositorio', 'github')",
        (f"cn_{a}", a),
    )

    ya_vencido = mes + dt.timedelta(days=5)  # mes en curso: a «ahora» ya pasó el plazo
    aun_vigente = mes_sig + dt.timedelta(days=5)  # mes siguiente: a «ahora» sigue dentro del plazo
    for creado, estado_tag in ((ya_vencido, "viejo"), (aun_vigente, "vigente")):
        mes_dia = creado.date().replace(day=1)
        for i in range(3):
            conn.execute(
                "INSERT INTO core.webhook_inbox (tenant_id, connection_id, delivery_id, evento, payload, estado, recibido_en, recibido_mes) "
                "VALUES (%s, %s, %s, 'push', '{}', 'procesado', %s, %s)",
                (a, f"cn_{a}", f"d-{estado_tag}-{i}-{s}", creado, mes_dia),
            )
            conn.execute(
                "INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo, creada_en) VALUES (%s, %s, %s, 'sistema', 't', %s)",
                (f"nt-{estado_tag}-{i}-{s}", a, f"us_{a}", creado),
            )

    # Claves de idempotencia, sesiones e invitaciones, relativas a «ahora».
    for clave, org, edad, status in (
        ("vencida_a", a, dt.timedelta(hours=49), 200),
        ("vencida_b", b, dt.timedelta(days=30), 200),
        ("activa_47h", a, dt.timedelta(hours=47), 200),
        ("activa_1h", b, dt.timedelta(hours=1), 200),
        ("reserva_en_curso", a, dt.timedelta(minutes=5), 0),
    ):
        conn.execute(
            "INSERT INTO core.idempotency_keys (clave, tenant_id, endpoint, respuesta, status_code, creado_en) "
            "VALUES (%s, %s, 'ep.ensayo', '{}', %s, %s)",
            (f"{clave}_{s}", org, status, ahora - edad),
        )
    for rid, edad in (("vieja", dt.timedelta(days=45)), ("reciente", dt.timedelta(days=2))):
        conn.execute(
            "INSERT INTO core.refresh_tokens (id, tenant_id, user_id, token_hash, emitido_en, expira_en, ip, user_agent) "
            "VALUES (%s, %s, %s, %s, %s, %s, '203.0.113.9', 'Navegador/1')",
            (
                f"rt_{rid}_{s}",
                a,
                f"us_{a}",
                f"h_{rid}_{s}",
                ahora - edad,
                ahora + dt.timedelta(days=30),
            ),
        )
    for iid, estado, edad in (
        ("aceptada_vieja", "aceptada", dt.timedelta(days=120)),
        ("enviada_vieja", "enviada", dt.timedelta(days=120)),
        ("aceptada_reciente", "aceptada", dt.timedelta(days=10)),
    ):
        conn.execute(
            "INSERT INTO core.invitations (id, tenant_id, email, rol, token_hash, invitado_por, estado, expira_en, creado_en) "
            "VALUES (%s, %s, %s, 'member', %s, %s, %s, %s, %s)",
            (
                f"inv_{iid}_{s}",
                a,
                f"{iid}@externo.dev",
                f"h_{iid}_{s}",
                f"us_{a}",
                estado,
                ahora - edad + dt.timedelta(days=7),
                ahora - edad,
            ),
        )

    # Auditoría: eventos del mes en curso (por el disparador real) y del mes siguiente (sellados a mano).
    for i in range(3):
        _evento_actual(conn, a, f"a-{i}")
    for i in range(2):
        _evento_actual(conn, b, f"b-{i}")
    _evento_futuro(conn, a, aun_vigente, "a-futuro")
    _evento_futuro(conn, b, aun_vigente, "b-futuro")

    inf.datos["sembrado"] = {
        "tenants": [a, b],
        "webhook_inbox": {"viejas": 3, "vigentes": 3},
        "notifications": {"viejas": 3, "vigentes": 3},
        "idempotency_keys": 5,
        "refresh_tokens": 2,
        "invitaciones": 3,
        "eventos_auditoria": {"mes_en_curso": 5, "mes_siguiente": 2},
    }
    return {"a": a, "b": b, "s": s}


# ---------------------------------------------------------------------------------------------------------
# El ensayo
# ---------------------------------------------------------------------------------------------------------
def ejecutar(conn: Any, destino: Path) -> Informe:
    """Ejecuta el ensayo en la transacción abierta de ``conn``. El llamador debe hacer ROLLBACK después."""
    inf = Informe()
    conn.execute("SET TIME ZONE 'UTC'")

    # -- fechas: «ahora» ficticio = fin del mes en curso + 91 días (el mes en curso vencido, el siguiente no)
    mes = _uno(conn, "SELECT date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'")
    mes_sig = _uno(
        conn,
        "SELECT (date_trunc('month', now() AT TIME ZONE 'UTC') + interval '1 month') AT TIME ZONE 'UTC'",
    )
    ahora = mes_sig + dt.timedelta(days=DIAS_RETENCION + 1)
    inf.datos["ahora_simulado"] = ahora.isoformat()

    # -- 0. pg_cron: los trabajos están definidos y llaman a las funciones que se ejecutan aquí
    if _uno(conn, "SELECT to_regnamespace('cron') IS NOT NULL"):
        trabajos = dict(conn.execute("SELECT jobname, command FROM cron.job").fetchall())
        esperados = {
            "retencion_purgas": "core.purgar_retencion",
            "idempotencia_purga": "core.purgar_idempotency_keys",
            "pii_minimizar": "core.minimizar_pii",
        }
        faltan = [j for j, f in esperados.items() if j not in trabajos or f not in str(trabajos[j])]
        inf.paso(
            "pg_cron: trabajos de retención, TTL y minimización definidos",
            not faltan,
            {"faltan": faltan},
        )
    else:
        inf.paso(
            "pg_cron: trabajos definidos (pg_cron no instalada: no aplicable)", True, "omitido"
        )

    # -- política de la auditoría solo para el ensayo (se revierte)
    conn.execute(
        "UPDATE core.retention_policies SET action = 'drop', retention_days = %s, is_active = true "
        "WHERE entity_name = 'audit.audit_events' AND tenant_id IS NULL",
        (DIAS_RETENCION,),
    )

    ids = _sembrar(conn, inf, ahora, mes, mes_sig)
    a, b = ids["a"], ids["b"]

    # -- 1. línea base
    base = _problemas(conn)
    # En una base limpia (CI) todo es 0. En una base con restos de otras pruebas puede haber problemas ajenos al
    # ensayo: la línea base los registra y el paso final exige que el ensayo no añada ninguno.
    inf.paso(
        "línea base: chequeos de integridad registrados (0 problemas en una base limpia)",
        True,
        {"chequeos": len(base), "con_problemas_previos": {k: v for k, v in base.items() if v}},
    )
    v_ini = {
        t: tuple(
            conn.execute(
                "SELECT integra, eventos_verificados FROM audit.verify_chain(%s)", (t,)
            ).fetchone()
        )
        for t in (a, b)
    }
    inf.paso(
        "línea base: las cadenas de auditoría de ambos tenants son íntegras",
        all(v[0] for v in v_ini.values()),
        {t: v[1] for t, v in v_ini.items()},
    )
    parts_audit = _particiones_audit(conn)
    en_curso = _uno(
        conn,
        "SELECT tableoid::regclass::text FROM audit.audit_events WHERE tenant_id = %s ORDER BY id LIMIT 1",
        (a,),
    )

    # -- 2. simulacro: informa y no toca nada
    antes = _uno(conn, "SELECT count(*) FROM core.webhook_inbox WHERE tenant_id = %s", (a,))
    simulado = _motor(conn, ahora, True)
    despues = _uno(conn, "SELECT count(*) FROM core.webhook_inbox WHERE tenant_id = %s", (a,))
    inf.paso(
        "simulacro: el motor informa de lo que purgaría y no borra nada",
        antes == despues
        and any(r["accion"] == "simulada" for r in simulado)
        and _existe(conn, en_curso),
        {"acciones": sorted({r["accion"] for r in simulado})},
    )

    # -- 3. purga real SIN export: la cola se purga, la auditoría se bloquea
    r1 = _motor(conn, ahora, False)
    audit_r1 = {r["objeto"]: r for r in r1 if r["entidad"] == "audit.audit_events"}
    bloqueadas = {o: r for o, r in audit_r1.items() if r["accion"] == "omitida"}
    inf.paso(
        "purga sin export: el motor omite el DROP de la auditoría («sin comprobante de export WORM»)",
        bool(bloqueadas)
        and all("sin comprobante de export WORM" in str(r["motivo"]) for r in bloqueadas.values())
        and _existe(conn, en_curso)
        and not any(r["accion"] == "eliminada" for r in audit_r1.values()),
        {"particiones_bloqueadas": sorted(bloqueadas)},
    )
    viejas = _uno(
        conn,
        "SELECT count(*) FROM core.webhook_inbox WHERE tenant_id = %s AND delivery_id LIKE 'd-viejo-%%'",
        (a,),
    )
    vigentes = _uno(
        conn,
        "SELECT count(*) FROM core.webhook_inbox WHERE tenant_id = %s AND delivery_id LIKE 'd-vigente-%%'",
        (a,),
    )
    viejas_n = _uno(
        conn,
        "SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND id LIKE 'nt-viejo-%%'",
        (a,),
    )
    vigentes_n = _uno(
        conn,
        "SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND id LIKE 'nt-vigente-%%'",
        (a,),
    )
    inf.paso(
        "colas (webhook_inbox y notifications): se descarta la partición vencida y se conserva la vigente",
        (viejas, vigentes, viejas_n, vigentes_n) == (0, 3, 0, 3),
        {
            "webhook_viejas": viejas,
            "webhook_vigentes": vigentes,
            "notif_viejas": viejas_n,
            "notif_vigentes": vigentes_n,
        },
    )

    # -- 4. export WORM de cada partición de auditoría bloqueada (la más antigua primero) + verificación externa
    exports: list[dict[str, Any]] = []
    for p in [p for p in parts_audit if p in bloqueadas]:
        e = worm.exportar(conn, p, destino, POR)
        exports.append(e)
    problemas_ext = []
    anterior: Path | None = None
    for e in exports:
        problemas_ext += worm.verificar_archivo(e["manifiesto"], anterior)
        anterior = e["manifiesto"]
    inf.paso(
        "export WORM de las particiones vencidas: pasa la verificación externa sin base de datos",
        bool(exports) and not problemas_ext,
        {
            "exports": [
                {
                    "archivo": Path(e["archivo"]).name,
                    "filas": e["filas"],
                    "sha256": e["archivo_sha256"],
                }
                for e in exports
            ],
            "problemas": problemas_ext,
        },
    )

    # -- 5. purga con comprobante: DROP de la partición antigua con ancla
    eventos_a_descartar = _uno(
        conn,
        "SELECT count(*) FROM audit.audit_events WHERE tenant_id = ANY(%s) AND creado_en < %s",
        ([a, b], mes_sig),
    )
    r2 = _motor(conn, ahora, False)
    audit_r2 = {r["objeto"]: r for r in r2 if r["entidad"] == "audit.audit_events"}
    ok_drop = all(
        audit_r2.get(p, {}).get("accion") == "eliminada" for p in bloqueadas
    ) and not _existe(conn, en_curso)
    inf.paso(
        "purga con comprobante: se descarta la partición antigua de auditoría",
        ok_drop,
        {p: r["accion"] for p, r in audit_r2.items()},
    )

    anclas = {
        t: conn.execute(
            "SELECT eventos_descartados FROM audit.chain_anchors WHERE tenant_id = %s", (t,)
        ).fetchone()
        for t in (a, b)
    }
    inf.paso(
        "se anota el ancla de cadena de cada tenant en la misma transacción del DROP",
        anclas[a] == (3,) and anclas[b] == (2,),
        {
            "descartados_a": anclas[a],
            "descartados_b": anclas[b],
            "eventos_en_la_particion": eventos_a_descartar,
        },
    )

    # -- 6. trazabilidad: la cadena restante sigue íntegra y lo posterior sigue protegido
    v_fin = {
        t: tuple(
            conn.execute(
                "SELECT integra, eventos_verificados, eventos_esperados, eventos_observados FROM audit.verify_chain(%s)",
                (t,),
            ).fetchone()
        )
        for t in (a, b)
    }
    inf.paso(
        "trazabilidad: tras el DROP las cadenas siguen íntegras (los eventos posteriores quedan verificados contra el ancla)",
        all(v[0] for v in v_fin.values()) and all(v[3] == 1 for v in v_fin.values()),
        {t: {"integra": v[0], "esperados": v[2], "observados": v[3]} for t, v in v_fin.items()},
    )

    # -- 7. TTL de idempotencia y minimización de PII
    ttl = _uno(conn, "SELECT core.purgar_idempotency_keys(%s)", (ahora,))
    claves = {
        str(f[0]).rsplit("_", 1)[0]
        for f in conn.execute(
            "SELECT clave FROM core.idempotency_keys WHERE endpoint = 'ep.ensayo'"
        ).fetchall()
    }
    inf.paso(
        "TTL de idempotency_keys: se eliminan las vencidas y se conservan las vigentes y la reserva en curso",
        ttl >= 2 and claves == {"activa_47h", "activa_1h", "reserva_en_curso"},
        {"eliminadas": ttl, "restantes": sorted(claves)},
    )
    pii = dict(conn.execute("SELECT regla, filas FROM core.minimizar_pii(%s)", (ahora,)).fetchall())
    rt_vieja = conn.execute(
        "SELECT ip, user_agent FROM core.refresh_tokens WHERE id = %s", (f"rt_vieja_{ids['s']}",)
    ).fetchone()
    rt_nueva = conn.execute(
        "SELECT ip IS NOT NULL, user_agent IS NOT NULL FROM core.refresh_tokens WHERE id = %s",
        (f"rt_reciente_{ids['s']}",),
    ).fetchone()
    emails = dict(
        conn.execute(
            "SELECT id, email FROM core.invitations WHERE id LIKE %s", (f"%\\_{ids['s']}",)
        ).fetchall()
    )
    anon = {
        k.split("_", 1)[1].rsplit("_", 1)[0]: v.startswith("anonimizado-")
        for k, v in emails.items()
    }
    inf.paso(
        "minimización de PII: se anonimiza lo que salió de su ventana y solo eso",
        pii.get("refresh_tokens_red", 0) >= 1
        and pii.get("invitaciones_email", 0) >= 1
        and rt_vieja == (None, None)
        and rt_nueva == (True, True)
        and anon == {"aceptada_vieja": True, "enviada_vieja": False, "aceptada_reciente": False},
        {"reglas": pii, "invitaciones_anonimizadas": anon},
    )
    pii2 = dict(
        conn.execute("SELECT regla, filas FROM core.minimizar_pii(%s)", (ahora,)).fetchall()
    )
    inf.paso(
        "minimización idempotente: una segunda ejecución no toca nada",
        all(v == 0 for v in pii2.values()),
        pii2,
    )

    # -- 8. evidencia en los registros
    purgas = {
        (r[0], r[1])
        for r in conn.execute("SELECT entidad, accion FROM core.retention_purgas").fetchall()
    }
    log = {r[0] for r in conn.execute("SELECT tarea FROM core.purgas_log").fetchall()}
    inf.paso(
        "registros: core.retention_purgas y core.purgas_log dejan constancia de cada purga",
        {
            ("audit.audit_events", "eliminada"),
            ("core.webhook_inbox", "eliminada"),
            ("core.notifications", "eliminada"),
        }
        <= purgas
        and {"idempotency_keys", "pii:refresh_tokens_red", "pii:invitaciones_email"} <= log,
        {"retention_purgas": sorted(f"{e}:{ac}" for e, ac in purgas), "purgas_log": sorted(log)},
    )

    # -- 9. validación final de integridad + re-verificación de los archivos contra el comprobante registrado
    fin = _problemas(conn)
    inf.paso(
        "validación final: los chequeos de integridad no empeoran (0 problemas en una base limpia)",
        all(v <= base.get(k, 0) for k, v in fin.items()),
        {
            "chequeos": len(fin),
            "con_problemas": {k: v for k, v in fin.items() if v},
            "base_limpia": not any(fin.values()),
        },
    )
    registrados = dict(
        conn.execute("SELECT archivo_sha256, manifiesto_sha256 FROM audit.worm_exports").fetchall()
    )
    reverif: list[str] = []
    for e in exports:
        reverif += worm.verificar_archivo(
            e["manifiesto"], None, registrados.get(e["archivo_sha256"])
        )
    inf.paso(
        "re-verificación final de los exports contra el hash del manifiesto registrado en la base",
        bool(exports) and not reverif and all(e["archivo_sha256"] in registrados for e in exports),
        {"problemas": reverif},
    )
    inf.paso(
        "los exports son de solo lectura en disco",
        bool(exports)
        and all(
            not (Path(p).stat().st_mode & stat.S_IWUSR)
            for e in exports
            for p in (e["archivo"], e["manifiesto"])
        ),
        None,
    )
    return inf


def _dsn() -> str:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        raise SystemExit("KC_DATABASE_URL_MIGRATIONS (rol administrador) no está definida")
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def main(argv: list[str] | None = None) -> int:
    import psycopg

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--destino",
        required=True,
        type=Path,
        help="carpeta donde dejar los exports WORM (evidencia)",
    )
    ap.add_argument(
        "--informe", type=Path, help="archivo Markdown del informe (además de imprimirlo)"
    )
    args = ap.parse_args(argv)

    conn = psycopg.connect(_dsn(), autocommit=False)
    try:
        inf = ejecutar(conn, args.destino)
    finally:
        conn.rollback()  # el ensayo no deja nada en la base
        conn.close()
    texto = inf.markdown()
    print(texto)
    if args.informe:
        args.informe.parent.mkdir(parents=True, exist_ok=True)
        args.informe.write_text(texto, encoding="utf-8")
    return 0 if inf.ok else 1


if __name__ == "__main__":
    sys.exit(main())
