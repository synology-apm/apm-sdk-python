SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build
APIDOCDIR     = api
PACKAGE_SRC   = ../packages/synology-apm-sdk/src/synology_apm
PACKAGE       = $(PACKAGE_SRC)/sdk

.PHONY: help clean sync apidoc html

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  apidoc   generate RST stubs from source (writes to $(APIDOCDIR)/)"
	@echo "  html     build HTML docs (runs apidoc first)"
	@echo "  clean    remove $(BUILDDIR)/ and $(APIDOCDIR)/"

clean:
	rm -rf $(BUILDDIR) $(APIDOCDIR)

# `uv run` only auto-syncs the default (dev) dependency group — sphinx/furo live in
# the non-default "docs" group, so without this, uv run silently falls back to
# whatever sphinx-build happens to be first on PATH instead of failing loudly.
sync:
	uv sync --group docs

apidoc: sync
	uv run sphinx-apidoc --implicit-namespaces -o $(APIDOCDIR) $(PACKAGE_SRC) $(PACKAGE)/_http.py --separate --module-first --no-toc -f -q

html: clean apidoc
	uv run $(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)/html $(O)
	@echo ""
	@echo "Build finished. Open $(BUILDDIR)/html/index.html to view."
