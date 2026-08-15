# mdtools — top-level build / install
PREFIX  ?= $(HOME)/.local
BINDIR  ?= $(PREFIX)/bin
LIBDIR  ?= $(PREFIX)/lib/mdtools
PYTHON  ?= python3

.PHONY: all mdfix prosevary-check mdquery-check mdterms-check mdlinks-check mdtools-check install uninstall clean test check-sync slow32-check asan fuzz check

all: mdfix

mdfix:
	$(MAKE) -C mdfix

mdterms-check: mdfix
	./scripts/mdterms --help >/dev/null
	@echo "mdterms CLI ok"

mdtools-check: mdfix
	./scripts/mdtools --help >/dev/null
	./scripts/mdtools config >/dev/null
	./scripts/mdcheck README.md docs/
	@echo "mdtools + mdcheck CLI ok"

mdlinks-check: mdfix
	./scripts/mdlinks --help >/dev/null
	./scripts/mdlinks README.md docs/*.md
	@echo "mdlinks CLI ok"

prosevary-check:
	./scripts/prosevary --help >/dev/null
	@echo "prosevary CLI ok"

mdquery-check: mdfix
	./scripts/mdquery --help >/dev/null
	./scripts/mdquery stats README.md >/dev/null
	@echo "mdquery CLI ok"

# Single package/launcher lists so install and uninstall stay in sync.
PACKAGES = prosevary mdquery mdterms mdlinks mdcheck mdtools_cli
LAUNCHERS = prosevary mdquery mdterms mdlinks mdcheck mdtools

install: mdfix
	install -d "$(BINDIR)" "$(LIBDIR)"
	install -m 755 mdfix/mdfix "$(BINDIR)/mdfix"
	@for pkg in $(PACKAGES); do \
		rm -rf "$(LIBDIR)/$$pkg"; \
		cp -R "$$pkg" "$(LIBDIR)/$$pkg"; \
		rm -rf "$(LIBDIR)/$$pkg/__pycache__"; \
	done
	find "$(LIBDIR)/prosevary" -name '*.sqlite' -delete 2>/dev/null || true
	find "$(LIBDIR)/prosevary" -name '*.sqlite-*' -delete 2>/dev/null || true
	@# Launcher with MDTOOLS_LIB fixed to this install
	@for tool in $(LAUNCHERS); do \
		sed -e 's|^MDTOOLS_LIB=.*|MDTOOLS_LIB="$(LIBDIR)"|' \
			"scripts/$$tool" > "$(BINDIR)/$$tool"; \
		chmod 755 "$(BINDIR)/$$tool"; \
	done
	@echo "Installed mdfix + $(LAUNCHERS) → $(BINDIR)"
	@echo "Ensure $(BINDIR) is on PATH."

uninstall:
	rm -f "$(BINDIR)/mdfix"
	@for tool in $(LAUNCHERS); do rm -f "$(BINDIR)/$$tool"; done
	rm -rf "$(LIBDIR)"

clean:
	$(MAKE) -C mdfix clean

test: mdfix prosevary-check mdquery-check mdterms-check mdlinks-check \
      mdtools-check
	./mdfix/mdfix -h >/dev/null
	$(PYTHON) -m unittest discover -s tests -v
	@echo "ok"

# Source integrity: the committed mdfix.c must be ragel's output for mdfix.rl.
# Requires ragel, so it is not part of `test` — building and testing from the
# committed .c must keep working without it.
check-sync:
	$(MAKE) -C mdfix check-sync

# Build the SLOW-32 guest mdfix and verify host/guest behavior parity.
# Needs the slow-32 repo (override SLOW32_ROOT / SLOW32_LLVM as needed);
# fails rather than skips when it is missing, and is not part of `make test`.
slow32-check:
	$(MAKE) -C mdfix mdfix slow32
	mdfix/test-slow32.sh

# Sanitizer pass over the repo's own markdown. Catches the class of bug the
# test suite cannot see: a few bytes written past a heap allocation.
asan:
	$(MAKE) -C mdfix asan

# Deep generative sweep (issue #10). `make test` runs a bounded slice of the
# same properties; this is the one that goes wide, and it runs against the
# sanitizer build so a memory bug on a generated document is caught here
# rather than on someone's manuscript.
#
# Not part of `check`: it takes minutes, and a gate people skip is not a gate.
# Run it after touching mdfix's line handling, and before a release.
FUZZ_SEEDS ?= 2000

fuzz: mdfix
	$(MAKE) -C mdfix asan
	@echo "fuzzing $(FUZZ_SEEDS) generated documents under ASan/UBSan..."
	@cd tests && MDFIX=../mdfix/mdfix-asan $(PYTHON) -c "\
import os, sys, tempfile; from pathlib import Path; import fuzz; \
d = tempfile.mkdtemp(); \
r = fuzz.Runner(Path(os.environ['MDFIX']), Path(d)); \
f = fuzz.sweep(r, range($(FUZZ_SEEDS))); \
[print('seed', s, sorted({k for k, _ in v}), '\n' + x.decode('utf-8', 'replace')) for s, v, x in f]; \
print(len(f), 'failing of $(FUZZ_SEEDS)'); \
sys.exit(1 if f else 0)"
	@echo "fuzz: $(FUZZ_SEEDS) documents, no violations"

# Everything CI runs. Use this before opening a PR.
check: test check-sync asan
	@echo "check: all green"
