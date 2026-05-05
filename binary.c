#include <stdio.h>
#include <stdlib.h>

void print_binary(unsigned int n) {
    for (int i = 31; i >= 0; i--) {
        printf("%u", (n >> i) & 1U);
        if (i % 4 == 0 && i != 0) {
            printf(" ");
        }
    }
}

unsigned int reverse_bits(unsigned int n) {
    unsigned int result = 0;
    for (int i = 0; i < 32; i++) {
        result <<= 1;
        result |= (n & 1U);
        n >>= 1;
    }
    return result;
}

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s <non-negative integer>\n", argv[0]);
        return 1;
    }

    unsigned int value = (unsigned int)strtoul(argv[1], NULL, 10);
    unsigned int reversed = reverse_bits(value);

    printf("%u: ", value);
    print_binary(value);
    printf("\n");

    printf("%u: ", reversed);
    print_binary(reversed);
    printf("\n");

    return 0;
}
