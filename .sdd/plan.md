Estado: APROBADO

# Plan de implementación: botón "Update" por módulo en la vista Modules

Rama de trabajo: `feature/gui-module-update`, nacida de `dev/v3` (la actual
`fix/170-gevent-port` no es la base).

## Diseño técnico

### Archivos que se tocan

| Archivo | Acción |
|---|---|
| `rocketdoo/core/odoo_db.py` | **Nuevo.** Acceso de solo lectura al PostgreSQL del proyecto. |
| `rocketdoo/pack_environment.py` | Borra dos helpers y los importa del módulo nuevo. |
| `rocketdoo/gui/api/odoo.py` | **Nuevo.** Router `/api/odoo` + validación y armado del comando. |
| `rocketdoo/gui/api/__init__.py` | Registra el router nuevo. |
| `rocketdoo/gui/server.py` | Extrae `_stream_process`; agrega la ruta `/ws/odoo/update`. |
| `rocketdoo/gui/static/index.html` | `DockerTerminal` por URL; selector de base, columna de estado y botón Update en Modules. |
| `tests/test_odoo_db.py` | **Nuevo.** |
| `tests/test_gui_api.py` | Tests de los endpoints nuevos, la validación y el streaming. |
| `CLAUDE.md` | Documenta el comando, los endpoints y el módulo nuevo. |

No se toca `_DOCKER_CMDS`, ni `docker_ops.py`, ni `modules.py`, ni
`unpack_environment.py`.

### `rocketdoo/core/odoo_db.py`

