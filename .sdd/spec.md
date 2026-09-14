# Especificación: botón "Update" por módulo en la vista Modules de la GUI

## Requisitos funcionales

### RF1 — Listado de bases de datos

- La GUI expone `GET /api/odoo/databases`, que devuelve las bases del contenedor
  PostgreSQL del proyecto, excluyendo `postgres` y las plantillas.
- Reutiliza la consulta que ya existe en `pack_environment`:
  `SELECT datname FROM pg_database WHERE datistemplate = false AND datname != 'postgres'`.
- Si no hay compose, no hay contenedor `db`, no está corriendo o no hay Docker,
  responde HTTP 200 con `{"databases": [], "error": "<motivo>"}`.

### RF2 — Estado de los módulos en una base

- La GUI expone `GET /api/odoo/module-states?db=<base>`, que devuelve un mapa
  `{nombre_modulo: state}` leído de `ir_module_module` de esa base.
- `db` se valida contra el resultado de RF1 antes de usarse.
- Si la base no está en la lista, responde `{"states": {}, "error": "unknown database"}`.
- Si la base existe pero no es de Odoo (no hay `ir_module_module`), responde
  `{"states": {}, "error": "<error de psql>"}`.

### RF3 — Selector de base en la vista Modules

- El header de la vista Modules incluye un `<select>` con las bases de RF1.
- Con exactamente una base, queda preseleccionada sin intervención del usuario.
- Con varias, se preselecciona la última usada, persistida en `localStorage`
  bajo una clave propia (`rkd-db`), igual que el SPA ya hace con el tema.
- Si la base persistida ya no existe, se cae a la primera de la lista.
- Sin bases disponibles, el selector muestra el motivo y la columna de estado
  queda vacía.

### RF4 — Estado por módulo en la tabla

- La tabla de Local Addons muestra, por fila, el estado del módulo en la base
  seleccionada: `installed`, `uninstalled`, `to upgrade`, `to install`,
  `to remove`, o `—` si no se pudo determinar.
- Al cambiar la base seleccionada se recargan los estados, sin re-escanear el
  filesystem.

### RF5 — Botón Update

- Cada fila tiene un botón **Update** que muestra la base de destino.
- Está deshabilitado, con tooltip que explica el motivo, cuando:
  - no hay base seleccionada;
  - el estado del módulo en esa base no es `installed`
    (tooltip: `-u` sobre un módulo no instalado no hace nada);
  - ya hay otro upgrade en curso.
- Al apretarlo se pide confirmación. El texto de la confirmación nombra el
  módulo y la base, y advierte que `-u` reaplica los datos XML sin `noupdate`
  y puede pisar registros modificados a mano.

### RF6 — Ejecución del upgrade

- El backend expone el WebSocket `/ws/odoo/update?module=<m>&db=<d>`.
- Ejecuta exactamente:
  `docker compose exec -T web odoo -d {db} -u {module} --stop-after-init --log-level=info`.
- Emite cada línea de stdout/stderr como mensaje de texto y cierra con
  `\x00exit:{returncode}`, el mismo protocolo que `/ws/docker/{action}`.

### RF7 — Validación en el trust boundary

- Antes de armar el comando, `db` se valida por pertenencia a RF1 y `module` por
  pertenencia a la lista de módulos que devuelve `ModuleScanner` sobre `addons/`.
- La validación es por pertenencia a listas reales, **no** por regex ni por
  escape.
- Si alguno no valida, el WebSocket emite `[error] <motivo>` y `\x00exit:1`, y
  **no** se ejecuta ningún proceso.

### RF8 — Visualización del log

- El log se muestra en el componente `DockerTerminal` que ya existe, con su
  coloreado de error/warning y su banner de exit code.
- El encabezado de la terminal muestra el comando lógico
  (`odoo -u <modulo> -d <base>`).

### RF9 — Reinicio de `web` al terminar

