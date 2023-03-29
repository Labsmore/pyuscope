#!/usr/bin/env bash
# Temporary wrapper script to demonstrate some more advanced features
./test/grbl/home.py
PYUSCOPE_SAVE_EXTENSION=.tif ./app/argus.py "$@"