Docstring en inglés explicando por qué existe (misma razón que `core/compose.py`
en #137: evitar la tercera copia de la consulta a `pg_database`).

```
PSQL_USER = "root"                      # POSTGRES_USER del template

db_container(compose_data: dict | None = None) -> str | None
    # services.db.container_name; si compose_data es None lee
    # project_info.read_docker_compose(). Cuerpo igual al actual
    # pack_environment._get_db_container, con el fallback agregado.

list_databases(compose_data: dict | None = None) -> list[str]
    # docker exec {container} psql -U root -d postgres -t -c
    #   "SELECT datname FROM pg_database
    #    WHERE datistemplate = false AND datname != 'postgres';"
    # Sin contenedor o ante cualquier fallo: [].

databases_result(compose_data=None) -> tuple[list[str], str]
    # Igual que list_databases pero devuelve también el motivo del vacío,
    # para que la GUI lo muestre. Mismo patrón que compose_ps_result().
    # list_databases() es databases_result()[0].

module_states(db: str) -> tuple[dict[str, str], str]
    # docker exec {container} psql -U root -d {db} -t -A -F '\t' -c
    #   "SELECT name, state FROM ir_module_module;"
    # Devuelve ({nombre: state}, error). Si la base no es de Odoo, psql
    # falla y se devuelve ({}, stderr).
```

`module_states` **no** valida `db`: eso es responsabilidad del trust boundary
(la capa GUI), y dejarlo en un solo lugar evita que la validación se duplique
con criterios distintos.

Importa `read_docker_compose` de `rocketdoo.project_info`, que solo depende de
stdlib y `yaml`: sin riesgo de import cíclico.

### `rocketdoo/gui/api/odoo.py`

```
ODOO_SERVICE = "web"                    # nombre de servicio del template

GET  /api/odoo/databases        -> {"databases": [...], "error": "..."}
GET  /api/odoo/module-states?db -> {"states": {...}, "error": "..."}

_known_modules() -> set[str]
    # ModuleScanner(addons_path=Path.cwd()/"addons").scan(force_rescan=True)
    # Mismo origen que alimenta la tabla de la vista, así el botón nunca
    # ofrece algo que la validación después rechace.

build_update_command(module: str, db: str) -> tuple[list[str] | None, str]
    # Valida por pertenencia: db in list_databases(), module in _known_modules().
    # Devuelve (argv, "") o (None, motivo).
    # argv exacto:
    #   ["docker", "compose", "exec", "-T", ODOO_SERVICE,
    #    "odoo", "-d", db, "-u", module,
    #    "--stop-after-init", "--log-level=info"]
```

Errores como HTTP 200 + clave `error`, igual que `modules.py` y `project.py`.

`build_update_command` vive acá y no en `server.py` para poder testearla sin
websocket. Es la única barrera entre el browser y el CLI de Odoo.

### `rocketdoo/gui/server.py`

`_stream_process` a nivel de módulo (no dentro de `create_app`), con el cuerpo
actual de `ws_docker_action` a partir de `create_subprocess_exec`:

```
async def _stream_process(websocket: WebSocket, cmd: list[str], timeout: float = 600.0) -> None
    # Asume el socket ya aceptado. Emite líneas, luego "\x00exit:{code}".
    # Maneja WebSocketDisconnect / TimeoutError / Exception igual que hoy.
    # En finally: mata el proceso si sigue vivo y cierra el socket.
```

`/ws/logs/{container_name}` **no** se toca: tiene otra semántica (sin banner de
exit, timeout de 30 s con mensaje de "no new logs"). Compartirlo obligaría a
parametrizar dos comportamientos para ahorrar diez líneas.

Ruta nueva:

```
@app.websocket("/ws/odoo/update")
async def ws_odoo_update(websocket: WebSocket, module: str, db: str):
    await websocket.accept()
    cmd, error = build_update_command(module, db)
    if error: -> send "[error] {error}", "\x00exit:1", close, return
    await _stream_process(websocket, cmd)
```

### `rocketdoo/gui/static/index.html`

**`DockerTerminal`**: props `{ url: String, label: String }` en lugar de
`action`. Conecta a `${proto}://${location.host}${props.url}` y muestra `label`
en el encabezado y en el placeholder de conexión. Dos llamadores: Dashboard
(`:url="'/ws/docker/'+termAction"`, `:label="'docker compose '+termAction"`) y
Modules.

**Vista Modules**, estado nuevo:
`dbs`, `dbError`, `db`, `states`, `updating` (nombre del módulo en curso o
`null`), `restarting`.

- `loadDbs()` → `GET /odoo/databases`; elige `localStorage['rkd-db']` si sigue en
  la lista, si no la primera; guarda la elección al cambiar.
- `loadStates()` → `GET /odoo/module-states?db=` + `encodeURIComponent(db)`;
  se dispara con `watch(db)` y al terminar un upgrade. No re-escanea módulos.
- `stateOf(m)` → `states[m.name] || '—'`.
- `canUpdate(m)` / `updateReason(m)` → habilitación y tooltip (RF5).
- `startUpdate(m)` → `confirm()` nativo (el SPA no tiene modales) con módulo,
  base y la advertencia de datos XML; setea `updating` y monta la terminal.
- `onUpdateDone(code)` → `POST /docker/service/restart {service:'web'}`,
  `notify()` del resultado, `loadStates()`, `updating = null`.

Tabla: columnas nuevas **State** y **Actions**; el botón muestra la base
(`Update (dev)`). La terminal se monta fuera del `v-if="busy"`, como en el
Dashboard, para que un refresh no la desmonte.

## Tareas

- [x] **T1 — `core/odoo_db.py` + `pack_environment` importa de ahí.**
  Crea el módulo con `db_container`, `databases_result`, `list_databases` y
  `module_states`. Borra `_get_db_container` y `_list_odoo_databases` de
  `pack_environment.py` y ajusta sus dos llamadores (líneas ~325 y ~339).
  Sin cambios de comportamiento en `rkd pack`.
  Validación: `tests/test_odoo_db.py` nuevo (monkeypatch de `subprocess.run`:
  parseo de la salida de `psql`, `[]` + motivo cuando no hay contenedor, mapa
  `{modulo: state}`, error propagado cuando psql falla) +
  `pytest tests/test_pack_environment.py tests/test_imports.py` en verde.
  Commit: `REF: acceso a la base de Odoo en core/odoo_db.py`.

- [x] **T2 — Endpoints `/api/odoo/databases` y `/api/odoo/module-states`.**
  Crea `gui/api/odoo.py` con los dos GET y registra el router en
  `gui/api/__init__.py` (`prefix="/odoo", tags=["odoo"]`).
  Validación: agregar `/api/odoo/databases` a `GET_ENDPOINTS` en
  `tests/test_gui_api.py` (contrato: HTTP 200 y dict, aun sin Docker) + test de
  que `module-states` con una `db` desconocida devuelve `states: {}` y `error`,
  y que no invoca `module_states` (monkeypatch que falla si se lo llama).
  Commit: `FEAT: endpoints de bases y estados de modulos en la GUI`.

- [x] **T3 — `build_update_command` con validación por pertenencia.**
  En `gui/api/odoo.py`: `_known_modules()` + `build_update_command()`.
  Validación: tests en `tests/test_gui_api.py` con `addons_tree` y monkeypatch
  de `list_databases`: (a) el argv es exactamente el de CA8, con `-T` y
  `--log-level=info`; (b) `module="--load-language=es"` devuelve error y
  `cmd is None`; (c) un módulo que no está en `addons/` devuelve error;
  (d) una `db` fuera de la lista devuelve error.
  Commit: `FEAT: validacion y armado del comando de update de modulo`.

- [x] **T4 — Extraer `_stream_process` en `server.py`.**
  Refactor puro: `/ws/docker/{action}` pasa a `await _stream_process(ws, cmd)`.
  Sin cambios de comportamiento observables.
  Validación: test unitario de `_stream_process` con un websocket falso
  (objeto con `send_text`/`close` async) y `cmd=[sys.executable, "-c", "print('hi')"]`
  → recibe `"hi"` y `"\x00exit:0"`; y un comando que devuelve 3 → `"\x00exit:3"`.
  Más `client.websocket_connect("/ws/docker/nope")` → `[error] Unknown action`
  + `\x00exit:1`. Manual: botón **Build** del Dashboard sigue mostrando el log.
  Commit: `REF: helper unico de streaming de procesos por websocket`.

- [x] **T5 — Ruta `/ws/odoo/update`.**
  Usa `build_update_command` + `_stream_process`.
  Validación: `websocket_connect("/ws/odoo/update?module=x&db=y")` en un dir
  vacío → `[error] ...` + `\x00exit:1`; test de que con argumentos inválidos no
  se llama a `asyncio.create_subprocess_exec` (monkeypatch que falla si corre).
  Commit: `FEAT: websocket de update de modulo Odoo`.

- [ ] **T6 — `DockerTerminal` acepta `url` y `label`.**
  Cambia los props y actualiza el único llamador actual (Dashboard).
  Sin backend involucrado; se puede mergear solo.
  Validación: manual — `rkd gui`, Dashboard → **Build**: el encabezado sigue
  diciendo `docker compose build`, el log fluye y aparece el banner de exit.
  Commit: `REF: DockerTerminal recibe la URL del stream`.

- [ ] **T7 — Selector de base y columna State en Modules.**
  `dbs` / `db` / `states`, persistencia en `localStorage['rkd-db']`,
  preselección de la única base, `watch(db)` → `loadStates()`, columna **State**
  en la tabla, y el motivo visible cuando no hay bases.
  Todavía sin botón Update: la vista queda usable y verde.
  Validación: manual — proyecto real arriba, la lista de bases coincide con
  `docker exec <db> psql -U root -l`; el estado coincide con Apps; recargar la
  página conserva la base; bajar el stack muestra el motivo sin errores
  crípticos.
  Commit: `FEAT: selector de base y estado de modulos en la vista Modules`.

- [ ] **T8 — Botón Update, confirmación, terminal y restart de `web`.**
  Columna **Actions**, habilitación y tooltips (RF5), `confirm()` con módulo,
  base y advertencia de datos XML, montaje de `DockerTerminal` con la URL del
  WebSocket, y `onUpdateDone` → `POST /docker/service/restart {service:'web'}`
  + `notify` + `loadStates()`.
  Validación: manual, escenarios E1 a E5 de la spec.
  Commit: `FEAT: boton de actualizar modulo en la GUI`.

- [ ] **T9 — Documentación.**
  `CLAUDE.md`: agregar a la tabla de vistas de la GUI que Modules actualiza
  módulos; agregar `GET /api/odoo/databases`, `GET /api/odoo/module-states` y
  `WS /ws/odoo/update` a la lista de la API; agregar `core/odoo_db.py` al árbol
  del proyecto y a la tabla de archivos clave; anotar la limitación de módulos
  anidados fuera del `addons_path` y la fase 2 (Install).
  Validación: revisión de lectura; sin cambios de código.
  Commit: `DOC: update de modulos desde la GUI`.

## Validación

### Cobertura automática por criterio

| Criterio | Cómo se verifica | Dónde |
|---|---|---|
| CA1 | `/api/odoo/databases` responde 200 con dict aun sin Docker | `tests/test_gui_api.py` (T2) |
| CA2 | `db` desconocida → `error` y no se consulta psql | `tests/test_gui_api.py` (T2) |
| CA8 | Comparación del argv completo con el literal esperado | `tests/test_gui_api.py` (T3) |
| CA9 | `module` con flags / módulo inexistente / db inexistente → error, `cmd is None`, y la ruta no lanza proceso | `tests/test_gui_api.py` (T3, T5) |
| CA10 | `_stream_process` con un comando real corto: líneas + `\x00exit:{code}` | `tests/test_gui_api.py` (T4) |
| CA13 | `/ws/docker/nope` → `[error] Unknown action` + `\x00exit:1` | `tests/test_gui_api.py` (T4) |
| CA14 | Suite de pack sin cambios | `tests/test_pack_environment.py` (T1) |
| CA15 | `ruff check . && ruff format --check . && pytest` | CI en el PR |
| RF10 | Parseo de `psql`, contenedor ausente, error propagado | `tests/test_odoo_db.py` (T1) |
| — | `from rocketdoo.core.odoo_db import ...` resuelve (incluye imports diferidos) | `tests/test_imports.py`, automático |

Ningún test nuevo necesita daemon de Docker: `subprocess.run` y
`create_subprocess_exec` se monkeypatchean, y el único proceso real que se lanza
es `sys.executable -c ...`.

### Verificación manual (obligatoria antes del PR)

Requiere un proyecto Rocketdoo real:

1. `cd` a un proyecto generado, `rkd up -d`, esperar a que Odoo levante.
2. Crear una base e instalar desde Apps un módulo de `addons/` (p. ej.
   `sale_extension`). Dejar un segundo módulo sin instalar.
3. `rkd gui --open` → pestaña **Modules**.
4. **CA3/CA5**: el selector muestra la base; el módulo instalado dice
   `installed` y el no instalado `uninstalled`.
5. **CA6**: el botón del módulo no instalado está gris con tooltip.
6. **CA7**: click en Update del instalado → el confirm nombra módulo y base y
   advierte sobre los datos XML. Cancelar: no pasa nada.
7. **CA10**: aceptar → el log fluye en vivo, con errores/warnings coloreados,
   y termina con `✓ completed`.
8. **CA11**: aparece la notificación del restart de `web`;
   `docker compose ps` muestra `web` recién levantado.
9. **CA12**: la columna de estado se recarga sola.
10. Cambiar algo visible del módulo (una vista y un `.py`), apretar Update y
    recargar Odoo: **ambos** cambios se ven (esto es lo que valida que el
    restart era necesario).
11. **E3**: romper un XML a propósito → banner `✗ exit 1`, traceback visible,
    `web` igual reiniciado.
12. **CA4**: con dos bases, elegir la segunda, recargar la página (F5) y
    confirmar que sigue elegida.
13. **E5**: `rkd down` → volver a Modules: el selector informa que no hay bases
    y los botones quedan deshabilitados, sin error críptico.
14. **CA13 (regresión)**: Dashboard → **Build** y **Restart** siguen mostrando
    su log como antes.
15. **CA14 (regresión)**: `rkd pack --no-db` y `rkd pack` completan.

### Criterio de "listo"

Todas las tareas cerradas, los 15 criterios verificados, `ruff` limpio, la suite
completa en verde y `CLAUDE.md` actualizado.
