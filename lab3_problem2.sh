#!/bin/bash

remove_empty() {
    local dir="$1"

    for entry in "$dir"/*; do
        if [ ! -e "$entry" ]; then
            continue
        fi

        if [ -f "$entry" ] && [ ! -s "$entry" ]; then
            echo "Removing: $entry"
            rm "$entry"
        elif [ -d "$entry" ]; then
            remove_empty "$entry"
        fi
    done
}

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

echo "Before:"
tree "$target" 2>/dev/null || ls -R "$target"

remove_empty "$target"

echo "After:"
tree "$target" 2>/dev/null || ls -R "$target"
