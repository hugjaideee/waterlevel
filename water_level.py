# ngrok imports
from ngrok import forward
import os
# water level imports
from requests import get, post
from base64 import b64decode
import cv2 as cv
from time import sleep, time
from math import ceil, floor, isclose
from traceback import print_exc
import numpy as np
from datetime import datetime
from statistics import median
# webhook imports
from threading import Thread
from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix
from waitress import serve
from json import dumps

# init
print("running file...")
datapath = os.path.dirname(__file__)+"\data"
if not os.path.exists(datapath):
    os.makedirs(datapath)
os.chdir(datapath)

def runProcess(target):
    Thread(target=target).start()

########################################## NGROK

print("starting ngrok server...")
ngrokHTTPtoken = os.environ.get("ngrok_http_authtoken")
ngrokListener = forward(addr="localhost:5051",authtoken=ngrokHTTPtoken, domain="regular-equally-shrew.ngrok-free.app")
print("done")

########################################## WATER LEVEL

def ReadWaterLevel():
    global alertLevelString
    global alertLevelReference
    global alertLevel
    global alertLevelThreshholds
    global averageWaterLevel
    global userIds
            
    waterLevelList = []
    waterLevelReference = [360, 350, 340, 329, 319, 308, 297, 286, 274, 262, 250, 237, 223, 209, 195, 179, 162, 145, 125, 105, 85, 65, 45]
    # pixels               0    50   100  150  200  250  300  350  400  450  500  550  600  650  700  750  800  850  900  950  1000 1050 1100
    alertLevelReference = ["NORMAL", "HIGH", "VERY HIGH", "CRITICAL","NONE"]
    alertLevelThreshholds = [180,220,250,"NONE"]
    alertLevel = 3
    alertLevelString = "OK"
    playlist = ""
    hourimg = 0
    hourpush = 0
    averageWaterLevel = 0

    while True:

        # write video from cctv
        print("getting mp4 filename...")
        try:
            while playlist == eval(get("http://101.109.253.60:8999/load.jsp").text)["videoname"]:
                sleep(1)
            playlist = eval(get("http://101.109.253.60:8999/load.jsp").text)["videoname"]
            print(f"got mp4 filename ({playlist}), getting mp4 file")
            vid = get(f"http://101.109.253.60:8999/{playlist}",timeout=60).content
            with open("waterlevel.mp4","wb") as videofile:
                videofile.write(vid)
            print("done")
        except:
            print("ERROR while getting mp4 file")
            sleep(3)
            continue
        
        # read video
        cap = cv.VideoCapture("waterlevel.mp4")
        waterLevelPixels = []
        print("reading mp4 frame-by-frame...")
        while True:

            # Capture frame-by-frame
            ret, frame = cap.read()
            if ret == False:
                break

            # image operations
            h=1080
            w=140
            y=0
            x=660

            frame = frame[y:y+h, x:x+w]
            gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
            blur = cv.GaussianBlur(gray,(5,5),0)
            edges = cv.Canny(blur, 100, 200)

            # find lowest edge pixels and append to waterLevelPixels 
            lowestedge = h
            for edge in np.flip(edges):
                if 255 in edge:
                    # print(edge)
                    # print(lowestedge)
                    waterLevelPixels.append(lowestedge)
                    break
                lowestedge -= 1
            
            # if hour is up then write 
            if userIds != [] and datetime.now().hour != hourimg:
                time = datetime.strftime(datetime.now(),r"%Y-%m-%d_%H-%M-%S")
                cv.imwrite(time+".png",frame)
                hourimg = datetime.now().hour

        # find median pixel
        medianPixels = median(waterLevelPixels)
        #  interpolate from medianPixels to medianWaterLevel using waterLevelReference
        interval = medianPixels / 50
        lowerWaterLevel = waterLevelReference[ceil(interval)]
        higherWaterLevel = waterLevelReference[floor(interval)]
        deltaWaterLevel = higherWaterLevel - lowerWaterLevel
        deltaPixel = 50
        lowerPixel = ceil(interval)*50
        medianWaterLevel = int(lowerWaterLevel + (deltaWaterLevel/deltaPixel)*(lowerPixel-medianPixels))
        print(f"median pixel wa-ter level: {medianPixels} px")
        print(f"median water level: {medianWaterLevel} cm")

        # moving average
        if len(waterLevelList) == 3:
            waterLevelList.pop(0)
        if isclose(averageWaterLevel,medianWaterLevel,abs_tol=5) or waterLevelList == []:
            waterLevelList.append(medianWaterLevel)
        averageWaterLevel = round(sum(waterLevelList)/len(waterLevelList))
        print(f"average water level: {averageWaterLevel} || list: {waterLevelList}")

    # alert system for floods
        oldAlertLevel = alertLevel
        if averageWaterLevel >= 180 and alertLevel == 0:
            alertLevel = 1
        if averageWaterLevel >= 220 and alertLevel == 1:
            alertLevel = 2
        if averageWaterLevel >= 250 and alertLevel == 2:
            alertLevel = 3
        if averageWaterLevel < 245 and alertLevel == 3:
            alertLevel = 2
        if averageWaterLevel < 215 and alertLevel == 2:
            alertLevel = 1
        if averageWaterLevel < 175 and alertLevel == 1:
            alertLevel = 0
        print("alert level: "+str(alertLevel))


        
        alertLevelString = alertLevelReference[alertLevel]
        if alertLevel > oldAlertLevel:
            Broadcast(alertLevelString)
    
    # every hour add image and send message
        if userIds != [] and datetime.now().hour != hourpush:
            for userId in userIds:
                print("PUSHING MESSAGE TO USER {}".format(userId))
                print(SendPushMessage(userId))
                hourpush = datetime.now().hour

