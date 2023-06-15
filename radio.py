#!/usr/bin/env python3
# if called directly requires action; start, stop or init
# otherwise called by importing & using a Flask app
# This script requires the following:
#
#    $ sudo apt-get install mpc mpd -y
#    $ sudo apt-get alsa -y

#    pip3 install python-mpd2
#    pip3 install python-crontab
# read-only FS
# requires cron, mpd.log, config to be R/W partition
# cron to call this at boot - boot
# cron to regularly check network connection, restore then call with init
# cron to keep time up to date on pi
# fail2ban to protect system
# job to regularly check network connection & reconnect if down
# systemd to run at boot & when ready
# boot - wait for network & mpd & user targer, systemd service to ensure we are connected & keep trying

import datetime
import subprocess
import time
import urllib.parse
import sys                              # read arguments passed on command line
import json                             # parse the config file
#import hashlib                          # compare config file & config string
# from Adafruit_MAX9744 import MAX9744    # amplifier i2c control
from mpd import (MPDClient, CommandError, MPDError)             # music player daemon
# from socket import (error as SocketError, timeout as SocketTimeout)
import socket
# from flask import Flask, render_template
from crontab import CronTab             # create & delete cron entries
# import RPi.GPIO                       # detect if read/write config is enabled
import requests                         # check internet connection
# read calling parameter(s)
# called by Flask, from cron, start, cron stop, or after boot
CONFIG_FILE = '/boot/radio_config.json'
LOG_FILE = '/home/jdf/radio.log'
#CON_ID = {'host':HOST, 'port':PORT}
CON_ID = {'host':'/run/mpd/socket'}
CON_TIMEOUT = 20


class ConfigObject():
    '''# url
    # schedule list
    # status, playing/stopped
    # could extend
    # print('config class')
    # init
    # validate config method
    # read config method
    # write config method
    '''
    def __init__(self,cfg_file=CONFIG_FILE):
        self.status = 'initialising'
        try:
            settings = self.read_config(cfg_file)
        except:
           print('load settings failed!')
           self.status = 'init failed'
           # quit here, fatal error
        # print(settings.items())
        self.volume = settings['volume']
        self.fade_secs = settings['fade_secs']
        self.station = settings['station_url']
        self.schedule = settings['events']

    def read_config(self, file):
        '''# read json config file
        # populate config class
        '''
        print('read config file')
        # return (url, volume, events as a list (start_day, start_time, stop_day, stop_time))
        # Open the JSON file
        with open(file, 'r', encoding="utf-8") as f:
            # Load the JSON data
            data = json.load(f)

            # Access RadioURL value
            #TD handle no url - fatal?
            station_url = data["station_url"]
            # print(f"Radio URL: {station_url}")
            # Access volume value
            #TD handle no volume - fatal?
            # fade_secs = data["fade_time"]
            # volume = data["volume"]
            print(f'volume: {data["volume"]}')

            # Access event values
            events = data["event"]
            schedule = [] # create a list
            for event in events:
                #start_day = event["start_day"]  # Monday, Tuesday etc
                #start_time = event["start_time"]  # HH:MM as a time object?
                #stop_time = event["stop_time"]
                # if any of these not present then return fatal error
                
                if event["start_day"] is None or event["start_time"] is None or event["stop_time"] is None:
                    print("A config parameter is missing!")
                    # TD return a fatal error

                stop_day = event["stopDay"] if "stopDay" in event else None
                if stop_day is None:
                    stop_day = event["start_day"]
                # print(f"Event: {start_day} {start_time} - {stop_day} {stop_time}")
                # append each event as a dictionary
                event_details = {
					"startDOW": event["start_day"],
					"startHHMM": event["start_time"],
					"stopDOW": stop_day,
					"stopHHMM": event["stop_time"],
                }
                schedule.append(event_details)

        # return a dictionary of values

        # print(f'done\n{schedule}')
        settings={
        	"volume": data["volume"],
        	"fade_secs": data["fade_time"],
        	"station_url": station_url,
        	"events": schedule
        }
        # settings[events] = schedule
        return settings
        
    def checkSchedule(self):
        '''# check if we should be playing now, return boolean
        # schedule contains dow as 0 = Monday
        this should be in config pobject
        TD check if multiple events for 1 day
        '''
        should_be_playing = False
        d_t = datetime.datetime.now()
        event_list = self.schedule
        # print(event_list)
        # get day/time
        # get day of week as an integer
        dow = d_t.strftime('%A')
        # get matching schedule day & get start time & end time
        today = list(filter(lambda event_list: event_list['startDOW'] == dow, event_list))
        # print(f'today count={len(today)}')
        for item in range(len(today)):
            t_start = datetime.datetime.strptime(today[item]['startHHMM'],'%H:%M')
            # TD: need to handle if stopping next day
            t_end = datetime.datetime.strptime(today[item]['stopHHMM'],'%H:%M')
            # if now > start time & now < end time
            if d_t.time() > t_start.time() and d_t.time() < t_end.time():
                should_be_playing = True
            print(f'should be playing:{should_be_playing}')
            # print(f'today is {today[item]["startDOW"]}, start time is {today[item]["startHHMM"]}, end time is {today[item]["stopHHMM"]}, should be playing?:{should_be_playing}')
        return should_be_playing


