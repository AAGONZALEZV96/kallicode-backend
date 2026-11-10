# KC-BE-05-5 — Confirmación de adjuntos y cuota QU-3

El portal reserva con `POST /api/v1/tickets/attachments`, sube directamente al blob y llama a
`POST /api/v1/tickets/attachments/{adjunto_id}/confirm` antes de enviar el ticket.

La confirmación consulta las propiedades del objeto, conserva el tamaño real incluso cuando
rechaza la subida, detecta el MIME con libmagic (`file`) y analiza el contenido con ClamAV
INSTREAM. Solo `confirmado=true` y `escaneo=limpio` permiten asociar o descargar el archivo.
Los infectados responden 423; los errores del antivirus dejan el adjunto bloqueado y responden
503. El tamaño real de un archivo rechazado sigue contando para futuras reservas.

La SAS de subida permite únicamente crear (`sp=c`), y la de descarga únicamente leer (`sp=r`).
Azurite verifica en las pruebas que no se puede sobrescribir un objeto existente con la SAS de
subida. La lectura de confirmación usa ETag para analizar una misma versión. El servidor
actualiza el MIME del blob y fuerza `Content-Disposition: attachment` tras el análisis.

Reservas y confirmaciones se serializan por tenant en PostgreSQL. Al alcanzar el 80 % se
registra un aviso y se crea una notificación de portal para cada owner/admin activo, como
máximo una por usuario y día. La respuesta también incluye `aviso`.

## Puesta en marcha

1. Aplicar las migraciones con el runner: `uv run python packages/core/migrate.py`.
   La migración 039 agrega `confirmado` y permite guardar el tamaño real de objetos rechazados
   mayores al límite anterior de la columna. Los adjuntos anteriores requieren confirmación.
2. Configurar `KC_BLOB_CONNECTION_STRING` con credenciales de Azure Blob. En Azurite puede
   utilizarse la constante pública `AZURITE_CONNECTION_STRING` de
   `packages/testing/src/kallicode_testing/storage_client.py`. Las firmas ficticias ya no se usan.
3. Iniciar `docker compose up -d clamav` y esperar su healthcheck. Configurar
   `KC_CLAMAV_HOST`, `KC_CLAMAV_PORT` y `KC_CLAMAV_TIMEOUT_SECONDS` si corresponde.
   Dentro de contenedores el host del servicio es `clamav`; en el host es `localhost`.
   ClamAV debe tener firmas actualizadas y `AlertExceedsMax yes` para bloquear los archivos que
   excedan sus límites internos de análisis, además de límites compatibles con 25 MiB de entrada.
4. Instalar `file` en entornos que ejecuten Python directamente. Las imágenes API/workers lo incluyen.

El worker acepta reintentos en `kc:adjuntos` con `{tenant_id, adjunto_id}` y aplica el mismo
servicio que la confirmación síncrona del portal. Los errores 5xx siguen la política de reintentos/DLQ.

## Verificación

```bash
uv run pytest packages/core/tests/test_adjuntos.py tests/integracion/test_cuota_storage.py -q
npm --prefix kallicode-portal test -- src/api/adjuntos.test.ts src/componentes/SubidaArchivos.test.tsx
```

Las pruebas de integración usan PostgreSQL y Azurite reales, con datos y migración de prueba
revertidos al finalizar. Verifican tamaño declarado falso, consumo real tras rechazo,
confirmación de contenido permitido, SAS sin sobrescritura y aviso persistente sin duplicados.
La prueba EICAR real verifica 423 en confirmación y descarga; se omite explícitamente si ClamAV
no está disponible. Las pruebas unitarias cubren además el protocolo INSTREAM, la respuesta de
firma EICAR, tipos ejecutables encubiertos y errores del antivirus.