runProcess(ReadWaterLevel)

########################################### WEBHOOK

LINE_ACCESS_TOKEN = os.environ.get("line-channel-access")
userIds = []

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)
@app.route("/webhook", methods=["POST","GET"])
def webhook():
    print("REQUEST RECIEVED")
    if request.method == "POST":
        for event in request.json["events"]:
            if event["type"] == "message" and event["message"]["type"]:
                if event["message"]["text"] == "hour":
                    global userId
                    userId = event["source"]["userId"]
                    if userId not in userIds:
                        userIds.append(userId)
                        print(f"ADDING USERID {userId} TO HOURLY LIST...")
                        reply(event["replyToken"], "Turned on hourly notifications")
                    else:
                        print(f"REMOVING USERID {userId} FROM HOURLY LIST...")
                        userIds.remove(userId)
                        reply(event["replyToken"], "Turned off hourly notifications")

                else:   
                    print("REPLYING...")
                    reply(event["replyToken"], f"Water level: {averageWaterLevel} cm"+
                          "\nWater level status: {alertLevelReference[alertLevel]}"+
                          "\n Next alert ({alertLevelReference[alertLevel+1]}) at {alertLevelThreshholds[alertLevel]} cm")
        return "POST", 200
    elif request.method == "GET":
        return "GET", 200

def reply(replytoken, content):
    LINE_API = 'https://api.line.me/v2/bot/message/reply'
    headers = {'Content-Type' : 'application/json',
               'Authorization' : f'Bearer {LINE_ACCESS_TOKEN}'}
    data = {"replyToken":f"{replytoken}",
            "messages":[
                {
                    "type":"text",
                    "text":f"{content}"
                },
                # {
                #     "type":"image",
                #     "originalContentUrl": "https://regular-equally-shrew.ngrok-free.app/webhook/static/waterlevel.jpg",
                #     "previewImageUrl": "https://regular-equally-shrew.ngrok-free.app/webhook/static/waterlevel.jpg"
                # }
                ]}
    data = dumps(data)
    message = post(LINE_API,headers=headers,data=data)
    print(f"REPLY STATUS: {message.status_code}")
    return message.status_code

def Broadcast(content):
    LINE_API = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {'Content-Type' : 'application/json',
               'Authorization' : f'Bearer {LINE_ACCESS_TOKEN}'}
    data = {
            "messages":[
                {
                    "type":"text",
                    "text":f"ALERT, water level is now {content}. ({alertLevelThreshholds[alertLevel-1]} cm)"
                }]}
    data = dumps(data)
    message = post(LINE_API,headers=headers,data=data)
    return message.status_code

def SendPushMessage(user):
    LINE_API = 'https://api.line.me/v2/bot/message/push'
    headers = { 'Content-Type' : 'application/json',
               'Authorization' : f'Bearer {LINE_ACCESS_TOKEN}'
    }
    data = {
            "to":f"{user}",
            "messages":[
                {
                    "type":"text",
                    "text":f"water level is at {averageWaterLevel} cm ({alertLevelString})"
                }]}
    data = dumps(data)
    message = post(LINE_API,headers=headers,data=data)
    return message.status_code

if __name__ == "__main__":
  serve(app, listen="localhost:5051")
