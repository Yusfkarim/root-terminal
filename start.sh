#!/bin/bash
# Restore apt packages saved in /data/apt.txt (persistent installs)
if [ -f /data/apt.txt ] && [ -s /data/apt.txt ]; then
  apt-get update -qq
  xargs -a /data/apt.txt apt-get install -y
fi
exec python3 /app/app.py
