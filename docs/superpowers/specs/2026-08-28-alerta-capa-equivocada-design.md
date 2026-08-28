# La alerta afirma la capa equivocada — diseño

**Fecha:** 2026-08-28
**Ítem:** primer "Bloquea trabajo" de `DEUDA.md` — `probe_error` tapa a `config_error`.
**Estado:** diseño aprobado en diagnóstico, sin implementar.

## 1. El problema, reencuadrado

La entrada de `DEUDA.md` lo describía como `config_error` tapado por
`probe_error`. **El diagnóstico del 2026-08-28 mostró que el encuadre era
más chico que el problema.**

Evidencia (contra `jax_memory_test`, con `_invoke_facet_dispatch`
sustituido por una excepción controlada — sin llamar a ningún proveedor):

```
config_error   : 2 fila(s) -> ['config_error', 'probe_error']
provider_error : 2 fila(s) -> ['provider_error', 'probe_error']
```

`provider_error` tiene el mismo patrón y es **mucho más frecuente**:
cualquier caída de un proveedor externo, el caso ordinario que el detector
existe para ver. `config_error` apareció primero sólo porque fue el que se
rompió a propósito en el deploy de la Task 8.

**La regla real:** *toda* excepción que `_invoke_facet` clasifica y
re-lanza produce una segunda fila `probe_error` cuando el llamador es la
sonda. Hoy son dos outcomes; mañana son los que se agreguen. El fix tiene
que ser sobre esa regla, no sobre la pareja.

**El costo, con precisión:** el lector toma `MAX(ts)` por facet y
`probe_error` gana por ~800 µs. El mensaje que llega a Fernando dice
**"la sonda falló"** cuando la causa accionable era otra — por ejemplo
"la fila de `model` no declara `max_tokens_param`", que trae hasta el
`UPDATE` a ejecutar. Es una alerta que afirma la capa equivocada **en el
punto exacto donde alguien la lee para decidir qué hacer**, y manda a
investigar la sonda en vez de la fila del catálogo.

## 2. Por qué se escriben dos filas: dos capas, no una duplicando

```
_invoke_facet (api/chat.py:893-925)          ← envoltorio TOTAL
  ├─ except ModelDispatchConfigError → record(config_error)   + raise
  ├─ except Exception               → record(provider_error)  + raise
  └─ éxito                          → record(ok | gate_denied | ...)

probe_facet (jax_engine/facet_canary.py:96-102)   ← el llamador, si es la sonda
  └─ except Exception → record(probe_error)       ← la SEGUNDA fila
```

La segunda fila la escribe **`probe_facet:100`**, no el `except` de
`probe_after_rebind:172` — ése nunca se alcanza en este camino, porque
`probe_facet` captura y **devuelve** en vez de re-lanzar.

**El alcance es sólo el camino de la sonda.** El chat real
(`api/chat.py:988-999`) captura la excepción y la convierte en
`HTTPException` sin registrar otra fila. La duplicación afecta exactamente
a `canary_periodic` y `canary_rebind`: los eventos de la detección
proactiva, que son los que alimentan las alertas.

## 3. `MAX(ts)` es una decisión, no un default — y sigue siendo correcta

Spec §2.5 de la ronda anterior, tabla explícita: *"`ok` — el evento más
reciente del facet en la ventana es `ok`"*. El caso que resolvía es la
**sucesión temporal**: un facet que falló hace 40 min y ahora responde debe
estar `ok`, no `down`.

Lo que no contempló: dos eventos **simultáneos** describiendo el mismo
fallo desde capas distintas. Ahí la recencia no significa "más actual" —
significa "más arriba en la pila de llamadas". 800 µs no es información
temporal, es orden de desenrollado del stack.

**Este diseño no toca `MAX(ts)`.** La decisión es correcta para el caso que
tenía en mente; el fix elimina el caso que no tenía en mente.

## 4. La pregunta de diseño, y su respuesta

> ¿Qué significa el ledger cuando la capa de abajo YA clasificó el fallo y
> la de arriba sólo puede decir que no pudo completar?

Sub-pregunta que la resuelve: **¿el `probe_error` aporta algo cuando ya hay
un evento clasificado?**

`_invoke_facet` es un **envoltorio total**: todo su cuerpo está dentro del
`try`, y los dos `except` registran *antes* del `raise`. No hay camino por
el que lance sin haber escrito. Verificado corriendo:

```
A) fallo DENTRO de _invoke_facet   : ['provider_error', 'probe_error']   ← redundante
B) fallo ANTES de _invoke_facet    : ['probe_error']                     ← único evento
C) fallo en invalidate_facet_cache : ['probe_error']                     ← único evento
```

**La regla estructural, que no depende de cuántos outcomes existan:**

