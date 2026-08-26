FROM debian:bookworm

WORKDIR /tvh_to_hls

RUN apt-get update &&\
	apt-get -y install python3 python3-uvicorn python3-fastapi python3-requests nginx ffmpeg fonts-dejavu-core procps

COPY ./src/tvhtohls/ ./tvhtohls/
COPY ./entrypoint.sh ./
COPY ./list_channels.py ./
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80/tcp
# Exec form: entrypoint.sh becomes PID 1 itself and receives SIGTERM from
# `docker stop`. With the shell form, `sh -c` would be PID 1 and would not
# pass the signal on.
ENTRYPOINT ["./entrypoint.sh"]
