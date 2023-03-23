# Generate animated GIFs which zoom into a specified location in the input image.
#
# Usage:
#   python3 zoom_gif.py --x=2900 --y=1000  --infile=image.jpg --outfile_scale=0.2

import argparse
import imageio
import numpy
from PIL import Image

parser = argparse.ArgumentParser(
    description=
    'Create an animated GIF zooming into specific location in the image.')
parser.add_argument('--zoom_factor',
                    type=float,
                    help='Zoom factor (float).',
                    default=0.05)
parser.add_argument('--zoom_steps',
                    type=int,
                    help='Number of times to zoom in (int).',
                    default=35)
parser.add_argument('--infile',
                    help='Input image filename',
                    default='image.png')
parser.add_argument('--outfile',
                    help='Output image filename',
                    default='zoomed.gif')
parser.add_argument('--x',
                    type=int,
                    help='X of point to zoom into.',
                    default=50)
parser.add_argument('--y',
                    type=int,
                    help='Y of point to zoom into.',
                    default=50)
parser.add_argument('--outfile_scale',
                    type=float,
                    help='Output file size scale (float).',
                    default=1)
parser.add_argument(
    '--infile_scale',
    type=float,
    help=
    'Pre-scale input image (This is not recommended, use outfile_scale). (float).'
)
parser.add_argument('--duration',
                    type=float,
                    help='Gif frame delay (float).',
                    default=0.1)
parser.add_argument('--endframes',
                    type=int,
                    help='Number of frames to hold on max zoom.',
                    default=5)
parser.add_argument('--startframes',
                    type=int,
                    help='Number of frames to hold on before zoom.',
                    default=5)
args = parser.parse_args()

im = Image.open(args.infile)
# Do an initial scale down if specified.
# NOTE: doing this here badly effects image
# quality. Prefer to scale down the output file.
if (args.infile_scale):
    im = im.resize((int(im.size[0] * args.infile_scale),
                    int(im.size[1] * args.infile_scale)))
    # Also scale the target x and y coordinates
    args.x = int(args.x * args.infile_scale)
    args.y = int(args.y * args.infile_scale)

target = (args.x, args.y)
# Output image size
w, h = im.size
w = int(w * args.outfile_scale)
h = int(h * args.outfile_scale)
outsize = (w, h)

# Initial bounds
top_left = [0, 0]
bot_right = [im.size[0], im.size[1]]
writer = imageio.get_writer(args.outfile, duration=args.duration, mode='I')
zoomed_im = None

# Add some frames in the start to hold before zooming
for i in range(args.startframes):
    writer.append_data(numpy.array(im.resize(outsize)))

# Loop over the zoom levels and create a new frame for each one
for i in range(0, args.zoom_steps - 1):
    print('.', end='', flush=True)
    # Calculate the top left corner of the crop
    top_left[0] = top_left[0] + ((target[0] - top_left[0]) * args.zoom_factor)
    top_left[1] = top_left[1] + ((target[1] - top_left[1]) * args.zoom_factor)
    # Calculate the bottom right corner of the crop
    bot_right[0] = bot_right[0] - \
        ((bot_right[0] - target[0]) * args.zoom_factor)
    bot_right[1] = bot_right[1] - \
        ((bot_right[1] - target[1]) * args.zoom_factor)

    zoomed_im = im.crop((top_left[0], top_left[1], bot_right[0], bot_right[1]))
    zoomed_im = zoomed_im.resize(outsize)
    writer.append_data(numpy.array(zoomed_im))

# Add max zoom frames to pause for few frames
zoomed_im = numpy.array(zoomed_im)
for i in range(args.endframes):
    writer.append_data(zoomed_im)
writer.close()
