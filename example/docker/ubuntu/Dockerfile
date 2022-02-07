FROM ubuntu:latest

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get -y dist-upgrade && \
    apt-get -y install supervisor \
                       python3 \
                       python3-pip \
                       python-dev

RUN pip3 install yacron
COPY entrypoint.sh /entrypoint.sh
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY yacrontab.yaml /etc/yacron.d/yacrontab.yaml
COPY stop-supervisor.sh /etc/supervisor/stop-supervisor.sh

RUN chmod 755 /entrypoint.sh && \
    chmod 755 /etc/supervisor/stop-supervisor.sh

ENTRYPOINT ["tail", "-f", "/dev/null"]
