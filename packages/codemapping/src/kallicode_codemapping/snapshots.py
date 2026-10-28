"""Gestión de snapshots, checkpoints y observabilidad estructurada en cm.repo_snapshots.

Implementa la máquina de estados de ingesta (KC-CM-03-2) y telemetría estructurada
con trace_id y tenant_id en formato JSONB para observabilidad determinista (KC-CM-04-4 / CM-F10).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .logging_setup import get_logger

logger = get_logger()


class SnapshotManager:
    """Administra el ciclo de vida, checkpoints y telemetría estructurada de snapshots."""

    def __init__(self, dsn: str, tenant_id: str) -> None:
        self.dsn = dsn
        self.tenant_id = tenant_id

    def obtener_o_crear_snapshot(
        self,
        commit_sha: str,
        rama: str,
        trace_id: str | None = None,
    ) -> tuple[str, str, int]:
        """Recupera un snapshot en curso para reanudar o crea uno nuevo registrando traza inicial.

        Retorna: (snapshot_id, estado, ultima_fase_completada)
        """
        tid = trace_id or f"tr_{commit_sha[:8]}_{int(datetime.now(UTC).timestamp())}"

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL app.tenant_id = %s;", (self.tenant_id,))

            # 1. Buscar si existe un snapshot fallido o en curso para este commit
            cur.execute(
                """
                SELECT id, estado, stats
                FROM cm.repo_snapshots
                WHERE tenant_id = %s AND commit_sha = %s AND estado IN ('iniciado', 'fallido')
                ORDER BY iniciado_en DESC LIMIT 1;
                """,
                (self.tenant_id, commit_sha),
            )
            row = cur.fetchone()
            if row and len(row) >= 3:
                snap_id, estado, stats_val = row[0], row[1], row[2]
                stats = stats_val if isinstance(stats_val, dict) else json.loads(stats_val or "{}")
                ultima_fase = stats.get("checkpoint_fase", 0)

                self.registrar_evento(
                    snapshot_id=str(snap_id),
                    trace_id=tid,
                    fase=f"resume-{ultima_fase}",
                    nivel="INFO",
                    mensaje="Reanudando snapshot existente",
                    payload={"commit_sha": commit_sha, "estado_previo": estado},
                    conn=conn,
                )
                conn.commit()
                return str(snap_id), str(estado), int(ultima_fase)

            # 2. Si no existe, crear uno nuevo con evento estructurado inicial
            snap_id = (
                f"snap_{self.tenant_id[:6]}_{commit_sha[:8]}_{int(datetime.now(UTC).timestamp())}"
            )
            evento_inicial = {
                "timestamp": datetime.now(UTC).isoformat(),
                "trace_id": tid,
                "tenant_id": self.tenant_id,
                "snapshot_id": snap_id,
                "fase": "inicio",
                "level": "INFO",
                "mensaje": "Snapshot iniciado",
                "data": {"commit_sha": commit_sha, "rama": rama},
            }

            stats_iniciales = {
                "checkpoint_fase": 0,
                "trace_id": tid,
                "events": [evento_inicial],
            }

            cur.execute(
                """
                INSERT INTO cm.repo_snapshots (
                    id, tenant_id, commit_sha, rama, iniciado_en, estado, stats
                ) VALUES (%s, %s, %s, %s, %s, 'iniciado', %s);
                """,
                (
                    snap_id,
                    self.tenant_id,
                    commit_sha,
                    rama,
                    datetime.now(UTC),
                    Jsonb(stats_iniciales),
                ),
            )
            conn.commit()
            return snap_id, "iniciado", 0

    def registrar_evento(
        self,
        snapshot_id: str,
        trace_id: str,
        fase: str,
        nivel: str,
        mensaje: str,
        payload: dict[str, Any] | None = None,
        conn: psycopg.Connection[Any] | None = None,
    ) -> None:
        """Registra un evento JSON estructurado dentro del array de auditoría en cm.repo_snapshots."""
        evento = {
            "timestamp": datetime.now(UTC).isoformat(),
            "trace_id": trace_id,
            "tenant_id": self.tenant_id,
            "snapshot_id": snapshot_id,
            "fase": fase,
            "level": nivel.upper(),
            "mensaje": mensaje,
            "data": payload or {},
        }

        sql = """
            UPDATE cm.repo_snapshots
            SET stats = jsonb_set(
                COALESCE(stats, '{}'::jsonb),
                '{events}',
                COALESCE(stats->'events', '[]'::jsonb) || %s::jsonb
            ) || jsonb_build_object('last_trace_id', %s::text)
            WHERE tenant_id = %s AND id = %s;
        """
        params = (Jsonb([evento]), trace_id, self.tenant_id, snapshot_id)

        if conn is not None and not bool(getattr(conn, "closed", False) is True):
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.tenant_id = %s;", (self.tenant_id,))
                cur.execute(sql, params)
        else:
            with psycopg.connect(self.dsn) as new_conn, new_conn.cursor() as cur:
                cur.execute("SET LOCAL app.tenant_id = %s;", (self.tenant_id,))
                cur.execute(sql, params)
                new_conn.commit()

    def registrar_checkpoint(
        self,
        snapshot_id: str,
        fase: int,
        stats_fase: dict[str, Any],
        trace_id: str | None = None,
    ) -> None:
        """Confirma el progreso de una fase como checkpoint atómico y anexa su telemetría."""
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL app.tenant_id = %s;", (self.tenant_id,))
            cur.execute(
                """
                SELECT stats FROM cm.repo_snapshots
                WHERE tenant_id = %s AND id = %s FOR UPDATE;
                """,
                (self.tenant_id, snapshot_id),
            )
            res = cur.fetchone()
            stats = (
                res[0]
                if (res and isinstance(res[0], dict))
                else json.loads((res[0] if res else None) or "{}")
            )

            tid = trace_id or stats.get("last_trace_id") or stats.get("trace_id") or "desconocido"
            stats["checkpoint_fase"] = fase
            stats[f"fase_{fase}"] = stats_fase

            # Evento estructurado de avance de fase
            evento = {
                "timestamp": datetime.now(UTC).isoformat(),
                "trace_id": tid,
                "tenant_id": self.tenant_id,
                "snapshot_id": snapshot_id,
                "fase": f"fase_{fase}",
                "level": "INFO",
                "mensaje": f"Checkpoint de fase {fase} registrado con éxito",
                "data": stats_fase,
            }
            eventos = stats.get("events", [])
            if not isinstance(eventos, list):
                eventos = []
            eventos.append(evento)
            stats["events"] = eventos

            cur.execute(
                """
                UPDATE cm.repo_snapshots
                SET stats = %s
                WHERE tenant_id = %s AND id = %s;
                """,
                (Jsonb(stats), self.tenant_id, snapshot_id),
            )
            conn.commit()

    def marcar_completado(
        self,
        snapshot_id: str,
        total_stats: dict[str, Any],
        trace_id: str | None = None,
    ) -> None:
        """Marca el snapshot como completo y vigente, anexando métricas acumuladas."""
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL app.tenant_id = %s;", (self.tenant_id,))
            cur.execute(
                """
                SELECT stats FROM cm.repo_snapshots
                WHERE tenant_id = %s AND id = %s FOR UPDATE;
                """,
                (self.tenant_id, snapshot_id),
            )
            res = cur.fetchone()
            stats = (
                res[0]
                if (res and isinstance(res[0], dict))
                else json.loads((res[0] if res else None) or "{}")
            )

            tid = trace_id or stats.get("last_trace_id") or stats.get("trace_id") or "desconocido"
            stats.update(total_stats)

            evento = {
                "timestamp": datetime.now(UTC).isoformat(),
                "trace_id": tid,
                "tenant_id": self.tenant_id,
                "snapshot_id": snapshot_id,
                "fase": "finalizado",
                "level": "INFO",
                "mensaje": "Pipeline de ingesta completado con éxito",
                "data": total_stats,
            }
            eventos = stats.get("events", [])
            if not isinstance(eventos, list):
                eventos = []
            eventos.append(evento)
            stats["events"] = eventos

            cur.execute(
                """
                UPDATE cm.repo_snapshots
                SET estado = 'vigente', finalizado_en = %s, stats = %s
                WHERE tenant_id = %s AND id = %s;
                """,
                (datetime.now(UTC), Jsonb(stats), self.tenant_id, snapshot_id),
            )
            conn.commit()

    def marcar_fallido(
        self,
        snapshot_id: str,
        error_msg: str,
        fase: int,
        trace_id: str | None = None,
    ) -> None:
        """Registra el fallo de forma estructurada en JSON preservando la bitácora de eventos."""
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL app.tenant_id = %s;", (self.tenant_id,))
            cur.execute(
                """
                SELECT stats FROM cm.repo_snapshots
                WHERE tenant_id = %s AND id = %s FOR UPDATE;
                """,
                (self.tenant_id, snapshot_id),
            )
            res = cur.fetchone()
            stats = (
                res[0]
                if (res and isinstance(res[0], dict))
                else json.loads((res[0] if res else None) or "{}")
            )

            tid = trace_id or stats.get("last_trace_id") or stats.get("trace_id") or "desconocido"
            error_data = {"fase": fase, "mensaje": error_msg}
            stats["error"] = error_data

            evento = {
                "timestamp": datetime.now(UTC).isoformat(),
                "trace_id": tid,
                "tenant_id": self.tenant_id,
                "snapshot_id": snapshot_id,
                "fase": f"fase_{fase}",
                "level": "ERROR",
                "mensaje": f"Pipeline fallido en fase {fase}: {error_msg}",
                "data": error_data,
            }
            eventos = stats.get("events", [])
            if not isinstance(eventos, list):
                eventos = []
            eventos.append(evento)
            stats["events"] = eventos

            cur.execute(
                """
                UPDATE cm.repo_snapshots
                SET estado = 'fallido', stats = %s
                WHERE tenant_id = %s AND id = %s;
                """,
                (Jsonb(stats), self.tenant_id, snapshot_id),
            )
            conn.commit()
