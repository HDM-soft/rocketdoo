# Revisión

Alcance: `git diff dev/v3...feature/gui-module-update` — T1 a T5 (backend).
T6–T9 (frontend y documentación) están fuera de este bloque y no se evalúan.

Verificaciones ejecutadas por el revisor:

- `pytest -q` → **708 passed** (coincide con `progress.md`).
- `ruff check .` → limpio. `ruff format --check .` → 83 files already formatted.
- `pytest -m docker` → 5 passed (con daemon disponible: los tests nuevos no
  dependen de Docker, cortan antes por `db_container() is None`).
- Comparación mecánica del bloque de streaming viejo vs. `_stream_process`
  (dedent + normalización del parámetro `timeout`): **byte-idéntico**, 38 líneas.
- Sondeos adversariales sobre `build_update_command` y `/ws/odoo/update`
  (valores vacíos, `None`, tipos no-str, mayúsculas, unicode, parámetros
  repetidos, directorio de módulo con nombre de flag, excepción del scanner).
- Verificación por AST del renombre `db_container` → `db_container_name`.

## Bloqueantes

Ninguno. No encontré ningún camino en el que el browser pueda inyectar
argumentos al CLI de Odoo, ni ningún cambio de comportamiento en
`/ws/docker/{action}`, ni un llamador roto de los helpers movidos.

Detalle de lo que se verificó y **sí** está bien, porque era lo más riesgoso:

**T4 — extracción de `_stream_process` (CA13).** El cuerpo de la función es
byte-idéntico al bloque original de `ws_docker_action`, línea por línea:
mismo `process = None`, mismo `create_subprocess_exec` con
`stderr=STDOUT`, mismo `readline` bajo `wait_for` con timeout **por línea**,
mismo `rstrip()`, mismo `await process.wait()` antes del banner, mismo orden
de `except WebSocketDisconnect / TimeoutError / Exception`, mismos textos
(`[error] Timed out after 10 minutes`, `[error] {e}`), mismos `try/except
Exception: pass` anidados alrededor de cada `send_text` de error, y mismo
`finally` (kill si `returncode is None`, luego `close()`, ambos protegidos).
El único cambio es `timeout=600.0` → parámetro con default `600.0`. El call
site tampoco cambió: `_DOCKER_CMDS.get(action)` sigue antes del `accept()`, y
la rama de acción desconocida sigue emitiendo `[error] Unknown action: {action}`
+ `\x00exit:1` + `close()` **sin** pasar por `_stream_process`.

**T1 — regresión en `rkd pack`.** El renombre `db_container` →
`db_container_name` se aplicó en los 5 usos (asignación, `if not`,
`_is_container_running`, f-string del warning, `_backup_database`); la única
referencia bare `db_container` que queda es la llamada a la función importada.
No hay otros llamadores: `unpack_environment.py` tiene sus propios
`_get_db_container_name` / parámetros locales llamados `db_container` y no
toca nada de esto. `list_databases(compose_data)` re-deriva el mismo
contenedor que el `_list_odoo_databases(db_container)` original.

**Trust boundary.** La validación es por pertenencia real y el comando se
ejecuta con `create_subprocess_exec(*argv)` (sin shell). Confirmado por sondeo:
`""`, `None`, `1`, `["dev"]`, `"prod"` vs `"Prod"` y un homoglifo unicode
caen todos en `(None, motivo)`. Parámetros repetidos (`?db=dev&db=evil`) no
permiten desincronizar validación y uso: FastAPI resuelve un único valor y es
el mismo que se valida y se usa.

## Importantes

### I1 — CA14 no tiene la cobertura que el plan le atribuye; la rama modificada de `pack` no se ejecuta nunca

