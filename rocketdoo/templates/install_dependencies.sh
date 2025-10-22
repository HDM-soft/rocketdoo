#!/bin/bash

file_to_check="$PWD/requirements.txt"

if [ -f "$file_to_check" ]; then
    # Verificar si el archivo tiene contenido (ignorando líneas vacías y comentarios)
    if grep -q '^[^#[:space:]]' "$file_to_check"; then
        echo "The file $file_to_check exists. Installing python dependencies"
        output=$(pip install --break-system-packages -r "$file_to_check" 2>&1)
        exit_code=$?
        
        if [ $exit_code -ne 0 ]; then
            echo "Warning: Unable to install at least one dependency of $file_to_check."
            echo "$output"
            echo "Please log into the container and install them manually or modify the dockerfile or docker compose."
            echo "(script returned an error)"
            
            # Preguntar si se debe continuar o usar --force
            if [[ "$*" == *"--force"* ]]; then
                echo "Continuing due to --force flag..."
                exit 0
            else
                echo ""
                echo "Run again with '--force' to ignore script errors"
                exit 1
            fi
        else
            echo "All dependencies installed successfully!"
        fi
    else
        echo "The file $file_to_check exists but is empty or contains only comments. Skipping installation."
    fi
else
    echo "The file $file_to_check does not exist. Skipping installation."
fi