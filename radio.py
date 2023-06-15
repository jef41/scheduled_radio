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
#from subprocess import Popen, PIPE, run
import time
# import logging
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
DEBUG = True # enable log file
WDG_INT = 10  # check stream every # seconds

def debugPrint(instr):
    ''' routine to format and display messages if debug is true
        TD routine to check file size & trim
    '''
    col = 35  # magenta debug messages

    if DEBUG:
        # write to console & log in file
        print(f"\033[{col}m{instr}\033[0m")
        with open(LOG_FILE, 'a') as f:
            f.write(f'\n{time.strftime("%Y-%m-%d-%H:%M:%S", time.localtime())}\t{instr}')
        f.close()
    # else:
    #     # print to console
    #     debugPrint(f"\033[{col}m{instr}\033[0m")


def timeInt(day, hhmm):
    ''' convert input strings to a minute epoch since start of week
        Monday = 0
        Tuesday 15:30 = 1*24*60 + 15*60 + 30 = 2370
    '''
    weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    if type(day) is str:
        weekday = weekdays.index(day.lower())
    else:
        weekday = int(day)
    hours = int(hhmm.split(':')[0])
    minutes = int(hhmm.split(':')[1])
    val = (weekday * 1440) + (hours * 60) + minutes
    return val


class ConfigObject():
    '''# url
    # schedule list
    # status, playing/stopped
    # could extend
    # debugPrint('config class')
    # init
    # validate config method
    # read config method
    # write config method
    '''
    def __init__(self,cfg_file=CONFIG_FILE):
        self.status = 'initialising'
        try:
            settings = self.read_config(cfg_file)
        except Exception as err:
           debugPrint(f'load settings failed!\n {err}')
           self.status = 'init failed'
           # quit here, fatal error
        # debugPrint(settings.items())
        # TD test for settings here or fatal error
        self.volume = settings['volume']
        self.fade_secs = settings['fade_secs']
        self.station = settings['station_url']
        self.schedule = settings['events']
        self.int_schedule = settings['int_events']

    def read_config(self, file):
        '''# read json config file
        # populate config class
        '''
        debugPrint('read config file')
        # return (url, volume, events as a list (start_day, start_time, stop_day, stop_time))
        # Open the JSON file
        with open(file, 'r', encoding="utf-8") as f:
            # Load the JSON data
            data = json.load(f)

            # Access RadioURL value
            #TD handle no url - fatal?
            station_url = data["station_url"]
            # debugPrint(f"Radio URL: {station_url}")
            # Access volume value
            #TD handle no volume - fatal?
            # fade_secs = data["fade_time"]
            # volume = data["volume"]
            debugPrint(f'volume: {data["volume"]}')

            # Access event values
            events = data["event"]
            schedule = [] # create a list
            int_sched = []  # a list of week-epoch minutes
            for event in events:
                #start_day = event["start_day"]  # Monday, Tuesday etc
                #start_time = event["start_time"]  # HH:MM as a time object?
                #stop_time = event["stop_time"]
                # if any of these not present then return fatal error
                
                if event["start_day"] is None or event["start_time"] is None or event["stop_time"] is None:
                    debugPrint("A config parameter is missing!")
                    # TD return a fatal error

                if "stop_day" in event:
                    stop_day = event["stop_day"] 
                else:
                    stop_day = event["start_day"]
                # debugPrint(f"Event: {start_day} {start_time} - {stop_day} {stop_time}")
                # append each event as a dictionary
                event_details = {
					"startDOW": event["start_day"],
					"startHHMM": event["start_time"],
					"stopDOW": stop_day,
					"stopHHMM": event["stop_time"],
                }
                schedule.append(event_details)
                # now save the same info as epoch from start of week in minutes
                event_epoch = {
                    "intStart": timeInt(event["start_day"], event["start_time"]),
                    "intStop": timeInt(stop_day, event["stop_time"])
                }
                int_sched.append(event_epoch)

        # return a dictionary of values

        # debugPrint(f'done\n{schedule}')
        settings={
        	"volume": data["volume"],
        	"fade_secs": data["fade_time"],
        	"station_url": station_url,
        	"events": schedule,
        	"int_events": int_sched
        }
        # settings[events] = schedule
        return settings
        
    def checkScheduleOLD(self):
        '''# check if we should be playing now, return boolean
        # schedule contains dow as 0 = Monday
        this should be in config pobject
        TD check if multiple events for 1 day
        '''
        should_be_playing = False
        d_t = datetime.datetime.now()
        event_list = self.schedule
        # debugPrint(event_list)
        # get day/time
        # get day of week as string name
        dow = d_t.strftime('%A')
        # get matching schedule day & get start time & end time
        today = list(filter(lambda event_list: event_list['startDOW'] == dow, event_list))
        # debugPrint(f'today count={len(today)}')
        for item in range(len(today)):
            t_start = datetime.datetime.strptime(d_t.strftime('%Y') + d_t.strftime('%W') + today[item]['startDOW']+today[item]['startHHMM'],'%Y%W%A%H:%M')
            # TD: need to handle if stopping next day
            t_end = datetime.datetime.strptime(d_t.strftime('%Y') + d_t.strftime('%W') + today[item]['stopDOW'] + today[item]['stopHHMM'],'%Y%W%A%H:%M')
            # if now > start time & now < end time
            if d_t.time() > t_start.time() and d_t.time() < t_end.time():
                should_be_playing = True
                debugPrint(f'should be playing:{should_be_playing}')
            # debugPrint(f'today is {today[item]["startDOW"]}, start time is {today[item]["startHHMM"]}, end time is {today[item]["stopHHMM"]}, should be playing?:{should_be_playing}')
        return should_be_playing


    def checkSchedule(self):
        ''' uses an epoch of minutes from start of Monday
            read the schedule & compare integers
        '''
        should_be_playing = False
        d_t = datetime.datetime.now()
        nowInt = timeInt(d_t.weekday(), str(d_t.hour) + ':' + str(d_t.minute))
        for event in self.int_schedule:
            if event["intStart"] <= nowInt <= event["intStop"]:
                should_be_playing = True
                #debugPrint('should be playing')
                debugPrint(f'should be playing:{should_be_playing}')
                break
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
            debugPrint("socket timed out")
            subprocess.call(["sudo", "systemctl", "restart", "mpd"])
            debugPrint("restarted MPD")
        except socket.error as msg: # SocketError as msg:
            debugPrint(f'Socket Error: {str(msg)}')
            conn = False
        except MPDError as msg:
            if str(msg) == "Already connected":
                conn = True
            else:
                debugPrint(f'Connection Error: {str(msg)}')
                conn = False
        else:
            break # stop the attempts
    if conn == False: # end of attempts
    	debugPrint("connection attempts failed: Fatal error")
    return conn


