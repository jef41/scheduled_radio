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
import os
#from subprocess import Popen, PIPE, run
import time
import logging
from logging.handlers import RotatingFileHandler
#import requests                         # check internet connection
#import urllib   # .request, urllib.parse
#import sys                              # read arguments passed on command line
import requests
from requests.adapters import HTTPAdapter
import urllib3
#print(urllib3.__version__)
from urllib3 import Retry
from sys import exit, argv
import json                             # parse the config file
#import hashlib                          # compare config file & config string
# from Adafruit_MAX9744 import MAX9744    # amplifier i2c control
from mpd import (MPDClient, CommandError, MPDError)             # music player daemon
# from socket import (error as SocketError, timeout as SocketTimeout)
import socket
# from flask import Flask, render_template
from crontab import CronTab             # create & delete cron entries
# import RPi.GPIO                       # detect if read/write config is enabled
# read calling parameter(s)
# called by Flask, from cron, start, cron stop, or after boot
CONFIG_FILE = '/boot/radio_config.json'
LOG_FILE = '/home/pi/radio/radio.log'
SILENT_FILE = '/home/pi/radio/silent'
STARTUP_SOUND = '/home/pi/radio/waterdrops.mp3'
CON_ID = {'host':'/run/mpd/socket'}
CON_TIMEOUT = 20
DEBUG = True # enable log file
WDG_INT = 7  # check stream every # seconds

# def createLog():
''' set up logging
    log debug & info to stdout, warn + to file
'''
#logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('radio_app')
logger.setLevel(logging.DEBUG)
# logger.setLevel(logging.INFO)

# show on stdout
s_handler = logging.StreamHandler()
s_handler.setLevel(logging.DEBUG)
# log to file
f_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=150000, backupCount=7)
f_handler.setLevel(logging.INFO)
# f_handler.setLevel(logging.DEBUG)

formatter = logging.Formatter(
        '%(asctime)s: %(levelname)-8s %(message)s')
s_handler.setFormatter(formatter)
f_handler.setFormatter(formatter)
logger.addHandler(s_handler)
logger.addHandler(f_handler)


### https://majornetwork.net/2022/04/handling-retries-in-python-requests/
class TimeoutHTTPAdapter(HTTPAdapter):
    def __init__(self, *args, **kwargs):
        if "timeout" in kwargs:
            self.timeout = kwargs["timeout"]
            del kwargs["timeout"]
        else:
            self.timeout = 5   # or whatever default you want
        super().__init__(*args, **kwargs)
    def send(self, request, **kwargs):
        if kwargs["timeout"] is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)


def debugPrint(instr):
    ''' routine to format and display messages if debug is true
        TD routine to check file size & trim
        now uses Python logging
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
        hhmm format is e.g. 2:45.10
    '''
    weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    if type(day) is str:
        weekday = weekdays.index(day.lower())
    else:
        weekday = int(day)
    hours = int(hhmm.split(':')[0])
    minutes = float(hhmm.split(':')[1])
    # float allows for seconds to be present, though not currenty used
    val = (weekday * 1440) + (hours * 60) + minutes
    return val


