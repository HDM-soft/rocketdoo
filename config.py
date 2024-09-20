# Importamos las librerías necesarias
import sys
import os
import yaml


# # Preguntar si el usuario quiere usar gitman
# usar_gitman = input("¿Desea utilizar gitman? (s/n): ").strip().lower()

dockerfile_path = "Dockerfile"

# Líneas SSH a buscar y descomentar/comentar según sea necesario
ssh_lines = [
    "#RUN mkdir -p /root/.ssh\n",
    "#COPY ./.ssh/rsa /root/.ssh/id_rsa\n",  # Se reemplazará "rsa" con el nombre correcto de la llave
    "#RUN chmod 700 /root/.ssh/id_rsa\n",
    '#RUN echo "StrictHostKeyChecking no" >> /root/.ssh/config\n',
]


def get_input(prompt, required=True):
    """Función que recibe un input del usuario, con opción de no ser obligatorio"""
    value = input(prompt)
    while required and not value:
        value = input("Este campo no puede estar vacío. " + prompt)
    return value


def manejar_ssh(repos_privados, dockerfile_path):
    """Descomenta o comenta las líneas relacionadas con SSH en el Dockerfile, y reemplaza 'rsa' por la llave privada correcta."""
    # Buscar la clave privada en la carpeta ./.ssh
    ssh_folder = "./.ssh"
    try:
        ssh_keys = [
            f
            for f in os.listdir(ssh_folder)
            if os.path.isfile(os.path.join(ssh_folder, f))
        ]
        if not ssh_keys:
            print(f"No se encontraron claves privadas en {ssh_folder}.")
            return

        # Preguntar al usuario qué clave usar si hay más de una
        ssh_key = ssh_keys[0]
        if len(ssh_keys) > 1:
            print("Se encontraron las siguientes claves privadas en ./.ssh:")
            for i, key in enumerate(ssh_keys):
                print(f"{i + 1}. {key}")
            key_index = (
                int(get_input("Selecciona el número de la clave que deseas usar: ")) - 1
            )
            ssh_key = ssh_keys[key_index]

        # Modificar el Dockerfile
        with open(dockerfile_path, "r") as file:
            lines = file.readlines()

        with open(dockerfile_path, "w") as file:
            for line in lines:
                # Si el usuario quiere usar repos privados, descomentamos las líneas relacionadas con SSH
                if repos_privados and any(
                    ssh_line.strip().lstrip("# ") in line for ssh_line in ssh_lines
                ):
                    # Solo descomentar líneas comentadas y modificar "rsa" por el nombre correcto de la llave
                    if line.startswith("#RUN mkdir -p /root/.ssh"):
                        file.write(line.lstrip("# "))  # Descomentar
                    elif "COPY ./.ssh/rsa" in line:
                        file.write(
                            line.replace("rsa", ssh_key).lstrip("# ")
                        )  # Descomentar y reemplazar
                    elif "RUN chmod 700 /root/.ssh/id_rsa" in line:
                        file.write(
                            line.replace("id_rsa", ssh_key).lstrip("# ")
                        )  # Descomentar y reemplazar
                    elif '#RUN echo "StrictHostKeyChecking no"' in line:
                        file.write(line.lstrip("# "))  # Descomentar
                    else:
                        file.write(line)
                # Si no quiere usar repos privados, mantener o agregar comentarios
                elif not repos_privados and any(
                    ssh_line.strip().lstrip("# ") in line for ssh_line in ssh_lines
                ):
                    file.write(f"# {line.lstrip('# ')}")  # Asegurar que esté comentada
                else:
                    file.write(line)

        print(
            f"Líneas relacionadas con SSH {'descomentadas' if repos_privados else 'comentadas'} en {dockerfile_path}."
        )

    except Exception as e:
        print(f"Error al manejar las claves SSH: {e}")


