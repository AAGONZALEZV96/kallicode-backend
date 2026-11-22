/**
 * Formateadores centralizados con Intl (KC-PT-09-3, cierra PT-F17).
 *
 * - Idioma: el elegido en el portal (es, en, pt), no el del navegador. Antes `fechaCorta` y
 *   `toLocaleString()` usaban el del navegador: un usuario con el portal en español y el
 *   navegador en inglés veía «1,520» y «11/21».
 * - Zona horaria: la de la organización (GET /organization → timezone). El backend entrega las
 *   fechas en ISO 8601 UTC y el frontend las localiza con esa zona (Diseño API v1.1). Mientras
 *   no se conoce, o si no es una zona IANA válida, se usa la del navegador.
 * - Los Intl.*Format se crean una vez por idioma y zona (useFormato los memoriza).
 */
import { useMemo } from "react";

import { useOrganizacion } from "../api/consultas";
import { useI18n, type Idioma } from "./i18n";

/** Idioma del portal → locale de Intl. «es» y «pt» van sin región: 24 h y separadores propios. */
export const LOCALES: Record<Idioma, string> = { es: "es", en: "en", pt: "pt" };

const VACIO = "—";

export interface Formato {
  locale: string;
  /** Zona IANA con la que se localizan las fechas (undefined = la del navegador). */
  zona: string | undefined;
  /** Día, mes, hora y minuto: «21/11, 14:30». Null o inválida → «—». */
  fecha(iso: string | null | undefined): string;
  /** Fecha completa con año: «21 nov 2026, 14:30». Para títulos y detalles. */
  fechaLarga(iso: string | null | undefined): string;
  /**
   * Fecha de calendario (YYYY-MM-DD, p. ej. plan.renueva_el): «1 dic 2026». Sin hora ni zona:
   * leída como medianoche UTC, en una zona al oeste de Greenwich mostraría el día anterior.
   */
  dia(fecha: string | null | undefined): string;
  /** Hora y minuto: «14:30». */
  hora(valor: string | Date | null | undefined): string;
  /** Hora con segundos: «14:30:05». */
  horaConSegundos(valor: string | Date | null | undefined): string;
  /** Hace cuánto (o en cuánto): «hace 5 minutos», «ayer», «en 2 horas». */
  relativo(iso: string | null | undefined, ahora?: number): string;
  /** Número con separadores del idioma: «1.520» en es, «1,520» en en. */
  numero(n: number | null | undefined): string;
  /** Número abreviado: «1,8 mil» en es, «1.8K» en en. */
  compacto(n: number | null | undefined): string;
  /** Porcentaje a partir de 0–100: «45 %» en es, «45%» en en. */
  porcentaje(valor: number | null | undefined, decimales?: number): string;
  /** Monto en una moneda ISO 4217: «US$ 49,00» en es, «$49.00» en en. */
  moneda(monto: number | null | undefined, codigo: string): string;
  /** Tamaño de archivo: «820 kB», «1,5 MB». */
  bytes(n: number | null | undefined): string;
  /**
   * Duración en segundos, con las dos unidades mayores: «2h 41min» (corta) o «2 horas y
   * 41 minutos» (larga, para un title o un lector de pantalla).
   */
  duracion(segundos: number | null | undefined, estilo?: "corta" | "larga"): string;
}

type Unidad = "day" | "hour" | "minute" | "second";

/** Las dos unidades mayores con valor (3 días y 4 h; 2 h y 41 min; 45 s). */
function partesDuracion(segundos: number): [Unidad, number][] {
  const s = Math.floor(segundos);
  const valores: [Unidad, number][] = [
    ["day", Math.floor(s / 86_400)],
    ["hour", Math.floor((s % 86_400) / 3600)],
    ["minute", Math.floor((s % 3600) / 60)],
    ["second", s % 60],
  ];
  const primera = valores.findIndex(([, v]) => v > 0);
  if (primera < 0) return [["second", 0]];
  return valores.slice(primera, primera + 2).filter(([, v]) => v > 0);
}

/** La zona si Intl la reconoce; si no, undefined (la del navegador) en vez de lanzar. */
export function zonaValida(zona: string | null | undefined): string | undefined {
  if (!zona) return undefined;
  try {
    new Intl.DateTimeFormat("en", { timeZone: zona });
    return zona;
  } catch {
    return undefined;
  }
}

const aFecha = (valor: string | Date | null | undefined): Date | null => {
  if (valor == null || valor === "") return null;
  const d = valor instanceof Date ? valor : new Date(valor);
  return Number.isNaN(d.getTime()) ? null : d;
};
const esNumero = (n: number | null | undefined): n is number =>
  typeof n === "number" && Number.isFinite(n);

