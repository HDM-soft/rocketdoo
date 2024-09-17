# Importamos las librerías necesarias
import sys
import os
import yaml


# Preguntar si el usuario quiere usar gitman
usar_gitman = input("¿Desea utilizar gitman? (s/n): ").strip().lower()

if usar_gitman != "s":
    if os.path.exists("gitman.yml"):
        os.remove("gitman.yml")
    print("Sin cambios en gitman")
    sys.exit(0)  # Finalizar el script

# Definimos la estructura inicial del archivo de configuración
config = {
    "location": "external_addons",
    "sources": [],
    "default_group": "",
    "groups": [],
}


def get_input(prompt, required=True):
    """Función que recibe un input del usuario, con opción de no ser obligatorio"""
    value = input(prompt)
    while required and not value:
        value = input("Este campo no puede estar vacío. " + prompt)
    return value


def agregar_repositorio():
    """Función para agregar un nuevo repositorio"""
    repo_info = {
        "repo": get_input("Ingresa el repositorio (repo): "),
        "name": get_input("Ingresa el nombre (name): "),
        "rev": get_input("Ingresa la revisión (branch): "),
        "type": "git",  # Se mantiene fijo
        "params": get_input(
            "Ingresa los parámetros (params) (opcional, presiona Enter para omitir): ",
            required=False,
        ),
        "sparse_paths": [
            get_input(
                "Ingresa el sparse_path (opcional, presiona Enter para omitir): ",
                required=False,
            )
        ],
        "links": [],
        "scripts": [
            "sh /usr/lib/python3/dist-packages/odoo/install_dependencies.sh"
        ],  # Se mantiene fijo
    }

    # Preguntamos si quiere agregar algún link opcional
    while True:
        link = get_input(
            "Ingresa un link (opcional, presiona Enter para omitir): ", required=False
        )
        if link:
            repo_info["links"].append(link)
        else:
            break

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
