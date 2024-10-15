
import sys
import os
import re
import yaml


docker_compose_path = "docker-compose.yml"
dockerfile_path = "Dockerfile"

# Function to modify docker-compose.yml

def modificar_docker_compose(edicion):
    if edicion.lower() == 'ee':
        try:
            # Read docker compose at first
            with open(docker_compose_path, 'r') as file:
                contenido = file.readlines()

            # Modify content memory
            contenido_modificado = []
            for linea in contenido:
                # Check if the line has the specific pattern
                if linea.lstrip().startswith('#- ./enterprise:/usr/lib/python3/dist-packages/odoo/enterprise'):
                    # Remove only the '#' symbol that appears after the spaces, without affecting the indentation.
                    indentacion = len(linea) - len(linea.lstrip())
                    linea = ' ' * indentacion + linea.lstrip().lstrip('#').lstrip()
                    contenido_modificado.append(linea)
                else:
                    contenido_modificado.append(linea)

            # Writes the modified content back to the file
            with open(docker_compose_path, 'w') as file:
                file.writelines(contenido_modificado)

            print("Ambiente preparado para la edición Enterprise. Asegurese de subir a la raiz del proyecto su carpeta Enterprise.")
        except FileNotFoundError:
            print(f"El archivo {docker_compose_path} no se encontró.")
        except Exception as e:
            print(f"Error al modificar {docker_compose_path}: {e}")

# Function to modify the odoo.conf file
def modificar_odoo_conf(edicion):
    odoo_conf_path = os.path.join("config", "odoo.conf")
    
    if edicion.lower() == 'ee':
        # Ensure that the file exists
        if not os.path.exists(odoo_conf_path):
            print(f"El archivo {odoo_conf_path} no existe.")
            return

        with open(odoo_conf_path, 'r') as file:
            contenido = file.read()

        # Add enterprise path to addons_path
        addons_path = "/usr/lib/python3/dist-packages/odoo/enterprise"
        contenido_modificado = re.sub(r'(addons_path\s*=\s*)(.*)', r'\1\2,{}'.format(addons_path), contenido)

        with open(odoo_conf_path, 'w') as file:
            file.write(contenido_modificado)

        print("Archivo odoo.conf modificado para incluir el path de Enterprise.")

# Ask for the odoo edition
edicion = input("¿En qué edición de Odoo va a desarrollar? Community o Enterprise (ce/ee): ").strip().lower()

# Apply modifications if Enterprise edition
if edicion == 'ee':
    modificar_docker_compose(edicion)
    modificar_odoo_conf(edicion)
else:
    print("Edición Community seleccionada")
    
# SSH lines to be searched and uncommented/commented as needed
ssh_lines = [
    "#RUN mkdir -p /root/.ssh\n",
    "#COPY ./.ssh/rsa /root/.ssh/id_rsa\n",  # The “rsa” will be replaced with the correct key name.
    "#RUN chmod 700 /root/.ssh/id_rsa\n",
    '#RUN echo "StrictHostKeyChecking no" >> /root/.ssh/config\n',
]


def get_input(prompt, required=True):
    """Function that receives an input from the user, with the option of not being mandatory."""
    value = input(prompt)
    while required and not value:
        value = input("Este campo no puede estar vacío. " + prompt)
    return value


