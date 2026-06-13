FROM debian:bookworm

WORKDIR /tvh_to_hls

RUN apt-get update &&\
	apt-get -y install python3 python3-uvicorn python3-fastapi python3-requests nginx ffmpeg

COPY ./src/tvhtohls/ ./tvhtohls/
COPY ./entrypoint.sh ./
COPY ./list_channels.py ./
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80/tcp
ENTRYPOINT ./entrypoint.sh
