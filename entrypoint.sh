#!/bin/bash
#
# This runs as PID 1 in the container, so it is what `docker stop` signals.
# Without forwarding that signal, the app never learns it should shut down: it
# gets SIGKILLed 10 s later, and its ffmpeg children (and the TVHeadend
# subscriptions they hold) are only cleaned up by the PID-namespace teardown.

nginx

ls -l

export hls_local_path=/tmp/tvhtohls/hls

mkdir -p $hls_local_path

app_pid=""
stopping=""

shutdown() {
	stopping=1
	if [ -n "$app_pid" ]; then
		kill -TERM "$app_pid" 2>/dev/null
		# Give the app up to 5 s to stop its ffmpeg children itself. Docker's
		# default stop grace period is 10 s, so leave room for nginx too.
		for _ in $(seq 1 50); do
			kill -0 "$app_pid" 2>/dev/null || break
			sleep 0.1
		done
		kill -KILL "$app_pid" 2>/dev/null
	fi
	# Safety net: any ffmpeg the app didn't reap. Its children also carry
	# PR_SET_PDEATHSIG, so they die with it even without this.
	command -v pkill >/dev/null && pkill -KILL -x ffmpeg 2>/dev/null
	nginx -s quit 2>/dev/null
	exit 0
}

trap shutdown TERM INT QUIT

while [ -z "$stopping" ]
do
	python3 -m tvhtohls &
	app_pid=$!
	wait "$app_pid"
	[ -n "$stopping" ] && break
	sleep 1
done