> El `probe_error` de `probe_facet` es **siempre** redundante.
> El de `probe_after_rebind` es **siempre** el único evento.

Son dos `except` con valor distinto que hoy escriben el mismo outcome. La
respuesta al ledger: **la capa de arriba no tiene nada que decir cuando la
de abajo ya clasificó**; su valor está exclusivamente en el caso en que la
de abajo no llegó a escribir.

## 5. Decisión: opción B

**`probe_facet` deja de escribir `probe_error`. `probe_after_rebind` sigue
escribiéndolo.**

No se arbitra el conflicto: se elimina la escritura que nunca aporta. No
hace falta definir "mismo instante" (un umbral temporal sería un número
arbitrario que falla bajo carga), ni mantener una tabla de precedencia
entre outcomes.

Qué dice la alerta en los tres escenarios con evidencia real:

| Escenario | Antes | Después |
|---|---|---|
| Proveedor caído | "la sonda falló" | `provider_error` |
| Fila de `model` mal sembrada | "la sonda falló" | `config_error` + el `UPDATE` en `detail` |
| La sonda no pudo completar | `probe_error` | `probe_error` (sin cambio) |

**Alternativas rechazadas:**
- **A (el lector prefiere el clasificado dentro del mismo instante):** no
  borra nada y falla hacia "información de más". Rechazada porque exige
  definir "mismo instante" y mantener una clasificación de outcomes que hay
  que actualizar cada vez que se agrega uno — vuelve el problema que la
  restricción 3 prohíbe. **Es el plan B si el test de §7 no se sostiene.**
- **C (`probe_error` como dimensión separada, salud del detector):** la más
  limpia conceptualmente, pero toca ENUM, migración, escritor, lector y
  tests, y abre una pregunta que hoy no tiene respuesta: si la salud del
  detector es su propia dimensión, quién la alerta. Se anota como dirección
  futura, no se hace ahora.

## 6. Qué se pierde — declarado, no minimizado

1. **El rastro de "la sonda no pudo completar" para el caso A desaparece de
   la tabla.** Cuando `_invoke_facet` lanza, sólo queda el evento
   clasificado. Es información real que se pierde, y se acepta porque el
   clasificado es estrictamente más informativo: dice *qué* falló, no sólo
   *que* la sonda no terminó. Quien audite la tabla en seis meses no va a
   poder distinguir "la sonda intentó y el proveedor falló" de "el chat
   intentó y el proveedor falló" mirando sólo el outcome — pero **sí** por
   la columna `source` (`canary_periodic`/`canary_rebind` vs `chat`), que
   no cambia.

2. **El `ValueError` de `record_facet_health` con `outcome`/`source`
   inválido (`facet_health.py:79,81`) es un camino teórico por el que
   `_invoke_facet` lanzaría sin registrar.** Se consideró y **no sostiene
   la decisión**: los outcomes son constantes literales del propio módulo,
   así que sólo se dispararía por un bug de programación, no por un fallo
   operativo. Queda nombrado acá para que quien lea este diseño en seis
   meses sepa que se miró y por qué se descartó, en vez de creer que se
   pasó por alto.

## 7. El test de política — parte del fix, no una mitigación

Sin él, B cambia una duplicación **visible** por una garantía **tácita**, y
una garantía tácita es peor: cuando se rompe, no avisa.

**Qué protege:** que `_invoke_facet` siga siendo un envoltorio total — todo
lo que pueda lanzar vive dentro del `try`, cada handler registra antes de
re-lanzar, y existe un handler genérico al final.

**No verifica que hoy esté bien: detecta la mutación.** Atacado con nueve
evasiones; las nueve dan rojo y el código real pasa sin falso positivo:

| Ataque | Detectado por |
|---|---|
| 1. sentencia antes del `try` | "hay N sentencia(s) ANTES del try" |
| 2. decorador que puede lanzar | "tiene N decorador(es)" |
| 3. `with` envolviendo el `try` | "el cuerpo tiene 0 bloques try" |
| 4. `return` temprano | "hay N sentencia(s) ANTES del try" |
| 5. handler que no registra | "NO registra antes de propagar" |
| 6. handler que no re-lanza | "NO re-lanza: seria fail-open" |
| 7. delegar todo a otra función | "el cuerpo tiene 0 bloques try" |
| 8. quitar el `except Exception` | "no hay `except Exception` como ULTIMO handler" |
| 9. archivo que no parsea | "el archivo no parsea" |

### Cómo se validó: atacando el TEST, no el código

Esto importa para entender el diseño, no es una nota de proceso. La
validación no fue "corro el test sobre el código actual y pasa" — eso sólo
prueba que hoy está bien. Fue **mutar el código a propósito de nueve formas
distintas y exigir que el test se pusiera rojo en las nueve**.

