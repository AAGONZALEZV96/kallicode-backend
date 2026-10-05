"""Gestión de snapshots y checkpoints de ingesta en cm.repo_snapshots (KC-CM-03-2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import psycopg


class SnapshotManager:
    """Administra el ciclo de vida, checkpoints y reanudación de snapshots de ingesta."""

    def __init__(self, dsn: str, tenant_id: str) -> None:
        self.dsn = dsn
        self.tenant_id = tenant_id

    def obtener_o_crear_snapshot(self, commit_sha: str, rama: str) -> tuple[str, str, int]:
        """Recupera un snapshot en curso para reanudar o crea uno nuevo.

        Retorna: (snapshot_id, estado, ultima_fase_completada)
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL app.tenant_id = %s;", (self.tenant_id,))

            # Buscar si existe un snapshot fallido o en curso para este commit
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
            if row:
                snap_id, estado, stats_val = row
                stats = stats_val if isinstance(stats_val, dict) else json.loads(stats_val or "{}")
                ultima_fase = stats.get("checkpoint_fase", 0)
                return str(snap_id), str(estado), int(ultima_fase)

            # Si no existe, crear uno nuevo
            snap_id = (
                f"snap_{self.tenant_id[:6]}_{commit_sha[:8]}_{int(datetime.now(UTC).timestamp())}"
            )
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
                    json.dumps({"checkpoint_fase": 0}),
                ),
            )
            conn.commit()
            return snap_id, "iniciado", 0

    def registrar_checkpoint(self, snapshot_id: str, fase: int, stats_fase: dict[str, Any]) -> None:
        """Confirma el progreso de una fase como checkpoint atómico."""
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
            stats["checkpoint_fase"] = fase
            stats[f"fase_{fase}"] = stats_fase

            cur.execute(
                """
                    UPDATE cm.repo_snapshots
                    SET stats = %s
                    WHERE tenant_id = %s AND id = %s;
                    """,
                (json.dumps(stats), self.tenant_id, snapshot_id),
            )
            conn.commit()

    def marcar_completado(self, snapshot_id: str, total_stats: dict[str, Any]) -> None:
        """Marca el snapshot como completo y vigente."""
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL app.tenant_id = %s;", (self.tenant_id,))
            cur.execute(
                """
                    UPDATE cm.repo_snapshots
                    SET estado = 'vigente', finalizado_en = %s, stats = %s
                    WHERE tenant_id = %s AND id = %s;
                    """,
                (datetime.now(UTC), json.dumps(total_stats), self.tenant_id, snapshot_id),
            )
            conn.commit()

    def marcar_fallido(self, snapshot_id: str, error_msg: str, fase: int) -> None:
        """Registra el fallo con el motivo exacto para permitir reanudación."""
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL app.tenant_id = %s;", (self.tenant_id,))
            cur.execute(
                """
                    UPDATE cm.repo_snapshots
                    SET estado = 'fallido',
                        stats = jsonb_set(COALESCE(stats, '{}'::jsonb), '{error}', %s::jsonb)
                    WHERE tenant_id = %s AND id = %s;
                    """,
                (json.dumps({"fase": fase, "mensaje": error_msg}), self.tenant_id, snapshot_id),
            )
            conn.commit()
