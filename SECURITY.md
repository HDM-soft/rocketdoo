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
| Password de VPS (deploy de instancia) | `.rkd/secrets/instance_{env}.env` | 0600, gitignored, **excluido de `rkd pack`** |
| Password de VPS (deploy de módulos) | `.rkd/secrets/vps_{target}.env` | 0600, gitignored, **excluido de `rkd pack`** |
| `odoo_pg_pass` / `admin_passwd` runtime (instancia) | `.rkd/secrets/instance_{env}_db.env` | 0600, gitignored, **excluido de `rkd pack`** |
| Password del rol PostgreSQL de la instancia | `.rkd/secrets/instance_{env}_db.env` en local; en el VPS `/etc/odoo/odoo.conf` (nativo) o `{env}/odoo_pg_pass` + `{env}/config/odoo.conf` (Docker) | 0600 en local y 0600 el `odoo_pg_pass` del VPS. El conf nativo queda `odoo:` 640; **el del camino Docker queda 644** y lleva este password y el `admin_passwd` — ver riesgos residuales (#248, #250) |
| Password del usuario `admin` de la instancia (nativo y Docker) | `.rkd/secrets/instance_{env}_db.env`, y hasheada en la base | 0600 en local; se fija sólo al crear la base, viaja por `stdin` y nunca como argumento (#248, #250) |
| `admin_passwd` (proyecto local) | `config/odoo.conf`, en claro | gitignored, sin permisos restringidos explícitos; **viaja en el ZIP de `rkd pack`** (ver abajo) |
| `admin_passwd` (config de instancia declarada) | `.rkd/instance.yaml`, en claro | gitignored, sin permisos restringidos explícitos, **excluido de `rkd pack`** |
| Targets de deploy (host, usuario, ruta de la clave SSH) | `.rkd/deploy.yaml`, en claro | gitignored y excluido de `rkd pack` desde 3.9.2 |
| Clave de cuenta y certificados de Let's Encrypt | `traefik/certs/acme.json`, que escribe Traefik | 0600 al crearlo; gitignored y excluido de `rkd pack` desde 3.9.2 (#246) |
| Variables de entorno del propio usuario | `.env` en cualquier parte del proyecto, en claro | gitignored por el template generado; excluido de `rkd pack` desde 3.10.0 (#253). `*.env.example` sí viaja |
| Clave SSH privada (build de imagen) | copiada a `.ssh/<clave>` dentro del contexto de build | gitignored (`.ssh/` en `SENSITIVE_ENTRIES`) |
| `SSHPASS` (autenticación por password a un VPS) | variable de entorno del subproceso `sshpass`, nunca en disco | vive solo en memoria del proceso hijo (desde 3.2.1) |
| Token de sesión de la GUI | memoria del proceso `rkd gui` (`app.state.rkd_token`) | efímero, no se persiste en disco |

`rkd scaffold`/`rkd init` generan un `.gitignore` que cubre las rutas marcadas "gitignored"
arriba; `rkd info` avisa si un proyecto existente le falta cobertura.

#### El ZIP de `rkd pack` es un canal de salida, igual que git (#245, 3.9.2)

Hasta la 3.9.1, `rkd pack` excluía únicamente `.ssh/`, así que `.rkd/secrets/`,
`.rkd/instance.yaml` y `.rkd/deploy.yaml` —password del VPS, host, usuario, ruta de la clave—
viajaban dentro del archivo cuyo propósito es entregárselo a otra persona. El verificador del ZIP
sólo buscaba nombres de clave SSH (`id_rsa` y parientes), así que el panel informaba éxito sin
advertencias.

Desde la 3.9.2 la exclusión y el verificador se derivan de la **misma** lista,
`gitignore_manager.SENSITIVE_ENTRIES`: lo que rkd declara secreto queda fuera del ZIP por
defecto, y que algo viaje requiere agregarlo explícitamente a `_SHAREABLE_SECRETS` en
`core/pack.py`. Hoy esa excepción tiene dos entradas, por necesidad del entorno recibido:

- **`odoo_pg_pass`** — `docker-compose.yaml` lo monta como Docker secret; sin él la base no arranca.
- **`config/odoo.conf`** — `Dockerfile.jinja:3` hace `COPY ./config/odoo.conf`; sin él no buildea.

Las dos son credenciales locales del entorno que se está compartiendo a propósito, no de un
servidor remoto. Con todo, **el `admin_passwd` de `config/odoo.conf` viaja en claro**: es el que
el usuario tipeó en `rkd init`, `rkd unpack` no lo rota, y el receptor queda corriendo con el
master password del emisor. Si ese password se reusa en otro lado, conviene cambiarlo antes de
empaquetar.

**Si ya compartiste un ZIP hecho con 3.9.1 o anterior** en un proyecto donde corriste
`rkd instance init` o `rkd deploy init`, esas credenciales salieron de tu máquina y hay que
rotarlas: un archivo enviado no se puede desenviar.

**Los dos caminos laterales, cerrados en #246 (3.9.2).** La exclusión compara la ruta relativa
dentro del proyecto, sin distinguir mayúsculas y aceptando patrones de gitignore, y había dos
formas de llegar al mismo archivo por otro lado. Un **enlace simbólico** a un secreto: `zipfile`
lo sigue y guarda el contenido bajo el nombre del enlace, así que ni la exclusión ni el
verificador lo veían; ahora **ningún enlace simbólico entra al ZIP** y el panel los nombra. Y un
`.rkd/` **anidado** más abajo del árbol, que los wizards podían crear corridos desde un
subdirectorio; ahora la exclusión y el verificador reconocen un `.rkd/` a cualquier profundidad,
y los wizards —CLI y GUI— se niegan a correr fuera de la raíz del proyecto. Un **hard link** a un
secreto del propio proyecto tampoco viaja: los secretos se identifican además por inodo.

**Si compartiste un ZIP hecho con 3.9.1 o anterior desde un proyecto con enlaces o con un `.rkd/`
anidado**, revisá qué contenía: lo mismo que arriba, un archivo enviado no se puede desenviar.

**Lo que esta defensa sigue sin cubrir.** Un **hard link a un archivo de afuera del proyecto**
(`ln ~/.ssh/id_rsa addons/clave`) es, para el sistema de archivos, un archivo más del proyecto:
no se distingue de haberlo copiado a mano, y la exclusión no lo ve. Desde la 3.10.0 el
verificador lo nombra si el archivo es una clave privada en formato PEM, pero eso es un aviso
sobre el ZIP ya escrito y no una exclusión. Copiar un secreto al proyecto, con cualquier nombre,
tiene exactamente el mismo efecto: **lo que el ZIP garantiza es que rkd no filtra lo que rkd
mismo escribió**, no que el proyecto no contenga secretos que alguien puso ahí.

#### El escalón siguiente: lo que pone el usuario (#253, 3.10.0)

#245 y #246 cerraron lo que rkd escribe y los caminos laterales hacia esos mismos archivos. Lo
que sigue es lo que el usuario deja en el proyecto, y son tres cambios:

- **`*.env` es un secreto más.** El `.gitignore` generado ya lo excluía (`*.env`, con
  `!*.env.example`) pero `SENSITIVE_ENTRIES` no lo listaba, así que un `.env` con claves de API
  viajaba en el ZIP y el verificador lo daba por limpio: el mismo hueco de #245 un nivel más
  arriba, con credenciales del usuario en vez de las de rkd. Ahora queda afuera, igual que todo
  lo que haya debajo de un *directorio* con ese nombre, y el panel **nombra** lo que dejó afuera
  —en la CLI y en la GUI— porque es lo que el receptor va a tener que volver a escribir.
  **`*.env.example` sigue viajando**: es la lista de nombres que el receptor necesita para
  escribir el suyo, y la excepción está acotada al patrón que la declara, así que un
  `…env.example` dentro de `.rkd/secrets/` no sale igual. El panel nombra hasta diez archivos y
  cuenta el resto —un `python -m venv .env` deja un par de cientos bajo un nombre que matchea el
  patrón—, y el reporte sigue trayendo la lista completa. Ningún template de rkd lee un `.env`
  —el compose generado no declara `env_file`— así que el entorno recibido levanta lo mismo.
  Sigue la grafía de git: `.env.local`, `.env.production` y `.envrc` **no** terminan en `.env` y
  por lo tanto viajan, exactamente como tampoco los ignora el `.gitignore` generado.
- **El verificador lee, no sólo adivina por el nombre.** Hasta la 3.9.2 reconocía claves privadas
  por los nombres que reparte OpenSSH (`id_rsa` y parientes, cualquier ruta bajo `.ssh/`), así
  que una clave copiada como `notas.txt` pasaba limpia. Ahora busca además el encabezado **PEM**
  (`-----BEGIN … PRIVATE KEY-----`) en los primeros 64 KiB de cada entrada. Sólo PEM: una clave
  de PuTTY (`.ppk`), un bloque PGP o una clave en DER binario no lo tienen y no se detectan.
  **Techo conocido y tomado a propósito:** es una ventana y no el archivo entero, porque el ZIP
  lleva un dump y un filestore de cientos de MB y lo que viaja comprimido adentro de un
  `.tar.gz` no se alcanza leyendo más. Medido sobre un ZIP de 422 MB con 34.000 entradas —el
  tamaño de un proyecto Enterprise real—: 3,2 s contra 1,2 s recorriendo sólo los nombres. Del
  lado del empaquetador, la entrada `*.env` obliga a comparar cada ruta contra un comodín: sobre
  un árbol real de 53.000 entradas, 0,9 s por pasada contra 0,3 s antes, y `rkd pack` hace dos.
  Las dos cifras son chicas al lado de los segundos que lleva comprimir el proyecto y volcar la
  base, pero son costo que antes no estaba.
- **Separadores.** Los nombres dentro de un ZIP usan `/` por especificación y `zipfile` normaliza
  lo que escribe, pero el verificador es justamente el chequeo que tiene que valer para un
  archivo armado por otra herramienta. El plegado de `\` vive en la función que comparan **las
  dos** puntas, el empaquetador y el verificador: que una excluyera y la otra condenara el mismo
  archivo es el bug que esta épica viene cerrando, no una variante nueva de él.

**Los dos hallazgos no son el mismo hallazgo, y el panel no los mezcla.** Que aparezca adentro
algo que rkd declara secreto es un defecto, y se informa como hasta ahora: aviso rojo en la CLI,
`stderr` en la GUI. Que un archivo *lea* como una clave privada es una pregunta para el usuario,
y tiene respuestas legítimas: medido sobre los proyectos Enterprise de este equipo, **cinco
archivos por pack** vienen de las localizaciones del propio Odoo —`l10n_ar_edi` (dos),
`l10n_cl_edi` y `l10n_cl_edi_stock` (plantillas CAF) y `l10n_co_dian`—, que traen claves de
prueba en `demo/` y `tests/`. Con un solo aviso rojo para las dos cosas, un proyecto Enterprise
lo vería en cada pack y dejaría de leerlo justo para el día que nombre un `notas.txt` de verdad.
Por eso va en un bloque propio y más tranquilo, que dice que rkd no escribió esos archivos y que
hay que mirar los que sean propios.

El verificador **avisa; no borra ni cancela el pack**. El archivo ya está escrito cuando el
chequeo corre, y lo que corresponde ahí es que decida el usuario. Un *miembro* que no se puede
leer —dañado, cifrado— tampoco lo cancela: se saltea, porque ninguno de los otros chequeos
depende de él. Si lo ilegible fuera el archivo entero, en cambio, el comando sí falla: eso
significaría que el ZIP que rkd acaba de escribir está roto, y callarlo sería peor que el error.

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
- **El `odoo.conf` de una instancia dockerizada queda legible para todo el VPS.** Lleva el
  `admin_passwd` y, desde #250, el password del rol PostgreSQL, con los 644 que deja `rsync`. No se
  restringe porque el contenedor lo monta y lo lee como uid 100: un `chmod 600` impide el arranque.
  Declararlo ahí mantiene el secreto fuera del `argv` del proceso, pero es un **canje y no una
  reducción**: la misma audiencia —cualquier usuario del VPS— lo alcanza con `cat`, y el archivo
  persiste con los contenedores apagados, cosa que el `argv` no hace. Cerrarlo de verdad pide que el
  deploy maneje el dueño del archivo, que hoy no hace.
- **Quien puede leer el filesystem o el entorno del proceso ya tiene las credenciales.**
  `config/odoo.conf` y `.rkd/instance.yaml` guardan `admin_passwd` en claro, sin permisos
  restringidos explícitos (a diferencia de `.rkd/secrets/`, que sí usa 0600). El token de la GUI
  y el manejo de `SSHPASS` no protegen contra un atacante que ya tiene acceso de lectura al
  proyecto o al entorno del proceso `rkd gui`.
- **Valores de configuración interpolados en comandos remotos se tratan como entrada
  confiable.** `remote_path`, `container_name` y `service_name` (entre otros) del propio
  `deploy.yaml`/`instance.yaml` del usuario se interpolan en comandos SSH sin sanitizar; están
  fuera de alcance porque son configuración que el propio usuario escribe, no entrada de un
  tercero. La excepción son `db_user` y `db_name` del deploy nativo, que
  desde #248 se validan como identificadores PostgreSQL antes de que se arme un solo comando:
  ahí el valor entra además en una sentencia SQL, y un nombre con comillas se escapa de ella
  sin necesidad de tocar el shell.
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
