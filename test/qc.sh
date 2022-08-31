#!/usr/bin/env bash
set -ex

rm -rf qc
mkdir qc

passes=10

./torture.sh --x --no-y --no-z --passes $passes
mv torture qc/torture_x

./torture.sh --no-x --y --no-z --passes $passes
mv torture qc/torture_y

./torture.sh --no-x --no-y --z --passes $passes
mv torture qc/torture_z

