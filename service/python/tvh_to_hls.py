#!/usr/bin/python3
import uvicorn
import os
from fastapi import FastAPI, Response
import requests
from requests.auth import HTTPDigestAuth
import time
import sched
import json
import html
import subprocess
import threading
import re


#Read settings
config={}
config["tvheadend_ip"]="192.168.5.5"
config["tvheadend_port"]="9981"
config["tvheadend_user"]="user"
config["tvheadend_pass"]="pass"
config["local_port"]=8888
config["local_http_path"]="/tmp/http"
config["static_http_path"]="/static"

for setting in ("tvheadend_ip", "tvheadend_port", "tvheadend_user", "tvheadend_pass", "local_port", "local_http_path", "static_http_path"):
    if setting in os.environ:
        config[setting]=os.environ[setting]

if not os.path.isdir(config["local_http_path"]):
    print("local_http_path '%s' is not a directory" % config["local_http_path"])
    exit()


tvh_base_url="http://"+config["tvheadend_ip"]+":"+config["tvheadend_port"]+"/"
tvh_base_url_auth="http://"+config["tvheadend_user"]+":"+config["tvheadend_pass"]+"@"+config["tvheadend_ip"]+":"+config["tvheadend_port"]+"/"

def tvheadend_get_raw(typ):
    url=tvh_base_url+"api/raw/export?class="+typ
    return tvheadend_get(url)

def tvheadend_get(url):
    req=requests.get(url, auth=HTTPDigestAuth(config["tvheadend_user"], config["tvheadend_pass"]))
    req.encoding="UTF-8"

    if req.status_code != 200:
        print("Couldn't get service list. Maybe user has insufficient rights. Code: ", req.status_code)
        exit()

    print("Parsing JSON")
    return json.loads(req.text)


