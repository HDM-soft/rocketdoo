# Golden paths

Cada archivo `.yaml` de este directorio define una combinación soportada de
Odoo, edición y PostgreSQL. Son la fuente de verdad de qué ofrece
`rkd init --profile` y `rkd profiles list`.

```bash
rkd profiles list              # ver la matriz
rkd profiles show odoo18-ce    # detalle de un perfil
rkd init --profile odoo18-ce   # crear el entorno sin preguntar nada
```

## Política de soporte

| Nivel | Qué significa |
|---|---|
| **golden** | El CI renderiza y construye esta combinación en cada PR de release. Es la que conviene elegir. |
| **best effort** | Está dentro de los requisitos que Odoo declara, y el wizard la ofrece, pero el CI no la construye. Si falla, se acepta el reporte pero no hay garantía. |

Combinaciones golden hoy: `odoo15-ce`, `odoo18-ce`, `odoo19-ee`. Cubren los tres
sistemas base distintos que usan las imágenes (ver abajo) y las dos ediciones.

## Matriz de compatibilidad

Los datos por imagen se leyeron de las imágenes `odoo:` publicadas; los mínimos
de PostgreSQL salen de la documentación de instalación de Odoo.

| Odoo | Base | Python | pip | PostgreSQL mínimo | Recomendado |
|---|---|---|---|---|---|
| 15.0 | debian-bullseye | 3.9 | 20.3.4 | 12 | 14 |
| 16.0 | debian-bullseye | 3.9 | 20.3.4 | 12 | 14 |
| 17.0 | ubuntu-jammy | 3.10 | 22.0.2 | 12 | 15 |
| 18.0 | ubuntu-noble | 3.12 | 24.0 | 12 | 16 |
| 19.0 | ubuntu-noble | 3.12 | 24.0 | **13** | 16 |

Odoo 19 subió el mínimo de PostgreSQL de 12 a 13. Antes de estos perfiles el
wizard ofrecía una lista fija de PostgreSQL 13–16 sin relacionarla con la
versión de Odoo elegida, así que dejaba armar combinaciones no soportadas.

Por qué importan `base` y `pip`: las cinco imágenes abarcan **tres sistemas base
con tres versiones de pip**, y por eso un flag de pip puede andar en una y
romper en otra — es exactamente lo que pasó en la issue #152. Cualquier código
que genere un Dockerfile debería consultar el perfil en lugar de asumir.

### Nota sobre Odoo 19 y AI

Las funciones de AI de Odoo 19 necesitan la extensión `pgvector`, que se
distribuye para PostgreSQL 15 en adelante. `rkd init --profile odoo19-*` avisa
si el perfil usa una versión menor.

## Agregar un perfil

Alcanza con dejar otro `.yaml` en este directorio:

```yaml
name: odoo18-ce-pg17
description: Odoo 18 Community on PostgreSQL 17
odoo_version: "18.0"
edition: Community
db_version: "17"
odoo_port: 8069
vsc_port: 8888
restart: unless-stopped
golden: false
```

La compatibilidad se valida al cargarlo: un `db_version` por debajo del mínimo
de esa versión de Odoo es un error, no una advertencia. Los datos de la imagen
(base, Python, pip, mínimo de PostgreSQL) no se declaran acá porque describen
la imagen publicada, no una decisión del perfil; viven en
`rocketdoo/core/models/profiles.py`.
