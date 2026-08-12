# mdtools — top-level build / install
PREFIX  ?= $(HOME)/.local
BINDIR  ?= $(PREFIX)/bin
LIBDIR  ?= $(PREFIX)/lib/mdtools
PYTHON  ?= python3

.PHONY: all mdfix prosevary-check mdquery-check install uninstall clean test check-sync asan check

all: mdfix

mdfix:
	$(MAKE) -C mdfix

prosevary-check:
	./scripts/prosevary --help >/dev/null
	@echo "prosevary CLI ok"

mdquery-check: mdfix
	./scripts/mdquery --help >/dev/null
	./scripts/mdquery stats README.md >/dev/null
	@echo "mdquery CLI ok"

install: mdfix
	install -d "$(BINDIR)" "$(LIBDIR)"
	install -m 755 mdfix/mdfix "$(BINDIR)/mdfix"
	rm -rf "$(LIBDIR)/prosevary" "$(LIBDIR)/mdquery"
	cp -R prosevary "$(LIBDIR)/prosevary"
	cp -R mdquery "$(LIBDIR)/mdquery"
	rm -rf "$(LIBDIR)/prosevary/__pycache__" "$(LIBDIR)/mdquery/__pycache__"
	find "$(LIBDIR)/prosevary" -name '*.sqlite' -delete 2>/dev/null || true
	find "$(LIBDIR)/prosevary" -name '*.sqlite-*' -delete 2>/dev/null || true
	# Launcher with MDTOOLS_LIB fixed to this install
	sed -e 's|^MDTOOLS_LIB=.*|MDTOOLS_LIB="$(LIBDIR)"|' \
		scripts/prosevary > "$(BINDIR)/prosevary"
	chmod 755 "$(BINDIR)/prosevary"
	sed -e 's|^MDTOOLS_LIB=.*|MDTOOLS_LIB="$(LIBDIR)"|' \
		scripts/mdquery > "$(BINDIR)/mdquery"
	chmod 755 "$(BINDIR)/mdquery"
	@echo "Installed mdfix + prosevary + mdquery → $(BINDIR)"
	@echo "Ensure $(BINDIR) is on PATH."

uninstall:
	rm -f "$(BINDIR)/mdfix" "$(BINDIR)/prosevary" "$(BINDIR)/mdquery"
	rm -rf "$(LIBDIR)"

clean:
	$(MAKE) -C mdfix clean

test: mdfix prosevary-check mdquery-check
	./mdfix/mdfix -h >/dev/null
	$(PYTHON) -m unittest discover -s tests -v
	@echo "ok"

# Source integrity: the committed mdfix.c must be ragel's output for mdfix.rl.
# Requires ragel, so it is not part of `test` — building and testing from the
# committed .c must keep working without it.
check-sync:
	$(MAKE) -C mdfix check-sync

# Sanitizer pass over the repo's own markdown. Catches the class of bug the
# test suite cannot see: a few bytes written past a heap allocation.
asan:
	$(MAKE) -C mdfix asan

# Everything CI runs. Use this before opening a PR.
check: test check-sync asan
	@echo "check: all green"
