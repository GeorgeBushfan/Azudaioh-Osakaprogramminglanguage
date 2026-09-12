# Makefile — Gate E native host (osakavm).
#
# Build:   make          (produces ./osakavm)
# Regenerate the transpiled VM:  make vm.c   (requires Python for the
# one-time transpiler only; the binary itself needs nothing but a C compiler).

CC ?= cc
CFLAGS ?= -O2 -std=c11 -Wall -Wno-unused-parameter

SRCS = rt.c json.c builtins.c main.c vm.c
HDRS = value.h

all: osakavm

vm.c: ../transpile_vm.py ../selfhost/support.saka ../selfhost/vm.saka $(HDRS)
	cd .. && python3 transpile_vm.py

osakavm: $(SRCS) $(HDRS)
	$(CC) $(CFLAGS) -o osakavm $(SRCS) -lm

clean:
	rm -f osakavm

.PHONY: all clean