/** Unidades de RelativeTimeFormat, de la más chica a la más grande, con su tamaño en segundos. */
const UNIDADES: [Intl.RelativeTimeFormatUnit, number][] = [
  ["second", 60],
  ["minute", 60],
  ["hour", 24],
  ["day", 30],
  ["month", 12],
  ["year", Infinity],
];

export function crearFormato(idioma: Idioma, zonaOrganizacion?: string | null): Formato {
  const locale = LOCALES[idioma] ?? LOCALES.es;
  const zona = zonaValida(zonaOrganizacion);
  const conZona = (o: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat(locale, { ...o, timeZone: zona });

  const corta = conZona({ day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  const larga = conZona({ dateStyle: "medium", timeStyle: "short" });
  const calendario = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: "UTC" });
  const diaEnZona = conZona({ dateStyle: "medium" });
  const hm = conZona({ hour: "2-digit", minute: "2-digit" });
  const hms = conZona({ hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const numero = new Intl.NumberFormat(locale);
  const compacto = new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 });
  const relativo = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const lista = new Intl.ListFormat(locale, { type: "conjunction" });
  const unidad = (u: Unidad, display: "narrow" | "long") =>
    new Intl.NumberFormat(locale, { style: "unit", unit: u, unitDisplay: display });
  const fechaCon = (f: Intl.DateTimeFormat) => (valor: string | Date | null | undefined) => {
    const d = aFecha(valor);
    return d ? f.format(d) : VACIO;
  };

  return {
    locale,
    zona,
    fecha: fechaCon(corta),
    fechaLarga: fechaCon(larga),
    dia: (fecha) => {
      const ymd = /^(\d{4})-(\d{2})-(\d{2})$/.exec(fecha ?? "");
      // Con hora (ISO completo): el día en la zona de la organización.
      if (!ymd) return fechaCon(diaEnZona)(fecha);
      const [anio, mes, dia] = [Number(ymd[1]), Number(ymd[2]), Number(ymd[3])];
      const d = new Date(Date.UTC(anio, mes - 1, dia));
      // Date.UTC corre los desbordes (mes 13 → enero siguiente): eso no es una fecha válida.
      const existe = d.getUTCMonth() === mes - 1 && d.getUTCDate() === dia;
      return existe ? calendario.format(d) : VACIO;
    },
    hora: fechaCon(hm),
    horaConSegundos: fechaCon(hms),
    relativo(iso, ahora = Date.now()) {
      const d = aFecha(iso);
      if (!d) return VACIO;
      let valor = (d.getTime() - ahora) / 1000;
      for (const [unidad, tamano] of UNIDADES) {
        if (Math.abs(valor) < tamano) return relativo.format(Math.round(valor), unidad);
        valor /= tamano;
      }
      return relativo.format(Math.round(valor), "year");
    },
    numero: (n) => (esNumero(n) ? numero.format(n) : VACIO),
    compacto: (n) => (esNumero(n) ? compacto.format(n) : VACIO),
    porcentaje: (valor, decimales = 0) =>
      esNumero(valor)
        ? new Intl.NumberFormat(locale, {
            style: "percent",
            maximumFractionDigits: decimales,
          }).format(valor / 100)
        : VACIO,
    moneda: (monto, codigo) => {
      if (!esNumero(monto)) return VACIO;
      try {
        return new Intl.NumberFormat(locale, { style: "currency", currency: codigo }).format(monto);
      } catch {
        // Código de moneda desconocido: el monto con su código, sin inventar el símbolo.
        return `${numero.format(monto)} ${codigo}`;
      }
    },
    duracion: (segundos, estilo = "corta") => {
      if (!esNumero(segundos) || segundos < 0) return VACIO;
      const partes = partesDuracion(segundos);
      if (estilo === "larga")
        return lista.format(partes.map(([u, v]) => unidad(u, "long").format(v)));
      return partes.map(([u, v]) => unidad(u, "narrow").format(v)).join(" ");
    },
    // Base 1024, como los límites de subida (25 MB = 25 × 1024² bytes se muestra «25 MB»).
    bytes: (n) => {
      if (!esNumero(n)) return VACIO;
      const [unidad, valor] =
        n < 1024
          ? (["byte", n] as const)
          : n < 1024 ** 2
            ? (["kilobyte", n / 1024] as const)
            : n < 1024 ** 3
              ? (["megabyte", n / 1024 ** 2] as const)
              : (["gigabyte", n / 1024 ** 3] as const);
      return new Intl.NumberFormat(locale, {
        style: "unit",
        unit: unidad,
        maximumFractionDigits: unidad === "byte" || unidad === "kilobyte" ? 0 : 1,
      }).format(valor);
    },
  };
}

/** Formateadores con el idioma del portal y la zona horaria de la organización. */
export function useFormato(): Formato {
  const { idioma } = useI18n();
  const { data: organizacion } = useOrganizacion();
  const zona = organizacion?.timezone;
  return useMemo(() => crearFormato(idioma, zona), [idioma, zona]);
}
