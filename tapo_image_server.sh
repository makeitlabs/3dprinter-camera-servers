#!/bin/bash

# add entry into crontab to restart if not running
# * * * * * pgrep -x "tapo_image_server.s" > /dev/null || /home/jayfr/3dprinter-camera-servers/tapo_image_server.sh > /dev/null 2>&1

cd /home/jayfr/3dprinter-camera-servers

# change the paths appropriately...
sudo /usr/bin/python3 /home/jayfr/3dprinter-camera-servers/tapo_image_server.py