def manejar_ssh(repos_privados, dockerfile_path):
    """Uncomment or comment out the SSH-related lines in the Dockerfile, and replace 'rsa' with the correct private key."""
    if usar_repos_privados == 's':
        manejar_claves_ssh
    else:
        return
    
    # Search for the private key in the ./.ssh folder.
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

        # Ask the user which password to use if there is more than one.
        ssh_key = ssh_keys[0]
        if len(ssh_keys) > 1:
            print("Se encontraron las siguientes claves privadas en ./.ssh:")
            for i, key in enumerate(ssh_keys):
                print(f"{i + 1}. {key}")
            key_index = (
                int(get_input("Selecciona el número de la clave que deseas usar: ")) - 1
            )
            ssh_key = ssh_keys[key_index]

        # Modify Dockerfile
        with open(dockerfile_path, "r") as file:
            lines = file.readlines()

        with open(dockerfile_path, "w") as file:
            for line in lines:
                # If the user wants to use private repos, we uncomment the lines related to SSH
                if repos_privados and any(
                    ssh_line.strip().lstrip("# ") in line for ssh_line in ssh_lines
                ):
                    # Just uncomment commented lines and modify “rsa” to the correct key name.
                    if line.startswith("#RUN mkdir -p /root/.ssh"):
                        file.write(line.lstrip("# "))  # Descomentar
                    elif "COPY ./.ssh/rsa" in line:
                        file.write(
                            line.replace("rsa", ssh_key).lstrip("# ")
                        )  # Uncomment and replace
                    elif "RUN chmod 700 /root/.ssh/id_rsa" in line:
                        file.write(
                            line.replace("id_rsa", ssh_key).lstrip("# ")
                        )  # Uncomment and replace
                    elif '#RUN echo "StrictHostKeyChecking no"' in line:
                        file.write(line.lstrip("# "))  # Uncomment
                        file.write(line)
                # If you don't want to use private repos, keep or add comments
                elif not repos_privados and any(
                    ssh_line.strip().lstrip("# ") in line for ssh_line in ssh_lines
                ):
                    file.write(f"# {line.lstrip('# ')}")  # Ensure to be commented
                else:
                    file.write(line)

        print(
            f"Líneas relacionadas con SSH {'descomentadas' if repos_privados else 'comentadas'} en {dockerfile_path}."
        )

    except Exception as e:
        print(f"Error al manejar las claves SSH: {e}")


def comentar_lineas():
    """Comment out specific lines in the Dockerfile."""
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
    
    "Manejar claves SSH solo si se desean utilizar repositorios privados."
    try:
        # Get the name of the private key in the folder ./.ssh
        ssh_folder = "./.ssh"
        private_key_name = ""

        # Check for files in the folder ./.ssh
        if os.path.exists(ssh_folder) and os.path.isdir(ssh_folder):
            files = os.listdir(ssh_folder)
            private_key_name = next(
                (f for f in files if not f.startswith(".")), None
            )  # Send hidden folders

        if not private_key_name:
            print("No se encontró ninguna clave privada en la carpeta ./.ssh")
            return

        # Modify Dockefile
        dockerfile_path = "Dockerfile"
        copy_ssh_line = (
            f"COPY ./.ssh/{private_key_name} /root/.ssh/id_{private_key_name}\n"
        )
        chmod_ssh_line = f"RUN chmod 700 /root/.ssh/id_{private_key_name}\n"

        with open(dockerfile_path, "r") as file:
            lines = file.readlines()

        # Check if the lines already exist, and uncomment if necessary.
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

# Function to validate user input
def obtener_respuesta_si_no(mensaje):
    while True:
        respuesta = input(mensaje).strip().lower()
        if respuesta in ["s", "n"]:
            return respuesta
        else:
            print("Por favor, ingrese 's' para sí o 'n' para no.")
    
    
# Ask if the user wants to use private repositories
usar_repos_privados = input("¿Desea utilizar repositorios privados? (s/n): ").strip().lower()


# Handle SSH in the Dockerfile based on user response
manejar_ssh(usar_repos_privados == "s", dockerfile_path)

# Ask if the user wants to user gitman with public repositorie
usar_gitman = input("¿Desea utilizar gitman, con repositorios de terceros? (s/n): ").strip().lower()

if usar_gitman != "s":
    if os.path.exists("gitman.yml"):
        os.remove("gitman.yml")

    print("Sin cambios en gitman. Comentando líneas del Dockerfile...")
    comentar_lineas()
    sys.exit(0)


# Here I would follow the rest of your code to configure Gitman and modify odoo.conf...

# Define the initial structure of the configuration file
config = {
    "location": "external_addons",
    "sources": [],
    "default_group": "",
    "groups": [],
}


