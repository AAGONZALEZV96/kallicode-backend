#!/bin/sh
# KC-DB-11-1 — archive_command de PostgreSQL: copia un segmento de WAL al archivo de recuperación (PITR).
#
#   archive_command = 'sh /usr/local/bin/archivar_wal.sh "%p" "%f"'
#
# Contrato de PostgreSQL: salir con 0 SOLO si el segmento quedó guardado de forma durable; cualquier otro código
# hace que PostgreSQL lo reintente (y no recicle el segmento). Por eso:
#   * se copia a un nombre temporal y se renombra al final (nunca queda un segmento a medias con el nombre bueno);
#   * si el segmento ya existe y es idéntico, sale con 0 (reintento tras un fallo: idempotente);
#   * si ya existe y es DISTINTO, falla (nunca se sobrescribe un WAL archivado: sería corromper la recuperación).
#
# El destino es un directorio (KC_WAL_ARCHIVO, por defecto /pitr/wal). En producción este script es el ÚNICO punto
# que cambia para guardar el WAL en otro sitio (otro volumen, un bucket, pgBackRest...): el resto del procedimiento
# (docs/recuperacion_pitr.md) no cambia. Un volumen local en el mismo servidor NO sobrevive a la pérdida del servidor.
set -eu

[ "$#" -eq 2 ] || { echo "uso: archivar_wal.sh <ruta_wal> <nombre_wal>" >&2; exit 2; }
ORIGEN="$1"
NOMBRE="$2"
DESTINO="${KC_WAL_ARCHIVO:-/pitr/wal}"

[ -f "$ORIGEN" ] || { echo "archivar_wal: no existe $ORIGEN" >&2; exit 1; }
[ -d "$DESTINO" ] || { echo "archivar_wal: no existe el destino $DESTINO" >&2; exit 1; }

if [ -f "$DESTINO/$NOMBRE" ]; then
    if cmp -s "$ORIGEN" "$DESTINO/$NOMBRE"; then
        exit 0
    fi
    echo "archivar_wal: $NOMBRE ya está archivado con contenido distinto; no se sobrescribe" >&2
    exit 1
fi

TMP="$DESTINO/.$NOMBRE.tmp"
cp "$ORIGEN" "$TMP"
sync "$TMP" 2>/dev/null || sync
mv "$TMP" "$DESTINO/$NOMBRE"