`plan.md` mapea CA14 (`rkd pack` sigue funcionando) a
`tests/test_pack_environment.py`. Ese archivo existe y pasa, pero **sus 8
tests invocan `pack --no-db`**, que corta en `if not no_db:` justo antes del
bloque que T1 modificó (`pack_environment.py:290-325`). Las líneas cambiadas
—las cinco de `db_container_name` y la llamada a `list_databases`— tienen
**cero cobertura automática**. Verifiqué el renombre por AST, así que hoy no
hay bug; pero la evidencia que respalda CA14 no existe, y este es exactamente
el tipo de fallo que ya se escapó una vez en este archivo (el
`UnboundLocalError` de #166 que documenta el docstring del propio test).

Además, la verificación manual obligatoria del plan (paso 15: `rkd pack --no-db`
y `rkd pack`) no figura como ejecutada en `progress.md`.

Acción: un test que monkeypatchee `_is_container_running` y `list_databases` y
corra `pack` sin `--no-db`, o dejar constancia de la corrida manual de
`rkd pack` con backup.

### I2 — `test_a_flag_disguised_as_a_module_is_rejected` pasa por la razón equivocada, y el caso límite de la spec no es cierto

El test pasa porque `--load-language=es` no es un directorio del
`addons_tree`, no porque los flags se rechacen. Sondeo real, creando
`addons/--load-language=es/__manifest__.py`:

```
build_update_command("--load-language=es", "dev")
→ (['docker','compose','exec','-T','web','odoo','-d','dev',
    '-u','--load-language=es','--stop-after-init','--log-level=info'], '')
```

Es decir: **acepta**. El impacto real es bajo (no hay shell; `optparse` de Odoo
consume el token siguiente a `-u` como valor aunque empiece con `-`; y quien
puede crear directorios en `addons/` ya puede ejecutar Python arbitrario en
Odoo), por eso no es bloqueante. Pero:

- `spec.md` (Casos límite) afirma: *"`module` con flags (`--load-language=es`),
  path traversal o vacío → Rechazado por no pertenecer a la lista del scanner"*.
  Es falso como enunciado general: lo que se rechaza es *lo que no existe en
  disco*, no *lo que parece un flag*.
- El nombre del test promete una propiedad que el código no tiene. Un test de
  seguridad que no puede fallar es peor que no tenerlo.

Acción: o bien renombrar el test a lo que efectivamente prueba (*"un módulo que
no está en disco se rechaza"*) y corregir esa fila de la spec, o bien agregar
el filtro de nombre. Lo primero es suficiente y es el diff más corto.

### I3 — `_known_modules()` no es "el mismo origen" que `GET /api/modules`

El docstring afirma: *"Same scanner and addons_path as GET /api/modules, so the
Update button never offers a module the validation below would then reject."*
No es exacto: `/api/modules` filtra por `is_installable` cuando
`include_all=False` (y el SPA llama `/modules?include_all='+all.value`), mientras
que `_known_modules()` devuelve **todos** los módulos escaneados. Verificado:
`build_update_command("legacy_module", "dev")` con `installable=False` devuelve
argv válido, aunque la tabla por defecto no muestre esa fila.

La asimetría es en la dirección segura (la validación es más amplia que la
tabla, no más angosta), así que no habilita nada que el usuario no pueda hacer
por CLI. Pero el comentario documenta una garantía que no se cumple, y es el
comentario en el que se va a apoyar el que toque esto dentro de un año.

### I4 — `async def` + `subprocess.run` bloqueante: hasta 30 s de event loop congelado por request

`get_databases` y `get_module_states` son `async def` y llaman a
`subprocess.run(..., timeout=15)`. `get_module_states` encadena dos
(`list_databases()` + `module_states(db)`) → hasta **30 s** bloqueando el event
loop, lo que congela toda la GUI, incluidos los WebSockets de log activos.
Lo mismo vale para `build_update_command` dentro de `ws_odoo_update` (docker
exec + `rglob` del scanner sobre el event loop).

Es el patrón que ya usa el resto de `gui/api/` (`docker_ops._run` con
`timeout=60` dentro de `async def`), así que no es una regresión introducida
acá — pero esta feature lo amplifica: RF4 recarga estados en cada cambio de
base. El arreglo más barato es sacarle el `async` a los dos handlers GET:
FastAPI corre los handlers `def` en threadpool y el problema desaparece sin una
línea más. Para la ruta WebSocket haría falta `asyncio.to_thread`.

## Menores

### M1 — Excepciones no capturadas en `/ws/odoo/update` rompen el protocolo del stream

Dos caminos cierran el socket sin `[error]` ni `\x00exit:1`:

1. Falta un query param (`/ws/odoo/update?module=x`, sin `db`): FastAPI
   responde con un cierre 1008 antes de entrar al handler. Verificado:
   `WebSocketDisconnect` sin ningún mensaje previo.
2. Si `build_update_command` lanza (p. ej. `ModuleScanner` con `OSError` al
   recorrer `addons/`), la excepción escapa del handler tras el `accept()`.
   Verificado con `_known_modules` parcheado para lanzar.

Ambos requieren un bug del frontend o un fallo de filesystem, pero el efecto
en T8 es que la terminal queda sin banner de exit y, si `onUpdateDone` sólo
se dispara con el marcador, `updating` queda colgado y todos los botones
Update deshabilitados (RF5). Envolver la validación en `try/except` y emitir
el banner ante cualquier fallo cierra los dos casos.

### M2 — El `finally` mata el cliente local, no el proceso dentro del contenedor

`spec.md` dice: *"El usuario cierra la pestaña a mitad del upgrade → El proceso
se mata"*. Matar el `docker compose exec` local no mata el `odoo -u` que corre
dentro de `web`: el daemon es el dueño de ese proceso. Consecuencia para T8: en
el camino de timeout (600 s) sí llega `\x00exit:1` al frontend, que entonces
dispara el `POST /api/docker/service/restart` de RF9 **mientras el upgrade
puede seguir corriendo adentro**. PostgreSQL hace rollback, así que no hay
corrupción, pero el módulo puede quedar en `to upgrade`. Conviene verificarlo
en la validación manual de T8 y corregir esa fila de la spec.

### M3 — `rkd pack` sí cambia de comportamiento: el `timeout=15` nuevo

`progress.md` lo documenta como deliberado, pero contradice RF10 (*"El
comportamiento de `rkd pack` no cambia"*). El `_list_odoo_databases` original
no tenía timeout. Si el `docker exec` supera 15 s, `list_databases()` devuelve
`[]` y `pack` imprime *"No Odoo databases found"* y **sigue de largo**, sin
preguntar, generando un ZIP sin dump de base. Probabilidad baja; consecuencia
(compartir un entorno sin datos creyendo que los lleva) desagradable. Al menos
dejarlo anotado en la spec, o darle a `pack` un timeout más holgado.

### M4 — Cobertura de tests: faltan los caminos positivos y los del `finally`

Ninguno pasa por la razón equivocada (verifiqué que los nuevos no dependen de
Docker: cortan en `db_container() is None`, no en "docker not found"), pero
faltan:

- `/api/odoo/module-states` con una `db` **válida**: hoy la membresía se prueba
  sólo por el lado negativo, con `list_databases()` real devolviendo `[]`. Un
  check que rechazara *todo* pasaría los dos tests del endpoint.
- `/ws/odoo/update` con argumentos válidos: nada verifica que el argv que
  arma `build_update_command` sea el que recibe `_stream_process`.
- `/ws/docker/{action}` con una acción **válida**: CA13 pide "mismas líneas,
  mismo marcador de exit" y el único test cubre la rama de acción desconocida,
  que ni siquiera entra a `_stream_process`. (El plan lo delegó a verificación
  manual; la extracción es byte-idéntica, así que el riesgo real es bajo.)
- `_stream_process`: sin cobertura del timeout, del `WebSocketDisconnect` ni
  del `process.kill()` del `finally`.
- `odoo_db`: sin cobertura de `subprocess.TimeoutExpired` (el `_patch_run` del
  test ya sabe lanzar excepciones, es una línea) ni de filas mal formadas en
  `module_states` (la rama `if name and state`, que hoy descarta silenciosamente
  un `state` NULL).

### M5 — Detalles de `core/odoo_db.py`

- `databases_result` usa `psql -t` (alineado) mientras `module_states` usa
  `-t -A`. La primera consulta sería más robusta también con `-A`; hoy depende
  de `strip()` para sacar el padding. No es regresión (es lo que hacía `pack`),
  pero ahora esa lista se usa para **validar por pertenencia**: un nombre de
  base con espacios al borde se normaliza y deja de coincidir con el nombre
  real. Caso patológico, sin impacto de seguridad.
- `module_states(db)` no acepta `compose_data` ni el nombre del contenedor,
  a diferencia de las otras tres funciones, así que `get_module_states` termina
  leyendo `docker-compose.yaml` dos veces y haciendo dos `docker exec` por
  request. Asimetría de API más que bug.
- `timeout=15` es razonable para ambas consultas (`SELECT` sobre
  `pg_database` e `ir_module_module` son instantáneos; el costo real es el
  arranque de `docker exec`). Sin objeción, salvo lo dicho en M3 para `pack`.
- `subprocess.TimeoutExpired` y `FileNotFoundError` quedan correctamente
  cubiertos por las cláusulas `except` (son subclases de `SubprocessError` y
  `OSError` respectivamente).

### M6 — `ws_odoo_update` ramifica por `if error:` y no por `cmd is None`

Hoy son equivalentes porque `build_update_command` devuelve siempre
`(None, motivo)` o `(argv, "")`. Si alguien agrega un warning no fatal, la ruta
deja de ejecutar en silencio. `if cmd is None:` expresa la intención real.

## Conclusión: REQUIERE CAMBIOS

El trabajo está bien hecho: el refactor más riesgoso (T4) es demostrablemente
byte-idéntico, el trust boundary está construido de la única forma correcta
(pertenencia a listas reales + argv sin shell) y resistió todos los sondeos de
evasión, y la regresión de `rkd pack` no existe. Nada de lo encontrado rompe en
runtime.

Lo que falta es evidencia, no código: **I1** deja un criterio de aceptación
(CA14) sin ninguna verificación real, e **I2** convierte el test de seguridad
central de la feature en uno que no puede fallar. Aprobar esto sería aprobarlo
porque "parece andar", que es justamente lo que no corresponde en la barrera
entre el browser y el CLI de Odoo.

Prioridad para volver a IMPLEMENTACIÓN:

1. **I1** — cubrir la rama de `pack` con backup (test) o registrar la corrida
   manual de `rkd pack`.
2. **I2** — renombrar el test a lo que prueba y corregir la fila de casos
   límite de la spec (o filtrar el nombre).
3. **I3** — corregir el docstring de `_known_modules()` (`include_all`).
4. **I4** — sacar `async` de los dos handlers GET de `gui/api/odoo.py`.
5. **M1** — `try/except` alrededor de la validación de `/ws/odoo/update` para
   no romper el protocolo del stream.
6. **M2, M3** — corregir las dos afirmaciones inexactas de `spec.md` antes de
   que T8 se apoye en ellas.
7. **M4** — los tests positivos que faltan (endpoint con `db` válida, ruta con
   argumentos válidos, `TimeoutExpired`).

M5 y M6 son opcionales.
