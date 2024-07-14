#!/bin/bash
#

nginx

export local_http_path=/tmp/http/static
export static_http_path=static/
export tvheadend_user=teletext
export tvheadend_pass=teletext

mkdir -p $local_http_path/hls/
while true
do
	venv/bin/tvhtohls
	sleep 1
done

