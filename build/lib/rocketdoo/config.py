import sys
import os
import re
import yaml
import shutil


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

            print("Environment prepared for the Enterprise edition. Be sure to upload your Enterprise folder to the root of the project.")
        except FileNotFoundError:
            print(f"The file {docker_compose_path} was not found.")
        except Exception as e:
            print(f"Error modifying {docker_compose_path}: {e}")

# Function to modify the odoo.conf file
def modificar_odoo_conf(edicion):
    odoo_conf_path = os.path.join("config", "odoo.conf")
    
    if edicion.lower() == 'ee':
        # Ensure that the file exists
        if not os.path.exists(odoo_conf_path):
            print(f"The file {odoo_conf_path} does not exist.")
            return

        with open(odoo_conf_path, 'r') as file:
            contenido = file.read()

        # Add enterprise path to addons_path
        addons_path = "/usr/lib/python3/dist-packages/odoo/enterprise"
        contenido_modificado = re.sub(r'(addons_path\s*=\s*)(.*)', r'\1\2,{}'.format(addons_path), contenido)

        with open(odoo_conf_path, 'w') as file:
            file.write(contenido_modificado)

        print("Modified odoo.conf file to include the Enterprise path.")

# Ask for the odoo edition
edicion = input("In which Odoo edition will you develop? Community or Enterprise (ce/ee): ").strip().lower()

# Apply modifications if Enterprise edition
if edicion == 'ee':
    modificar_docker_compose(edicion)
    modificar_odoo_conf(edicion)
else:
    print("Community Edition Selected")
    
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
        value = input("This field can not empty " + prompt)
    return value



def manejar_ssh(repos_privados, dockerfile_path):
    """Maneja las claves SSH según si se utilizan repositorios privados."""
    if not repos_privados:
        print("No private repositories will be used. SSH keys will not be modified.")
        return

    ssh_folder = os.path.expanduser("~/.ssh")  # Carpeta del usuario
    build_context_ssh_folder = os.path.join(os.path.dirname(dockerfile_path), ".ssh")  # Contexto de construcción

    try:
        # Buscar claves privadas en ~/.ssh
        if not os.path.exists(ssh_folder):
            print(f"The {ssh_folder} folder was not found. Make sure you have SSH keys configured.")
            return

        ssh_keys = [
            f for f in os.listdir(ssh_folder)
            if os.path.isfile(os.path.join(ssh_folder, f)) and not f.endswith('.pub')
        ]

        if not ssh_keys:
            print(f"No private keys were found in {ssh_folder}.")
            return

        print("The following private keys were found in ~/.ssh:")
        for i, key in enumerate(ssh_keys):
            print(f"{i + 1}. {key}")

        key_index = int(get_input("Selecciona el número de la clave que deseas usar: ")) - 1
        selected_key = ssh_keys[key_index]
        print(f"You have selected the key: {selected_key}")

        # Crear la carpeta .ssh en el contexto de construcción si no existe
        if not os.path.exists(build_context_ssh_folder):
            os.makedirs(build_context_ssh_folder)

        # Copiar la clave seleccionada al contexto de construcción
        source_key_path = os.path.join(ssh_folder, selected_key)
        dest_key_path = os.path.join(build_context_ssh_folder, selected_key)
        shutil.copy(source_key_path, dest_key_path)

        print(f"Key {selected_key} copied to the construction context: {dest_key_path}")

        # Modificar el Dockerfile para usar la clave copiada
        with open(dockerfile_path, "r") as file:
            lines = file.readlines()

        with open(dockerfile_path, "w") as file:
            for line in lines:
                if line.startswith("#RUN mkdir -p /root/.ssh"):
                    file.write("RUN mkdir -p /root/.ssh\n")
                elif "#COPY ./.ssh/rsa" in line:
                    file.write(
                        f"COPY ./.ssh/{selected_key} /root/.ssh/{selected_key}\n"
                    )
                elif "#RUN chmod 700 /root/.ssh/id_rsa" in line:
                    file.write(
                        f"RUN chmod 700 /root/.ssh/{selected_key}\n"
                    )
                elif '#RUN echo "StrictHostKeyChecking no"' in line:
                    file.write('RUN echo "StrictHostKeyChecking no" >> /root/.ssh/config\n')
                else:
                    file.write(line)

        print(f"Dockerfile has been updated to use the key {selected_key}.")

    except IndexError:
        print("Invalid selection. Please try again.")
    except Exception as e:
        print(f"Error handling SSH keys: {e}")



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

        print("Lines commented correctly in the Dockerfile.")

    except FileNotFoundError:
        print(f"The file {dockerfile_path} was not found.")
    except Exception as e:
        print(f"Unexpected error: {e}")


