#!/bin/bash
#

nginx

ls -l

export hls_local_path=/tmp/tvhtohls/hls

mkdir -p $hls_local_path

while true
do
	python3 -m tvhtohls
	sleep 1
done