def mpdConnect(client, con_id):
    ''' return a connection reference  to mpd player
    ConnectionError("Already connected")
    '''
    conn = False
    for attempt in range(3):
        try:
            client.timeout = CON_TIMEOUT                # network timeout in seconds (floats allowed),
            client.connect(**con_id)
            conn = True
        except socket.timeout: # SocketTimeout:
            print("socket timed out")
            subprocess.call(["sudo", "systemctl", "restart", "mpd"])
            print("restarted MPD")
        except socket.error as msg: # SocketError as msg:
            print(f'Socket Error: {str(msg)}')
            conn = False
        except MPDError as msg:
            if str(msg) == "Already connected":
                conn = True
            else:
                print(f'Connection Error: {str(msg)}')
                conn = False
        else:
            break # stop the attempts
    if conn == False: # end of attempts
    	print("connection attempts failed: Fatal error")
    return conn


def clear_cron():
    ''' delete cron created by this script
    '''
    with CronTab(user=True) as cron:
        cron.remove_all(comment='jdf_radio')
    print('deleted cron jobs')


def set_cron(config, script):
    '''# create a cron job
    # print('create cron job')
    # get events list
    '''
    event_list = config.schedule
    for event in event_list:
        # st day, st time, stop time
        start_day, start_time, stop_time = event['startDOW'], event['startHHMM'], event['stopHHMM']
        stop_day = event['stopDOW'] if event['stopDOW'] else event['startDOW']
        with CronTab(user=True) as cron:
            start_job = cron.new(command=script + ' start ' + config.station, comment='jdf_radio')
            start_job.dow.on(start_day[0:3])
            start_job.minute.on(start_time[-2:])
            start_job.hour.on(start_time[0:2])

            end_job = cron.new(command=script + ' stop', comment='jdf_radio')
            end_job.dow.on(stop_day[0:3])
            end_job.minute.on(stop_time[-2:])
            end_job.hour.on(stop_time[0:2])
    print('cron.write() was just executed')


def netConnection(config):
    ''' check that we can connect to the URL vbefore attemepting to start playing
        returns boolean
    '''
    os_up = login_up = connected = mpd_up = False
    # stat = os.system('service systemd-logind status')
    stat = subprocess.call(["systemctl", "is-active", "--quiet", "systemd-logind"])
    # if stat == 0: login_up = True
    login_up = True if (stat == 0) else False

    # stat = subprocess.call(["service", "mpd", "status"])
    stat = subprocess.call(["systemctl", "is-active", "--quiet", "mpd"])
    mpd_up = True if (stat == 0) else False

    # if not os_up: time.sleep(3)
    if login_up & mpd_up:
        domain = urllib.parse.urlparse(config.station)
        url = domain.scheme + '://' + domain.netloc
        timeout = 3
        try:
            # requesting URL
            requests.get(url, timeout=timeout)
            print("Internet is connected")
            connected = True
        # catching exception
        except (requests.ConnectionError,
                requests.Timeout) as exception:
            print(f"Internet is not available:\n{exception}")
            # wait a bit longer
            time.sleep(10)
    os_up = connected & login_up  & mpd_up
    print(f'netConnection is {os_up}')
    return os_up


