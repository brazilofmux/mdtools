RAGEL  ?= ragel
CC     ?= cc
CFLAGS ?= -O2

.PHONY: all clean asan

all: mdfix

mdfix: mdfix.c
	$(CC) $(CFLAGS) -o $@ $<

# Sanitizer build. Every fixer mutates its line in place and several of them
# lengthen it, so a wrong-sized line allocation overflows the heap by a few
# bytes — invisible to the test suite, obvious to ASan. Run this after
# touching read_all or any fixer that grows a line.
asan: mdfix.c
	$(CC) -O1 -g -fsanitize=address,undefined -o mdfix-asan mdfix.c
	@err=$$(mktemp); \
	for f in ../*.md ../*/*.md; do \
		for p in --canonical --technical "--canonical --pandoc-safe-links"; do \
			./mdfix-asan -n -q $$p "$$f" >/dev/null 2>$$err || \
				{ cat $$err; rm -f $$err; exit 1; }; \
		done; \
	done; \
	rm -f $$err; \
	echo "asan: clean over repo markdown"

# Regenerate C from Ragel when the .rl changes (requires ragel).
# Pre-generated mdfix.c is committed so builds work without ragel.
mdfix.c: mdfix.rl
	$(RAGEL) -G2 $< -o $@

clean:
	rm -rf mdfix mdfix-asan mdfix-asan.dSYM
	# Keep mdfix.c in the tree; only drop the binary.
