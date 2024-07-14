#!/bin/bash
#

nginx

export hls_local_path=/tmp/tvhtohls/hls
export tvheadend_user=teletext
export tvheadend_pass=teletext

mkdir -p $hls_local_path
while true
do
	venv/bin/tvhtohls
	sleep 1
done

