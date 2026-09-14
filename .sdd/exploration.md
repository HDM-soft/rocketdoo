# Exploración: botón de actualizar módulo en la GUI

Relevamiento previo a la propuesta. Verificado contra el código real del
repositorio y contra las imágenes `odoo:` publicadas — no contra documentación
ni suposiciones.

## Pedido

En la vista **Modules** de la GUI (`rkd gui`), que cada módulo local tenga un
botón para actualizarlo (equivalente a `odoo -u <modulo>`) sin salir de la
interfaz. Hoy el dev tiene que ir a la terminal o a Apps dentro de Odoo.

## Infraestructura existente reutilizable

| Pieza | Ubicación | Estado |
|---|---|---|
| Listado de módulos | `rocketdoo/gui/api/modules.py` | `GET /api/modules`, usa `core/module_scanner.py` |
| Ejecución de comandos | `rocketdoo/gui/api/docker_ops.py` | `_run(cmd, timeout)` + endpoints docker |
| Restart de un servicio | `docker_ops.py:63` | `POST /api/docker/service/restart` |
| Streaming por WebSocket | `rocketdoo/gui/server.py:63-107` | `/ws/docker/{action}`, dict fijo `_DOCKER_CMDS`, emite `\x00exit:{code}` |
| Visor de logs en el SPA | `gui/static/index.html:316` | Componente `DockerTerminal`: buffer por rAF, colorea traceback/error/warn, banner de exit code |
| Listado de bases de datos | `pack_environment.py:68-86` | Query `SELECT datname FROM pg_database ...` ya escrita |
| Nombres de contenedor | `pack_environment.py:37,45` | `_get_db_container()`, `_get_odoo_container()` |
| Helpers compose | `core/compose.py` (v3.2/#137) | `compose_path`, `run_compose`, `compose_ps`, `container_running` |

Servicios del compose generado: `web` (Odoo) y `db` (PostgreSQL).

## Huecos

1. **La GUI no conoce las bases de datos.** No hay endpoint que las liste; sólo
   `pack_ops.py` acepta un `db_name` que el usuario tipea.
2. **La GUI no conoce el estado de instalación de un módulo.** El scanner lee
   `__manifest__.py` del filesystem; no consulta `ir_module_module`.
3. **`_DOCKER_CMDS` es un dict de comandos sin parámetros.** No sirve para un
   comando que recibe módulo y base desde el browser.

## Hallazgos que condicionan el diseño

### `docker compose exec` no entra en conflicto con el Odoo en marcha

- `templates/Dockerfile.jinja:48` → `CMD python3 -m debugpy --listen 0.0.0.0:{{vsc_port}} /usr/bin/odoo -c /etc/odoo/odoo.conf`. Un `exec` no pasa por el `CMD`, así que no toca debugpy.
- En `odoo/service/server.py` de las imágenes reales: `ThreadedServer.start()` hace
  `if test_mode or (config['http_enable'] and not stop): self.http_spawn()`.
  Con `--stop-after-init` **no se bindea ningún puerto HTTP**. No hay colisión con 8069/8888.
- `odoo/modules/loading.py` fija `SET SESSION lock_timeout = '15s'` durante la carga:
  Odoo ya contempla que haya otro proceso trabajando sobre la misma base.
- La imagen define `ODOO_RC=/etc/odoo/odoo.conf`, así que no hace falta pasar `-c`.

### El registry se recarga, pero los módulos Python no

`registry.signal_changes()` / `check_signaling()` recargan el registry en el
proceso vivo, pero la recarga re-ejecuta `_build_model` sobre clases **ya
importadas**: `load_openerp_module` usa `importlib.import_module`, que está
cacheado. Vistas, datos y assets se ven al instante; cambios en `.py` no.

Consecuencia: hace falta reiniciar `web` después del upgrade. Como la mayoría
de los upgrades manuales son por código Python, no vale la pena distinguir casos.

### `-u` sobre un módulo no instalado es un no-op silencioso

`load_modules` filtra por `[('state','=','installed'), ('name','in',...)]`. Si el
módulo no está instalado, el comando termina con éxito y no hace nada. Es el
modo de falla más probable de esta feature: *"apreté el botón y no pasó nada"*.

### El log por defecto es ilegible

`templates/config/odoo.conf.jinja` fija `log_level = debug` y
`log_handler = [':DEBUG']`. Sin pisarlo, el log del upgrade es ruido.

### `addons_path` es sólo la raíz

El `addons_path` generado apunta a `/usr/lib/python3/dist-packages/odoo/extra-addons`.
El `ModuleScanner` recorre recursivo, así que la GUI puede listar un
`addons/sub/mod` que Odoo no ve (`invalid module names, ignored`).

## Restricciones

- **Trust boundary**: `module` y `db` vienen del browser. Aunque
  `create_subprocess_exec` no usa shell, un valor como `--load-language=...`
  llegaría igual al CLI de Odoo. Validar contra listas reales, no con regex.
- **`docker compose exec` requiere `-T`** al correr bajo uvicorn (sin TTY en
  stdin falla con "the input device is not a TTY").
- `-u` reaplica los datos XML sin `noupdate`: registros tuneados a mano en la
  base vuelven al valor del módulo. Es el comportamiento de Odoo, no algo que
  introduzcamos, pero el usuario debe verlo antes de confirmar.
- El SPA no tiene paso de build (Vue 3 por CDN): el cambio es HTML/JS en un
  único archivo de 2259 líneas.
