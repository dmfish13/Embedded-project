#!/bin/bash

if [ $# -eq 0 ]; then
    echo "Usage: $0 <num1> <num2> ..."
    exit 1
fi

sum=0
for n in "$@"; do
    sum=$((sum + n))
done

echo "Sum: $sum"
