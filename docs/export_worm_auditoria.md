# Export WORM de la auditoría antes de descartar particiones (KC-DB-10-2)

Migración `055_export_worm.sql` · herramienta `db/tools/worm_auditoria.py` · prueba
`packages/core/tests/integracion/test_export_worm.py`.

## 1. Qué problema resuelve

`audit.audit_events` es una cadena de hashes por tenant: cada evento incluye el sello del anterior (ver
`docs/auditoria_cadena.md`). Al purgar una partición vieja (KC-DB-10-1) se perderían eventos que son **evidencia**, y la
cadena de cada tenant empezaría en un eslabón que ya no existe. Dos garantías lo evitan:

1. **Antes del DROP**, el contenido de la partición se serializa a un archivo inmutable y se **verifica sin la base de
   datos**; solo entonces se registra un comprobante. Sin comprobante vigente, el motor no descarta nada.
2. **En el DROP**, se anota por tenant cuántos eventos se descartan y cuál fue el último sello (un **ancla**), de modo que
   `audit.verify_chain`, V16 y V17 siguen dando «íntegra» sobre lo que queda y siguen detectando cualquier pérdida
   posterior.

## 2. El procedimiento

```bash
# 1. ver qué particiones hay y cuáles no tienen comprobante
python db/tools/worm_auditoria.py pendientes
# 2. exportar la partición MÁS ANTIGUA sin exportar (rol administrador: KC_DATABASE_URL_MIGRATIONS)
python db/tools/worm_auditoria.py exportar --particion audit.audit_events_p20260101 --destino /ruta/worm --por "nombre"
# 3. (en cualquier máquina, sin base de datos) verificar el export
python db/tools/worm_auditoria.py verificar /ruta/worm/audit_events_p20260101.<fecha>.manifest.json \
       [--anterior <manifiesto del export previo>] [--sha256-manifiesto <el registrado en audit.worm_exports>]
```

`exportar` hace, en este orden: comprueba que las cadenas de los tenants de la partición son íntegras (si no, **no
exporta**); escribe el archivo (JSON Lines, modo exclusivo, nunca sobrescribe) y el manifiesto; los deja de solo
lectura; **los verifica con el verificador externo**; y solo si pasan llama a `audit.registrar_export_worm`, que
registra el comprobante en `audit.worm_exports` (append-only).

Después, cuando una política de retención de `audit.audit_events` ordene descartarla, `core.purgar_retencion()` consulta
`audit.comprobante_worm_vigente`: si lo hay (y la partición no cambió) ancla la cadena y descarta.

## 3. Qué comprueba cada cosa

| Qué | Dónde | Cómo |
|---|---|---|
| El archivo no cambió | verificador externo | `sha256` del archivo = el del manifiesto |
| Ningún evento fue alterado | verificador externo | recalcula el sello de **cada** evento con su propio contenido (mismo canónico que `audit.calcular_sello`, versiones 1 y 2) |
| Ninguno falta ni sobra | verificador externo | recuento, digest `id:sello` y cadena de cada tenant recorrida de principio a fin sin eslabones sueltos ni bifurcaciones; coincide con el resumen por tenant del manifiesto |
| Los exports forman una cadena | verificador externo + base | el manifiesto guarda el `sha256` del export anterior; la base se niega a registrar uno que no enlace con el último |
| El export es de lo que hay en la base | base | `registrar_export_worm` recalcula el digest y el resumen por tenant y exige que coincidan |
| No se descarta contenido distinto del exportado | base | `comprobante_worm_vigente` vuelve a calcular el digest al descartar: si la partición cambió tras el export, se bloquea hasta exportar de nuevo |
| Se descarta en orden | base | cada partición debe ser un **prefijo** de la cadena de cada tenant (las más antiguas primero) |

**Para frenar a quien reescriba archivo *y* manifiesto**, hay que comparar el hash del manifiesto con el registrado en
la base (`--sha256-manifiesto`, columna `manifiesto_sha256` de `audit.worm_exports`, que es append-only). El verificador por sí solo
demuestra que el archivo es internamente coherente y está encadenado; el comprobante de la base ancla ese hash.

## 4. Las anclas de cadena

`audit.chain_anchors(tenant_id, eventos_descartados, ultimo_sello_descartado)`. Tras descartar la partición de enero, el
primer evento de febrero de un tenant sigue apuntando (`sello_previo`) al último de enero, que ya no está: es el ancla.
`audit.verify_chain` acepta ese enlace y descuenta lo anclado del recuento esperado de `audit.chain_heads`; V17 igual.
Solo pueden **avanzar** (un trigger rechaza `DELETE`, `TRUNCATE` y cualquier `UPDATE` que no aumente el contador).
Una pérdida de eventos **posterior** al descarte sigue rompiendo la cadena
(`test_el_ancla_no_oculta_un_borrado_posterior`).

## 5. Qué NO hace y límites

* **No decide cuánto tiempo se conserva la auditoría.** La política de `audit.audit_events` en `core.retention_policies`
  sigue siendo `conservar`: sin una política que ordene descartar, nada se purga. Pasar a `drop` con N días es una decisión de
  negocio y legal; este mecanismo solo hace que, si se toma, sea seguro.
* **La inmutabilidad real del archivo depende del almacenamiento.** La herramienta lo crea en modo exclusivo y de solo lectura, pero
  no puede impedir que alguien con acceso al disco lo borre. Para «write once» de verdad hay que depositar el export en
  un volumen WORM, un blob con política de inmutabilidad o un bucket con object lock (no verificado aquí: no hay ninguno
  configurado en el entorno de pruebas).
* **Quién lo ejecuta no está automatizado**: hace falta una tarea programada que exporte cada partición vencida (la
  herramienta es un script, no un trabajo de pg_cron: una función SQL no puede escribir archivos). Mientras no se
  automatice, `pendientes` lista lo que falta.
* **Derecho al olvido.** Un export WORM contiene eventos de auditoría de todos los tenants de la partición, y un
  export es por definición inmutable: `core.suprimir_tenant` no puede borrarlos de él. Las anclas no llevan clave foránea
  (sobreviven a la supresión del tenant) y `suprimir_tenant` no las borra. Hay que decidir la política de retención de los
  propios exports.
* **El canónico del sello depende de la zona horaria de la sesión** (`to_char` sin conversión). La herramienta fuerza
  `UTC` al exportar; si algún día se sellara en otra zona, el verificador externo lo detectaría (el sello no coincidiría).
* El orden de descarte exige exportar y descartar de la más antigua a la más reciente; un hueco bloquea las siguientes.

## 6. Diagnóstico y resolución de una cadena rota (KC-DB-11-5)

El export también sirve para **investigar y reparar** una alteración posterior (procedimiento completo: `docs/runbook_incidentes_db.md`, I3):

```bash
# qué eventos y qué columnas difieren de su export (alterados, borrados, añadidos posteriores)
python db/tools/worm_auditoria.py comparar <export>.manifest.json
# devuelve UN evento alterado a su contenido original (superusuario; verifica el export y el hash registrado, y
# deshace todo si la cadena del tenant no vuelve a ser íntegra)
python db/tools/worm_auditoria.py restaurar <export>.manifest.json --evento <id> --sha256-manifiesto <registrado>
```

Límites: `restaurar` solo corrige eventos **alterados** (no inserta eventos borrados: para eso, una copia PITR) y solo es legítimo si el
export es el registrado en `audit.worm_exports` y supera la verificación externa.

