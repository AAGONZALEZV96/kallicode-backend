"""Pruebas unitarias para logging estructurado JSON en snapshots (KC-CM-04-4)."""

from unittest.mock import MagicMock, patch

from kallicode_codemapping.snapshots import SnapshotManager


def test_registrar_evento_snapshot_estructura_json() -> None:
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    # Evitar la trampa booleana de MagicMock en 'not conn.closed'
    mock_conn.closed = False
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    mgr = SnapshotManager(
        dsn="postgresql://postgres:dev@localhost:5432/kallicode",
        tenant_id="tenant-acme",
    )

    mgr.registrar_evento(
        snapshot_id="snap-123",
        trace_id="tr-xyz-789",
        fase="1/7",
        nivel="INFO",
        mensaje="Repo sincronizado",
        payload={"commit": "abc1234"},
        conn=mock_conn,
    )

    mock_cursor.execute.assert_called()
    call_args = mock_cursor.execute.call_args[0]
    params = call_args[1]

    # Validar propagación estructurada en el payload JSONB
    json_eventos = params[0].obj
    assert len(json_eventos) == 1
    ev = json_eventos[0]
    assert ev["trace_id"] == "tr-xyz-789"
    assert ev["tenant_id"] == "tenant-acme"
    assert ev["fase"] == "1/7"
    assert ev["level"] == "INFO"
    assert ev["mensaje"] == "Repo sincronizado"
    assert ev["data"]["commit"] == "abc1234"


@patch("psycopg.connect")
def test_registrar_evento_sin_conn_crea_conexion_aislada(mock_connect: MagicMock) -> None:
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_connect.return_value.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    mgr = SnapshotManager(
        dsn="postgresql://postgres:dev@localhost:5432/kallicode",
        tenant_id="tenant-acme",
    )

    # Invocación sin pasar conn: debe usar psycopg.connect aislado
    mgr.registrar_evento(
        snapshot_id="snap-123",
        trace_id="tr-xyz-789",
        fase="2/7",
        nivel="ERROR",
        mensaje="Fallo en ingesta",
    )

    mock_connect.assert_called_once()
    mock_cursor.execute.assert_called()
    mock_conn.commit.assert_called_once()
