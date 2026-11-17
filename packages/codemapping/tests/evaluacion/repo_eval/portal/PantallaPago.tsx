// Pantalla de pago de una factura.

import { ClienteApi } from "./api";
import { formatearMoneda } from "./formato";
import { enviarPago } from "./formularios";

interface Props {
  api: ClienteApi;
  facturaId: number;
  pendiente: number;
}

export function PantallaPago(props: Props) {
  const pagar = (valor: string) => enviarPago(props.api, props.facturaId, valor);
  return (
    <form onSubmit={(e) => pagar((e.target as HTMLFormElement).importe.value)}>
      <p>Pendiente: {formatearMoneda(props.pendiente)}</p>
      <button type="submit">Pagar</button>
    </form>
  );
}