class TVChannel:
    def __init__(self, name, tags,tvh_uuid):
        self.name=name
        self.tags=tags
        self.tvh_uuid=tvh_uuid
        self.hls_uuid=tvh_uuid
        self.tvh_url=tvh_base_url_auth+"stream/channel/"+tvh_uuid
        self.m3u8_file=config["local_http_path"]+"/"+tvh_uuid+".m3u8"
        self.stream=None
        self.last_used=time.time()
    def start_stream(self):
        self.last_used=time.time()
        if self.stream:
            if os.path.isfile(self.m3u8_file):
                return "stream.m3u8?uuid="+self.hls_uuid
            else:
                if self.stream.poll() is None:
                    return False
        #Start stream
        self.stream=subprocess.Popen(["/usr/bin/ffmpeg", "-i", "cache:"+self.tvh_url, 
            "-f", "hls", "-g", "50", 
            "-preset", "fast", 
            "-c:v", "libx264", "-b:v", "2M", 
            "-c:a", "aac", "-b:a", "96k",
            "-filter:v", "scale=720:576",
            "-r", "25", "-sn",
            "-hls_flags", "delete_segments",
            "-hls_list_size", "100",
            "-hls_time", "2", "-hls_playlist_type", "event",self.m3u8_file], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        self.last_used=time.time()
        return False
    def clean_stream(self):
        stream_path_base=config["local_http_path"]
        files=os.listdir(stream_path_base)
        for f in files:
            if f.startswith(f):
                print("clean_stream: erasing file %s" % (f))
                os.remove(stream_path_base+"/"+f)
        self.stream=None
        return True


def tvhedend_get_tv_channellist():
    #Get Data from tvheadend like channels and channel tags
    channeltags_list=tvheadend_get(tvh_base_url+"/api/channeltag/list")
    channeltags=channeltags_list["entries"]
    channels_grid=tvheadend_get(tvh_base_url+"/api/channel/grid?limit=99999")
    channels=channels_grid["entries"]
    #Get the tags for Radio and TV channels
    radio_tag=None
    tv_tag=None
    channel_tags={}
    for tag in channeltags:
        if tag["val"]=="Radio channels":
            radio_tag=tag["key"]
            print("%s = %s" % (tag["key"], tag["val"]))
        if tag["val"]=="TV channels":
            tv_tag=tag["key"]
            print("%s = %s" % (tag["key"], tag["val"]))
        channel_tags[tag["key"]]=tag["val"]
    #Filter channels to only have TV channels
    channel_list=[]
    for channel in channels:
        name=channel["name"]
        if name == "{name-not-set}":
            continue
        if "tags" in channel and len(channel["tags"]):
            tag_list=[]
            for t in channel["tags"]:
                if t in channel_tags:
                    tag_list.append(channel_tags[t])
            tags="("+", ".join(tag_list)+")"
        channel["new_name"]=name
        if radio_tag in channel["tags"]:
            continue
        if not tv_tag is None and not tv_tag in channel["tags"]:
            continue
        ch=TVChannel(name, tags, channel["uuid"])
        channel_list.append(ch)
    channel_hash={}
    for ch in channel_list:
        channel_hash[ch.hls_uuid]=ch
    return (sorted(channel_list, key=lambda x: x.name), channel_hash)

end_program=0
main_thread={}


print("Getting service list from tvheadend")

channel_list=[]
channel_hash={}

(channel_list, channel_hash) = tvhedend_get_tv_channellist()

        
print("%s channels for TV services" % len(channel_list))

app = FastAPI()

@app.get("/")
async def read_root():
    data="<html><head><title>Channels</title></head>"
    data=data+"<body>"
    data=data+"<table>"
    data=data+'<tr><th scope="col">Name</th><th scope="col">Tags</th> </tr>'
    for service in channel_list:
        name=service.name
        tags=service.tags
        uuid=service.tvh_uuid
        data=data+'<tr><td><a href="stream?uuid='+html.escape(uuid)+'">'+html.escape(name)+'</a></td><td>'+html.escape(tags)+'</td></tr>'
    data=data+"</table>"
    data=data+"</body></html>"
    return Response(content=data, media_type="text/html;charset=utf-8")

@app.get("stream.m3u8")
async def read_m3u8(uuid: str=""):
    if not uuid in channel_hash:
        return Response(content="NIX", media_type="text/plain;charset=utf-8")
    channel=channel_hash[uuid]
    res=channel.start_stream()
    if (res==False):
        return Response(content="NIX", media_type="text/plain;charset=utf-8")

    data=""
    m3u8=open(channel.m3u8_file , "r")
    for line in m3u8.readlines():
        if line[0]=="#":
            data=data+line
        else:
            data=data+config["static_http_path"]+line

    return Response(content=data, media_type="text/plain;charset=utf-8")


def player_page(uri: str="", name: str=""):
    data="<html><head><title>%s</title></head>"
    data=data+"<body>"
    data=data+'<script src="//cdn.jsdelivr.net/npm/hls.js@1"></script>'
    data=data+'''
    <center>
      <h1>%s</h1>
      <video height="576" id="video" controls></video>
    </center>

    <script>
      var video = document.getElementById('video');
      if (Hls.isSupported()) {
        var hls = new Hls({
          debug: true,
        });
        hls.loadSource('%s');
        hls.attachMedia(video);
        hls.on(Hls.Events.MEDIA_ATTACHED, function () {
          video.muted=true;
          video.play();
        });
      }
      else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = '%s';
        video.addEventListener('canplay', function () {
          video.muted=true;
          video.play();
        });
      }
    </script>
''' % (html.escape(name),uri,uri)
    data=data+'<br><a href="%s">URL for use with VLC</a>'%(uri)
    data=data+"</body>"

    return Response(content=data, media_type="text/html;charset=utf-8")


@app.get("/stream")
async def read_stream(uuid: str=""):
    if not uuid in channel_hash:
        return Response(content="NIX", media_type="text/plain;charset=utf-8")
    channel=channel_hash[uuid]
    res=channel.start_stream()
    if res:
        return player_page(res, channel.name)
    data='<html><head><title>Bitte warten</title><meta http-equiv="refresh" content="5"></head>'
    data=data+"<body>"
    data=data+"Bitte warten, Stream startet"
    data=data+"</body></html>"
    return Response(content=data, media_type="text/html;charset=utf-8")

def check_status():
    global main_thread
    global stream_ffmpeg
    while main_thread.is_alive():
        for channel in channel_list:
            if channel.stream is None:
                continue
            age=time.time()-channel.last_used
            if (age>90):
                channel.stream.kill()
                time.sleep(1)
            if channel.stream.poll() is None:
                continue
            channel.clean_stream()
        time.sleep(1)

if __name__ == "__main__":
    main_thread=threading.currentThread()
    x=threading.Thread(target=check_status, args=[])
    x.start()
    uvicorn.run(app, port=8888, host="0.0.0.0", log_level="info")    
    print("Ending program")
    end_program=1