def clear_cron():
    ''' delete cron created by this script
    '''
    with CronTab(user=True) as cron:
        cron.remove_all(comment='jdf_radio')
    debugPrint('deleted cron jobs')


def set_cron(config, script):
    '''# create a cron job
    # debugPrint('create cron job')
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
            start_job.minute.on(start_time.split(':')[1])
            start_job.hour.on(start_time.split(':')[0])

            end_job = cron.new(command=script + ' stop', comment='jdf_radio')
            end_job.dow.on(stop_day[0:3])
            end_job.minute.on(stop_time.split(':')[1])
            end_job.hour.on(stop_time.split(':')[0])
    debugPrint('cron.write() was just executed')


def netConnectionOLD(config):
    ''' check that we can connect to the URL before attemepting to start playing
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
            debugPrint("Internet is connected")
            connected = True
        # catching exception
        except (requests.ConnectionError,
                requests.Timeout) as exception:
            debugPrint(f"Internet is not available:\n{exception}")
            # wait a bit longer
            time.sleep(10)
    os_up = connected & login_up  & mpd_up
    debugPrint(f'netConnection is {os_up}')
    return os_up


def checkServices():
    ''' wait indefinitely for relevant services to start
    '''
    os_up = login_up = mpd_up = False
    # stat = os.system('service systemd-logind status')
    stat = subprocess.call(["systemctl", "is-active", "--quiet", "systemd-logind"])
    # if stat == 0: login_up = True
    login_up = True if (stat == 0) else False

    # stat = subprocess.call(["service", "mpd", "status"])
    stat = subprocess.call(["systemctl", "is-active", "--quiet", "mpd"])
    mpd_up = True if (stat == 0) else False

    os_up = login_up & mpd_up
    debugPrint(f'checkServices is {os_up}')
    return os_up


def boot():
    '''
    '''
    debugPrint('start of def boot')
    clear_cron()
    # write cron jobs
    set_cron(config, str(sys.argv[0]))
    # wait (indefinitely) for network
    # while netConnection(config) is False:
    debugPrint('boot sequence - check services')
    while checkServices() is False:
        time.sleep(5)
        # this will loop indefinitely
        # unless another script or service is trying to start mpd & login
    # check schedule
    # call start if appropriate
    debugPrint('boot sequence - OS is ready')
    if config.checkSchedule():
        debugPrint('boot sequence - schedule says play')
        startStream(config.station)
    else:   
        # play some sound so we know that things are working
        checkNet()
        # this should get network working
        player.disconnect()
        if mpdConnect(player, CON_ID):
            debugPrint('boot sequence - MPD connection')
        else:
            debugPrint('boot sequence - fail to connect MPD server.')
            # clear cron created by this script
        debugPrint('boot sequence - just play startup')
        # player.add(config.station)
        player.add('file:///home/jdf/waterdrops.mp3')
        player.play() # really this should be a soft startup sound
        time.sleep(5)  
        endStream()
    # finally
    player.disconnect()
    debugPrint('end of def boot')


def endStream():
    ''' stop playing & clear queue
    '''
    if mpdConnect(player, CON_ID):
        debugPrint('Got connected!')
    else:
        debugPrint('fail to connect MPD server.')
    player.stop()
    player.clear()
    player.disconnect()
    debugPrint('stopped stream')