# Function to validate user input
def obtener_respuesta_si_no(mensaje):
    while True:
        respuesta = input(mensaje).strip().lower()
        if respuesta in ["y", "n"]:
            return respuesta
        else:
            print("Please enter 'y' for yes or 'n' for no.")
    
    
# Ask if the user wants to use private repositories
usar_repos_privados = input("Do you want to use private repositories (y/n): ").strip().lower()


# Handle SSH in the Dockerfile based on user response
manejar_ssh(usar_repos_privados == "y", dockerfile_path)

# Ask if the user wants to user gitman with public repositorie
usar_gitman = input("Do you want to use third-party repositories (y/n): ").strip().lower()

if usar_gitman != "y":
    if os.path.exists("gitman.yml"):
        os.remove("gitman.yml")

    print("Not use third-party repositories. Commenting lines of the Dockerfile...")
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
        "repo": get_input("Write or paste the repository (URL): "),
        "name": get_input("Write a name (name): "),
        "rev": get_input("Write revision/version (branch): "),
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
            raise FileNotFoundError(f"The file {odoo_conf_path} does not exist.")

        # We check if we have read and write permissions
        if not os.access(odoo_conf_path, os.R_OK):
            raise PermissionError(
                f"Cannot read the {odoo_conf_path} file. Check the permissions."
            )
        if not os.access(odoo_conf_path, os.W_OK):
            raise PermissionError(
                f"Cannot write to the {odoo_conf_path} file. Check the permissions."
            )

        print(f"Modifying the {odoo_conf_path} file...")

        # We read the gitman.yml file to obtain the names
        with open("gitman.yml", "r") as file:
            gitman_data = yaml.safe_load(file)

        # We extract the values of 'name' from each repository in sources, making sure not to include empty ones.
        nombres_repositorios = [
            repo["name"] for repo in gitman_data["sources"] if repo["name"]
        ]
        print(f"Extracted repositories: {nombres_repositorios}")

        # If there are no repository names, we do nothing.
        if not nombres_repositorios:
            print("No repositories were found to add.")
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
                print(f"addons_path line modified: {lines[i]}")
                break

        # If addons_path is not found, we add it at the end
        if not addons_path_encontrado:
            lines.append(f"addons_path = {nuevas_rutas}\n")
            print("A new addons_path line was added.")

        # Save the changes in the odoo.conf file.
        with open(odoo_conf_path, "w") as file:
            file.writelines(lines)

        print("odoo.conf file successfully updated.")

    except FileNotFoundError as fnf_error:
        print(fnf_error)
    except PermissionError as perm_error:
        print(perm_error)
    except Exception as e:
        print(f"Unexpected error when modifying odoo.conf: {e}")


# Main to add repositories
while True:
    agregar_repositorio()

    # We ask if the user wants to add more repositories
    agregar_mas = input("Do you want to add another repository? (y/n): ").strip().lower()

    # Validating answer
    if agregar_mas != "y":
        print("Finished configuring gitman to third-party repository.")
        break

# Save the gitman files and their configurations on a YAML format
with open("gitman.yml", "w") as file:
    yaml.dump(config, file, default_flow_style=False, sort_keys=False)

print("File gitman.yml generated successfully.")

# Calling function to modify odoo.conf
modificar_odoo_conf()
