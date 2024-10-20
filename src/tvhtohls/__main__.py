#!/usr/bin/env python3
import pathlib

import uvicorn
import os
from fastapi import FastAPI, Response, BackgroundTasks
from fastapi.staticfiles import StaticFiles
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
config["hls_local_path"]="/tmp/tvhtohls/hls"
config["hls_http_path"]="hls/"
config["static_local_path"]=pathlib.Path(__file__).parent / 'static'
config["static_http_path"]="static/"
config["sort"]="name" # or "number"
config["segment_len"]=5

for setting in config.keys():
    if setting in os.environ:
        config[setting]=os.environ[setting]

if not os.path.isdir(config["hls_local_path"]):
    print("hls_local_path '%s' is not a directory" % config["hls_local_path"])
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


tvc_hash={}
def clean_name(name):
    global tvc_hash
    out=""
    for x in name.upper():
        if x>='A' and x<='Z':
            out=out+x
        if x>='0' and x<='9':
            out=out+x
        if x==' ':
            out=out+'_'
    if len(out)<2:
        out="INVALID"
    if out in tvc_hash:
        tvc_hash[out]=tvc_hash[out]+1
        out=out+"-"+str(tvc_hash[out])
    else:
        tvc_hash[out]=1
    return out

class TVChannel:
    def __init__(self, name, tags, number, tvh_uuid):
        self.name=name
        self.tags=tags
        self.number=number
        self.tvh_uuid=tvh_uuid
        self.hls_uuid=clean_name(name)
        self.tvh_url=tvh_base_url_auth+"stream/channel/"+tvh_uuid
        self.m3u8_file=config["hls_local_path"]+"/"+self.hls_uuid+".m3u8"
        self.stream=None
        self.last_used=time.time()
        self.clean_stream()
    def start_stream(self):
        self.last_used=time.time()
        if self.stream:
            if os.path.isfile(self.m3u8_file):
                return "stream.m3u8?uuid="+self.hls_uuid
            else:
                if self.stream.poll() is None:
                    return False
        #Start stream
        self.stream=subprocess.Popen(["/usr/bin/ffmpeg", "-i", self.tvh_url, "-probesize", "100000",
            "-f", "hls", 
            "-preset", "veryfast", 
            "-sc_threshold", "0", 
            "-map", "v:0", "-c:v:0", "libx264", "-b:v:0", "2000k",
            "-map", "v:0", "-c:v:1", "libx264", "-b:v:1", "500k",
  #          "-map", "v:0", "-c:v:2", "libx264", "-b:v:1", "100k",
            "-map", "a:0", "-map", "a:0","-c:a", "aac", "-b:a", "96k", "-ac", "2",
            "-filter:v:0", "yadif,scale=720:576",
            "-filter:v:1", "yadif,scale=512:288",
#            "-filter:v:2", "yadif,scale=256:144",
            "-f", "hls",
            "-r", "25", "-sn",
            "-hls_flags", "delete_segments",
            "-hls_flags", "independent_segments",
            "-hls_segment_filename", config["hls_local_path"]+"/"+self.hls_uuid+"_%v_%02d.ts",
            "-hls_list_size", "10",
            "-hls_time", str(config["segment_len"]), "-hls_playlist_type", "event",
            "-master_pl_name", self.hls_uuid+".m3u8",
            "-var_stream_map", "v:0,a:0, v:1,a:1 ", self.m3u8_file+"+%v"
            ])#, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        self.last_used=time.time()
        return False
    def clean_stream(self):
        stream_path_base=config["hls_local_path"]
        files=os.listdir(stream_path_base)
        for f in files:
            if f.startswith(self.hls_uuid):
                print("clean_stream: erasing file %s" % (f))
                os.remove(stream_path_base+"/"+f)
        self.stream=None
        return True

class tv_channel_epg:
    def __init__(self, uuid, event_hash):
        self.uuid=uuid
        self.now=None
        self.events={}
        self.last_update=time.time()
        self.add(event_hash)
    def add(self, event_hash):
        eventid=event_hash["eventId"]
        event_hash["start"]=int(event_hash["start"])
        event_hash["stop"]=int(event_hash["stop"])
        stop=event_hash["stop"]
        if stop<time.time():
            return
        self.events[eventid]=event_hash
        if (self.now is None) or (not self.now in self.events) or self.events[eventid]["start"]<self.events[self.now]["start"]:
            self.now=eventid
    def update(self):
        if self.now is None:
            return False
        while self.now in self.events and (self.events[self.now]["stop"]<time.time()):
            ev=self.events[self.now]
            del self.events[self.now]
            if "nextEventId" in ev:
                self.now=ev["nextEventId"]
            else:
                self.now=None
        if self.now in self.events:
            return False
        print("Getting fresh EPG for channel %s"%self.uuid)
        epg_json=tvheadend_get(tvh_base_url+"/api/epg/events/grid?limit=10&channel="+self.uuid)
        for event in epg_json["entries"]:
            channel_uuid=event["channelUuid"]
            if channel_uuid==self.uuid:
                self.add(event)
        return True
    def format_now_next(self):
        if self.now is None:
            return ""
        try:
            self.update()
            data=""
            if self.now in self.events:
                cur=self.events[self.now]
                if "title" in cur:
                    data="<b>"+html.escape(cur["title"])+"</b>"
                remaining=(cur["stop"]-time.time())/60
                data=data+" {:9.1f} min".format(remaining)
                if "nextEventId" in cur and cur["nextEventId"] in self.events:
                    nxt=self.events[cur["nextEventId"]]
                    if "title" in nxt:
                        data=data+" <b>"+html.escape(nxt["title"])+"</b>"
            return data
        except:
            return "Error"


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
                    if t!=tv_tag:
                        tag_list.append(channel_tags[t])
            tags="("+", ".join(tag_list)+")"
        channel["new_name"]=name
        if radio_tag in channel["tags"]:
            continue
