# Manual de facturación

Guía de uso del módulo de facturación para el equipo de operaciones.

## Emisión de facturas

Cada `Invoice` se crea con su monto neto. El total con impuestos lo calcula
`BillingService.calculate_total`, que aplica la tasa de IVA configurada (19 % por defecto).

```bash
# Esto es un comentario dentro de un bloque de código, no un encabezado
make facturar
```

### Formato de montos

Los montos se muestran con `format_currency`, siempre con dos decimales.

## Notas de crédito

Una nota de crédito anula total o parcialmente una factura emitida. Nunca se edita una
factura pagada: se emite una nota de crédito y una factura nueva. Las notas de crédito
quedan asociadas a la factura original para la conciliación contable.

## Pagos

Al confirmar el pago, operaciones ejecuta `mark_as_paid`. Una factura pagada no puede
volver a estado pendiente.
