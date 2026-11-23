CC ?= cc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Wconversion -Werror -pedantic
SANITIZERS ?= -fsanitize=address,undefined -fno-omit-frame-pointer

.PHONY: test sanitize clean
test: firmware_header_test
	./firmware_header_test

firmware_header_test: firmware_header.c test_firmware_header.c firmware_header.h
	$(CC) $(CFLAGS) firmware_header.c test_firmware_header.c -o $@

sanitize:
	$(CC) $(CFLAGS) $(SANITIZERS) firmware_header.c test_firmware_header.c -o firmware_header_test_san
	./firmware_header_test_san

clean:
	rm -f firmware_header_test firmware_header_test_san
