SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build
APIDOCDIR     = api
PACKAGE_SRC   = ../packages/synology-apm-sdk/src/synology_apm
PACKAGE       = $(PACKAGE_SRC)/sdk

.PHONY: help clean apidoc html

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  apidoc   generate RST stubs from source (writes to $(APIDOCDIR)/)"
	@echo "  html     build HTML docs (runs apidoc first)"
	@echo "  clean    remove $(BUILDDIR)/ and $(APIDOCDIR)/"

clean:
	rm -rf $(BUILDDIR) $(APIDOCDIR)

apidoc:
	sphinx-apidoc --implicit-namespaces -o $(APIDOCDIR) $(PACKAGE_SRC) $(PACKAGE)/_http.py --separate --module-first --no-toc -f -q

html: clean apidoc
	$(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)/html $(O)
	@echo ""
	@echo "Build finished. Open $(BUILDDIR)/html/index.html to view."
