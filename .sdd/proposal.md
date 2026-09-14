# Propuesta: botón "Update" por módulo en la vista Modules de la GUI

## Problema

En la vista **Modules** de `rkd gui` el dev ve sus addons locales (nombre, ruta,
versión, `installable`, dependencias) pero no puede hacer nada con ellos. Para
aplicar un cambio tiene que salir de la GUI: abrir una terminal y correr
`docker compose exec web odoo -d <base> -u <modulo> --stop-after-init`, o entrar
a Apps dentro de Odoo y buscar el módulo. Es el gesto más repetido del ciclo de
desarrollo y es el único que la GUI no cubre.

Además la GUI hoy no conoce dos cosas que hacen falta para hacerlo bien:
qué bases de datos existen y en qué estado (`installed` / `uninstalled`) está
cada módulo en esa base.

## Objetivo

Que desde la vista Modules se pueda actualizar un módulo instalado en una base
elegida, ver el log del upgrade en vivo y quedar con el servidor reiniciado y
listo para probar, sin tocar la terminal.

## Alcance incluido

1. Selector de base de datos en el header de la vista Modules.
2. Columna de estado del módulo en la base seleccionada, leída de
   `ir_module_module`.
3. Botón **Update** por fila, habilitado solo cuando el módulo está instalado en
   la base seleccionada.
4. Confirmación previa que nombra módulo y base y advierte sobre la reaplicación
   de datos XML.
5. Streaming del log del upgrade en el componente `DockerTerminal` que ya existe.
6. Reinicio automático del servicio `web` al terminar, con el endpoint que ya
   existe (`POST /api/docker/service/restart`).
7. Validación server-side de `module` y `db` contra las listas reales.
8. Módulo nuevo `rocketdoo/core/odoo_db.py` con el acceso a la base, absorbiendo
   los helpers equivalentes que hoy viven en `pack_environment.py`.

## Alcance excluido

- **Botón "Install" para módulos no instalados.** Es la fase 2 natural: el
  comando sería `-i base,{mod}` (incluir `base` fuerza el `update_list()` que
  necesita un módulo que todavía no figura en `ir_module_module`). Se anota, no
  se planifica.
- **Uninstall.** No se agrega: es destructivo y tiene un camino claro en Apps.
- **Actualizar varios módulos a la vez** (`-u a,b`). Trivial de agregar después
  sobre esta base; no se hace ahora.
- **Módulos de `external_addons/` (gitman).** La vista lista solo `addons/` y la
  validación usa esa misma lista; queda igual.
- Cualquier cambio en el core de Odoo o en addons de terceros.
- `unpack_environment.py` tiene helpers casi iguales a los que se mueven
  (`_get_db_container_name`, `_is_container_running`), con otra firma. No se
  tocan en esta fase: consolidarlos es un refactor aparte, sin relación con el
  pedido.

## Enfoque propuesto

**Comando.** `docker compose exec -T web odoo -d {db} -u {module}
--stop-after-init --log-level=info`.
El `-T` es obligatorio (bajo uvicorn no hay TTY en stdin). El `--log-level=info`
pisa el `debug` que fija `templates/config/odoo.conf.jinja`, sin el cual el log
es ruido. Verificado en la exploración: con `--stop-after-init` el proceso no
bindea HTTP, así que no colisiona con el Odoo en marcha ni con debugpy.

Se descartó bajar el stack (`stop` + `run --rm` + `start`) porque triplica la
superficie de error, y RPC `button_immediate_upgrade` porque no da log de
progreso, que es la mitad del valor de la feature.

**Reinicio de `web`.** Incondicional al terminar, con éxito o con error. Con
éxito, porque `importlib` cachea los módulos Python y el registry recargado
re-ejecuta `_build_model` sobre clases ya importadas: los cambios en `.py` no se
ven sin reiniciar. Con error, porque el arranque corre `reset_modules_state` y
desbloquea los crons. Se hace desde el frontend contra el endpoint existente;
no se agrega backend para esto.

**Elección de base.** `<select>` en el header, sin modal. Una sola base queda
preseleccionada; con varias se recuerda la última en `localStorage` (el SPA ya
persiste el tema ahí). El nombre de la base se ve en el botón y en el confirm.

