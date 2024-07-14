#!/bin/bash
#

nginx

export local_http_path=/tmp/http/static
export static_http_path=static/
export tvheadend_user=teletext
export tvheadend_pass=teletext

mkdir -p $local_http_path
chmod a+r hls.js
mkdir -p $local_http_path/hls/
cp hls.js $local_http_path/hls/hls.js
while true
do
	./tvh_to_hls.py
	sleep 1
done

