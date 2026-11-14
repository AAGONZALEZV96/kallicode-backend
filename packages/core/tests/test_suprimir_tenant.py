import hashlib
import os
import uuid

import psycopg


def _conn_admin() -> psycopg.Connection:
    url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@127.0.0.1:5432/kallicode_test"
    )
    return psycopg.connect(url, autocommit=True)


def test_soft_delete_y_suprimir_tenant() -> None:
    tenant_id = f"org_del_{uuid.uuid4().hex[:10]}"
    user_id = f"usr_{uuid.uuid4().hex[:10]}"
    ticket_id = f"tk_{uuid.uuid4().hex[:10]}"
    snap_id = f"snp_{uuid.uuid4().hex[:10]}"
    node_id = f"nod_{uuid.uuid4().hex[:10]}"

    with _conn_admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.organizations (id, nombre, idioma, timezone, despliegue, fabrica_activa)
            VALUES (%s, 'Tenant Prueba Supresión', 'es', 'UTC', 'cloud', true)
            """,
            (tenant_id,),
        )
        cur.execute(
            """
            INSERT INTO core.users (id, tenant_id, email, nombre, rol, estado, deleted_at)
            VALUES (%s, %s, %s, 'Usuario Prueba', 'member', 'activo', now())
            """,
            (user_id, tenant_id, f"{user_id}@example.com"),
        )
        cur.execute(
            """
            INSERT INTO core.tickets (
                id, tenant_id, numero, titulo, descripcion, tipo, prioridad, etapa, origen, reportado_por, deleted_at
            )
            VALUES (%s, %s, 1, 'Ticket Supresión', 'Desc', 'bug', 'media', 'triage', 'portal', %s, now())
            """,
            (ticket_id, tenant_id, user_id),
        )
        cur.execute(
            """
            INSERT INTO cm.repo_snapshots (tenant_id, id, commit_sha, rama, estado)
            VALUES (%s, %s, %s, 'main', 'completo')
            """,
            (tenant_id, snap_id, "a" * 40),
        )
        cur.execute(
            """
            INSERT INTO cm.nodes (tenant_id, id, kind)
            VALUES (%s, %s, 'definition')
            """,
            (tenant_id, node_id),
        )
        vec_literal = "[" + ",".join(["0.01"] * 1024) + "]"
        cur.execute(
            """
            INSERT INTO vec.ticket_embeddings (
                tenant_id, ticket_id, embedding, texto_hash
            )
            VALUES (%s, %s, %s::vector, %s)
            """,
            (tenant_id, ticket_id, vec_literal, "b" * 64),
        )

        cur.execute(
            "SELECT core.suprimir_tenant(%s, 'dpo@kallicode.io', 'derecho_al_olvido')",
            (tenant_id,),
        )
        acta_id = cur.fetchone()[0]
        assert acta_id.startswith("del_")

        for tabla, col in [
            ("core.organizations", "id"),
            ("core.users", "tenant_id"),
            ("core.tickets", "tenant_id"),
            ("cm.repo_snapshots", "tenant_id"),
            ("cm.nodes", "tenant_id"),
            ("vec.ticket_embeddings", "tenant_id"),
            ("audit.audit_events", "tenant_id"),
        ]:
            cur.execute(f"SELECT count(*) FROM {tabla} WHERE {col} = %s", (tenant_id,))
            assert cur.fetchone()[0] == 0, f"Quedaron filas de {tenant_id} en {tabla}"

        tenant_hash = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        cur.execute(
            """
            SELECT tenant_hash, filas_eliminadas
            FROM audit.tenant_deletion_receipts
            WHERE id = %s
            """,
            (acta_id,),
        )
        fila_acta = cur.fetchone()
        assert fila_acta is not None
        assert fila_acta[0] == tenant_hash
        assert fila_acta[1]["core.organizations"] == 1
        assert fila_acta[1]["vec.ticket_embeddings"] == 1
