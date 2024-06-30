#!/usr/bin/env python3
''' if called directly requires action; start, stop or init
    # This script requires the following:
    #
    #    $ sudo apt-get install mpc mpd -y
    #    $ sudo apt-get alsa -y
    #    pip3 install python-mpd2
    # read-only FS
    # systemd to run at boot & when ready
    # boot - wait for network & mpd & user targer, systemd service to ensure we are connected & keep trying
'''
from sys import exit, argv
#import sys
import datetime
import subprocess
import os
import time
import logging
from logging.handlers import RotatingFileHandler
import socket
import json # parse the config file
import requests
from requests.adapters import HTTPAdapter
import urllib3
#print(urllib3.__version__)
from urllib3 import Retry
# from Adafruit_MAX9744 import MAX9744    # amplifier i2c control
from mpd import (MPDClient,  MPDError)  # CommandError,music player daemon

CONFIG_FILE = '/boot/radio_config.json'

LOG_FILE = '/home/pi/radio/radio.log'
SILENT_FILE = '/home/pi/radio/silent'
STARTUP_SOUND = '/home/pi/radio/waterdrops.mp3'

#CON_ID = {'host':HOST, 'port':PORT}
CON_ID = {'host':'/run/mpd/socket'}
CON_TIMEOUT = 20
DEBUG = True # enable log file
WDG_INT = 5  # check stream every # seconds

# def createLog():
''' set up logging
    log debug & info to stdout, warn + to file
'''
#logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('radio_app')
logger.setLevel(logging.DEBUG)
#logger.setLevel(logging.INFO)

# show on stdout
s_handler = logging.StreamHandler()
s_handler.setLevel(logging.DEBUG)
#s_handler.setLevel(logging.INFO)                               
# log to file
f_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=150000, backupCount=7)
f_handler.setLevel(logging.INFO)
#f_handler.setLevel(logging.DEBUG)

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
        with open(LOG_FILE, 'a', encoding="utf-8") as f:
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
    # if type(day) is str:
    if isinstance(day, str):
        weekday = weekdays.index(day.lower())
    else:
        weekday = int(day)
    hours = int(hhmm.split(':')[0])
    minutes = float(hhmm.split(':')[1])
    # float allows for seconds to be present
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
            logger.critical('load settings failed!\n %s', err)
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
        self.restartRqd = True
        self.stallCount = 0
        self.errCount = 0

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

                if (event["start_day"] is None
                     or event["start_time"] is None
                     or event["stop_time"] is None):
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

    def nextStartStop(self):
        ''' return secs until next start & stop events
            smallest positive integer using list comprehension
            min([i for i in l if i > 0])
        '''
        startEvents = []  # a list of week-epoch seconds
        stopEvents = []  # a list of week-epoch seconds
        d_t = datetime.datetime.now()
        nowIntSecs = (timeInt(d_t.weekday(),
                               str(d_t.hour) + ':' + str(d_t.minute))  * 60) + d_t.second
        #nowInt = timeInt(d_t.weekday(), str(d_t.hour) + ':' + str(d_t.minute))
        # create a list of seconds until start events
        for event in self.int_schedule:
            secs_to_go = event["intStart"] * 60 - nowIntSecs
            if secs_to_go < 0:
                # event has happened already this week, so add a week
                secs_to_go += 604800 # 1 week in seconds
                # otherwise we could end up with all events in the past
            startEvents.append (secs_to_go)
        # find the minimum positive value in this list
        nextStart = min(i for i in startEvents if i > 0)
        # create a list of seconds until stop events
        for event in self.int_schedule:
            secs_to_go = event["intStop"] * 60 - nowIntSecs
            if secs_to_go < 0:
                # event has happened already this week, so add a week
                secs_to_go += 604800 # 1 week in seconds
                # otherwise we could end up with all events in the past
            stopEvents.append (secs_to_go)
        # find the minimum positive value in this list
        # nextStop = min([i for i in stopEvents if i > 0])
        nextStop = min(i for i in stopEvents if i > 0)
        return (nextStart, nextStop)


