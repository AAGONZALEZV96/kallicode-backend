# Guía de curación del mapa de su sistema

**Para:** las personas de su organización que revisan lo que Kallicode aprende sobre su sistema (responsables de
producto, arquitectos, jefes de equipo). No hace falta saber programar.

> **Estado:** borrador pendiente de la revisión del Product Owner (ver el final).

## 1. Qué es la curación y por qué se la pedimos

Cuando conectan su repositorio, Kallicode lee el código y construye un **mapa de su sistema**: qué piezas hay, cuáles
dependen de cuáles y qué tablas de datos usa cada una. Esa parte es exacta, porque sale directamente del código.

Además, Kallicode intenta entender **el negocio**: qué hace cada módulo en términos de su empresa («cálculo de la
factura», «alta de cliente»), qué conceptos maneja y qué reglas deben respetarse. Esto lo propone un modelo de
inteligencia artificial y **puede equivocarse**.

Por eso aplicamos una regla sencilla:

- Lo que la inteligencia artificial propone con **mucha seguridad** entra en el mapa directamente.
- Lo que propone con **dudas** no entra: queda en una lista de revisión hasta que una persona de su organización
  decide. Eso es la curación.

Ninguna regla de negocio que Kallicode vaya a aplicar al construir software para ustedes entra sin que una persona la
haya aprobado.

## 2. Por qué importa su decisión

El mapa es lo que consultan los agentes de Kallicode antes de diseñar o cambiar algo. Una propuesta mal aprobada hace
que trabajen con una idea equivocada de su negocio; una propuesta correcta rechazada hace que la ignoren. En los dos
casos el resultado se nota en los tickets que reciben.

No hace falta revisar todo el mismo día: lo pendiente no bloquea el resto del servicio. Pero cuanto antes se revise,
antes trabaja Kallicode con su conocimiento.

## 3. Qué le vamos a pedir que revise

| Tipo de propuesta | Qué significa | Ejemplo |
|---|---|---|
| **Enlace** | «Esta parte del sistema tiene que ver con este concepto de negocio» | «El módulo de facturación *expresa* el concepto *Factura rectificativa*» |
| **Concepto** | Un término de su negocio que Kallicode cree haber descubierto, con su definición | «*Cliente moroso*: cliente con facturas vencidas hace más de 60 días» |
| **Regla aprendida** | Una regla o restricción sacada de su documentación o de incidentes pasados, para no repetir errores | «No se puede emitir una factura sin número de identificación fiscal» |

Cada propuesta muestra **cuánta seguridad** tenía la inteligencia artificial (por ejemplo, 62 %) y, cuando la hay,
**la evidencia**: el fragmento de documento o de código del que sale. Se muestran primero las más dudosas.

## 4. Cómo decidir

Para cada propuesta hay dos respuestas: **aprobar** o **rechazar**. Puede añadir un comentario.

**Apruebe** cuando:

- la frase es cierta en su empresa tal como está escrita;
- usted se la explicaría así a una persona nueva del equipo.

**Rechace** cuando:

- es falsa, o solo es cierta en un caso particular que la frase no dice;
- mezcla dos cosas distintas o usa un término que en su empresa significa otra cosa;
- era cierta antes y ya no lo es.

**En caso de duda, rechace y explíquelo en el comentario.** Rechazar no cuesta nada: si más adelante aparece
información nueva que lleva a una propuesta distinta, se le volverá a preguntar. Una propuesta aprobada por error, en
cambio, se usa hasta que alguien la detecte.

Si la propuesta es *casi* correcta, rechácela y diga en el comentario cómo sería la versión correcta.

## 5. Quién puede decidir

Pueden aprobar o rechazar las personas de su organización con rol de **propietario, administrador, arquitecto o
aprobador**. Cualquier otra persona puede ver la lista, pero no decidir. Cada decisión queda registrada con quién la
tomó y cuándo, en el registro de auditoría de su organización.

Una vez decidida, una propuesta no se puede volver a decidir. Si se equivocó, avise a su contacto en Kallicode.

## 6. Dónde se hace

En el portal, el paso de **activación** del onboarding indica cuántos elementos esperan revisión. La revisión se hace
desde la sección de curación del portal; mientras esa sección no esté disponible para su organización, su contacto en
Kallicode le enviará la lista y registrará sus decisiones.

## 7. Qué pasa después

- **Aprobado:** entra en el mapa y los agentes de Kallicode lo tienen en cuenta desde la siguiente tarea.
- **Rechazado:** no entra y no se vuelve a proponer igual.
- **Sin decidir:** sigue en la lista. No se usa, pero tampoco se pierde.

Cuando su código cambia, Kallicode actualiza el mapa cada día y puede proponer cosas nuevas. Cuando se entrega un
ticket, también puede proponer reglas aprendidas de ese trabajo.

## 8. Preguntas frecuentes

**¿La inteligencia artificial ve nuestro código?** Kallicode lee su código para construir el mapa. El contenido de su
repositorio se trata como información, nunca como instrucciones, y las respuestas del modelo se validan antes de
guardarse.

**¿Cuánto tiempo me llevará?** Depende del tamaño del sistema. La primera revisión es la más larga; después suelen ser
pocas propuestas por semana.

**¿Puedo delegar?** Sí, en cualquier persona de su organización con uno de los roles del apartado 5.

**¿Qué pasa si nadie revisa?** El servicio funciona igual con la parte exacta del mapa, pero sin el conocimiento de
negocio pendiente. Los resultados serán más genéricos.

---

## Revisión

| Revisión | Nombre | Fecha | Observaciones |
|---|---|---|---|
| Product Owner (lenguaje de negocio y exactitud) | | | |
