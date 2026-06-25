import argparse
import subprocess
import signal
import sys

# Define la versión del paquete
VERSION = "1.3.1"

# Maneja la interrupción con Ctrl+C
def signal_handler(sig, frame):
    print("\nRocketdoo has been cancelled by the user.")
    sys.exit(0)

# Registra el manejador de señal para SIGINT (Ctrl+C)
signal.signal(signal.SIGINT, signal_handler)

def main():
    # Configura el analizador de argumentos
    parser = argparse.ArgumentParser(description="Rocketdoo CLI")
    parser.add_argument(
        '--version', action='store_true', help="Show Rocketdoo version"
    )
    
    # Parsea los argumentos
    args = parser.parse_args()

    # Si se pasa el argumento --version, muestra la versión y sale
    if args.version:
        print(f"Rocketdoo version {VERSION}")
        return

    # Ejecuta Copier si no se pasa --version
    try:
        subprocess.run(["copier", "copy", "./", "./", "--trust"])
    except KeyboardInterrupt:
        # Este bloque captura el Ctrl+C y permite salir limpiamente
        print("\nRocketdoo has been cancelled by the user.")
    except Exception as e:
        # Captura cualquier otro error, sin mostrar traceback detallado
        print(f"\nUnexpected Error: {e}")
    finally:
        sys.exit(0)