def startStream(url):
    ''' 
    '''
    if mpdConnect(player, CON_ID):
        debugPrint('mpd connected!')
    else:
        debugPrint('failed to connect MPD server.')
    player.add(url)
    player.play()
    #wdg(force=True)  # for testing
    wdg()
    # wdg should run until stream ends
    # & ensure stream keeps working as long as it should
    player.disconnect()


def wdg(force=False):
    '''
    '''
    last_elapsed = 0
    restart_reqd = True
    while config.checkSchedule() or force:  # should be playing?
        if mpdConnect(player, CON_ID):
            debugPrint('MPD wdg connected')
        else:
            debugPrint('fail to connect MPD server.')
        status = player.status()
        try:
            if (last_elapsed < float(status["elapsed"]) or last_elapsed + float(status["elapsed"]) == 0):
                last_elapsed = float(status["elapsed"])
                restart_reqd = False
                debugPrint(f'playing OK: {last_elapsed}')
            else:
                restart_reqd = True
                debugPrint(f'elapsed time is stuck {last_elapsed}:{status["elapsed"]}')
        except KeyError:
                debugPrint(f'not playing:{status}')
                restart_reqd = True
                # break  # out of try
        finally:
            if restart_reqd:   
                # check net connection
                domain = urllib.parse.urlparse(config.station)
                checkNet(domain)
                debugPrint('back from checkNet')
                player.clearerror()
                # player.add(url)
                player.stop()
                player.play()
                debugPrint('attempted to restart stream')
                status = player.status() # reload status
                if "elapsed" in status:
                    # reset elapsed counter
                    last_elapsed = float(status["elapsed"])
        player.disconnect()
        time.sleep(WDG_INT)
    debugPrint('schedule says stop')


def checkNet(gw='google.com'):
    ''' check that we have a functional network connection
        if not try by restarting dhcpcp, or rebooting
        will loop until we can ping the target
    '''
    ping_worked = False
    connected = False
    fail_count = 0
    while not connected:
        debugPrint('check Network')
        pw = 2
        # TD add wait internval - was fail instant or waited for timout
        for i in range(20):
            t_start = time.perf_counter()
            command = ['ping', '-c', '1', '-W', str(pw), gw]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0:
                ping_worked = True
                debugPrint('ping test worked')
                break  # out of for loop
            else:
                debugPrint(f'could not ping {gw}')
                fail_count += 1
                t_stop = time.perf_counter()
                elapsed = t_stop - t_start
                if elapsed < pw:
                    # we probably received an immediate not accessible
                    # so wait a little longer before retrying
                    time.sleep(5)
        connected = True if ping_worked is True else False
        # it could be that the url is down, or that the wifi is not up
        if not connected:
            # restart network connection
            if fail_count < 10:
                debugPrint('restart wlan0')
                #command = ['sudo', 'ip', 'link', 'set', 'wlan0', 'down']
                #result = subprocess.run(command, capture_output=True, text=True)
                #debugPrint(f'wlan0 down;\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                #command = ['sudo', 'ip', 'link', 'set', 'wlan0', 'up']
                #result = subprocess.run(command, capture_output=True, text=True)
                #debugPrint(f'wlan0 up\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                command = ['sudo', 'systemctl', 'restart', 'dhcpcd']
                result = subprocess.run(command, capture_output=True, text=True)
                debugPrint(f'dhcpcd restart:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                # TD print IP')
            else:
                # we have failed repeatedly with dhcpcd
                debugPrint('ping has failed for a long time, try a reboot')
                command = ['sudo', 'init', '6']
                result = subprocess.run(command)
    # if we get here should be connected
    # the problem in development was that the USB wifi was someitmes not detected
    # adding usbcore.old_scheme_first=1 
    # to /boot/cmdline.txt resolved that


def test(player):
    ''' 
    '''
    #config = ConfigObject(CONFIG_FILE)
    #url = config.station
    if mpdConnect(player, CON_ID):
        debugPrint('Got connected!')
    else:
        debugPrint('fail to connect MPD server.')
    player.add('file:///home/jdf/waterdrops.mp3')
    #player.clear()
    player.play()
    debugPrint(player.status())
    time.sleep(3)
    #debugPrint(player.status())
    #time.sleep(3)
    player.stop()
    #debugPrint(player.status())
    player.clear()
    #startStream(url)
    player.disconnect()
    


# #################### main

debugPrint('\nscript called')
player = MPDClient()
config = ConfigObject(CONFIG_FILE)
if mpdConnect(player, CON_ID):
    debugPrint('MPD connected!')
else:
    debugPrint('fail to connect MPD server.')

if __name__ == "__main__":
    # if scriupt is being called directly (rather than imported)
    actions = ('start', 'stop', 'boot', 'test')
    # debugPrint(f'length;{len(sys.argv)} text:{str(sys.argv)}')
    if len(sys.argv) >= 2 and str(sys.argv[1]) in actions:
        debugPrint(str(sys.argv))
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
            debugPrint('start test')
            test(player)
