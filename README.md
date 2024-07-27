# tvheadend_to_hls
A small web service that exports tvheadend services via HLS

# How to set this up

1. Setup [tvheadend](https://tvheadend.org/)
2. Map channels either manually or via the automated mapping.
3. Create a user and allow it to stream. Be sure to limit the IP adresses to the machien you run `tvheadend_to_hls` on.
4. Create a `.env`-File in the directory you downloaded `tvheadend_to_hls` to configure it.
```
tvheadend_user=username
tvheadend_pass=password
tvheadend_ip=ip
```
5. In the directory you have gotten `tvheadend_to_hls`, run `docker-compose build` and then `docker-compose up` to run it.


## I don't like Docker
This is fine. The src subdirectory contains a Python program which acts as a web server and calls ffmpeg to create the files for the stream. It should also serve those files. This is probably not wise, as a dedicated webserver likely is faster.

### I want to scale this

This is not meant to be scalable. This is a small personal project to provide, for example, TV to a small dormatory or community.

Make sure you understand your bottleneck. If you are dealing with many different channels that are being streamed, make sure the encoder has enough oompf to handle it or lower the encoding complexity. 

If your stream the same channel to a lot of people, just install a caching proxy in front of it.



# Example configuration:

```
tvheadend_user=user
tvheadend_pass=pass
tvheadend_ip=ip
```