def agregar_repositorio():
    """Functions to add new repositories"""
    repo_info = {
        "repo": get_input("Ingresa el repositorio (repo): "),
        "name": get_input("Ingresa el nombre (name): "),
        "rev": get_input("Ingresa la revisión (branch): "),
        "type": "git",  # keeping fixed,
        "scripts": [
            "sh /usr/lib/python3/dist-packages/odoo/install_dependencies.sh"
        ],  # keeping fixed
    }

    # Add the repository information to the source list
    config["sources"].append(repo_info)


# Function to modify the odoo.conf file in the config folder
def modificar_odoo_conf():
    """Modify the addons_path line in config/odoo.conf by adding the new repositories"""
    try:
        # Path to the odoo.conf file in the config folder
        odoo_conf_path = os.path.join("config", "odoo.conf")

        # Verifying the existence of the odoo.conf file
        if not os.path.exists(odoo_conf_path):
            raise FileNotFoundError(f"El archivo {odoo_conf_path} no existe.")

        # We check if we have read and write permissions
        if not os.access(odoo_conf_path, os.R_OK):
            raise PermissionError(
                f"No se puede leer el archivo {odoo_conf_path}. Verifica los permisos."
            )
        if not os.access(odoo_conf_path, os.W_OK):
            raise PermissionError(
                f"No se puede escribir en el archivo {odoo_conf_path}. Verifica los permisos."
            )

        print(f"Modificando el archivo {odoo_conf_path}...")

        # We read the gitman.yml file to obtain the names
        with open("gitman.yml", "r") as file:
            gitman_data = yaml.safe_load(file)

        # We extract the values of 'name' from each repository in sources, making sure not to include empty ones.
        nombres_repositorios = [
            repo["name"] for repo in gitman_data["sources"] if repo["name"]
        ]
        print(f"Repositorios extraídos: {nombres_repositorios}")

        # If there are no repository names, we do nothing.
        if not nombres_repositorios:
            print("No se encontraron repositorios para agregar.")
            return

        # We create the new string for addons_path with the new paths
        nuevas_rutas = ",".join(
            [
                f"/usr/lib/python3/dist-packages/odoo/external_addons/{nombre}"
                for nombre in nombres_repositorios
            ]
        )

        # Read the odoo.conf file
        with open(odoo_conf_path, "r") as file:
            lines = file.readlines()

        # We look for the line containing addons_path
        addons_path_encontrado = False
        for i, line in enumerate(lines):
            if line.startswith("addons_path ="):
                # We add the new routes to the existing line, if they are not already there.
                linea_actual = line.strip().split(" = ")[1]
                lineas_rutas_existentes = linea_actual.split(",")

                # We add the new routes to the existing ones, if they are not already there.
                rutas_actualizadas = lineas_rutas_existentes + [
                    ruta
                    for ruta in nuevas_rutas.split(",")
                    if ruta not in lineas_rutas_existentes
                ]
                lines[i] = f"addons_path = {','.join(rutas_actualizadas)}\n"
                addons_path_encontrado = True
                print(f"Línea addons_path modificada: {lines[i]}")
                break

        # If addons_path is not found, we add it at the end
        if not addons_path_encontrado:
            lines.append(f"addons_path = {nuevas_rutas}\n")
            print("Se agregó una nueva línea addons_path.")

        # Save the changes in the odoo.conf file.
        with open(odoo_conf_path, "w") as file:
            file.writelines(lines)

        print("Archivo odoo.conf actualizado exitosamente.")

    except FileNotFoundError as fnf_error:
        print(fnf_error)
    except PermissionError as perm_error:
        print(perm_error)
    except Exception as e:
        print(f"Error inesperado al modificar odoo.conf: {e}")


# Main to add repositories
while True:
    agregar_repositorio()

    # We ask if the user wants to add more repositories
    agregar_mas = input("¿Deseas agregar otro repositorio? (s/n): ").strip().lower()

    # Validating answer
    if agregar_mas != "s":
        print("Finalizó la configuración de gitman.")
        break

# Save the gitman files and their configurations on a YAML format
with open("gitman.yml", "w") as file:
    yaml.dump(config, file, default_flow_style=False, sort_keys=False)

print("Archivo gitman.yml generado exitosamente.")

# Calling function to modify odoo.conf
modificar_odoo_conf()