- Al cerrar el stream, con exit code 0 o distinto de 0, el frontend llama a
  `POST /api/docker/service/restart` con `{"service": "web"}`.
- No se agrega ningún endpoint para esto.
- Al terminar el restart se notifica el resultado y se recargan los estados
  (RF2), para que la columna refleje el upgrade.

### RF10 — Consolidación del acceso a la base

- Módulo nuevo `rocketdoo/core/odoo_db.py` con, como mínimo:
  `db_container(compose_data=None)`, `list_databases()`, `module_states(db)`.
- `pack_environment.py` elimina `_get_db_container` y `_list_odoo_databases` y
  pasa a importar del módulo nuevo. El comportamiento de `rkd pack` no cambia.

## Requisitos no funcionales

- **Sin dependencias nuevas.** Todo con lo que ya está en `pyproject.toml`.
- **Sin paso de build en el frontend.** El SPA sigue siendo un único
  `gui/static/index.html` con Vue 3 por CDN.
- **Código y comentarios en inglés, sin emojis.** PEP 8, `ruff check` y
  `ruff format --check` limpios.
- **Tests.** Toda lógica no trivial deja test en `tests/`. La suite completa
  sigue verde y los tests nuevos no requieren daemon de Docker (los que sí lo
  requieran van marcados `docker`).
- **Mínimo código.** Se reutiliza `DockerTerminal`, `POST /api/docker/service/restart`,
  `ModuleScanner`, `project_info.read_docker_compose` y el protocolo de streaming
  existente. La única abstracción nueva es `_stream_process`, justificada por
  dos consumidores reales.
- **Tareas mergeables de a una**: cada tarea del plan deja el repo en verde.

## Escenarios de uso

### E1 — Camino feliz (una sola base)

El dev tiene el proyecto arriba (`rkd up -d`) con la base `dev` y el módulo
`sale_extension` instalado. Abre `rkd gui`, va a Modules. El selector muestra
`dev` ya elegido. La fila de `sale_extension` dice `installed` y el botón dice
`Update (dev)`. Lo aprieta, confirma, ve el log del upgrade en vivo, banner
`✓ completed`, notificación de que `web` se reinició. Recarga Odoo en el
navegador y ve su cambio.

### E2 — Varias bases

El proyecto tiene `dev`, `dev_clean` y `demo`. El selector arranca en la última
usada (`dev_clean`). El dev cambia a `demo`: la columna de estado se recarga y
`sale_extension` aparece como `uninstalled`, con el botón deshabilitado y el
tooltip explicándolo. Vuelve a `dev_clean` y actualiza. Al recargar la GUI al día
siguiente, el selector vuelve a `dev_clean`.

### E3 — El upgrade falla

El dev rompió un XML. Aprieta Update, el log muestra el traceback coloreado y el
banner `✗ exit 1`. `web` se reinicia igual (así `reset_modules_state` desbloquea
los crons). El dev corrige y vuelve a apretar.

### E4 — Módulo nuevo, nunca instalado

`sale_extension_v2` aparece en la tabla como `uninstalled`. El botón Update está
gris con tooltip: hay que instalarlo desde Apps. (El botón Install es fase 2.)

### E5 — Contenedores apagados

El dev abre Modules con el stack bajo. El selector muestra que no hay bases y el
motivo. Todos los botones Update están deshabilitados. No hay error críptico.

## Criterios de aceptación

