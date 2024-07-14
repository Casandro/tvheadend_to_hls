FROM debian:bookworm

WORKDIR /tvh_to_hls

RUN apt-get update &&\
	apt-get -y install python3 python3-uvicorn python3-fastapi python3-requests nginx ffmpeg

COPY python ./
COPY entrypoint.sh ./
COPY nginx/nginx.conf /etc/nginx/nginx.conf
ADD https://cdn.jsdelivr.net/npm/hls.js@1 hls.js

EXPOSE 80/tcp
ENTRYPOINT ./entrypoint.sh