#        if not tv_tag is None and not tv_tag in channel["tags"]:
#            continue
        ch=TVChannel(name, tags, channel["number"], channel["uuid"])
        channel_list.append(ch)
    channel_hash={}
    for ch in channel_list:
        channel_hash[ch.hls_uuid]=ch
    return (sorted(channel_list, key=lambda x: getattr(x, config["sort"])), channel_hash, tv_tag)

end_program=0
main_thread={}


print("Getting service list from tvheadend")

channel_list=[]
channel_hash={}
tv_tag=None

(channel_list, channel_hash, tv_tag) = tvhedend_get_tv_channellist()

epg={}
    
print("%s channels for TV services" % len(channel_list))

print("Getting EPG")
epg_json=tvheadend_get(tvh_base_url+"/api/epg/events/grid?limit=10000")
for event in epg_json["entries"]:
    channel_uuid=event["channelUuid"]
    if channel_uuid in epg:
        epg[channel_uuid].add(event)
    else:
        epg[channel_uuid]=tv_channel_epg(channel_uuid, event)


app = FastAPI()

@app.get("/")
async def read_root(s: str="", d: str="i"):
    if not s in ("name", "number"):
        s=config["sort"]
    cl_sorted=sorted(channel_list, key=lambda x: getattr(x, s), reverse=(d=='d'))
    data="<html><head><title>Channels</title></head>"
    data=data+"<body>"
    data=data+"<h1>List of TV channels</h1>"
    data=data+"<table>"
    data=data+'<tr><th scope="col"><a href="?s=number">▲</a><a href="?s=number&d=d">▼</a></th><th scope="col">Name <a href="?s=name">▲</a><a href="?s=name&d=d">▼</a></th></th><th scope="col">Now Next</th></tr>'
    for service in cl_sorted:
        name=service.name
        if service.stream:
            name=name+" 👀"
        tags=service.tags
        uuid=service.hls_uuid
        if service.tvh_uuid in epg:
            now_next=epg[service.tvh_uuid].format_now_next()
        else:
            now_next=""
        number=str(service.number)
        if number=="0":
            number=""
        data=data+'<tr><td>'+html.escape(number)+'</td><td><a href="stream?uuid='+html.escape(uuid)+'" rel="nofollow">'+html.escape(name)+'</a></td><td>'+now_next+'</td></tr>'
    data=data+"</table>"
    data=data+"</body></html>"
    return Response(content=data, media_type="text/html;charset=utf-8")

@app.get("/stream.m3u8")
async def read_m3u8(uuid: str="", stream_id: int=-1):
    if not uuid in channel_hash:
        return Response(content="NIX", media_type="text/plain;charset=utf-8")
    channel=channel_hash[uuid]
    res=channel.start_stream()
    if (res==False):
        return Response(content="NIX", media_type="text/plain;charset=utf-8")

    suffix=""
    if stream_id>=0:
        suffix="+"+str(stream_id)
    data=""
    m3u8=open(channel.m3u8_file+suffix, "r")
    for line in m3u8.readlines():
        if line.find(".ts")>=0:
            data=data+config["hls_http_path"]+line
            continue
        if line.find(".m3u8+")>=0:
            data=data+"stream.m3u8?uuid="+line.replace(".m3u8+", "&stream_id=")
            continue
        data=data+line


    return Response(content=data, media_type="text/plain;charset=utf-8")


def player_page(uri: str="", name: str=""):
    data="<html><head><title>%s</title></head>"%html.escape(name)
    data=data+"<body>"
    data=data+'<script src="'+config["static_http_path"]+'hls.js"></script>'
    data=data+'''
    <center>
      <h1>%s</h1>
      <video width="100%%" id="video" controls></video>
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
          setTimeout(startPlayer, 5000)
        });
      }
      else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = '%s';
        video.addEventListener('canplay', function () {
          video.muted=true;
          setTimeout(startPlayer, 5000)
        });
      }

      function startPlayer() {
        video.play()
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
    data='<html><head><title>Bitte warten</title><meta http-equiv="refresh" content="1"></head>'
    data=data+"<body>"
    data=data+"Please wait while the stream is starting. This can take 30 seconds or more. This page will meanwhile reload."
    data=data+"</body></html>"
    return Response(content=data, media_type="text/html;charset=utf-8")


@app.on_event("startup")
def startup_event():
    print("startup_event")
    threading.Thread(target=check_status, daemon=True).start()

# for really static files, such as javascript, images etc
app.mount("/"+config["static_http_path"], StaticFiles(directory=config["static_local_path"]), name="static")
# HLS stream files which are dynamically generated
app.mount("/"+config["hls_http_path"], StaticFiles(directory=config["hls_local_path"]), name="hls")


def check_status():
    global main_thread
    global stream_ffmpeg
    print("check_status starting", flush=True)
    while main_thread.is_alive():
        time.sleep(1)
        try:
            print("Main_thread")
            for channel in channel_list:
                if channel.stream is None:
                    continue
                age=time.time()-channel.last_used
                if (age>30):
                    channel.stream.kill()
                    time.sleep(1)
                if channel.stream.poll() is None:
                    continue
                channel.clean_stream()
            for channel in channel_list:
                uuid=channel.tvh_uuid
                if not uuid in epg:
                    continue
                epg[uuid].update()
        except:
            print("Exception raised, continuing", flush=True)


def main():
    global main_thread, end_program
    main_thread=threading.current_thread()
    uvicorn.run(app, port=8888, host="0.0.0.0", log_level="info")    
    print("Ending program")
    end_program=1


if __name__ == "__main__":
    main()

