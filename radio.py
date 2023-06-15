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
import urllib
#import sys                              # read arguments passed on command line
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
import requests                         # check internet connection
# read calling parameter(s)
# called by Flask, from cron, start, cron stop, or after boot
CONFIG_FILE = '/boot/radio_config.json'
LOG_FILE = '/media/log/radio.log'
#CON_ID = {'host':HOST, 'port':PORT}
CON_ID = {'host':'/run/mpd/socket'}
CON_TIMEOUT = 20
DEBUG = True # enable log file
WDG_INT = 17  # check stream every # seconds

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
                logger.debug(f'should be playing:{should_be_playing}')
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
                logger.debug(f'should be playing:{should_be_playing}')
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
            subprocess.call(["sudo", "systemctl", "restart", "mpd"])
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
            logger.info("Internet is connected")
            connected = True
        # catching exception
        except (requests.ConnectionError,
                requests.Timeout) as exception:
            logger.warning(f"Internet is not available:\n{exception}")
            # wait a bit longer
            time.sleep(10)
    os_up = connected & login_up  & mpd_up
    logger.debug(f'netConnection is {os_up}')
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
    logger.debug(f'checkServices is {os_up}')
    return os_up


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
    # try to make sure that the time is correct
    command = ['timedatectl']
    result = subprocess.run(command, capture_output=True, text=True)
    logger.debug(f'timedatectl returns:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
    command = ['sudo', 'systemctl', 'restart', 'systemd-timesyncd']
    result = subprocess.run(command, capture_output=True, text=True)
    logger.debug(f'systemd-timesyncd restart:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
    command = ['timedatectl']
    result = subprocess.run(command, capture_output=True, text=True)
    logger.debug(f'timedatectl returns:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
    time.sleep(3)  # allow time to be updated
    logger.info('boot sequence - timedatectl restarted')
    if config.checkSchedule():
        logger.info('boot sequence - schedule says play')
        startStream(config.station)
    elif os.path.isfile('/media/log/silent'):
        # don't play any sound, intended for scheduled reboots
        logger.info('silent boot sequence')
        player.disconnect()
        # checkNet()
        os.remove('/media/log/silent')
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
        player.add('file:///home/jdf/waterdrops.mp3')
        player.play() # really this should be a soft startup sound
        time.sleep(5)  
        endStream()
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
            logger.debug('MPD wdg connected')
        else:
            logger.error('fail to connect MPD server.')
        status = player.status()
        try:
            if (last_elapsed < float(status["elapsed"]) or last_elapsed + float(status["elapsed"]) == 0):
                last_elapsed = float(status["elapsed"])
                restart_reqd = False
                logger.debug(f'playing OK: {last_elapsed}')
            else:
                restart_reqd = True
                logger.warning(f'elapsed time is stuck {last_elapsed}:{status["elapsed"]}')
                last_elapsed = 0
        except KeyError:
                logger.warning(f'not playing:{status}')
                restart_reqd = True
                # break  # out of try
        finally:
            if restart_reqd:   
                # check net connection
                domain = urllib.parse.urlparse(config.station)
                checkNet(domain.scheme + '://' + domain.netloc)
                logger.debug('back from checkNet')
                if mpdConnect(player, CON_ID):
                    logger.debug('MPD wdg connected')
                else:
                    logger.error('failed to connect MPD server.')
                player.clearerror()
                # player.add(url)
                player.stop()
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


def checkNet(gw='https://google.com'):
    ''' check that we have a functional network connection
        if not try by restarting dhcpcp, or rebooting
        will loop until we can ping the target
        ping was replaced because whilst we can ping google.com
        otehr sites may not respond to ping
    '''
    ping_worked = False
    #website_is_up = False
    connected = False
    fail_count = 0
    while not connected:
        logger.debug('check Network')
        # pw = 2
        for i in range(12):
            status_code = 0
            t_start = time.perf_counter()
            #command = ['ping', '-c', '1', '-W', str(pw), gw]
            #result = subprocess.run(command, capture_output=True, text=True)
            try:
                #status_code = urllib.request.urlopen('https://httpstat.us/random/200,201,500-504').getcode()
                status_code = urllib.request.urlopen(gw, timeout=3)
                logger.debug(f'website returns status: {status_code.getcode()}')
                if status_code.getcode() == 200:
                    connected = True
                    break
                if status_code.getcode() != 200:
                    #fail_count += 1
                    connected = False
                    logger.warning(f'website not responding, code:{status_code}')
                    time.sleep(5)
            except urllib.error.HTTPError as err:
                logger.warning(f'website responding with an error: {err}')
                #fail_count += 1
                connected = False
                time.sleep(10)
            except urllib.error.URLError as err:
                logger.warning(f'wlan interface or website down or invalid url: {err}')
                #fail_count += 1
                connected = False
                time.sleep(10)  # wait longer
            #except Exception as Argument:
            #    logger.exception('Error calling urllib, probably an invlaid hostname?')
            #    time.sleep(10)
            #    continue
            #if result.returncode == 0:
            #    ping_worked = True
            #    logger.debug('ping test worked')
            #    break  # out of for loop
            #else:
            #    logger.warning(f'could not ping {gw}')
            #    fail_count += 1
            #    t_stop = time.perf_counter()
            #    elapsed = t_stop - t_start
            #    if elapsed < pw:
            #        # we probably received an immediate not accessible
            #        # so wait a little longer before retrying
            #        time.sleep(5)
        #connected = True if ping_worked is True else False
        #connected = website_is_up
        # it could be that the url is down, or that the wifi is not up
        if connected is False:
            fail_count += 1
            logger.warning(f'reconnect attempt:{fail_count}')
            # restart network connection
            # TD this doesn't work fail_count will always be 12
            if fail_count == 1:
                command = ['sudo', 'systemctl', 'restart', 'dhcpcd']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.info(f'dhcpcd restart:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
            elif fail_count == 2 :
                #command = ['sudo', 'ip', 'link', 'set', 'wlan0', 'down']
                #result = subprocess.run(command, capture_output=True, text=True)
                #debugPrint(f'wlan0 down;\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                #command = ['sudo', 'ip', 'link', 'set', 'wlan0', 'up']
                #result = subprocess.run(command, capture_output=True, text=True)
                #debugPrint(f'wlan0 up\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                command = ['sudo', 'systemctl', 'stop', 'dhcpcd']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.info(f'dhcpcd stop:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                time.sleep(2)
                command = ['sudo', 'systemctl', 'start', 'dhcpcd']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.info(f'dhcpcd (re)start:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                #systemctl status dhcpcd.service
                command = ['sudo', 'systemctl', 'status', 'dhcpcd.service']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.debug(f'dhcpcd.service status:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
            elif fail_count == 3:
                command = ['sudo', 'systemctl', 'stop', 'dhcpcd']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.debug(f'dhcpcd stop:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                command = ['sudo', 'rm', '/var/lib/dhcpcd/wlan0-*.lease']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.debug(f'remove lease:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                time.sleep(2)
                command = ['sudo', 'systemctl', 'start', 'dhcpcd']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.debug(f'dhcpcd (re)start:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                #systemctl status dhcpcd.service
                command = ['sudo', 'systemctl', 'status', 'dhcpcd.service']
                result = subprocess.run(command, capture_output=True, text=True)
                logger.debug(f'dhcpcd.service status:\n\t{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                # TD print IP')
            else:
                # we have failed repeatedly with dhcpcd
                logger.warning('ping has failed for a long time, try a reboot')
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
    player.add('file:///home/jdf/waterdrops.mp3')
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

logger.debug('\nscript called')
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
