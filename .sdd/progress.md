# Progreso

## T1 — `core/odoo_db.py` + `pack_environment` importa de ahí — COMPLETADA

- Archivos:
  - `rocketdoo/core/odoo_db.py` (nuevo): `db_container`, `databases_result`,
    `list_databases`, `module_states`, siguiendo la firma exacta del plan y el
    estilo de docstrings de `core/compose.py`.
  - `rocketdoo/pack_environment.py`: elimina `_get_db_container` y
    `_list_odoo_databases`; importa `db_container` y `list_databases` de
    `rocketdoo.core.odoo_db`. La variable local `db_container` (nombre del
    contenedor) se renombró a `db_container_name` para no sombrear la función
    importada; se ajustaron sus tres usos (línea del `if`, del warning y de
    `_backup_database`).
  - `tests/test_odoo_db.py` (nuevo): 13 tests, `subprocess.run` monkeypatchado
    en todos los casos (sin Docker).
- Validación:
  - `pytest tests/test_odoo_db.py tests/test_pack_environment.py tests/test_imports.py -q`
    → 275 passed.
  - `pytest -q` (suite completa) → 689 passed (676 previos + 13 nuevos).
  - `ruff check .` y `ruff format --check .` → limpios.
- Nota fuera del plan: la función `databases_result`/`module_states` agrega
  `timeout=15` a `subprocess.run`, que el `_list_odoo_databases` original no
  tenía. Es el mismo valor que usa `compose_ps_result` y evita que un
  `docker exec` colgado bloquee una request HTTP de la GUI (motivo por el que
  T2 en adelante va a llamar a este módulo desde un endpoint). Sin impacto en
  `rkd pack`, que ya esperaba a `subprocess.run` sin límite.

## T2 — Endpoints `/api/odoo/databases` y `/api/odoo/module-states` — COMPLETADA

- Archivos:
  - `rocketdoo/gui/api/odoo.py` (nuevo): `GET /databases` envuelve
    `databases_result()`; `GET /module-states?db=` valida `db` contra
    `list_databases()` antes de llamar a `module_states(db)` (que no valida
    `db` por diseño de T1), y devuelve `{"states": {}, "error": "unknown
    database"}` sin tocar psql si `db` no está en la lista.
  - `rocketdoo/gui/api/__init__.py`: registra el router con
    `prefix="/odoo", tags=["odoo"]`, mismo patrón que el resto de los routers.
  - `tests/test_gui_api.py`: agrega `/api/odoo/databases` a `GET_ENDPOINTS`;
    `TestOdooEndpoints` con dos casos: sin contenedor devuelve `databases: []`
    + motivo, y una `db` desconocida devuelve `states: {}` + `error` sin
    invocar `module_states` (monkeypatch que lanza `AssertionError` si se
    llama).
- Validación:
  - `pytest tests/test_gui_api.py -q` → 44 passed.
  - `pytest -q` (suite completa) → 697 passed (689 previos + 8 nuevos).
  - `ruff check .` y `ruff format --check .` → limpios.
- Sin hallazgos fuera del plan.

## T3 — `build_update_command` con validación por pertenencia — COMPLETADA

- Archivos:
  - `rocketdoo/gui/api/odoo.py`: agrega `ODOO_SERVICE = "web"`,
    `_known_modules()` (mismo `ModuleScanner` y `addons_path` que
    `GET /api/modules`, para que el botón nunca ofrezca un módulo que la
    validación después rechace) y `build_update_command(module, db)`, que
    valida `db` contra `list_databases()` y `module` contra
    `_known_modules()` por pertenencia — nunca por regex ni por escape — y
    arma el argv exacto de CA8.
  - `tests/test_gui_api.py`: `TestBuildUpdateCommand` con `addons_tree` +
    monkeypatch de `list_databases`: argv exacto (con `-T` y
    `--log-level=info`), `module="--load-language=es"` rechazado,
    módulo fuera de `addons/` rechazado, `db` fuera de la lista rechazada.
- Validación:
  - `pytest tests/test_gui_api.py -q` → 48 passed.
  - `pytest -q` (suite completa) → 702 passed (697 previos + 5 nuevos).
  - `ruff check .` y `ruff format --check .` → limpios.
- Sin hallazgos fuera del plan.

## T4 — Extraer `_stream_process` en `server.py` — COMPLETADA

- Archivos:
  - `rocketdoo/gui/server.py`: nueva función a nivel de módulo
    `_stream_process(websocket, cmd, timeout=600.0)` con el cuerpo exacto que
    antes vivía en `ws_docker_action` (mismo manejo de
    `WebSocketDisconnect`/`TimeoutError`/`Exception`, mismo `finally` que mata
    el proceso y cierra el socket, mismo `\x00exit:{code}` y mismo timeout de
    600s por línea). `ws_docker_action` ahora valida la acción y, si existe,
    delega en `await _stream_process(websocket, cmd)`. `/ws/logs/{container_name}`
    no se tocó.
  - `tests/test_gui_api.py`: `TestStreamProcess` (websocket falso con
    `send_text`/`close` async, vía `asyncio.run`: un `print('hi')` produce
    `["hi", "\x00exit:0"]` y cierra el socket; `raise SystemExit(3)` produce
    `\x00exit:3`) y `TestDockerActionWebSocket` (`/ws/docker/nope` vía
    `client.websocket_connect` devuelve `[error] Unknown action: nope` +
    `\x00exit:1`, regresión de CA13).
