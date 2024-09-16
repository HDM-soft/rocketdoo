# Importamos las librerías necesarias
import sys
import yaml

# Preguntar si el usuario quiere usar gitman
usar_gitman = input("¿Desea utilizar gitman? (s/n): ").strip().lower()

if usar_gitman != 's':
    print("Sin cambios en gitman")
    sys.exit(0)  # Finalizar el script
    
# Definimos la estructura inicial del archivo de configuración
config = {
    "location": "external_addons",
    "sources": [],
    "default_group": "",
    "groups": []
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
        "rev": get_input("Ingresa la revisión (rev): "),
        "type": "git",  # Se mantiene fijo
        "params": get_input("Ingresa los parámetros (params) (opcional, presiona Enter para omitir): ", required=False),
        "sparse_paths": [get_input("Ingresa el sparse_path (opcional, presiona Enter para omitir): ", required=False)],
        "links": [],
        "scripts": ["sh /usr/lib/python3/dist-packages/odoo/install_dependencies.sh"]  # Se mantiene fijo
    }

    # Preguntamos si quiere agregar algún link opcional
    while True:
        link = get_input("Ingresa un link (opcional, presiona Enter para omitir): ", required=False)
        if link:
            repo_info["links"].append(link)
        else:
            break
    
    # Agregamos la información del repositorio a la lista de sources
    config["sources"].append(repo_info)

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