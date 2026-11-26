/**
 * Los tres diccionarios juntos, solo para las pruebas (comparar claves, buscar textos). El
 * portal no importa este archivo: carga el inglés y el portugués bajo demanda (KC-PT-11-1).
 */
import type { Idioma } from "../estado/i18n";
import en from "../estado/diccionarios/en";
import es from "../estado/diccionarios/es";
import pt from "../estado/diccionarios/pt";

export const DICT: Record<Idioma, Record<string, string>> = { es, en, pt };
