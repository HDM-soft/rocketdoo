#!/bin/bash

file_to_check="$PWD/requirements.txt"

if [ -f "$file_to_check" ]; then
    echo "The file $file_to_check exists. Installing python dependencies"
    pip install -r "$file_to_check" 2>&1
    exit_code = $?
    if [ $exit_code -ne 0 ]; then
      echo "Error: Unable to install at least one dependency of $file_to_check.\n"
      echo "$output"
      echo "Please log into the container and install them manually or modify the dockerfile or docker compose."
      exit 0
    fi
else
    echo "The file $file_to_check does not exist."
fi