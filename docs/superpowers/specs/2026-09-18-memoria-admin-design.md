# Pantalla de Memoria — diseño

**Fecha:** 2026-09-18
**Autor:** Mr. Hyde, con Fernando Ruiz
**Origen:** Fernando, hoy: *"no tengo dónde hacer aprobar, no se construyó nada para
administrar la memoria"*. Y antes: *"la intención es que aprenda, que la memoria a largo plazo
le permita mejorar"*.

---

## 1. El problema, medido

La memoria de Axioma **extrae, guarda, busca y sintetiza**. No tiene forma de ser gobernada.
Medido contra `jax_memory` el 2026-09-18:

| Dato | Valor | Qué significa |
|---|---|---|
| `messages` | 1.638 filas, **todas** con vector | la memoria conversacional está completa |
| `facts` | **116** hechos activos | lo que el sistema cree saber |
| verificados | **1** de 116 | por su propia regla, casi nada cuenta como verdad |
| último mensaje aprendido | **2026-09-15** | tres días de trabajo real sin registrar |
| endpoints de memoria | **0** | no hay backend |
| pantallas de memoria | **0** de 10 pantallas de admin | no hay frontend |

Hay diez pantallas para administrar costos, facetas, modelos, motores, repositorio, ajustes,
SMTP y usuarios. **Ninguna para la memoria**, que es lo único que el sistema acumula sobre
nosotros.

### Los tres defectos que eso produce, con ejemplos reales

1. **Nadie puede aprobar.** La columna `is_verified` existe y no hay forma de ponerla en 1.
   115 hechos esperan una revisión que no tiene puerta.
2. **Hechos podridos que nadie puede corregir.** Los hechos 136, 138 y 139 dicen lo mismo —
   *"JAX no tiene capacidad nativa para SQL"*— con tres redacciones distintas, del 4 de
   septiembre. **Hoy son falsos.** Están ahí, listos para que alguien los cite.
3. **Nada caduca.** La columna `expires_at` existe en la tabla y **ningún código la lee**
   (verificado por búsqueda en `jax/memory/`). Una VERDAD OPERACIONAL de hace dos semanas pesa
   igual que la de hoy.

Y el deduplicador no alcanza: sus umbrales son `0,05` para duplicado y `0,25` para candidato a
corrección (`jax/memory/db.py:84-85`). Los tres hechos de arriba los esquivaron, porque están
parafraseados, no repetidos.

---

## 2. Qué se construye

Una pantalla **Memoria** en la Mesa, con su backend, que hace cuatro cosas y las hace fáciles.

### 2.1 · Ver, agrupado por tema

Fernando pidió explícitamente que *"junte las cosas del mismo tema"*. No es una lista de 116
filas ordenadas por fecha: es la memoria **agrupada por cercanía semántica**, usando los
embeddings que ya existen.

- Cada grupo muestra su tema, cuántos hechos tiene y cuántos están sin verificar.
- Dentro del grupo, los hechos ordenados por fecha, con el **más reciente arriba** — porque
  cuando dos se contradicen, el nuevo suele ser el que manda.
- **Los casi-duplicados se ven juntos y marcados.** Si el sistema ya calcula distancia coseno,
  la pantalla la usa: "estos tres dicen lo mismo" con un botón para fundirlos.

Cada hecho muestra **su procedencia**, siempre: de qué mensaje salió, qué faceta lo extrajo,
cuándo, y con qué confianza. *La memoria sin procedencia es otra forma de suposición.*

### 2.2 · Aprobar

Un hecho pasa a verificado con **quién** y **cuándo**. Es lo único que hoy es imposible.

Aprobar en lote dentro de un grupo, porque revisar 115 de a uno no lo hace nadie — y una
función que nadie usa es igual a no tenerla.

**No hay aprobación automática.** Si el sistema se aprueba a sí mismo, `is_verified` deja de
significar algo y volvemos a 116 hechos que nadie miró, con un sello encima. El que produce no
aprueba: es la misma regla del árbitro.

### 2.3 · Corregir

