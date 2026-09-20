# Seguridad de Rocketdoo

Este documento describe el modelo de amenazas de Rocketdoo: qué protege, qué no, y por qué.
No es una promesa de "herramienta segura para producción" — es la descripción honesta de una
herramienta de desarrollo local.

## Qué es Rocketdoo

Rocketdoo (`rkd`) es una herramienta de línea de comandos y una GUI web opcional (`rkd gui`)
para gestionar entornos Odoo dockerizados **en la máquina del propio desarrollador**. Está
pensada para un solo usuario, corriendo como el mismo usuario del sistema operativo que la
invoca, sin separación de privilegios ni multiusuario.

Esto define el modelo de amenazas: el atacante relevante es **otro proceso o usuario del mismo
host**, o **una página web que el desarrollador visita en su navegador** mientras `rkd gui`
está corriendo — no un atacante remoto autenticándose contra un servicio expuesto a Internet.
Rocketdoo no está diseñado para correr en un host compartido con usuarios que no confían entre
sí, ni para exponerse fuera de `127.0.0.1` sin una capa adicional (ver "Riesgos residuales").

## Superficie expuesta por `rkd gui`

`rkd gui` levanta un servidor HTTP local (FastAPI + uvicorn, `127.0.0.1:8070` por defecto) con
una API que:

- **Toca el filesystem**: `POST /api/workspace/navigate` cambia el directorio de trabajo de
  todo el proceso servidor; `POST /api/workspace/mkdir` crea directorios; `POST
  /api/workspace/discover` y `POST /api/workspace/browse` listan el filesystem recursivamente
  buscando proyectos Rocketdoo; `POST /api/setup/scaffold` y `POST /api/setup/init` escriben
  `Dockerfile`, `docker-compose.yaml`, `config/odoo.conf` (con `admin_passwd` en claro) y copian
  una clave SSH privada al contexto de build.
- **Ejecuta comandos**: `docker_ops.py` corre `docker compose {up,down,restart,stop,build}` y
  `docker logs` sobre el proyecto activo; los WebSocket `/ws/docker/{action}` y
  `/ws/odoo/update` hacen lo mismo en vivo; `POST /api/instances/deploy` dispara un deploy
  completo a un VPS (build remoto, `docker compose up -d`, o instalación nativa vía
  apt/systemd) usando las credenciales de `.rkd/instance.yaml`; `POST /api/deploy/run` dispara
  un deploy de módulos vía `VPSDeployer`; `pack_ops.py` ejecuta `rkd pack`/`rkd unpack` (dump de
  base de datos y filestore).

Cualquier proceso capaz de hablar con `127.0.0.1:8070` — no solo un navegador — puede invocar
todo esto. CORS es una política que **solo respetan los navegadores**; un script local
(`curl`, `python-requests`) no la ve.

### Credenciales que Rocketdoo maneja y dónde viven en disco

| Credencial | Dónde vive | Protección |
|---|---|---|
| Password de VPS (deploy de instancia) | `.rkd/secrets/instance_{env}.env` | 0600, gitignored |
| Password de VPS (deploy de módulos) | `.rkd/secrets/vps_{target}.env` | 0600, gitignored |
| `odoo_pg_pass` / `admin_passwd` runtime (instancia) | `.rkd/secrets/instance_{env}_db.env` | 0600, gitignored |
| `admin_passwd` (proyecto local) | `config/odoo.conf`, en claro | gitignored, sin permisos restringidos explícitos |
| `admin_passwd` (config de instancia declarada) | `.rkd/instance.yaml`, en claro | gitignored, sin permisos restringidos explícitos |
| Clave SSH privada (build de imagen) | copiada a `.ssh/<clave>` dentro del contexto de build | gitignored (`.ssh/` en `SENSITIVE_ENTRIES`) |
| `SSHPASS` (autenticación por password a un VPS) | variable de entorno del subproceso `sshpass`, nunca en disco | vive solo en memoria del proceso hijo (desde 3.2.1) |
| Token de sesión de la GUI | memoria del proceso `rkd gui` (`app.state.rkd_token`) | efímero, no se persiste en disco |

`rkd scaffold`/`rkd init` generan un `.gitignore` que cubre las rutas marcadas "gitignored"
arriba; `rkd info` avisa si un proyecto existente le falta cobertura.

## Las tres defensas y qué cubre cada una

Rocketdoo endureció esta superficie en tres pasos independientes, cada uno cerrando un vector
distinto:

1. **CORS restringido a los orígenes propios de la GUI (3.2.0).** Antes, `allow_origins=["*"]`
   con `allow_credentials=True` dejaba que cualquier página que el desarrollador visitara en su
   navegador mientras `rkd gui` corría pudiera **leer** la respuesta de `/api/workspace` — es
   decir, exfiltrar rutas de su disco. `local_origins()` ahora solo permite el propio origen de
   la GUI. **Cubre**: la lectura de respuestas por una web ajena.

   **Lo que CORS no cubre, y conviene tener claro**: la política gobierna si el navegador deja
   *leer* la respuesta, no si la petición se *envía*. Un `POST /api/docker/down` sin cuerpo ni
   cabeceras propias es una petición simple: no dispara preflight y el servidor la ejecuta
   igual, aunque el atacante nunca vea el resultado. Ese escenario —un CSRF a ciegas— lo cierra
   el token, no CORS.
2. **Secretos fuera del `argv` (3.2.1).** El password del VPS viaja por la variable de entorno
   `SSHPASS` del subproceso `sshpass` (nunca como argumento de línea de comandos), el `sudo`
   remoto lo recibe por `stdin`, y el `odoo.conf` con `admin_passwd` se escribe vía `stdin` en
   vez de interpolarse en un comando SSH. **Cubre**: cualquier otro proceso o usuario del mismo
   host (o del VPS remoto) leyendo `ps aux` o la lista de procesos mientras un deploy corre; y,
   como efecto colateral, un password con `'`, `;` o `$(...)` ya no puede alterar el comando
   remoto ejecutado.
