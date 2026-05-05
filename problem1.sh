#!/bin/bash

if [ $# -ne 1 ]; then
    echo "Usage: $0 <file_or_directory>"
    exit 1
fi

target="$1"

if [ ! -e "$target" ]; then
    echo "Error: '$target' does not exist."
    exit 1
fi

if [ -d "$target" ]; then
    echo "'$target' is a directory."
elif [ -f "$target" ]; then
    echo "'$target' is a file."
else
    echo "'$target' is neither a regular file nor a directory."
fi

if [ -r "$target" ]; then
    echo "Read permission: yes"
else
    echo "Read permission: no"
fi

if [ -w "$target" ]; then
    echo "Write permission: yes"
else
    echo "Write permission: no"
fi

if [ -x "$target" ]; then
    echo "Execute permission: yes"
else
    echo "Execute permission: no"
fi

if [ -f "$target" ]; then
    size=$(du -b "$target" | cut -f1)
    echo "File size: ${size} bytes"

    if [ "$size" -gt 1048576 ]; then
        echo "Category: large file"
    elif [ "$size" -gt 102400 ]; then
        echo "Category: medium file"
    else
        echo "Category: small file"
    fi
fi
