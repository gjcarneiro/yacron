


all:
	python setup.py bdist_wheel
	/usr/bin/python3 -m venv pyoxidizer/venv
	pyoxidizer/venv/bin/pip install dist/yacron-$(shell python setup.py --version)-*.whl
	cd pyoxidizer/yacron && pyoxidizer build --release


clean:
	rm -rf build
	rm -rf dist
	rm -rf pyoxidizer/venv
	rm -rf pyoxidizer/yacron/build
