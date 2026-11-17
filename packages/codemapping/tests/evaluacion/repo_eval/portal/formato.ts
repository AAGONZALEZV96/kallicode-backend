// Formato de importes en el portal.

export function redondear(valor: number): number {
  return Math.round(valor * 100) / 100;
}

export function formatearMoneda(valor: number): string {
  return `${redondear(valor).toFixed(2)} EUR`;
}

export function formatearLista(valores: number[]): string[] {
  return valores.map((v) => formatearMoneda(v));
}
