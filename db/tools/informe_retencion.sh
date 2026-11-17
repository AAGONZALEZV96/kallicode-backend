#!/usr/bin/env bash
# ============================================================================
# KALLICODE · db/tools/informe_retencion.sh
# KC-DB-10-4 — Genera el informe de cumplimiento de retención y falla si hay alertas o hallazgos de integridad.
#
#   db/tools/informe_retencion.sh [destino] [--sin-ciclo]
#
# Conexión por las variables estándar de libpq (PGHOST, PGPORT, PGUSER, PGDATABASE, PGPASSWORD), con
# kallicode_admin o un superusuario.
#
#   * Por defecto ejecuta antes el CICLO COMPLETO de retención: core.purgar_retencion() (10-1, con el gancho WORM de
#     10-2), core.purgar_idempotency_keys() y core.minimizar_pii() (10-3). Es el modo del CI y del nocturno, sobre una
#     base efímera.
#   * --sin-ciclo solo lee: es el modo del smoke post-deploy contra una base real (las purgas reales las lanza
#     pg_cron en su ventana nocturna, no un despliegue).
#
# Deja en <destino> (por defecto ./informe-retencion) informe_retencion.txt, alertas.txt, integridad.txt y, con ciclo,
# ciclo.txt. Sale con 1 si core.alertas_retencion() devuelve filas o core.validar_integridad() algún problema.
# ============================================================================
set -euo pipefail

destino="informe-retencion"
ciclo=1
for arg in "$@"; do
    case "$arg" in
        --sin-ciclo) ciclo=0 ;;
        -*) echo "opción desconocida: $arg" >&2; exit 2 ;;
        *) destino="$arg" ;;
    esac
done

raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$destino"
psql_q=(psql -X -v ON_ERROR_STOP=1)

if [ "$ciclo" = 1 ]; then
    "${psql_q[@]}" \
        -c "SELECT * FROM core.purgar_retencion()" \
        -c "SELECT core.purgar_idempotency_keys() AS idempotency_keys_purgadas" \
        -c "SELECT * FROM core.minimizar_pii()" \
        > "$destino/ciclo.txt"
fi

"${psql_q[@]}" -f "$raiz/db/tools/informe_retencion.sql" | tee "$destino/informe_retencion.txt"

"${psql_q[@]}" -c "SELECT * FROM core.alertas_retencion()" > "$destino/alertas.txt"
"${psql_q[@]}" -c "SELECT * FROM core.validar_integridad() ORDER BY chequeo" > "$destino/integridad.txt"
alertas="$("${psql_q[@]}" -tA -c "SELECT count(*) FROM core.alertas_retencion()")"
problemas="$("${psql_q[@]}" -tA -c "SELECT coalesce(sum(problemas), 0) FROM core.validar_integridad() WHERE problemas > 0")"
sin_politica="$("${psql_q[@]}" -tA -c "SELECT problemas FROM core.validar_integridad() WHERE chequeo = 'V26_tabla_sin_retencion'")"

resumen="Alertas de retención: ${alertas} · problemas de integridad: ${problemas} (V26 tablas sin retención: ${sin_politica})"
echo "$resumen"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
        echo "## Informe de cumplimiento de retención (KC-DB-10-4)"
        echo
        echo "$resumen"
        echo
        echo '```text'
        sed -n '/=== 1\./,/=== 2\./p' "$destino/informe_retencion.txt" | sed '$d'
        echo '```'
    } >> "$GITHUB_STEP_SUMMARY"
fi

test "$alertas" = "0" && test "$problemas" = "0"
