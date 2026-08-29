# Esquemas de configuración

Definición única de los archivos de configuración de Rocketdoo, compartida por
la CLI, la GUI/API y los deployers. Antes de estos modelos cada consumidor
tenía su propia idea del esquema, y la GUI y los deployers no coincidían: los
`.rkd/instance.yaml` escritos desde la GUI fallaban con `KeyError: 'vps'` al
desplegar.

## `.rkd/instance.yaml` — `InstanceConfig`

Forma canónica, la que se escribe siempre:

```yaml
environments:
  stage:
    type: docker              # docker | native
    vps:
      host: vps.example.com
      port: 22
      user: ubuntu
      auth_method: ssh_key    # ssh_key | password
      ssh_key: ~/.ssh/id_ed25519
      password: ""            # literal o "${VAR}"; excluyente con ssh_key
    odoo_version: "17.0"
    odoo_tag: "17.0"          # por defecto, igual a odoo_version
    domain: stage.example.com
    traefik_email: ops@example.com
    db_version: "16"
    db_user: odoo_stage
    admin_passwd: "..."
    use_enterprise: false
    use_gitman: false
    gitman_config: ""
    pg_profile: small         # small | medium | large
    remote_path: /opt/odoo-stage
```

### Formas que el lector acepta

El lector es tolerante para no romper proyectos existentes. Estas tres entradas
producen el mismo modelo, y al guardarse quedan en la forma canónica:

| Forma | Origen |
|---|---|
| Conexión anidada bajo `vps:` | Wizard `rkd instance init` |
| Campos de conexión planos en el entorno | GUI anterior a v3.2 |
| Sin la clave `environments:` de primer nivel | Lector de la GUI anterior a v3.2 |

También se aceptan dos nombres antiguos de la GUI: `password_ref` → `password`
y `email` → `traefik_email`.

Campos desconocidos se rechazan (`extra="forbid"`): un `odoo_verison` mal
escrito falla al validar en vez de ignorarse en silencio.

## `.rkd/deploy.yaml` — `DeployConfig`

```yaml
modules:
  auto_detect: true
  base_path: addons
  exclude_patterns: ["*/tests/*", "*/__pycache__/*"]
targets:
  production:
    type: vps                 # vps | odoo-sh
    deployment_type: docker   # docker | native   (sólo type: vps)
    connection:               # sólo type: vps
      host: vps.example.com
      user: odoo
      port: 22
      ssh_key: ~/.ssh/id_rsa
      password: ""            # excluyente con ssh_key
  staging:
    type: odoo-sh
    odoo_sh:
      project_id: abc123
      branch: production
```

`validation_errors()` devuelve mensajes que nombran el campo
(`Target 'production': missing connection.host`), no la regla que falló.

## Perfiles por versión de Odoo — `Profile`

`get_profile("17.0")` describe lo que es fijo para cada versión soportada
(15.0–19.0). Las imágenes `odoo:` abarcan **tres bases distintas con tres
versiones de pip**, que es la razón por la que un flag de pip puede funcionar
en una y romper en otra (issue #152):

| Odoo | Base | pip | `--break-system-packages` |
|---|---|---|---|
| 15.0, 16.0 | debian-bullseye | 20.3 | no |
| 17.0 | ubuntu-jammy | 22.0 | no |
| 18.0, 19.0 | ubuntu-noble | 24.0 | sí |

Cualquier código que genere un Dockerfile debería consultar el perfil en vez de
asumir una base.