def boot():
    '''
    '''
    print(datetime.datetime.now())
    if mpdConnect(player, CON_ID):
        print('Got connected!')
    else:
        print('fail to connect MPD server.')
        # clear cron created by this script
    clear_cron()
    # write cron jobs
    set_cron(config, str(sys.argv[0]))
    # wait (indefinitely) for network
    while netConnection(config) is False:
        time.sleep(5)
        # this will loop indefinitely
        # unless another script or dervice is trying to restore the connection
    # check schedule
    # call start if appropriate
    if config.checkSchedule():
        startStream(config.station)
    else:   
        # play some sound so we know that things are working
        player.add(config.station)
        player.play() # really this should be a soft startup sound
        time.sleep(5)  
        endStream()
    # finally
    player.disconnect()
    print('end of boot sequence')


def endStream():
    ''' stop playing & clear queue
    '''
    if mpdConnect(player, CON_ID):
        print('Got connected!')
    else:
        print('fail to connect MPD server.')
    player.stop()
    player.clear()
    player.disconnect()
    print('stopped stream')


def startStream(url):
    ''' 
    '''
    if mpdConnect(player, CON_ID):
        print('mpd connected!')
    else:
        print('fail to connect MPD server.')
    player.add(url)
    player.play()
    wdg(force=True)
    player.disconnect()
    # ensure a service or thread (?) is running to check stream is still active
    # while status playing  = playing
    # TD this is the big TODO!


def wdg(force=False):
    '''
    '''
    last_elapsed = 0
    restart_reqd = True
    while config.checkSchedule() or force:  # should be playing?
        if mpdConnect(player, CON_ID):
            print('Got connected!')
        else:
            print('fail to connect MPD server.')
        status = player.status()
        if "error" in status:
            restart_reqd = True
            print(f'error: {status["error"]}')
        if "state" in status and status["state"] == "play":
            if last_elapsed < float(status["elapsed"]):
                last_elapsed = float(status["elapsed"])
                restart_reqd = False
                print(f'playing OK: {last_elapsed}')
            else:
                restart_reqd = True
                print(f'elapsed time is stuck {last_elapsed}:{status["elapsed"]}')
        if restart_reqd:    
            player.clearerror()
            # player.add(url)
            player.stop()
            player.play()
            print(f'restarting stream')
            status = player.status() # reload status
            if "elapsed" in status:
                last_elapsed = float(status["elapsed"])
        player.disconnect()
        time.sleep(10)
    print('schedule says stop')

def test(player):
    ''' 
    '''
    config = ConfigObject(CONFIG_FILE)
    url = config.station
    #if mpdConnect(player, CON_ID):
    #    print('Got connected!')
    #else:
    #    print('fail to connect MPD server.')
    #player.add(url)
    #player.play()
    #print(player.status())
    #time.sleep(3)
    #print(player.status())
    #time.sleep(3)
    #player.stop()
    #print(player.status())
    #player.clear()
    startStream(url)
    player.disconnect()
    


# #################### main
player = MPDClient()
config = ConfigObject(CONFIG_FILE)
if mpdConnect(player, CON_ID):
    print('Got connected!')
else:
    print('fail to connect MPD server.')

if __name__ == "__main__":
    # if scriupt is being called directly (rather than imported)
    actions = ('start', 'stop', 'boot', 'test')
    # print(f'length;{len(sys.argv)} text:{str(sys.argv)}')
    if len(sys.argv) >= 2 and str(sys.argv[1]) in actions:
        print(str(sys.argv))
        # then call that action
        #action=locals()[sys.argv[1]]
        #action()
        action=sys.argv[1]
        if action == "boot":
            boot()
        if action == 'start':
            # check if we have a url passed, if not try to read it.
            try:
                url = sys.argv[2]
            except IndexError:
                config = ConfigObject(CONFIG_FILE)
                url = config.station
            startStream(url)
        if action == 'stop':
            endStream()
        if action == 'test':
            print('start test')
            test(player)
