#!/usr/bin/env python3
import os
from setuptools import setup
import shutil
import glob
import sys

# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


if not os.path.exists('build'):
    os.mkdir('build')
scripts = []
scripts += list(glob.glob("util/*.py"))
scripts += list(glob.glob("demo/*.py"))
scripts_dist = []
print(scripts)
for script in scripts:
    # Make script names more executable like
    # util/main_gui.py => pyuscope-main-gui
    dst = 'build/pyuscope-' + script.replace('.py', '').replace('_', '-').replace("/", "-")
    dst = dst.replace("-util", "")
    print(script, dst)
    if os.path.exists(dst):
        os.unlink(dst)
        print("removed")
    if "develop" in sys.argv:
        # switch to symlink to make "develop" work correctly
        print("check", dst)
        os.symlink(os.path.abspath(script), dst)
    else:
        shutil.copy(script, dst)
    scripts_dist.append(dst)

setup(
    name="pyuscope",
    version="2.0.0",
    author="John McMaster",
    author_email='JohnDMcMaster@gmail.com',
    description=("Microscope panorama GUI"),
    license="BSD",
    keywords="microscope touptek",
    url='https://github.com/JohnDMcMaster/pyuscope',
    packages=['uscope'],
    scripts=scripts_dist,
    # FIXME
    install_requires=[
    ],
    long_description=read('README.md'),
    classifiers=[
        "License :: OSI Approved :: BSD License",
    ],
)