def mpdConnect(client, con_id):
    ''' return a connection reference  to mpd player
    ConnectionError("Already connected")
    '''
    conn = False
    for _ in range(3):
        try:
            client.timeout = CON_TIMEOUT  # network timeout in seconds (floats allowed),
            client.connect(**con_id)
            conn = True
        except socket.timeout: # SocketTimeout:
            logger.error("socket timed out")
            command = ["sudo", "systemctl", "restart", "mpd"]
            subprocess.run(command, check=False)
            logger.debug("restarted MPD")
        except socket.error as msg: # SocketError as msg:
            logger.error('Socket Error: %s', str(msg))
            conn = False
        except MPDError as msg:
            if str(msg) == "Already connected":
                conn = True
            else:
                logger.error('Connection Error: %s', str(msg))
                conn = False
        else:
            break # stop the attempts
    if conn is False: # end of attempts
        logger.critical("connection attempts failed: Fatal error")
    return conn


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
    logger.debug('checkServices is %s', os_up)
    return os_up


def checkSNTP():
    ''' try to validtae that time & date have been updated
        so timestamp is correct before we check the schedule
    '''
    # try to make sure that the time is correct
    updated = False
    # systemctl status systemd-timesyncd --no-pager
    count = 0
    #command = ['systemctl', 'status', 'systemd-timesyncd', '--no-pager']
    #result = subprocess.run(command, capture_output=True, text=True)
    #logger.info(f'checkSNTP - systemd-timesyncd status:\n{result.stdout}')
    while updated is False:
        try:
            count +=1
            command = ['timedatectl']
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            logger.debug('timedatectl returns:\n%s', result.stdout)
            #updated = result.stdout.index('Initial synchronization to time server')
            updated = result.stdout.index('System clock synchronized: yes')
        except Exception as err:
            # probably .index no match found
            # try waiting a few seconds
            logger.debug('fail count: %s, error:%s', count, err)
            time.sleep(1)
            if count == 5 or (count % 30 == 0):
                # try restarting service on 5th then every 30 iterations
                # count = -60
                #command = ['timedatectl']
                #result = subprocess.run(command, capture_output=True, text=True)
                #logger.debug(f'timedatectl returns:\n\t
                #{result.returncode}\n\t {result.stdout}\n\t {result.stderr}')
                command = ['sudo', 'systemctl', 'restart', 'systemd-timesyncd']
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                logger.debug('systemd-timesyncd restart:\n\t%s\n\t %s\n\t %s',
                             result.returncode, result.stdout, result.stderr)
                command = ['timedatectl']
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                logger.debug('timedatectl returns:\n%s\n%s', result.stdout, result.stderr)
                time.sleep(3)  # allow time to be updated
                logger.warning('boot sequence - timedatectl restarted')
            elif count >= 2050:
                #reboot device
                command = ['sudo', 'init', '6']
                result = subprocess.run(command, check=False)
                logger.critical('unable to sync time, rebooting')
                exit()

    # just to confirm:
    # stat /var/lib/systemd/timesync/clock
    command = ['stat', '/var/lib/systemd/timesync/clock']
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    logger.debug('checkSNTP - clock stats:\n%s', result.stdout)
    logger.debug('System clock has synchronised')


def boot():
    ''' boot sequence
    '''
    logger.debug('start of def boot')
    # clear_cron()
    # write cron jobs
    # svc disable this set_cron(config, str(argv[0]))
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
    if checkNet() == True:
        logger.info('boot sequence - network test OK')
    # this will pause until networking is working

    # check clock is up to date
    checkSNTP()
    # this will pause until time has been updated
    logger.info('boot sequence - SNTP updated')

    if config.checkSchedule():
        # schedule says we should be playing music, so...
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
        #checkNet()
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
    logger.info('boot sequence - complete')
    # start the watchdog service
    wdg_svc(config.nextStartStop())


def bootSilent():
    ''' I think this is redundant
    '''
    logger.debug('start of silent boot')
    # clear_cron()
    # write cron jobs
    # set_cron(config, str(argv[0]))
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


