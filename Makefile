PYTHON ?= python3
VENV   ?= .venv

.PHONY: help run dry-run test deb install-deps clean lint

help:
	@echo "make run          launch the app from this checkout"
	@echo "make dry-run      report what a clean would remove, touching nothing"
	@echo "make test         run the test suite"
	@echo "make deb          build ../purge-linux_*.deb"
	@echo "make install-deps install the build and runtime dependencies (Ubuntu)"

run:
	$(PYTHON) -m purgelinux

dry-run:
	PURGE_DRY_RUN=1 $(PYTHON) -m purgelinux --dry-run

test:
	$(PYTHON) -m pytest -q

install-deps:
	sudo apt install -y devscripts debhelper dh-python pybuild-plugin-pyproject \
		python3-all python3-setuptools \
		python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
		python3-psutil python3-apt python3-pytest

deb:
	dpkg-buildpackage -us -uc -b
	@echo "Built: $$(ls -1 ../purge-linux_*.deb | tail -1)"

lint:
	desktop-file-validate data/io.github.purgelinux.desktop
	appstreamcli validate --no-net data/io.github.purgelinux.metainfo.xml || true

clean:
	rm -rf build dist *.egg-info .pytest_cache .pybuild
	@command -v dh_clean >/dev/null 2>&1 && dh_clean || true
	find . -name __pycache__ -type d -exec rm -rf {} +
