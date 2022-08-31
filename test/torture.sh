 rm -rf torture/
 mkdir torture
 python3 -u ./test/motion/motion_torture.py --microscope lip-a1 "$@" |tee torture/log.txt

