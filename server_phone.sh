#!/system/bin/sh
pkill -9 python
export PATH=/data/data/com.termux/files/usr/bin:$PATH
export LD_LIBRARY_PRELOAD=/data/data/com.termux/files/usr/lib

python /data/data/com.termux/files/home/server.py
