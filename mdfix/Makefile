RAGEL  ?= ragel
CC     ?= cc
CFLAGS ?= -O2

.PHONY: all clean

all: mdfix

mdfix: mdfix.c
	$(CC) $(CFLAGS) -o $@ $<

# Regenerate C from Ragel when the .rl changes (requires ragel).
# Pre-generated mdfix.c is committed so builds work without ragel.
mdfix.c: mdfix.rl
	$(RAGEL) -G2 $< -o $@

clean:
	rm -f mdfix
	# Keep mdfix.c in the tree; only drop the binary.
