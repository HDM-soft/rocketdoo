#!/bin/bash

file_to_check="$PWD/requirements.txt"

if [ -f "$file_to_check" ]; then
    # Verificar si el archivo tiene contenido (ignorando líneas vacías y comentarios)
    if grep -q '^[^#[:space:]]' "$file_to_check"; then
        echo "The file $file_to_check exists. Installing python dependencies"
        
        # Detectar la versión de pip para decidir si usar --break-system-packages
        pip_version=$(pip --version | awk '{print $2}')
        pip_major=$(echo "$pip_version" | cut -d. -f1)
        pip_minor=$(echo "$pip_version" | cut -d. -f2)
        
        # Usar --break-system-packages solo si pip >= 22.1
        if [ "$pip_major" -gt 22 ] || ([ "$pip_major" -eq 22 ] && [ "$pip_minor" -ge 1 ]); then
            output=$(pip install --break-system-packages -r "$file_to_check" 2>&1)
        else
            # Para versiones antiguas de pip, usar sin la opción
            output=$(pip install -r "$file_to_check" 2>&1)
        fi
        
        exit_code=$?
        
        if [ $exit_code -ne 0 ]; then
            echo ""
            echo "=========================================="
            echo "WARNING: Unable to install dependencies"
            echo "=========================================="
            echo "File: $file_to_check"
            echo ""
            echo "Error details:"
            echo "$output"
            echo ""
            echo "ACTION REQUIRED:"
            echo "Please log into the container and install the dependencies manually:"
            echo "  docker exec -it <container_name> bash"
            echo "  pip install -r $file_to_check"
            echo ""
            echo "Or modify your Dockerfile/docker-compose to handle these dependencies."
            echo "=========================================="
            echo ""
            # No detenemos el proceso, solo advertimos
            exit 0
        else
            echo "All dependencies installed successfully!"
        fi
    else
        echo "The file $file_to_check exists but is empty or contains only comments. Skipping installation."
    fi
else
    echo "The file $file_to_check does not exist. Skipping installation."
fi