def comentar_lineas():
    """Comenta las líneas específicas en el Dockerfile."""
    copy_line = "COPY ./gitman.yml /usr/lib/python3/dist-packages/odoo/\n"
    gitman_line = "RUN gitman install -r /usr/lib/python3/dist-packages/odoo/\n"

    try:
        with open(dockerfile_path, "r") as file:
            lines = file.readlines()

        with open(dockerfile_path, "w") as file:
            for line in lines:
                if line == copy_line or line == gitman_line:
                    file.write(f"# {line}")
                else:
                    file.write(line)

        print("Líneas comentadas correctamente en el Dockerfile.")

    except FileNotFoundError:
        print(f"El archivo {dockerfile_path} no se encontró.")
    except Exception as e:
        print(f"Error inesperado: {e}")


def manejar_claves_ssh():
    """Descomentar las líneas para manejar claves SSH privadas en el Dockerfile."""
    try:
        # Obtener el nombre de la clave privada en la carpeta ./.ssh
        ssh_folder = "./.ssh"
        private_key_name = ""

        # Verificar si hay archivos en la carpeta ./.ssh
        if os.path.exists(ssh_folder) and os.path.isdir(ssh_folder):
            files = os.listdir(ssh_folder)
            private_key_name = next(
                (f for f in files if not f.startswith(".")), None
            )  # Evitar archivos ocultos

        if not private_key_name:
            print("No se encontró ninguna clave privada en la carpeta ./.ssh")
            return

        # Modificar el Dockerfile
        dockerfile_path = "Dockerfile"
        copy_ssh_line = (
            f"COPY ./.ssh/{private_key_name} /root/.ssh/id_{private_key_name}\n"
        )
        chmod_ssh_line = f"RUN chmod 700 /root/.ssh/id_{private_key_name}\n"

        with open(dockerfile_path, "r") as file:
            lines = file.readlines()

        # Verificar si ya existen las líneas, y descomentar si es necesario
        new_lines = []
        for line in lines:
            if line.strip() == "#RUN mkdir -p /root/.ssh":
                new_lines.append("RUN mkdir -p /root/.ssh\n")
            elif line.strip() == "#COPY ./.ssh/rsa /root/.ssh/id_rsa":
                new_lines.append(copy_ssh_line)
            elif line.strip() == "#RUN chmod 700 /root/.ssh/id_rsa":
                new_lines.append(chmod_ssh_line)
            else:
                new_lines.append(line)

        with open(dockerfile_path, "w") as file:
            file.writelines(new_lines)

        print("Se ha actualizado el Dockerfile con las claves SSH privadas.")
    except Exception as e:
        print(f"Error al manejar las claves SSH: {e}")


# Preguntar si el usuario quiere usar repositorios privados
usar_repos_privados = (
    input("¿Desea utilizar repositorios privados? (s/n): ").strip().lower()
)
if usar_repos_privados == "s":
    manejar_claves_ssh()
else:
    print("No se utilizaran repositorios privados")

# Manejar SSH en el Dockerfile según la respuesta del usuario
manejar_ssh(usar_repos_privados == "s", dockerfile_path)

# Preguntar si el usuario quiere usar gitman
usar_gitman = input("¿Desea utilizar gitman? (s/n): ").strip().lower()

if usar_gitman != "s":
    if os.path.exists("gitman.yml"):
        os.remove("gitman.yml")

    print("Sin cambios en gitman. Comentando líneas del Dockerfile...")
    comentar_lineas()
    sys.exit(0)


# Aquí seguiría el resto de tu código para configurar Gitman y modificar odoo.conf...

# Definimos la estructura inicial del archivo de configuración
config = {
    "location": "external_addons",
    "sources": [],
    "default_group": "",
    "groups": [],
}


def agregar_repositorio():
    """Función para agregar un nuevo repositorio"""
    repo_info = {
        "repo": get_input("Ingresa el repositorio (repo): "),
        "name": get_input("Ingresa el nombre (name): "),
        "rev": get_input("Ingresa la revisión (branch): "),
        "type": "git",  # Se mantiene fijo,
        "scripts": [
            "sh /usr/lib/python3/dist-packages/odoo/install_dependencies.sh"
        ],  # Se mantiene fijo
    }

    # Agregamos la información del repositorio a la lista de sources
    config["sources"].append(repo_info)