class ConfigObject():
    '''# url
    # schedule list
    # status, playing/stopped
    # could extend
    # logger.debug('config class')
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
           logger.critical(f'load settings failed!\n {err}')
           self.status = 'init failed'
           # quit here, fatal error
           quit()
        # debugPrint(settings.items())
        # TD test for settings here or fatal error
        # self.volume = settings['volume']
        # self.fade_secs = settings['fade_secs']
        self.station = settings['station_url']
        self.schedule = settings['events']
        self.int_schedule = settings['int_events']

    def read_config(self, file):
        '''# read json config file
        # populate config class
        '''
        logger.debug('read config file')
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
            # debugPrint(f'volume: {data["volume"]}')

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
                    logger.critical("A config parameter is missing!")
                    # TD return a fatal error
                    quit()

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
        	# "volume": data["volume"],
        	# "fade_secs": data["fade_time"],
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
                # logger.debug(f'should be playing:{should_be_playing}')
            # debugPrint(f'today is {today[item]["startDOW"]}, start time is {today[item]["startHHMM"]}, end time is {today[item]["stopHHMM"]}, should be playing?:{should_be_playing}')
        return should_be_playing


    def checkSchedule(self):
        ''' uses an epoch of minutes from start of Monday
            read the schedule & compare integers
        '''
        should_be_playing = False
        d_t = datetime.datetime.now()
        #nowInt = timeInt(d_t.weekday(), str(d_t.hour) + ':' + str(d_t.minute) + '.' + str(d_t.second))
        nowInt = timeInt(d_t.weekday(), str(d_t.hour) + ':' + str(d_t.minute))
        for event in self.int_schedule:
            if event["intStart"] <= nowInt < event["intStop"]:
                should_be_playing = True
                #debugPrint('should be playing')
                # logger.debug(f'should be playing:{should_be_playing}')
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
            logger.error("socket timed out")
            command = ["sudo", "systemctl", "restart", "mpd"]
            subprocess.run(command)
            logger.debug("restarted MPD")
        except socket.error as msg: # SocketError as msg:
            logger.error(f'Socket Error: {str(msg)}')
            conn = False
        except MPDError as msg:
            if str(msg) == "Already connected":
                conn = True
            else:
                logger.error(f'Connection Error: {str(msg)}')
                conn = False
        else:
            break # stop the attempts
    if conn == False: # end of attempts
    	logger.critical("connection attempts failed: Fatal error")
    return conn


def clear_cron():
    ''' delete cron created by this script
    '''
    with CronTab(user=True) as cron:
        cron.remove_all(comment='jdf_radio')
    logger.info('deleted cron jobs')


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
    logger.info('cron.write() was just executed')


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
    logger.debug(f'checkServices is {os_up}')
    return os_up


def checkSNTP():
    ''' try to validtae that time & date have been updated
        so timestamp is correct before we check the schedule
    '''
    
    # try to make sure that the time is correct
    updated = False
    # systemctl status systemd-timesyncd --no-pager
    # 
    count = 0
    #command = ['systemctl', 'status', 'systemd-timesyncd', '--no-pager']
    #result = subprocess.run(command, capture_output=True, text=True)
    #logger.info(f'checkSNTP - systemd-timesyncd status:\n{result.stdout}')
    while updated is False:
        try:
            count +=1
            command = ['timedatectl']
            result = subprocess.run(command, capture_output=True, text=True)
            logger.debug(f'timedatectl returns:\n{result.stdout}')
            #updated = result.stdout.index('Initial synchronization to time server')
            updated = result.stdout.index('System clock synchronized: yes')
        except Exception as err:
            # probably .index no match found
            # try waiting a few seconds
            logger.debug(f'fail count: {count}')
            time.sleep(1)
            if count == 5 or (count % 30 == 0):
                # try restarting service on 5th then every 30 iterations
                # count = -60
                #command = ['timedatectl']
                #result = subprocess.run(command, capture_output=True, text=True)
                #logger.debug(f'timedatectl returns:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                command = ['sudo', 'systemctl', 'restart', 'systemd-timesyncd']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.debug(f'systemd-timesyncd restart:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                command = ['timedatectl']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.debug(f'timedatectl returns:\n{result.stdout}\n{result.stderr}')
                time.sleep(3)  # allow time to be updated
                logger.warning('boot sequence - timedatectl restarted')
            elif count >= 2050:
                #reboot device
                command = ['sudo', 'init', '6']
                result = subprocess.run(command)
                logger.critical('unable to sync time, rebooting')
                exit()

    # just to confirm:
    # stat /var/lib/systemd/timesync/clock
    command = ['stat', '/var/lib/systemd/timesync/clock']
    result = subprocess.run(command, capture_output=True, text=True)
    logger.debug(f'checkSNTP - clock stats:\n{result.stdout}')
    logger.debug('System clock has synchronised')


def boot():
    '''
    '''
    logger.debug('start of def boot')
    clear_cron()
    # write cron jobs
    set_cron(config, str(argv[0]))
    # wait (indefinitely) for network
    # while netConnection(config) is False:
    logger.info('boot sequence - check services')
    while checkServices() is False:
        time.sleep(2)
        # this will loop indefinitely
        # unless another script or service is trying to start mpd & login
    # check schedule
    # call start if appropriate
    logger.info('boot sequence - OS is ready')
    checkNet()
    # this will pause until networking is working

    # check clock is up to date
    checkSNTP()
    # this will pause until networking is working
    logger.info('boot sequence - SNTP updated')

    if config.checkSchedule():
        logger.info('boot sequence - schedule says play')
        startStream(config.station)
    elif os.path.isfile(SILENT_FILE):
        # don't play any sound, intended for scheduled reboots
        logger.info('silent boot sequence')
        player.disconnect()
        # checkNet()
        os.remove(SILENT_FILE)
    else:   
        # play some sound so we know that things are working
        checkNet()
        # this should get network working
        player.disconnect()
        if mpdConnect(player, CON_ID):
            logger.debug('boot sequence - MPD connection')
        else:
            logger.warning('boot sequence - fail to connect MPD server.')
            # clear cron created by this script
        logger.info('boot sequence - play startup sound')
        # player.add(config.station)
        player.clear() # ensure nothing else in queue
        player.add('file://' + STARTUP_SOUND)
        player.play() # really this should be a soft startup sound
        time.sleep(5)  
        endStream()
        player.disconnect()
    # finally
    player.disconnect()
    logger.info('end of boot sequence')


def bootSilent():
    '''
    '''
    logger.debug('start of silent boot')
    clear_cron()
    # write cron jobs
    set_cron(config, str(argv[0]))
    # wait (indefinitely) for network
    # while netConnection(config) is False:
    logger.info('silent boot sequence - check services')
    while checkServices() is False:
        time.sleep(5)
        # this will loop indefinitely
        # unless another script or service is trying to start mpd & login
    # check schedule
    # call start if appropriate
    logger.info('silent boot sequence - OS is ready')
    # check clock is up to date
    checkSNTP()
    # this will pause until networking is working
    logger.info('boot sequence - SNTP updated')
       
    if config.checkSchedule():
        logger.info('silent boot sequence - schedule says play')
        startStream(config.station)
    # finally
    player.disconnect()
    logger.info('end of silent boot sequence')


def endStream():
    ''' stop playing & clear queue
    '''
    if mpdConnect(player, CON_ID):
        logger.debug('mpd connected!')
    else:
        logger.error('fail to connect MPD server.')
    player.stop()
    player.clear()
    # because we clear the playlist the watchdog cannot restart it
    # if the timings overlap
    player.disconnect()
    logger.info('stopped stream')


def startStream(url):
    ''' 
    '''
    if mpdConnect(player, CON_ID):
        logger.debug('mpd connected!')
    else:
        logger.error('failed to connect MPD server.')
    player.clear() # ensure nothing else in queue
    player.add(url)
    logger.info(f'play: {url}')
    player.play()
    # wdg(force=True)  # for testing
    wdg()
    # wdg should run until stream ends
    # & ensure stream keeps working as long as it should
    player.disconnect() 


def wdg(force=False):
    ''' try to keep the stream playing
        & restart if necessary
        
    '''
    last_elapsed = 0
    restart_reqd = True
    while config.checkSchedule() or force:  # should be playing?
        if mpdConnect(player, CON_ID):
            # logger.debug('MPD wdg connected')
            pass
        else:
            logger.error('fail to connect MPD server.')
        status = player.status()
        try:
            if (last_elapsed < float(status["elapsed"]) or last_elapsed + float(status["elapsed"]) == 0):
                last_elapsed = float(status["elapsed"])
                if restart_reqd:
                    logger.info('stream resumed')
                restart_reqd = False
                # logger.debug(f'playing OK: {last_elapsed}')
            else:
                restart_reqd = True
                logger.warning(f'elapsed time is stuck {last_elapsed}:{status["elapsed"]}')
                last_elapsed = 0
        except KeyError:
                logger.warning(f'KeyError, not playing, status:{status}')
                restart_reqd = True
                # break  # out of try
        finally:
            # do this whether we had an error or not
            if restart_reqd:   
                # check net connection
                # this can take some time, so disconnect player,
                # otherwise we get a broken pipe error after 60 seconds
                player.disconnect()
                domain = urllib3.util.parse_url(config.station)  # urllib3.
                checkNet(domain.scheme + '://' + domain.host + '/')
                # checkNet(config.station)
                logger.debug('back from checkNet')
                if mpdConnect(player, CON_ID):
                    logger.debug('MPD wdg connected')
                else:
                    logger.error('failed to connect MPD server.')
                player.clearerror()
                player.clear()
                player.add(config.station)
                player.play()
                logger.debug('attempted to restart stream')
                status = player.status() # reload status
                if "elapsed" in status:
                    # reset elapsed counter
                    last_elapsed = float(status["elapsed"])
                #time.sleep(2)  # don't spam the website with requests
        player.disconnect()
        time.sleep(WDG_INT)
    logger.info('schedule says stop')


def checkNet(gw='https://google.com/'):
    ''' check that we have a functional network connection
        if not try by restarting dhcpcp, or rebooting
        will loop until we can ping the target
        ping was replaced because whilst we can ping google.com
        otehr sites may not respond to ping
        timeout after about 60 seconds
    '''
    not_connected = True
    fail_count = 0
    while not_connected:
        logger.debug('check Network')
        session = requests.Session()
        #adapter = TimeoutHTTPAdapter(timeout=(3,6))
        #adapter = TimeoutHTTPAdapter(timeout=(3, 6), max_retries=Retry(total=5, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504]))
        adapter = TimeoutHTTPAdapter(timeout=(3, 6), max_retries=Retry(total=15, backoff_factor=0, status_forcelist=[429, 500, 502, 503, 504]))
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        start_time = time.monotonic()
        try:
            # r = session.get("https://media-ice.musicradio.com", timeout=4)
            my_headers = { "User-Agent":"RaspberryPi MPC player"} #,
            #'Cache-Control': 'no-cache, no-store, must-revalidate',
            #'Pragma': 'no-cache',
            #'Expires': '0'}
            r = session.get(gw, headers=my_headers)
            logger.info(f'website response is; {r}')
            # not_connected = r
            stop_time = time.monotonic()
            logger.debug(f"{round(stop_time-start_time, 2)} seconds")
            test_list = [401,200,201,202,203,204,205,206,207,208,300,301,302,307,308]
            if (hasattr(r, 'status_code') and r.status_code in test_list):
                not_connected = False
                r.close()
            else:
                not_connected = True
            # r = ""
        except Exception as e:
            logger.debug(type(e))
            logger.debug(e)
            #continue
        #finally:
        # return is like "<Response [200]>" or [401]
        if not_connected:
            fail_count += 1
            logger.info(f'reconnect attempt {fail_count} failed')
            # restart network connection
            if fail_count == 2:
                # stop dhcpd, remove lease, restart
                command = ['sudo', 'systemctl', 'stop', 'dhcpcd']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.debug(f'dhcpcd stop:{result.returncode}\n\t{result.stdout}\n\t{result.stderr}')
                #command = ['sudo', 'rm', '/var/lib/dhcpcd/wlan0-*.lease']
                #result = subprocess.run(command, capture_output=True, text=True)
                #logger.debug(f'remove lease:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                #time.sleep(2)
                command = ['sudo', 'systemctl', 'start', 'dhcpcd']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.debug(f'dhcpcd (re)start:{result.returncode}\n\t{result.stdout}\n\t{result.stderr}')
                #systemctl status dhcpcd.service
                command = ['sudo', 'systemctl', 'status', 'dhcpcd.service']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.debug(f'dhcpcd.service status:{result.returncode}\n\t{result.stdout}\n\t{result.stderr}')
                command = ['sudo', 'systemctl', 'daemon-reload']
                result = subprocess.run(command, capture_output=True, text=True)
                                              
            elif fail_count > 2:
                # we have failed repeatedly 
                logger.warning('url test has failed {fail_count} times, try a reboot')
                command = ['sudo', 'init', '6']
                result = subprocess.run(command)
                exit()
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
        logger.debug('mpd connected!')
    else:
        logger.error('fail to connect MPD server.')
    player.clear() # ensure nothing else in queue
    player.add('file:///home/pi/waterdrops.mp3')
    #player.clear()
    player.play()
    logger.debug(player.status())
    time.sleep(3)
    #debugPrint(player.status())
    #time.sleep(3)
    player.stop()
    #debugPrint(player.status())
    player.clear()
    #startStream(url)
    player.disconnect()
    logger.debug('log debug test stdout only')
    logger.info('log info test')
    logger.warning('log warn test')
    logger.error('log error test')
    logger.critical('log cricital test')
    


# #################### main

logger.debug('script called')
player = MPDClient()
config = ConfigObject(CONFIG_FILE)
#if mpdConnect(player, CON_ID):
#    logger.debug('MPD connected!')
#else:
#    logger.error('failed to connect MPD server.')

if __name__ == "__main__":
    #createLog()
    # if scriupt is being called directly (rather than imported)
    actions = ('start', 'stop', 'boot', 'bootsilent', 'test')
    # debugPrint(f'length;{len(argv)} text:{str(argv)}')
    if len(argv) >= 2 and str(argv[1]) in actions:
        logger.debug(str(argv))
        # then call that action
        #action=locals()[argv[1]]
        #action()
        action=argv[1]
        if action == "boot":
            boot()
        if action == "bootsilent":
            bootSilent()
        if action == 'start':
            # check if we have a url passed, if not try to read it.
            try:
                url = argv[2]
            except IndexError:
                config = ConfigObject(CONFIG_FILE)
                url = config.station
            startStream(url)
        if action == 'stop':
            endStream()
        if action == 'test':
            logger.info('start test')
            test(player)