Escribir la versión nueva **marca la vieja como superada**, no la borra: queda `superseded_by`,
`superseded_at` y quién la corrigió. La maquinaria ya existe (`supersede_fact()` en `db.py`) y
nadie puede invocarla.

> *"Una memoria falsa no se borra en silencio: se marca como corregida, con versión nueva,
> quién y cuándo."*

### 2.4 · Caducar

Poner fecha de vencimiento a lo que deja de ser cierto, y **que el sistema la respete** — hoy
la columna existe y nadie la lee. Un hecho vencido no se borra: deja de pesar en la búsqueda y
se ve como vencido.

Por tipo, con los defaults del Protocolo de la Memoria Viva:

| Tipo de hecho | Caducidad por defecto |
|---|---|
| DECISIÓN, HISTORIA | nunca |
| HECHO del mundo | cuando la realidad cambia (sin fecha, revisable) |
| VERDAD OPERACIONAL | **corta** — el estado de un servidor de hace dos semanas no prueba nada hoy |
| PENDIENTE | al completarse |

La tabla ya tiene `fact_type` con seis valores (`user`, `technical`, `social`, `preference`,
`project`, `financial`). **No son los mismos tipos que el Protocolo**, y esa diferencia hay que
resolverla en la implementación: o se mapean, o se agrega la dimensión que falta. No se
inventa un tercer vocabulario.

---

## 3. La puerta para Hyde

La misma pantalla y **los mismos endpoints** los usa Claude Code para buscar en la memoria y
para proponer hechos nuevos. No un camino aparte.

- **Buscar:** lo que hoy no puedo hacer, y la razón por la que Fernando repite cosas que ya me
  dijo.
- **Proponer:** lo que aprendo en una sesión entra como hecho **sin verificar**, con su
  procedencia, a la misma cola que todo lo demás. Nunca verificado por mí.

Eso convierte el trabajo real en memoria. Hoy el trabajo pasa por Claude Code y la memoria solo
escucha el chat de la Mesa: por eso el último hecho es del 12 de septiembre, mientras que entre
el 16 y el 18 se hizo una ronda entera, un despliegue y seis misiones del Ejecutor.

---

## 4. Rendimiento, declarado antes de escribir código

- **Indexing:** la pantalla filtra por verificado, tipo y fecha, y ordena. Todas esas columnas
  tienen que estar indexadas, verificado con `EXPLAIN` sobre la consulta real. El agrupamiento
  por tema usa el índice vectorial — y el antecedente de esta casa es que **un índice que
  existe no es un índice que se usa**: un JOIN de más lo descartó una vez y costó 58,5 ms
  contra 0,4 ms.
- **Async:** agrupar 116 hechos por similitud no puede bloquear el request. Con 116 filas es
  trivial; con 10.000 no. Se diseña para el segundo caso.
- **Carga:** se mide con el peor caso — la memoria llena, el grupo más grande, el usuario con
  más hechos. Sin número medido, no hay GO.

---

## 5. Fuera de alcance

- **Aprobación automática.** Ver §2.2.
- **Borrar hechos.** Se corrigen o se caducan; borrar es perder la historia de lo que creímos.
  Si aparece un caso real (un dato sensible que nunca debió entrar), se diseña aparte con su
  propia auditoría.
- **Cambiar el extractor.** Esta ronda gobierna lo que ya se extrae; afinar *qué* se extrae es
  otra cosa.
- **El escritor del Ejecutor** (que cada misión deje su lección). Depende de esta pantalla —
  sin dónde aprobar, una lección más es un hecho más sin verificar. Va en la ronda siguiente.

---

## 6. Cómo se sabe que funcionó

1. Fernando abre la pantalla y **en un rato deja los 116 hechos revisados** — aprobados,
   corregidos o caducados. Si revisar 116 se siente imposible, la pantalla está mal hecha.
2. Los tres hechos falsos sobre SQL quedan **corregidos y superados**, no borrados.
3. Yo puedo buscar en la memoria desde una sesión nueva y encontrar algo que aprendimos hoy.
4. Hay un número de carga medido, con fecha, en la Biblioteca.

En memoria de Jairo Urbina.
