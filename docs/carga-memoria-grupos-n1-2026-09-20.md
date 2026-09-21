# `/grupos` a 10.000 hechos: dónde se iba el tiempo de verdad (2026-09-20)

Segunda vuelta sobre la deuda registrada como **«N+1: la consulta de vecinos
corre ~9.000 veces por request»**. Medido en hall9000 contra la misma base
sembrada que `carga-memoria-2026-09-20.md` (9.000 hechos activos de 10.000).

> **La deuda apuntaba al lugar equivocado.** El N+1 existe, pero es el **27 %**
> del request. El **61 %** se iba en CPU, dentro de `_casi_duplicados_del_grupo`.
> Si se hubiera «arreglado el N+1» se habría ganado, como mucho, un cuarto.

## 1 · El desglose que faltaba

| Fase | Antes | % | Después | |
|---|---|---|---|---|
| `SQL_ACTIVOS_CON_VECTOR` (98,3 MB de vectores en texto) | 0,599 s | 9 % | 0,615 s | +3 % (ruido) |
| 9.000 × `SQL_VECINOS` (el N+1) | 1,817 s | 27 % | 1,981 s | +9 % (ruido) |
| union-find del grafo de temas | 0,012 s | 0,2 % | — | despreciable |
| **`_casi_duplicados_del_grupo`** | **4,291 s** | **64 %** | **0,878 s** | **4,9×** |
| **TOTAL** | **6,706 s** | | **3,474 s** | **1,93×** |

Dentro de esa fase, con el desglose fino:

```
grupos elegibles (2..90 miembros)   521
pares a comparar                    94.695
json.loads de los vectores          0,330 s
las 94.695 distancias coseno        4,122 s   (43,5 us por par)
```

## 2 · Las dos causas, las dos dentro de `_distancia_coseno`

1. **El producto punto en Python puro.** `sum(x*y for x,y in zip(a,b))` sobre
   1.024 dimensiones. `math.sumprod` (stdlib, en C) es mucho más rápido.
2. **La norma recomputada en cada par.** `_distancia_coseno` calculaba
   `norma_a` y `norma_b` adentro. En un grupo de 90 miembros, la norma de cada
   vector se recalculaba **89 veces**: 8.010 raíces en vez de 90.

Medido aparte, 4.005 pares de 1.024 dimensiones:

| | por par | |
|---|---|---|
| Como estaba (`sum`/`zip` + normas por par) | 47,4 µs | |
| `sumprod` + norma una vez por vector | 5,8 µs | **8,2×** |
| Normalizar una vez y usar `1 - dot` | 5,5 µs | 8,7× |

Se eligió la segunda, no la tercera: gana lo mismo y **no cambia el contrato**
de `_distancia_coseno` para quien la llame con dos vectores sueltos.

## 3 · Que ninguna decisión cambia — y por qué, con el número correcto

> **Corrección.** La primera versión de este documento decía que `math.sumprod`
> daba «el mismo resultado hasta el último dígito». **Es falso**, y lo refutó una
> auditoría adversarial: `sumprod` acumula compensado. Medido sobre los **94.435
> pares reales** de la base de carga, el **2,87 %** difiere en los últimos bits.

Lo que hace seguro el cambio no es una identidad que no existe, es el **margen**:

| | |
|---|---|
| Error máximo entre la vieja y la nueva | **3,3 × 10⁻¹⁶** |
| Distancia real más cercana al umbral 0,25 | **2,94 × 10⁻²** |
| El margen es el error multiplicado por | **8,8 × 10¹³** |
| Pares reales cuya decisión cambia | **0** |

Catorce órdenes de magnitud. Tiene sentido: el hueco entre «mismo tema»
(0,097-0,145, medido sobre redacciones reales) y «tema distinto» (~0,77) es
enorme comparado con el último bit de un `float`.

**La red que lo defiende, en el repo y no en un scratchpad:**

- `_casi_duplicados_ingenuo()` — la implementación anterior al arreglo, versionada
  como referencia — y siete casos parametrizados que comparan contra ella el
  **camino de producción** (el de cuatro argumentos, el único que corre), con
  grupos de 2 a 90 miembros y dos casos con normas muy dispares entre sí.
- Un centinela del cableado: cruzar la norma de `a` con la de `b` tiene que
  cambiar el resultado.
- La norma se calcula una vez por vector, **contando llamadas** y no midiendo
  tiempo: un test de reloj sería un flake y además no diría por qué.