# Función para modificar el archivo odoo.conf en la carpeta config
def modificar_odoo_conf():
    """Modifica la línea addons_path en config/odoo.conf agregando los nuevos repositorios"""
    try:
        # Ruta al archivo odoo.conf en la carpeta config
        odoo_conf_path = os.path.join("config", "odoo.conf")

        # Verificación de la existencia del archivo odoo.conf
        if not os.path.exists(odoo_conf_path):
            raise FileNotFoundError(f"El archivo {odoo_conf_path} no existe.")

        # Verificamos si tenemos permisos de lectura y escritura
        if not os.access(odoo_conf_path, os.R_OK):
            raise PermissionError(
                f"No se puede leer el archivo {odoo_conf_path}. Verifica los permisos."
            )
        if not os.access(odoo_conf_path, os.W_OK):
            raise PermissionError(
                f"No se puede escribir en el archivo {odoo_conf_path}. Verifica los permisos."
            )

        print(f"Modificando el archivo {odoo_conf_path}...")

        # Leemos el archivo gitman.yml para obtener los nombres
        with open("gitman.yml", "r") as file:
            gitman_data = yaml.safe_load(file)

        # Extraemos los valores de 'name' de cada repositorio en sources, asegurándonos de no incluir vacíos
        nombres_repositorios = [
            repo["name"] for repo in gitman_data["sources"] if repo["name"]
        ]
        print(f"Repositorios extraídos: {nombres_repositorios}")

        # Si no hay nombres de repositorios, no hacemos nada
        if not nombres_repositorios:
            print("No se encontraron repositorios para agregar.")
            return

        # Creamos la nueva cadena para addons_path con las nuevas rutas
        nuevas_rutas = ",".join(
            [
                f"/usr/lib/python3/dist-packages/odoo/external_addons/{nombre}"
                for nombre in nombres_repositorios
            ]
        )

        # Leemos el archivo odoo.conf
        with open(odoo_conf_path, "r") as file:
            lines = file.readlines()

        # Buscamos la línea que contiene addons_path
        addons_path_encontrado = False
        for i, line in enumerate(lines):
            if line.startswith("addons_path ="):
                # Añadimos las nuevas rutas a la línea existente, si no están ya
                linea_actual = line.strip().split(" = ")[1]
                lineas_rutas_existentes = linea_actual.split(",")

                # Añadimos las nuevas rutas a las existentes, si no están ya
                rutas_actualizadas = lineas_rutas_existentes + [
                    ruta
                    for ruta in nuevas_rutas.split(",")
                    if ruta not in lineas_rutas_existentes
                ]
                lines[i] = f"addons_path = {','.join(rutas_actualizadas)}\n"
                addons_path_encontrado = True
                print(f"Línea addons_path modificada: {lines[i]}")
                break

        # Si no se encuentra addons_path, lo añadimos al final
        if not addons_path_encontrado:
            lines.append(f"addons_path = {nuevas_rutas}\n")
            print("Se agregó una nueva línea addons_path.")

        # Guardamos los cambios en el archivo odoo.conf
        with open(odoo_conf_path, "w") as file:
            file.writelines(lines)

        print("Archivo odoo.conf actualizado exitosamente.")

    except FileNotFoundError as fnf_error:
        print(fnf_error)
    except PermissionError as perm_error:
        print(perm_error)
    except Exception as e:
        print(f"Error inesperado al modificar odoo.conf: {e}")


# Ciclo principal para agregar repositorios
while True:
    agregar_repositorio()

    # Preguntamos si se quiere agregar otro repositorio
    agregar_mas = input("¿Deseas agregar otro repositorio? (s/n): ").strip().lower()

    # Validamos la respuesta
    if agregar_mas != "s":
        print("Finalizó la configuración de gitman.")
        break

# Guardamos el archivo de configuración en formato YAML
with open("gitman.yml", "w") as file:
    yaml.dump(config, file, default_flow_style=False, sort_keys=False)

print("Archivo gitman.yml generado exitosamente.")

# Llamamos a la función para modificar odoo.conf
modificar_odoo_conf()
