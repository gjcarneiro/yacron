
all:
	docker build -t yacron-pyinstaller-build -f Dockerfile ..
	docker run --name yacron-pyinstaller-build  --rm -d yacron-pyinstaller-build sleep infinity
	mkdir -p dist
	docker cp yacron-pyinstaller-build:/root/yacron/dist/yacron dist/
	docker kill yacron-pyinstaller-build
	ls -sh dist/yacron
	dist/yacron --version
