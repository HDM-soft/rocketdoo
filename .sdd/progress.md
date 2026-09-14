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

Pendiente: T3 a T9 (no implementadas, fuera del alcance de esta tarea).