| # | Criterio |
|---|---|
| CA1 | `GET /api/odoo/databases` devuelve la lista de bases; sin Docker devuelve `{"databases": [], "error": ...}` con HTTP 200. |
| CA2 | `GET /api/odoo/module-states?db=X` devuelve `{modulo: state}`; con una `db` que no está en la lista devuelve error y no ejecuta psql contra ella. |
| CA3 | Con una sola base, queda preseleccionada sin intervención. |
| CA4 | Con varias bases, la elección sobrevive a recargar la página (`localStorage`). |
| CA5 | La tabla muestra el estado de cada módulo en la base seleccionada. |
| CA6 | El botón Update está deshabilitado, con tooltip, si el módulo no está `installed`, si no hay base, o si hay otro upgrade corriendo. |
| CA7 | El confirm nombra módulo y base y advierte sobre los datos XML. |
| CA8 | El comando ejecutado es exactamente `docker compose exec -T web odoo -d {db} -u {module} --stop-after-init --log-level=info`. |
| CA9 | Un `module` o `db` que no está en las listas reales produce `[error] ...` + `\x00exit:1` sin lanzar ningún proceso. |
| CA10 | El log se ve en vivo en `DockerTerminal` y termina con el banner de exit code. |
| CA11 | Al terminar el stream, con cualquier exit code, se reinicia `web` y se notifica. |
| CA12 | Tras el restart, la columna de estado se recarga. |
| CA13 | `/ws/docker/{action}` sigue comportándose igual tras extraer `_stream_process` (mismas líneas, mismo marcador de exit, mismo mensaje para acción desconocida). |
| CA14 | `rkd pack` sigue funcionando tras mover los helpers a `core/odoo_db.py`. |
| CA15 | `ruff check .`, `ruff format --check .` y la suite de `pytest` en verde. |

## Restricciones técnicas

- `docker compose exec` **requiere `-T`** bajo uvicorn: sin TTY en stdin falla
  con *"the input device is not a TTY"*.
- `--log-level=info` es obligatorio para que el log sea legible: el template fija
  `log_level = debug` y `log_handler = [':DEBUG']`.
- No se toca `_DOCKER_CMDS` ni se parametriza `/ws/docker/{action}`.
- El acceso a `pg_database` y a `ir_module_module` es por `docker exec` sobre el
  contenedor `db` con `psql -U root`, igual que hace hoy `pack_environment`.
- Las respuestas de error de la API usan HTTP 200 con clave `error`, la
  convención del resto de los endpoints de la GUI (`modules.py`, `project.py`),
  para que `api.get()` del SPA no tenga que manejar excepciones.
- El SPA no tiene build: el cambio es HTML/JS dentro de un archivo de ~2260
  líneas. Estilos inline, como el resto del archivo.
- Se mantiene el protocolo de stream existente (`\x00exit:{code}`): el
  `DockerTerminal` ya lo parsea.

## Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Docker no instalado / daemon caído | `databases: []` + `error`; selector vacío con el motivo; botones deshabilitados. |
| Contenedor `db` definido pero parado | Igual al anterior, con el error de `docker exec`. |
| Compose sin `container_name` en `db` | `db_container()` devuelve `None` → `databases: []` + error explicativo. |
| Base seleccionada que no es de Odoo | `module-states` devuelve `{}` + error; columna en `—`; botones deshabilitados. |
| Base borrada entre el `GET` y el click | La validación server-side la rechaza: `[error] unknown database` + `exit:1`. |
| `module` con flags (`--load-language=es`), path traversal o vacío | Rechazado por no pertenecer a la lista del scanner; no se ejecuta nada. |
| Módulo listado pero fuera del `addons_path` de Odoo (`addons/sub/mod`) | El comando corre y Odoo lo ignora; se ve en el log. Limitación conocida, no se resuelve. |
| El servicio `web` no está corriendo | `docker compose exec` falla; el error se ve en la terminal y el exit code es distinto de 0. No se pre-chequea. |
| Upgrade largo sin salida por más de 600 s | Timeout de la ruta: `[error] Timed out after 10 minutes` + `exit:1`. El timeout es por línea, no total. |
| El usuario cierra la pestaña a mitad del upgrade | El proceso se mata (mismo `finally` que hoy). Puede quedar un módulo en `to upgrade`; se recupera reiniciando `web`. |
| Dos clicks de Update seguidos | El segundo no sale: los botones quedan deshabilitados mientras hay un upgrade en curso. |
| `addons/` vacío o inexistente | La tabla sigue mostrando el estado vacío actual; nada nuevo falla. |