- Validación:
  - `pytest -q` (suite completa) → 705 passed (702 previos + 3 nuevos).
  - `ruff check .` y `ruff format --check .` → limpios.
- Diff puramente mecánico: se movió el cuerpo del `try/except/finally` sin
  modificar una sola línea de lógica; solo cambió el nivel de indentación al
  quedar dentro de una función de módulo en lugar de un closure de
  `create_app`. Nada del comportamiento observable de `/ws/docker/{action}`
  cambia.

## T5 — Ruta `/ws/odoo/update` — COMPLETADA

- Archivos:
  - `rocketdoo/gui/server.py`: `from rocketdoo.gui.api.odoo import
    build_update_command` (import diferido dentro de `create_app`, mismo
    estilo que el import de `api_router`) y la ruta
    `@app.websocket("/ws/odoo/update")`, que acepta el socket primero, arma el
    comando con `build_update_command(module, db)` y, si hay error, envía
    `[error] {error}` + `\x00exit:1` y cierra sin lanzar ningún proceso; si no,
    delega en `_stream_process`.
  - `tests/test_gui_api.py`: `TestOdooUpdateWebSocket` con dos casos sobre un
    `project_dir` vacío (sin Docker): argumentos inválidos devuelven
    `[error] ...` + `\x00exit:1`, y con `asyncio.create_subprocess_exec`
    monkeypatcheado para lanzar `AssertionError` si se lo llama, la misma
    ruta no lo invoca (CA9).
- Validación:
  - `pytest -q` (suite completa) → 708 passed (705 previos + 3 nuevos:
    2 de `TestOdooUpdateWebSocket` + 1 parametrización nueva de
    `tests/test_imports.py` sobre el import diferido agregado).
  - `ruff check .` y `ruff format --check .` → limpios.
- Hallazgo fuera del plan: ninguno. El orden "aceptar primero, validar
  después" del plan se respetó literalmente; no se lanza ningún subproceso
  cuando `build_update_command` devuelve error.

## T6 — `DockerTerminal` acepta `url` y `label` — COMPLETADA

- Archivos:
  - `rocketdoo/gui/static/index.html`: `DockerTerminal` cambia sus props de
    `{ action: String }` a `{ url: String, label: String }`. El `WebSocket` se
    conecta a `${proto}://${location.host}${props.url}` en lugar de armar la
    URL con `/ws/docker/${props.action}`; el encabezado y el placeholder de
    "Connecting to…" muestran `label` en vez de interpolar `action`. El único
    llamador (Dashboard) pasa `:url="'/ws/docker/'+termAction"` y
    `:label="'docker compose '+termAction"`, preservando el comportamiento
    visible actual.
- Validación:
  - `pytest -q` (suite completa) → 712 passed, sin cambios (el HTML no tiene
    tests automáticos).
  - `ruff check .` y `ruff format --check .` → limpios.
  - `node --check` sobre el contenido de `<script>` extraído del HTML → sin
    errores de sintaxis.
  - Manual (RF/CA13, pendiente de verificación con Docker real): botón
    **Build** del Dashboard debería seguir mostrando `docker compose build` en
    el encabezado y el log en vivo. No se pudo ejecutar en este entorno (sin
    Docker); ver sección "Pendiente" al final.

## T7 — Selector de base y columna State en Modules — COMPLETADA

- Archivos:
  - `rocketdoo/gui/static/index.html`: en `Modules.setup()` agrega
    `dbs`, `dbError`, `db`, `states`; `loadDbs()` llama a
    `GET /api/odoo/databases`, resuelve la selección desde
    `localStorage['rkd-db']` si sigue en la lista, o cae a la primera base (o
    `''` si no hay ninguna); `loadStates()` llama a
    `GET /api/odoo/module-states?db=` + `encodeURIComponent(db)` y notifica el
    error de psql sin romper la vista; `stateOf(m)` devuelve
    `states[m.name] || '—'`; `stateBadgeClass(s)` mapea `installed` a verde,
    cualquier `to *` a amarillo, y el resto (incluido `uninstalled` y `—`) a
    gris. `watch(db, ...)` persiste la selección en `localStorage` (solo si no
    está vacía, para no pisar la preferencia guardada cuando el stack está
    abajo) y dispara `loadStates()`. `onActivated` ahora llama también a
    `loadDbs()`. Plantilla: `<select v-model="db">` en el header (mismo patrón
    `class="input" style="width:auto"` que el resto del SPA) junto con el
    motivo (`dbError`) cuando no hay bases; nueva columna **State** en la
    tabla de Local Addons con un badge coloreado. Todavía sin botón Update
    (eso es T8): la vista queda usable y verde con la base y los estados
    visibles.
- Validación:
  - `pytest -q` (suite completa) → 712 passed (el HTML no tiene tests
    automáticos, sin impacto en la suite Python).
  - `ruff check .` y `ruff format --check .` → limpios.
  - `node --check` sobre el `<script>` extraído → sin errores de sintaxis.
  - Manual (CA3/CA4/CA5, pendiente de verificación con Docker real y un
    proyecto Rocketdoo real): no se pudo ejecutar en este entorno sandbox
    (requiere `rkd up -d` con Postgres real). Ver sección "Pendiente".

Pendiente: T8 y T9.