def startStream(url_stream):
    ''' start playing stream at url
    '''
    if mpdConnect(player, CON_ID):
        logger.debug('mpd connected!')
    else:
        logger.error('failed to connect MPD server.')
    player.clear() # ensure nothing else in queue
    player.add(url_stream)
    logger.info('play: %s', url_stream)
    player.play()
    # wdg(force=True)  # for testing
    # wdg() # wdg svc remove this
    # wdg should run until stream ends
    # & ensure stream keeps working as long as it should
    player.disconnect()


def wdg_svc(cdn_timers, event=False):
    ''' if script is run as a service then use this function
        which should run after the boot sequence
        this def will monitor & will resume, start or stop a stream as appropriate
        use systemd to keep this script running
    '''
    #restart_reqd = True
    last_elapsed = 0
    wdg_secs = 7
    x_secs = 0
    secs_until_start, secs_until_stop = cdn_timers
    while True:
        #outer loop
        if event:
            # load config settings
            #secs_until_stop = config.nextStart()
            #secs_until_start = config.nextStop()
            secs_until_start, secs_until_stop = config.nextStartStop()
        # assign config settings
        # secs_until_stop = 0
        # secs_until_start = 0
        # should_be_playing = config.checkSchedule()
        if config.checkSchedule() :
            # should_be_playing:
            x_secs = wdg_secs
        else:
            # set sleep to smallest of
            x_secs = min(secs_until_stop, secs_until_start)
        time.sleep(x_secs)
        # subtract sleep time from counters
        secs_until_stop -= x_secs
        secs_until_start -= x_secs
        if min(secs_until_stop, secs_until_start) < 0:
            # something has gone wrong, log message
            logger.warning('negative value means a missed event; stop:%.2f start:%.2f',
                            secs_until_stop, secs_until_start)
        if secs_until_stop == 0 and secs_until_start == 0:
            # something has gone wrong, log message
            logger.warning('both stop and start counter are zero!')
        if config.checkSchedule():
            # should be playing
            # check stream is still running and get result
            last_elapsed, duration = check_stream(last_elapsed)
            # subtract duration of test from counters
            secs_until_stop -= duration
            secs_until_start -= duration
            event = False
        if secs_until_start <= 0:
            # start stream
            logger.debug('scheduled to start playing')
            startStream(config.station)
            event = True
        if  secs_until_stop <= 0:
            # stop stream
            logger.debug('scheduled to stop playing')
            endStream()
            event = True