**Streaming.** WebSocket nuevo `/ws/odoo/update?module=X&db=Y`, consumido por el
componente `DockerTerminal`. No se mete en `_DOCKER_CMDS`: ese dict es de
comandos sin parámetros, y parametrizarlo abriría `/ws/docker/{action}` a
argumentos arbitrarios del browser. La única refactorización necesaria es
extraer el bucle de streaming de `server.py` a `_stream_process(websocket, cmd)`,
compartido por las dos rutas; si no, son ~40 líneas duplicadas.

**Estado de instalación.** Se lee `state` de `ir_module_module` en la base
seleccionada y se deshabilita el botón, con tooltip, cuando el módulo no está
instalado. Sin esto el modo de falla más probable es *"apreté el botón y no pasó
nada"*: `load_modules` filtra por `state = 'installed'` y `-u` sobre un módulo no
instalado termina con éxito sin hacer nada.

**Acceso a la base.** Módulo nuevo `rocketdoo/core/odoo_db.py` con
`db_container()`, `list_databases()` y `module_states(db)`. Absorbe
`pack_environment._get_db_container` y `pack_environment._list_odoo_databases`,
que pasa a importar de ahí. Mismo criterio que `core/compose.py` en #137: evitar
la tercera copia.

**Validación.** `module` y `db` llegan del browser. `create_subprocess_exec` no
usa shell, pero un valor como `--load-language=...` llegaría igual al CLI de
Odoo. Se validan por pertenencia a las listas reales (módulos del scanner, bases
de `pg_database`), no con regex.

## Riesgos principales

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Cerrar la pestaña durante el upgrade mata el proceso (el `finally` del handler hace `process.kill()`) y puede dejar módulos en estado `to upgrade`. | Es la misma semántica que un Ctrl-C en la terminal. La recuperación es reiniciar `web`: `reset_modules_state` corre al arrancar. Se documenta. |
| R2 | `-u` reaplica los datos XML sin `noupdate`: registros tuneados a mano vuelven al valor del módulo. | Comportamiento de Odoo, no algo que introduzcamos. El confirm lo dice antes de ejecutar. |
| R3 | No-op silencioso sobre módulo no instalado. | Columna de estado + botón deshabilitado con tooltip. |
| R4 | El reinicio de `web` corta las sesiones abiertas del dev y la conexión de debugpy. | Aceptado: es el precio de ver los cambios en `.py`. La notificación lo avisa. |
| R5 | La validación server-side es la única barrera entre el browser y el CLI de Odoo. Si alguien la "simplifica", se abre pasar flags arbitrarios. | Test dedicado que falla si la validación desaparece. |
| R6 | El refactor de `_stream_process` toca `/ws/docker/{action}`, que hoy funciona (Build, Up, Down del Dashboard). | Test unitario del helper con un comando real corto + prueba manual del botón Build. |
| R7 | Mover helpers fuera de `pack_environment` puede romper `rkd pack`. | `tests/test_pack_environment.py` ya cubre el flujo; el refactor no cambia firmas. |
| R8 | Un módulo bajo `addons/sub/mod` aparece en la GUI pero Odoo no lo ve: el `addons_path` generado apunta solo a la raíz. El upgrade es un no-op. | Limitación conocida, visible en el log (`invalid module names, ignored`). Fuera de alcance. |

## Supuestos abiertos

- El servicio Odoo se llama `web` y el de PostgreSQL `db`, como fija
  `templates/docker-compose.yaml.jinja`. Un compose editado a mano queda fuera.
- El usuario de PostgreSQL es `root` (`POSTGRES_USER=root` en el template), que
  es lo que ya asume `pack_environment`.
- `docker compose` v2 disponible en el host, como ya asume toda la GUI.
- La GUI corre en la misma máquina que los contenedores (es su modo de uso).

## Preguntas / decisiones pendientes

1. **Refresco del estado tras el upgrade.** Propuesta: recargar los estados al
   terminar (un GET barato). Incluido en el plan; se puede sacar si molesta.
2. **Timeout.** Se hereda el de la ruta existente: 600 s *sin una sola línea de
   salida*, no 600 s totales. Un upgrade que imprime logs no lo toca. Si alguna
   base enorme lo alcanza, se sube el valor en un solo lugar.
3. **Fase 2 (Install)** queda anotada arriba: confirmar si se quiere antes de
   cerrar el ciclo, o si se deja para un pedido aparte.
