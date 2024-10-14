import argparse
import subprocess
import signal
import sys

# Define la versión del paquete
VERSION = "1.0"

# Maneja la interrupción con Ctrl+C
def signal_handler(sig, frame):
    print("\nRocketdoo ha sido cancelado.")
    sys.exit(0)

# Registra el manejador de señal para SIGINT (Ctrl+C)
signal.signal(signal.SIGINT, signal_handler)

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
