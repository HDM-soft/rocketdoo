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

Pendiente: T2 a T9 (no implementadas, fuera del alcance de esta tarea).