def check_stream(last_elapsed_secs):
    ''' test to see if elapsed playtime is incrementing
        if not try to reconnect
        return a list of
            the latest elapsed time from the player
            how long this operation took
            restart status
    '''
    start_time = time.monotonic()
    if mpdConnect(player, CON_ID):
        # logger.debug('MPD wdg connected')
        pass
    else:
        logger.error('fail to connect MPD server.')
    status = player.status()
    
    try:
        playing_secs = float(status["elapsed"])
        config.errCount = 0
    except KeyError:
        config.errCount += 1
        # don't sam the log every wdg interval
        if config.stallCount == 1:
            logger.warning('KeyError, not playing')
            logger.debug('status:%s', status)
        playing_secs = 0
    
    if playing_secs > 0:
        # mpc playing time is incrementing
        config.stallCount = 0
    try:
        #playing_secs = float(status["elapsed"])
        if (playing_secs > last_elapsed_secs
             or last_elapsed_secs + playing_secs == 0):
            # then we *should* be playing OK
            # last_elapsed_secs + playing_secs == 0 can happen on first start/restart
            last_elapsed_secs = playing_secs
            if last_elapsed_secs == 0:
                logger.debug('0 seconds, this is first run or mpd has failed and not restarted')
                # artificially nudge elapsed time, on next run if playing_secs is still at 0, then will be detected
                last_elapsed_secs = WDG_INT/10  # dummy value that is less than watchdog interval, but more than 0
                #addntl_msg = ", 0secs elapsed, stalled?"
                # this should be picked up on next wdg check
                config.stallCount +=1
            if config.restartRqd and config.stallCount == 0:
                logger.info('stream resumed')
            # reset the flag
            config.restartRqd = False
            # logger.debug(f'playing OK: {last_elapsed}')
        else:
            config.restartRqd = True
            if last_elapsed_secs != WDG_INT/10:
                # no need to spam the log if stream is stuck
                logger.warning('elapsed time is stuck %.3f:%s', last_elapsed_secs, playing_secs)
            last_elapsed_secs = WDG_INT/10  # dummy value that is less than watchdog interval, but more than 0
    #except KeyError:
    #    logger.warning('KeyError, not playing')
    #    logger.debug('status:%s', status)
    #    config.restartRqd = True
        # break  # out of try
    finally:
        # do this whether we had an error or not
        if config.restartRqd:
            # check net connection
            # this can take some time, so disconnect player,
            # otherwise we get a broken pipe error after 60 seconds
            player.disconnect()
            domain = urllib3.util.parse_url(config.station)  # urllib3.
            #checkNet(domain.scheme + '://' + domain.host + '/')
            logger.debug('calling checkNet; %s %s', domain.host, domain.scheme)
            checkNet(domain.host, domain.scheme)
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
            # player.stop()
            # player.play()
            logger.debug('attempted to restart stream')
            status = player.status() # reload status
            if "elapsed" in status:
                # reset elapsed counter
                last_elapsed_secs = 0
            #time.sleep(2)  # don't spam the website with requests
        if config.stallCount > 0 and config.stallCount % 5 == 0:
            # stream seems to have stalled 5 times
            # ie can open socket, but cannot start stream
            # bandwidth limit, problem with stream url or problem with network?
            logger.info('mpc stalled')
            player.disconnect()
            logger.debug('restart networking')
            restartNetworking()
            logger.debug('back from restartNetworking')
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
                last_elapsed_secs = 0
    player.disconnect()
    stop_time = time.monotonic()
    # return latest elapsed time from player
    # and how long this test took
    result = (last_elapsed_secs, round(stop_time-start_time, 2))
    return result


#def checkNet(gw='https://google.com/'):
def checkNet(host='google.com', protocol='http'):
    ''' check that we have a functional network connection
        if not try by restarting dhcpcp, or rebooting
        will loop until we can ping the target
        opening a socket shows connectivity, but does not test bandwidth
        the previous HTTP session approach requires bandwitdth
        using the socket test should mean fewer unnecessary restarts ie when network is available, but bandwidth is not
        timeout after about 60 seconds
    '''
    port = 443 if protocol == 'https' else 80
    not_connected = True
    #server_response = "none"
    fail_count = 0
    logger.debug('check Network')
    while not_connected:
        # attempt socket connection to streaming server
        try:
            args = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
            for family, socktype, proto, canonname, sockaddr in args:
                s = socket.socket(family, socktype, proto)
                s.settimeout(CON_TIMEOUT)
        #try:
            s.connect(sockaddr)
        except socket.error as msg:
            #not_connected = True
            logger.warning("error connecting to socket; %s", msg)
        except socket.gaierror as msg:
            # failure in name resolution
            logger.warning("socket.gaierror; %s", msg)
        except socket.timeout:
            logger.warning("socket timeout")
        except Exception as err:
            # anything else
            logger.warning("socket error:%s", err)
        else:
            s.close()
            not_connected = False
            logger.debug("socket test OK, host %s, port %i", host, port)
            # if called with non default host & protocol message above is logged twice, why?

        '''
        # this should be changed to use socket on 80 or 443
        logger.debug('check Network')
        session = requests.Session()
        adapter = TimeoutHTTPAdapter(timeout=(3, 6), max_retries=Retry(
            total=15,
            backoff_factor=0,
            status_forcelist=[429, 500, 502, 503, 504]))
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
            logger.debug('website response is; %s', r)
            # not_connected = r
            stop_time = time.monotonic()
            logger.debug("%s seconds", round(stop_time-start_time, 2))
            test_list = [401,403,200,201,202,203,204,205,206,207,208,300,301,302,307,308]
            if (hasattr(r, 'status_code') and r.status_code in test_list):
                server_response = r.status_code
                not_connected = False
                r.close()
            else:
                not_connected = True
            # r = ""
        except Exception as err:
            logger.debug("error type:%s", type(err))
            logger.debug("error msg:%s", err)
            #continue
        #finally:
        # return is like "<Response [200]>" or [401]
        '''
        if not_connected:
            fail_count += 1
            #logger.info('reconnect attempt %s failed. Server response: %s', fail_count, server_response)
            logger.info('reconnect attempt %s failed.', fail_count)
            # restart network connection
            restartNetworking(fail_count)
            #if fail_count == 2:
            '''if 2 <= fail_count <= 3:
                # if 2 or 3 fails; stop dhcpd, remove lease, restart
                command = ['sudo', 'systemctl', 'stop', 'dhcpcd']
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                logger.debug('dhcpcd stop:%s\n\t%s\n\t%s',
                             result.returncode,
                             result.stdout,
                             result.stderr)
                command = ['sudo', 'systemctl', 'start', 'dhcpcd']
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                logger.debug('dhcpcd (re)start:%s\n\t%s\n\t%s', 
                             result.returncode, result.stdout, result.stderr)
                #systemctl status dhcpcd.service
                command = ['sudo', 'systemctl', 'status', 'dhcpcd.service']
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                logger.debug('dhcpcd.service status:%s\n\t%s\n\t%s', 
                             result.returncode, result.stdout, result.stderr)
                command = ['sudo', 'systemctl', 'daemon-reload']
                result = subprocess.run(command, capture_output=True, text=True, check=False)
            elif fail_count > 3:
                # we have failed repeatedly, restart
                logger.warning('url test has failed %d times, try a reboot', fail_count)
                command = ['sudo', 'init', '6']
                result = subprocess.run(command, check=False)
                exit()'''
    else:
        # we have a connection
        logger.debug("Network connection confirmed, return")
        return True
    # if we get here should be connected
    # the problem in development was that the USB wifi was someitmes not detected
    # adding usbcore.old_scheme_first=1
    # to /boot/cmdline.txt resolved that


