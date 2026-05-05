#!/bin/bash

if [ $# -ne 1 ]; then
    echo "Usage: $0 <filename>"
    exit 1
fi

filename="$1"

if [ ! -e "$filename" ]; then
    echo "Error: '$filename' does not exist."
    exit 1
fi

timestamp=$(date +"%Y%m%d_%H%M%S")
backup="${filename}.bak.${timestamp}"

cp "$filename" "$backup"

if [ $? -eq 0 ]; then
    echo "Copied '$filename' to '$backup'"
else
    echo "Error: failed to copy '$filename'"
    exit 1
fi
