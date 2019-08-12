import argparse        
from PIL import Image

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='spaghetti code')
    parser.add_argument('fn', help='to process')
    args = parser.parse_args()
    
    img = Image.open(args.fn)
    i = 0
    rval = 0
    gval = 0
    bval = 0
    xs = 5
    ys = 5
    for y in xrange(0, img.size[1], ys):
        for x in xrange(0, img.size[0], xs):
            (r, g, b) = img.getpixel((x, y))
            if i < 10:
                    print x, y, ':', r, g, b
            i += 1
            rval += r
            gval += g
            bval +=b
    sz = img.size[0] * img.size[1] / xs / ys
    rbal = 1.0 * rval / gval
    gbal = 1.0 * gval / gval
    bbal = 1.0 * bval / gval
    print 'R: %d' % int(rbal * 1000.0)
    print 'G: %d' % int(gbal * 1000.0)
    print 'B: %d' % int(bbal * 1000.0)
