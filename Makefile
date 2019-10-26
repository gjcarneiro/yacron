

VERSION := $(shell python setup.py --version)
TARGET_VERSION ?= $(VERSION)
WHEEL := yacron-$(VERSION)-py3-none-any.whl

all: yacron-oxidized


pyoxidizer/build/$(WHEEL):
	python3 setup.py bdist_wheel
	mkdir -p pyoxidizer/build
	cp dist/$(WHEEL) pyoxidizer/build/


yacron-oxidized-img: pyoxidizer/build/$(WHEEL)
	cd pyoxidizer && docker build -t yacron-oxidized .


yacron-oxidized: yacron-oxidized-img
	cd pyoxidizer && docker run --rm -v $(shell pwd)/pyoxidizer/build:/export yacron-oxidized \
		cp /code/yacron/build/apps/yacron/x86_64-unknown-linux-gnu/release/yacron \
		/export/yacron-$(TARGET_VERSION)-x86_64-unknown-linux-gnu
	strip pyoxidizer/build/yacron-$(TARGET_VERSION)-x86_64-unknown-linux-gnu
	xz pyoxidizer/build/yacron-$(TARGET_VERSION)-x86_64-unknown-linux-gnu


clean:
	rm -rf build
	rm -rf dist
	rm -rf pyoxidizer/build
