# mdtools — top-level build / install
PREFIX  ?= $(HOME)/.local
BINDIR  ?= $(PREFIX)/bin
LIBDIR  ?= $(PREFIX)/lib/mdtools
PYTHON  ?= python3

.PHONY: all mdfix prosevary-check install uninstall clean test

all: mdfix

mdfix:
	$(MAKE) -C mdfix

prosevary-check:
	./scripts/prosevary --help >/dev/null
	@echo "prosevary CLI ok"

install: mdfix
	install -d "$(BINDIR)" "$(LIBDIR)"
	install -m 755 mdfix/mdfix "$(BINDIR)/mdfix"
	rm -rf "$(LIBDIR)/prosevary"
	cp -R prosevary "$(LIBDIR)/prosevary"
	rm -rf "$(LIBDIR)/prosevary/__pycache__"
	find "$(LIBDIR)/prosevary" -name '*.sqlite' -delete 2>/dev/null || true
	find "$(LIBDIR)/prosevary" -name '*.sqlite-*' -delete 2>/dev/null || true
	# Launcher with MDTOOLS_LIB fixed to this install
	sed -e 's|^MDTOOLS_LIB=.*|MDTOOLS_LIB="$(LIBDIR)"|' \
		scripts/prosevary > "$(BINDIR)/prosevary"
	chmod 755 "$(BINDIR)/prosevary"
	@echo "Installed mdfix + prosevary → $(BINDIR)"
	@echo "Ensure $(BINDIR) is on PATH."

uninstall:
	rm -f "$(BINDIR)/mdfix" "$(BINDIR)/prosevary"
	rm -rf "$(LIBDIR)"

clean:
	$(MAKE) -C mdfix clean

test: mdfix prosevary-check
	./mdfix/mdfix -h >/dev/null
	$(PYTHON) -m unittest discover -s tests -v
	@echo "ok"
