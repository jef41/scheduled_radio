# scheduled_radio
python script to play a radio station on a schedule

## Design 
Designed for a Raspberry Pi Zero to run in read-only mode (using overlay filesystem), headless & unattended. Additional components were a phatDAC and MAX7944 digital amplifier. The Amp was used in analogue mode, though could easily be used in digital, the original design used digital control & fade in/out routines & rotary encoder to set the volume.

## Operation
the Python script is called at boot by setting a Cron @reboot parameter (see steps below). The script when called waits for a network conenction & SNTP time sync, if these are not present it loops and tries to restart services. Once these services are running the script parses the radio_config files and updates the user crontab with the schedule. 

The mpd service is used to play the stream and when running a watchdog tries to ensure that the stream is resumed appropriately after any network interruptions.

The only open port is for ssh access. Although only on a local network Fail2ban is running, well because that's good practice especially for something unattended.

I used a short audio file 'waterdrops.mp3' to play at boot. If the device is manually rebooted it will either play the stream (if the schedule dicatates) or it will play this startup sound to indicate success. An additional cron job creates a file on the r/w partition and reboots the device. When the script runs it looks for this 'silent' file, if present no startup sound is played (unless the schedule dictates it should) and the 'silent' file is cleared.

Steps below are for setting this up on a Pi Zero

## Steps
install the requirements;
```
sudo apt update && sudo apt install -y fail2ban python3 python3-pip mpd mpc && pip3 install python-mpd2 python-crontab && sudo nano /etc/modprobe.d/raspi-blacklist.conf
```
Configure the DAC, PhatDac setup instructions can be found at; https://learn.pimoroni.com/article/raspberry-pi-phat-dac-install. 
<details>
  <summary>These are summarised as;</summary>
  
  ```
  sudo nano /etc/modules
  ```
  changing:
  
  ```
  blacklist i2c-bcm2708
  blacklist snd-soc-pcm512x
  blacklist snd-soc-wm8804
  ```
  To:
  
  ```	
  # blacklist i2c-bcm2708
  # blacklist snd-soc-pcm512x
  # blacklist snd-soc-wm8804
  ```
  then:
  
  ```
  sudo nano /etc/modules
  ```
  Remove the default sound driver, so change the line:
  
  ```
  snd_bcm2835
  ```
  to:

  ```
  # snd_bcm2835
  ```
  then

  ```
  sudo nano /etc/asound.conf
  ```
  enter;

  ```
  pcm.!default  {
	 type hw card 0
	}
	ctl.!default {
	 type hw card 0
	}
  ```

  edit /boot/config.txt;

  ```
  sudo nano /boot/config.txt
  ```
  and add the line:
  
  ```
  dtoverlay=hifiberry-dac
  ```
  While you have that file open, check for the following entry, and if it exists, comment it out:
  
  ```
  # dtparam=audio=on
  ```
  Reboot;
  
  ```
  sudo init 6
  ```
  
</details>

now edit the mpd config;
```
sudo nano /lib/systemd/system/mpd.socket
ListenStream=127.0.0.1:6600
```
above is important, the mpd.socket service overrides /etc/mpd.conf. If left unedited 6600 will listen on any interface

```
sudo cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local
sudo nano /etc/fail2ban/jail.local
```
set Fail2Ban as appropriate

ensure that that the audio file in the script (waterdrops.mp3) exists on your system, or edit accordingly

to check on open ports at the end of this;
```
sudo nmap -sT -p- 192.168.1.64
```
will scan a remote host for any open ports, without '-p-' will scan only the 1000 most popular
  
### Crontab
the initial crontab should have entries like:
```
	crontab -e
	# at boot run the radio script
	@reboot /home/pi/radio.py boot
	# once a day record RAM usage
	0 12 * * * free >> /media/log/radio.log
	# once a week reboot silently by some magic
	0 3 * * Sun touch /media/log/silent && sudo init 6
	# below are created by script
```
