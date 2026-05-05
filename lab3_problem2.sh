#!/bin/bash

if [ $# -eq 0 ]; then
    target="."
elif [ $# -eq 1 ]; then
    target="$1"
else
    echo "Usage: $0 [directory]"
    exit 1
fi

if [ ! -d "$target" ]; then
    echo "Error: '$target' is not a directory."
    exit 1
fi

find "$target" -type f -empty -print -delete