**Los ataques 8 y 9 salieron de ese ejercicio, no del código.** La primera
versión del test los dejaba pasar en verde:

- **Ataque 8 — quitar el `except Exception` genérico.** Sin él, una
  excepción no prevista escapa de `_invoke_facet` **sin registrar**, que es
  exactamente la propiedad de la que depende que `probe_facet` ya no
  escriba. El test verificaba que cada handler existente registrara y
  re-lanzara, pero no que **existiera** un handler que atrapara todo. Un
  test que no atrapa eso protege la mitad de lo que dice proteger.
- **Ataque 9 — archivo que no parsea.** Daba traceback crudo. Rojo, sí,
  pero con un mensaje que no le dice nada a quien lo lee.

**Por eso el handler genérico como ÚLTIMO handler es un requisito del
diseño, no una preferencia estilística.** Si alguien en el futuro lo quita
porque "los except específicos ya cubren los casos conocidos", rompe la
garantía completa: los casos conocidos no son el problema, los no previstos
sí. El test falla y dice por qué.

**Qué NO cubre el test** (límites reales, no formalidad):
- **Es estático sobre `_invoke_facet` y sólo sobre ella.** No verifica que
  `_invoke_facet_dispatch` —la función que hace el trabajo real— no tenga
  su propio fail-open adentro. **Qué sí lo cubre:** el scanner P10
  (`test_no_fail_open_except.py`, corriendo en CI) sobre todo el árbol, y
  los tests de comportamiento de outcomes (`test_facet_health_outcomes.py`),
  que ejercitan los puntos de retorno reales del dispatch.
  **Y no se amplía a propósito:** un guard que intenta cubrir dos funciones
  distintas termina cubriendo mal las dos. Este protege una propiedad
  estructural de una función; ésa es toda su promesa.
- Verifica **estructura**, no que cada handler registre el outcome
  *correcto* (que `ModelDispatchConfigError` escriba `config_error` y no
  `ok`). Eso lo cubren los tests de comportamiento existentes.
- No depende de cuántos llamadores tenga `_invoke_facet` — la propiedad es
  de la función, no de sus llamadores. Si mañana aparece un tercer
  llamador, el diseño no cambia.
- **No cubre la mutación del namespace en runtime.**
  `globals()["_invoke_facet"] = otra`, `setattr(sys.modules[__name__], ...)`,
  `exec(...)`, o —el caso realista— `api.chat._invoke_facet = otra` desde
  otro módulo, dejan el guard en verde con la propiedad rota. Verificado en
  runtime, no supuesto. **Es un límite estructural declarado, no un
  pendiente**, y la razón NO es que no se pueda detectar: buscar esos
  deletreos dentro de `chat.py` es trivial. Son dos razones distintas:
  **(1) sería enumeración sin oráculo.** El scoping estático se chequea sin
  enumerar constructos porque `symtable` ES el compilador respondiendo. Para
  la mutación dinámica no hay a quién preguntarle, y la lista (`globals`,
  `vars`, `setattr`, `sys.modules`, `__dict__`, `exec`, `importlib`) es
  abierta: repetiría el error que la Ronda de corrección 3 ya juzgó, esta
  vez sin la salida que aquella tuvo.
  **(2) el vector principal vive en otro archivo.**
  `api.chat._invoke_facet = otra` es literalmente lo que hace
  `monkeypatch.setattr` en los tests. Un guard que lee un solo archivo no lo
  puede ver nunca, y cerrar el deletreo raro dejando abierto el común daría
  la impresión contraria a la verdadera.
  **Qué sí lo cubre, para el sujeto:** `test_facet_health_outcomes.py`
  resuelve `chat_mod._invoke_facet` como atributo del módulo en cada
  llamada, así que una sustitución en runtime pone rojos esos tests.
  **Para el registro no lo cubre nada** — `_capture` monkeypatchea
  `chat_mod.record_facet_health` y pisa la re-ligadura — y por eso la
  re-ligadura del registro se cierra estáticamente (Ronda de corrección 6).

**Criterio de cierre (octava lección de método):** el test corre en un job
de CI, y eso se verifica **rompiéndolo con el job real**, no localmente.
Que aparezca en `policy.yml` y salga verde no alcanza.

## 8. Alcance — qué NO entra

- No se toca `MAX(ts)` ni la máquina de estados del lector (§3).
- No se toca el escritor `record_facet_health`.
- No se toca `probe_after_rebind`: su `except` sigue igual.
- No se incluye el `detail` en el texto de la alerta. Es una mejora real
  (el mensaje de `config_error` trae el `UPDATE` a ejecutar) pero es otro
  cambio, en otro archivo, con su propia pregunta de diseño (cuánto texto
  entra en un mensaje de Telegram). **Se anota como candidato de la próxima
  ronda, no se hace acá.**
