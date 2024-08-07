#!/bin/bash
#

nginx

ls -l

export hls_local_path=/tmp/tvhtohls/hls

mkdir -p $hls_local_path
while true
do
	./__main__.py
	sleep 1
done

