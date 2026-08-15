RAGEL  ?= ragel
CC     ?= cc
CFLAGS ?= -O2

# SLOW-32 cross build (make slow32). Needs the slow-32 repo's toolchain and
# a clang with the slow32 backend; override these when they live elsewhere.
SLOW32_ROOT ?= $(HOME)/slow-32
SLOW32_LLVM ?= $(HOME)/llvm-project/build/bin
S32_CLANG    = $(SLOW32_LLVM)/clang
S32_LLC      = $(SLOW32_LLVM)/llc
S32_AS       = $(SLOW32_ROOT)/tools/assembler/slow32asm
S32_LD       = $(SLOW32_ROOT)/tools/linker/s32-ld
S32_RUNTIME  = $(SLOW32_ROOT)/runtime
S32_CFLAGS   = -target slow32-unknown-none -S -emit-llvm -O1 \
               -nostdinc -fno-builtin -I$(S32_RUNTIME)/include -I.
S32_SRCS     = mdfix.c vendor/utf_width.c vendor/utf_nfc.c vendor/utf_word.c

.PHONY: all clean asan check-sync slow32

all: mdfix

mdfix: mdfix.c vendor/utf_width.c vendor/utf_nfc.c vendor/utf_word.c
	$(CC) $(CFLAGS) -o $@ mdfix.c vendor/utf_width.c vendor/utf_nfc.c vendor/utf_word.c

# SLOW-32 guest binary. Every buffer line is a full MAX_LINE (8K) malloc by
# design — overflow headroom for the in-place fixers — so heap scales at
# 8K x lines. 224M of heap covers a ~28k-line whole-book file; the guest
# address space caps at 256M, so this is close to as generous as it gets.
# The 8M stack absorbs ragel -G2's large frames (256K provably overflowed).
slow32: mdfix.s32x

mdfix.s32x: $(S32_SRCS)
	@set -e; \
	for src in $(S32_SRCS); do \
		base=$${src%.c}; \
		$(S32_CLANG) $(S32_CFLAGS) $$src -o $$base.s32.ll; \
		$(S32_LLC) -mtriple=slow32-unknown-none $$base.s32.ll -o $$base.s32.s; \
		$(S32_AS) $$base.s32.s $$base.s32o >/dev/null; \
	done
	$(S32_LD) --mmio 64K --heap-size 224M --stack-size 8M --rodata-size 4M \
		-o $@ \
		$(S32_RUNTIME)/crt0.s32o \
		mdfix.s32o vendor/utf_width.s32o vendor/utf_nfc.s32o \
		vendor/utf_word.s32o \
		$(S32_RUNTIME)/libc_mmio.s32a $(S32_RUNTIME)/libs32.s32a
	@rm -f mdfix.s32.ll mdfix.s32.s vendor/*.s32.ll vendor/*.s32.s
	@echo "Built $@ — run with: $(SLOW32_ROOT)/tools/emulator/slow32-fast $@"

# Sanitizer build. Every fixer mutates its line in place and several of them
# lengthen it, so a wrong-sized line allocation overflows the heap by a few
# bytes — invisible to the test suite, obvious to ASan. Run this after
# touching read_all or any fixer that grows a line.
#
# The sweep is over every .md in the tree, which is why tests/fixtures/nfc
# exists: the repo's own prose is all NFC, so without a deliberately
# decomposed document --normalize-nfc would be a no-op here and the DFA table
# walks in the vendored tables would never be sanitized at all.
asan: mdfix.c vendor/utf_width.c vendor/utf_nfc.c vendor/utf_word.c
	$(CC) -O1 -g -fsanitize=address,undefined -o mdfix-asan mdfix.c vendor/utf_width.c vendor/utf_nfc.c vendor/utf_word.c
	@err=$$(mktemp); \
	find .. -name '*.md' -not -path '*/.git/*' -print0 \
	| while IFS= read -r -d '' f; do \
		for p in --canonical --technical "--canonical --pandoc-safe-links" \
		         --normalize-nfc; do \
			./mdfix-asan -n -q $$p "$$f" >/dev/null 2>$$err || \
				{ cat $$err; rm -f $$err; exit 1; }; \
		done; \
		./mdfix-asan --emit-ir "$$f" >/dev/null 2>$$err || \
			{ cat $$err; rm -f $$err; exit 1; }; \
		printf '' | ./mdfix-asan -q --apply-edits "$$f" >/dev/null 2>$$err || \
			{ cat $$err; rm -f $$err; exit 1; }; \
	done || { rm -f $$err; exit 1; }; \
	rm -f $$err; \
	echo "asan: clean over repo markdown"

# Regenerate C from Ragel when the .rl changes (requires ragel).
# Pre-generated mdfix.c is committed so builds work without ragel.
mdfix.c: mdfix.rl
	$(RAGEL) -G2 $< -o $@

# Verify the committed mdfix.c really is ragel's output for mdfix.rl.
#
# The .c is generated but committed, so the two can drift: a hand-edit to the
# .c, or a .rl change committed without regenerating, both produce a binary
# that does not match its source. Reviewing the .rl then tells you nothing
# about what ships.
#
# Regenerating into a temp directory under the *same filename* matters —
# ragel writes the -o path into #line directives, so comparing against a
# differently-named file reports differences that are pure artifact.
#
# Fails rather than skips when ragel is missing: a source-integrity check that
# silently passes when it cannot run is worse than no check. This target is
# deliberately not part of `make test`, so contributors without ragel can
# still build and test from the committed .c.
check-sync:
	@command -v $(RAGEL) >/dev/null 2>&1 || { \
		echo "check-sync: $(RAGEL) not found — cannot verify mdfix.c against mdfix.rl"; \
		exit 1; }
	@echo "check-sync: using $$($(RAGEL) --version | head -1)"
	@tmp=$$(mktemp -d) || exit 1; \
	cp mdfix.rl "$$tmp/" && (cd "$$tmp" && $(RAGEL) -G2 mdfix.rl -o mdfix.c) || { \
		rm -rf "$$tmp"; echo "check-sync: ragel failed"; exit 1; }; \
	if cmp -s mdfix.c "$$tmp/mdfix.c"; then \
		rm -rf "$$tmp"; \
		echo "check-sync: mdfix.c matches mdfix.rl byte for byte"; \
		exit 0; \
	fi; \
	grep -v '^#line' mdfix.c > "$$tmp/committed.stripped"; \
	grep -v '^#line' "$$tmp/mdfix.c" > "$$tmp/regenerated.stripped"; \
	if cmp -s "$$tmp/committed.stripped" "$$tmp/regenerated.stripped"; then \
		echo "check-sync: mdfix.c matches mdfix.rl (differs only in #line directives)"; \
		echo "  Those are debug metadata whose absolute values depend on the ragel"; \
		echo "  build; the generated code is identical, so this is not drift."; \
		rm -rf "$$tmp"; \
		exit 0; \
	fi; \
	echo "check-sync: mdfix.c is NOT ragel's output for mdfix.rl."; \
	echo "  Regenerate with: rm -f mdfix/mdfix.c && make -C mdfix"; \
	echo "  --- differences outside #line directives ---"; \
	diff "$$tmp/committed.stripped" "$$tmp/regenerated.stripped" | head -30; \
	rm -rf "$$tmp"; \
	exit 1

clean:
	rm -rf mdfix mdfix-asan mdfix-asan.dSYM
	rm -f mdfix.s32x mdfix.s32o vendor/*.s32o
	# Keep mdfix.c in the tree; only drop the binary.
