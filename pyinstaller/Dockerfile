FROM ubuntu:16.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive \
	apt-get install -y -q \
		curl git ca-certificates build-essential zlib1g-dev \
		libbz2-dev libssl-dev libreadline-dev libffi-dev \
		upx-ucl
RUN curl https://pyenv.run | bash

ENV PATH="/root/.pyenv/bin:$PATH"

RUN pyenv install -l | grep 3.8
RUN CONFIGURE_OPTS=--enable-shared pyenv install 3.8.6
RUN pyenv global 3.8.6

RUN /root/.pyenv/versions/3.8.6/bin/python -m venv /yacron
ENV PATH=/yacron/bin:$PATH
COPY pyinstaller/requirements.txt /root
RUN pip install -U pip
RUN pip install -r /root/requirements.txt

COPY setup.py README.rst HISTORY.rst /root/
COPY yacron /root/yacron
WORKDIR /root
COPY .git /root/.git
COPY yacron/version.py /root/yacron/version.py
RUN ls -F
RUN cat setup.py
RUN python setup.py install
COPY pyinstaller/yacron.spec /root
COPY pyinstaller/yacron /root/yacron-main
RUN python /root/yacron-main --version
RUN pyinstaller yacron.spec
RUN ls -sFh /root/dist/yacron
RUN /root/dist/yacron --version
