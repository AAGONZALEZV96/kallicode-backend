// Cliente HTTP del portal de clientes.

export interface Http {
  get(url: string): Promise<unknown>;
  post(url: string, cuerpo: unknown): Promise<unknown>;
}

export class ClienteApi {
  constructor(private readonly http: Http) {}

  obtenerFacturas(clienteId: number): Promise<unknown> {
    return this.http.get(`/clientes/${clienteId}/facturas`);
  }

  pagarFactura(facturaId: number, importe: number): Promise<unknown> {
    return this.http.post(`/facturas/${facturaId}/pagos`, { importe });
  }
}
