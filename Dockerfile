FROM debian:bookworm

WORKDIR /tvh_to_hls

RUN apt-get update &&\
	apt-get -y install python3 python3-pip python3-venv nginx ffmpeg

COPY ./ ./
RUN python3 -m venv venv
RUN venv/bin/pip install .
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80/tcp
ENTRYPOINT ./entrypoint.sh