def restartNetworking(fail_count=2):
    if 2 <= fail_count <= 3:
        # if 2 or 3 fails; stop dhcpd, remove lease, restart
        command = ['sudo', 'systemctl', 'stop', 'dhcpcd']
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        #logger.debug('dhcpcd stop:%s\n\t%s\n\t%s',
        #             result.returncode,
        #             result.stdout,
        #             result.stderr)
        command = ['sudo', 'systemctl', 'start', 'dhcpcd']
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        #logger.debug('dhcpcd (re)start:%s\n\t%s\n\t%s', 
        #             result.returncode, result.stdout, result.stderr)
        #systemctl status dhcpcd.service
        command = ['sudo', 'systemctl', 'status', 'dhcpcd.service']
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        #logger.debug('dhcpcd.service status:%s\n\t%s\n\t%s',
        #             result.returncode, result.stdout, result.stderr)
        #command = ['sudo', 'systemctl', 'daemon-reload']
        #result = subprocess.run(command, capture_output=True, text=True, check=False)
    elif fail_count > 3:
        # we have failed repeatedly, restart
        logger.warning('url test has failed %d times, try a reboot', fail_count)
        command = ['sudo', 'init', '6']
        result = subprocess.run(command, check=False)
        exit()


def test(player):
    ''' just a test def
    '''
    #config = ConfigObject(CONFIG_FILE)
    #url = config.station
    '''if mpdConnect(player, CON_ID):
        logger.debug('mpd connected!')
    else:
        logger.error('fail to connect MPD server.')
    player.clear() # ensure nothing else in queue
    player.add('file://' + STARTUP_SOUND)'''
    #player.clear()
    '''player.play()
    logger.debug(player.status())
    time.sleep(3)'''
    #debugPrint(player.status())
    #time.sleep(3)
    '''player.stop()
    #debugPrint(player.status())
    player.clear()
    #startStream(url)
    player.disconnect()
    logger.debug('log debug test stdout only')
    logger.info('log info test')
    logger.warning('log warn test')
    logger.error('log error test')
    logger.critical('log cricital test')'''


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
        logger.debug("arguments passed: %s", str(argv))
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
