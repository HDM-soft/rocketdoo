import argparse
import subprocess

# Define la versión del paquete
VERSION = "1.0"

def main():
    # Configura el analizador de argumentos
    parser = argparse.ArgumentParser(description="Rocketdoo CLI")
    parser.add_argument(
        '--version', action='store_true', help="Muestra la versión de Rocketdoo"
    )
    
    # Parsea los argumentos
    args = parser.parse_args()

    # Si se pasa el argumento --version, muestra la versión y sale
    if args.version:
        print(f"Rocketdoo version {VERSION}")
        return

    # Ejecuta Copier si no se pasa --version
    subprocess.run(["copier", "copy", "./", "./", "--trust"], check=True)
