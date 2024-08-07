FROM debian:bookworm

WORKDIR /tvh_to_hls

RUN apt-get update &&\
	apt-get -y install python3 python3-uvicorn python3-fastapi python3-requests nginx ffmpeg

COPY ./src/tvhtohls/ ./
COPY ./entrypoint.sh ./
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80/tcp
ENTRYPOINT ./entrypoint.sh
