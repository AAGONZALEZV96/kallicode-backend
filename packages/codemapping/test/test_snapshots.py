"""Pruebas unitarias para Snapshots y Checkpoints (KC-CM-03-2)."""

from unittest.mock import MagicMock, patch

from kallicode_codemapping.snapshots import SnapshotManager


def test_obtener_o_crear_snapshot_nuevo() -> None:
    with patch("psycopg.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.return_value = None  # No hay snapshot existente

        manager = SnapshotManager(dsn="postgresql://localhost/db", tenant_id="org_test")
        snap_id, estado, checkpoint = manager.obtener_o_crear_snapshot("sha_12345", "main")

        assert snap_id.startswith("snap_org_te_sha_1234")
        assert estado == "iniciado"
        assert checkpoint == 0
        assert mock_conn.commit.called


def test_reanudar_snapshot_existente() -> None:
    with patch("psycopg.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Simular snapshot en curso en fase 3
        mock_cur.fetchone.return_value = ("snap_previo", "fallido", {"checkpoint_fase": 3})

        manager = SnapshotManager(dsn="postgresql://localhost/db", tenant_id="org_test")
        snap_id, estado, checkpoint = manager.obtener_o_crear_snapshot("sha_12345", "main")

        assert snap_id == "snap_previo"
        assert estado == "fallido"
        assert checkpoint == 3