3. **Token de sesión efímero para la GUI (esta versión).** `create_app()` genera un token con
   `secrets.token_urlsafe(32)` en cada arranque; un middleware ASGI exige ese token (header
   `X-RKD-Token` o query param `token`) en todo `/api/*` y `/ws/*`, HTTP y WebSocket por igual.
   **Cubre** dos cosas: otro proceso local que descubra el puerto abierto y le hable directo
   sin pasar por un navegador; y el CSRF a ciegas del punto 1, porque una web ajena no conoce
   el token y su petición se rechaza antes de ejecutarse.

4. **El master password no se tipea a la vista (#190).** Los dos wizards que lo piden ocultan
   lo que se escribe: `rkd init` con `hide_input=True` y `rkd instance init` con `password=True`.
   Este último ofrece un password generado al azar como default y ya no lo muestra en el texto
   del prompt (`show_default=False`): lo imprime después, y solo si el usuario aceptó el default,
   que es el único caso en que necesita verlo. **Cubre** poco del modelo de amenazas —el valor
   termina en claro en `config/odoo.conf` y en `.rkd/instance.yaml` de todos modos— y sí un
   escenario concreto: la terminal compartida en un pair, un stream o una captura de pantalla.

5. **El traceback no sale del servidor (#191).** `/api/setup/init` devolvía
   `traceback.format_exc()` en el cuerpo de la respuesta: rutas absolutas del filesystem del
   desarrollador y la estructura interna del paquete, para quien tuviera el token — y antes del
   punto 3, para cualquiera. Ahora se registra con `logger.exception()` y queda en la terminal
   que corre `rkd gui`, que es donde el desarrollador ya está mirando. **Cubre** la fuga de
   información; no cubre el `str(e)`, que sigue viajando a propósito (ver riesgos residuales).

Ninguna de las cinco reemplaza a las otras, pero no son capas equivalentes: **el token es la más
fuerte**, porque actúa antes de ejecutar y no depende de que el atacante respete nada. CORS solo
limita lo que un navegador deja leer, el manejo de secretos protege de quien puede observar la
lista de procesos pero no leer el disco ni la memoria, y ocultar el prompt solo protege de quien
mira la pantalla.

## Lo que esto NO cubre

**`rkd gui --open` expone el token en la lista de procesos.** `webbrowser.open()` resuelve en
Linux a `Popen(["xdg-open", url])`, así que la URL completa —token incluido— queda como
argumento de ese proceso y es legible con `ps aux` mientras dura. Es el mismo canal que la
defensa 2 cierra para los passwords de VPS, y acá se acepta a conciencia: `--open` es opt-in,
viene desactivado por defecto, y el comando lo advierte al usarlo. Sin `--open`, el token solo
aparece en la terminal que corre `rkd gui`.

Riesgos residuales aceptados, documentados en lugar de prometidos como resueltos:

- **El token viaja en la URL.** `?token=...` puede quedar en el historial del navegador, en
  logs de proxies intermedios (no aplica aquí, es tráfico local) o visible sobre el hombro de
  alguien mirando la pantalla. No hay cookie de sesión ni almacenamiento más seguro porque el
  SPA se sirve como archivo estático sin motor de plantillas.
- **Sin cifrado en tránsito.** `rkd gui` habla HTTP plano en `127.0.0.1`; no hay TLS. Es
  aceptable en loopback, pero **`rkd gui --host 0.0.0.0`** expone esto a la red y convierte al
  token en la única barrera real, viajando en claro. No es la configuración recomendada.
- **Quien puede leer el filesystem o el entorno del proceso ya tiene las credenciales.**
  `config/odoo.conf` y `.rkd/instance.yaml` guardan `admin_passwd` en claro, sin permisos
  restringidos explícitos (a diferencia de `.rkd/secrets/`, que sí usa 0600). El token de la GUI
  y el manejo de `SSHPASS` no protegen contra un atacante que ya tiene acceso de lectura al
  proyecto o al entorno del proceso `rkd gui`.
- **Valores de configuración interpolados en comandos remotos se tratan como entrada
  confiable.** `remote_path`, `container_name` y `service_name` (entre otros) del propio
  `deploy.yaml`/`instance.yaml` del usuario se interpolan en comandos SSH sin sanitizar; están
  fuera de alcance porque son configuración que el propio usuario escribe, no entrada de un
  tercero.
- **El mensaje de error del wizard puede contener una ruta.** `/api/setup/init` devuelve
  `str(e)`, y una excepción de archivo trae la ruta que falló (`'/home/<usuario>/.ssh/...'`).
  Se acepta: es la ruta que el propio usuario pidió en la petición, y sin ella el error deja de
  ser accionable. El traceback completo, que además revela dónde está instalado el paquete, ya
  no viaja (#191).
- **`sequence = 1` de Mailpit no garantiza capturar el correo.** Un `ir.mail_server` propio con
  `from_filter` coincidente puede ganarle a Mailpit en la selección de servidor de Odoo,
  independientemente de la `sequence`. Ver la sección de `rkd mail` en `CLAUDE.md`/`README.md`.

## Cómo reportar una vulnerabilidad

Por ahora no existe un canal privado dedicado: reportá el hallazgo abriendo un
[issue en GitHub](https://github.com/HDM-soft/rocketdoo/issues) describiendo el problema y su
impacto, sin publicar un exploit funcional en el issue.
