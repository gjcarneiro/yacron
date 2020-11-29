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

COPY . /root/yacron
WORKDIR /root/yacron
RUN git status
RUN python setup.py install
RUN python pyinstaller/yacron --version
RUN pyinstaller pyinstaller/yacron.spec
RUN ls -sFh dist/yacron
RUN dist/yacron --version
