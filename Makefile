# Osaka — top-level convenience targets.
#
#   make build     build the native VM (native/osakavm)
#   make install   install the toolchain (see install.sh)
#   make release   build an optimized VM and pack a release tarball in dist/
#
# The release tarball contains a ready-to-use toolchain:
#   bin/osakavm  bin/osaka  bin/osakac  lib/osakac.stage2.sbc

VERSION ?= 0.1.0

.PHONY: all build install release clean

all: build

build:
	$(MAKE) -C native osakavm

install:
	./install.sh

release: build
	@OS="$(shell uname -s | tr '[:upper:]' '[:lower:]')"; \
	ARCH="$(shell uname -m)"; \
	NAME="osaka-$(VERSION)-$$OS-$$ARCH"; \
	DIR="dist/$$NAME"; \
	rm -rf "$$DIR" && mkdir -p "$$DIR/bin" "$$DIR/lib"; \
	cp native/osakavm "$$DIR/bin/osakavm"; \
	cp bin/osaka bin/osakac "$$DIR/bin/"; \
	cp bootstrap/osakac.stage2.sbc "$$DIR/lib/"; \
	chmod +x "$$DIR/bin/osaka" "$$DIR/bin/osakac" "$$DIR/bin/osakavm"; \
	tar -czf "dist/$$NAME.tar.gz" -C dist "$$NAME"; \
	rm -rf "$$DIR"; \
	echo "release: dist/$$NAME.tar.gz"

clean:
	$(MAKE) -C native clean
	rm -rf dist