Esa red nació de un agujero real. La primera versión sólo probaba la firma de
**dos** argumentos y el conteo de normas; la auditoría mutó el cableado a
`normas[a], normas[a]` y **los 28 tests siguieron en verde con los grupos
saliendo mal**. Con los tests de ahora, esa misma mutación cae en tres.

## 4 · Bajo carga

`/grupos`, contra los números de la primera vuelta:

| c | antes (p50) | después (p50) | |
|---|---|---|---|
| 1 | 7,2 s | **3,37 s** | 2,14× |
| 3 | 17,2 s | **6,88 s** | 2,50× |
| 5 | 32,7 s | **13,71 s** | 2,38× |

Los otros dos endpoints no se tocaron y siguen igual: `/hechos?verificado=false`
p50 10,0 ms a c=1 y 165 ms a c=25, sin errores; `/hechos` sin filtro 15,0 ms y
177,8 ms. Verificación previa: las tres respuestas son reales (200, 159-273 KB,
1.598 grupos, el mayor con 541 hechos, 521 con casi-duplicados), no un 4xx rápido.

## 5 · Lo que NO se hizo, con el número al lado

- **El N+1 se queda.** Se midió la hipótesis obvia —que el costo fuera pedir una
  conexión del pool 9.000 veces— y **es falsa**: reusar una conexión por
  trabajador sale **0,9×**, o sea peor. *(Medición de una corrida, con un guion
  de scratchpad que no quedó versionado: tómese como HECHO de una sola
  observación, no como resultado reproducible desde el repo.)* Las 9.000 consultas son genuinamente de
  0,22 ms cada una; con concurrencia 6 eso son ~2 s y es su piso con este diseño.
- **No se subió la concurrencia.** Medido **sobre la fase de vecinos sola**, no
  sobre el endpoint entero: 4 → 2,63 s · 6 → 2,17 s · 8 → 1,76 s · 10 → 1,49 s.
  (El valor de hoy es 6; la fase mide 1,98 s en la tabla §1 y 2,17 s acá porque
  son corridas distintas.) Pero el pool tiene `maxsize=10`: ir a 10 es quedarse con el pool
  entero y hambrear a las demás peticiones. Ganaría esta pantalla a costa del
  resto, y bajo concurrencia real sería peor, no mejor. La decisión de dejar 4
  conexiones libres sigue siendo la correcta.
- **`SQL_ACTIVOS_CON_VECTOR` sigue siendo full scan, y está bien.** Devuelve
  9.000 de 10.000 filas: leer el 90 % de la tabla por un índice sería más lento,
  no más rápido. Un `type=ALL` no es un defecto cuando se lee casi todo.
- **Para bajar de ~3 s hay que dejar de recalcular en cada request.** Lo que
  queda (0,6 s de traer 98 MB de texto + 2 s de vecinos) no se optimiza: se
  evita. Eso es una caché con invalidación explícita, y cruza procesos (`jax`
  escribe los hechos, `jax-platform` sirve la pantalla), así que necesita un
  sello en la base como el de `facet_resolver`. Es una decisión de Fernando, no
  un ajuste: va con su propia ronda y su propia auditoría.

## 6 · Hallazgo aparte: `/grupos` NO es determinista

Dos llamadas seguidas **con el mismo código y los mismos datos** devuelven
distinta cantidad de grupos: 1.603 y 1.599; a lo largo del día se vieron 1.596,
1.598, 1.602, 1.603 y 1.606. La primera vuelta ya lo insinuaba sin nombrarlo
(«1.419-1.429 grupos»).

Es el HNSW: la búsqueda de vecinos es **aproximada**, así que el conjunto de
aristas cambia entre corridas y el union-find agrupa distinto en los bordes. No
lo introduce este cambio —se reprodujo con el código viejo— y no se corrige acá.
Queda anotado porque una pantalla de administración que muestra grupos distintos
al recargar, sin que nadie haya tocado nada, **parece un defecto de datos** y no
lo es.

## 7 · De paso: el medidor no podía autenticarse

`loadtest/memoria_medir.py` fabricaba el JWT con `tv: 0` fijo y sin `role`.
Cualquier usuario que hubiera iniciado sesión alguna vez tiene `token_version`
mayor, así que el token salía **401** — y el medidor habría medido un 401 rápido
si no fuera porque su propia verificación previa se niega a medir sobre un
endpoint roto (ese freno funcionó). Ahora los dos valores se **leen de la base**.
