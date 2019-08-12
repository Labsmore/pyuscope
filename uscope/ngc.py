'''
Standard references
http://linuxcnc.org/docs/html/gcode.html
http://www.shender4.com/thread_chart.htm
https://www.fnal.gov/pub/takefive/pdfs/Drill_Press_Speed_Chart.pdf
'''

import sys
from os import path

cnc = None
class CNC(object):
    def __init__(self, em=-1, rpm=None, fr=2.0, fr_z=1.0, verbose=False):
        # was default 1./8
        # for drilling?
        if em is not None and em <= 0:
            raise ValueError("Invalid endmill diameter")
        
        # Endmill diameter
        self.em = em
        
        # Z rising to clear part ("chaining")
        # Slow withdrawl in material
        self.clear_zps = 0.050
        # After material clear
        self.clear_zp = 0.100
        
        # Depth to go all the way through the part
        self.clear_zn_u = -0.020
        
        # Distance between finishing pass contours
        self.finish_u = 0.005
        
        # Main feedrate (xy)
        self.fr = fr
        # Plunge feedrate
        self.fr_z = fr_z
        
        self.rpm = rpm
        self.verbose = verbose
        
        # Name output .ngc same as the generating python file
        # Could support multiple files by adding an open function if needed
        m_fn = path.abspath(sys.modules['__main__'].__file__)
        ngc_fn = m_fn.replace('.py', '.ngc')
        if ngc_fn.find('.ngc') < 0:
            raise Exception("Failed to replace extension")
        # Output file
        self.f = open(ngc_fn, 'w')
        
        self.chain = clear_z
        
def init(*args, **kwargs):
    global cnc
    
    cnc = CNC(*args, **kwargs)
    start()
    return cnc

def line(s='', verbose=None):
    if verbose is None:
        verbose = cnc.verbose
    cnc.f.write(s + '\n')
    if verbose:
        print s

def comment(s):
    if s.find('(') >= 0:
        raise ValueError("Nested comment")
    line('(%s)' % s)

def comment_block(s):
    comment('*' * 80)
    comment(s)
    comment('*' * 80)

def start():
    if cnc.em is None:
        comment('Endmill: none.  Drill only')
    else:
        comment('Endmill: %0.4f' % cnc.em)
    line('G90')
    clear_zq()
    rpm(cnc.rpm)

def rpm(val):
    line('M3 S%0.1f' % val)

def end():
    line()
    # Make sure don't crash
    clear_zq()
    line('G0 X0 Y0')
    line('M30')

def fmt(f):
    return '%+0.3f' % f

def clear_z():
    line('G1 Z%0.3f F%0.3f' % (cnc.clear_zps, cnc.fr_z))
    line('G0 Z%0.3f' % cnc.clear_zp)

def clear_zq():
    line('G0 Z%0.3f' % cnc.clear_zp)

def clear_zn():
    line('G0 Z%0.3f' % cnc.clear_zps)
    line('G1 Z%0.3f F%0.3f' % (cnc.clear_zn_u, cnc.fr_z))

# Exact clearance
def clear_ep(pos):
    line('(ClearE+ %0.3f)' % pos)
    return '%0.3f' % (pos + cnc.em/2)

def clear_en(pos):
    line('(ClearE- %0.3f)' % pos)
    return '%0.3f' % (pos - cnc.em/2)

# Delta clearance
def clear_dp(pos):
    line('(Clear+ %0.3f)' % pos)
    return '%0.3f' % (pos + cnc.em/2 + 0.25)

def clear_dn(pos):
    line('(Clear- %0.3f)' % pos)
    return '%0.3f' % (pos - cnc.em/2 - 0.25)

def g0(x=None, y=None, z=None):
    xstr = ''
    ystr = ''
    zstr = ''
    if x is not None:
        xstr = ' X%s' % fmt(x)
    if y is not None:
        ystr = ' Y%s' % fmt(y)
    if z is not None:
        zstr = ' Z%s' % fmt(z)
    line('G0%s%s%s' % (xstr, ystr, zstr))

def g1(x=None, y=None, z=None):
    xstr = ''
    ystr = ''
    zstr = ''
    if x is not None:
        xstr = ' X%s' % fmt(x)
    if y is not None:
        ystr = ' Y%s' % fmt(y)
    if z is not None:
        zstr = ' Z%s' % fmt(z)
    line('G1%s%s%s F%0.3f' % (xstr, ystr, zstr, cnc.fr))

def m0():
    line('M0')

def m1():
    line('M1')

# Cut rectangle with upper left coordinate given
# Cutter centered on rectangle
def rect_slot_ul(x, y, w, h, com=True, chain=True, leadin='g0'):
    if com:
        line()
        line('(rect_slot_ul X%s Y%s W%s H%s)' % (fmt(x), fmt(y), fmt(w), fmt(h)))
    if leadin == 'g0':
        g0(x, y)
        clear_zn()
    elif leadin == 'g1':
        g1(x, y)
    else:
        raise Exception("Oops")
    g1(x + w, y + 0)
    g1(x + w, y + h)
    g1(x + 0, y + h)
    g1(x + 0, y + 0)
    if chain:
        cnc.chain()
    
# Cut rectangle, compensating to cut inside of it
# Endmill is assumed to be square
def rect_in_ul(x, y, w, h, finishes=1, chain=True, com=True):
    if com:
        line()
        line('(rect_in_ul X%s Y%s W%s H%s)' % (fmt(x), fmt(y), fmt(w), fmt(h)))
    # Roughing pass
    if finishes:
        if finishes != 1:
            raise Exception("FIXME")
        line('(Rough)')
        rect_slot_ul(x + cnc.em/2 + cnc.finish_u, y + cnc.em/2 + cnc.finish_u, w - cnc.em - cnc.finish_u, h - cnc.em - cnc.finish_u, com=False, chain=False)
        # Finishing pass
        line('(Finish)')
        rect_slot_ul(x + cnc.em/2, y + cnc.em/2, w - cnc.em, h - cnc.em, com=False, chain=chain, leadin='g1')
    else:
        # Finishing pass
        rect_slot_ul(x + cnc.em/2, y + cnc.em/2, w - cnc.em, h - cnc.em, com=False, chain=chain)

def rect_in_cent(x, y, w, h, *args, **kwargs):
    x0 = x - w/2
    y0 = y - h/2
    if kwargs.get('com', True):
        line()
        line('(rect_in_cent X%s Y%s W%s H%s)' % (fmt(x), fmt(y), fmt(w), fmt(h)))
    kwargs['com'] = False
    rect_in_ul(x0, y0, w, h, *args, **kwargs)

'''
G2: clockwise arc
G3: counterclockwise arc
''' 
def circ_cent_slot(x, y, r, cw=False, com=True, leadin='g0', chain=True):
    if com:
        line()
        line('(circ_cent_slot X%sf Y%s R%s)' % (fmt(x), fmt(y), fmt(r)))

    # Arbitrarily start at left
    x0 = x - r
    if leadin == 'g0':
        g0(x0, y)
        clear_zn()
    elif leadin == 'g1':
        g1(x0, y)
    else:
        raise Exception("Oops")

    line('G3 I%0.3f F%0.3f' % (r, cnc.fr))
    if chain:
        cnc.chain()

# Cut circle centered at x, y 
# Leaves a hole the size of r
def circ_cent_in(x, y, r):
    line()
    line('(circ_cent_in X%s Y%s R%s)' % (fmt(x), fmt(y), fmt(r)))
    raise Exception("FIXME")

# Cut circle centered at x, y 
# Leaves a cylinder the size of r
def circ_cent_out(x, y, r, finishes=1):
    line()
    line('(circ_cent_out X%s Y%s R%s)' % (fmt(x), fmt(y), fmt(r)))
    # Roughing pass
    if finishes:
        if finishes != 1:
            raise Exception("FIXME")
        line('(Rough)')
        circ_cent_slot(x, y, r + cnc.em + cnc.finish_u, cw=True, com=False, chain=False)
        line('(Finish)')
        circ_cent_slot(x, y, r + cnc.em, cw=False, com=False, leadin='g1')
    else:
        circ_cent_slot(x, y, r + cnc.em, cw=False, com=False)


def endrange(start, end, inc, finish=0.001, com=False):
    '''Inclusive float range(): ending at end instead of beginning like range does'''
    if com:
        comment('endrange %0.3f, %0.3f, %0.3f, finish=%0.3f' % (start, end, inc, finish))
    ret = []
    if inc < 0:
        raise ValueError()
    if finish:
        ret.append(end)
        pos = end + finish
    else:
        pos = end
    if start < end:
        while pos > start:
            ret.append(pos)
            pos -= inc
    else:
        while pos < start:
            ret.append(pos)
            pos += inc
    ret.reverse()
    return ret

'''
for cutting a pocket with the edge at the bottom (below y)

pre-chain:
endmill should be below the part edge
it will rapid move x into position and then actuate y

post-chain
endmill will be in lower right corner

align
lr: lower right
goes left right
coordinates relative to align
'''
def pocket_lr(x, y, w, h, finishes=1, finish_only=False):
    '''
    # clear Y
    g0(y=(cnc.em/2 + 0.05))
    # Back to X
    g0(x=-(x + cnc.em/2))
    
    for y in endrange(-(y + cnc.em / 2), -(y + h - cnc.em / 2), cnc.em/2):
        g1(y=y)
        g1(x=-(x + w + cnc.em / 2))
        # Clear
        g0(x=-(x + cnc.em / 2))
    '''

    comment('pocket_lr X%0.3f Y%0.3f W%0.3F H%0.3F' % (x, y, w, h))

    # left, right
    # upper, lower
    xl = x - w + cnc.em/2
    xr = x - cnc.em/2
    yu = y - h + cnc.em/2
    yl = y - cnc.em/2
    
    finish = 0.005
    if finishes:
        comment('Finish: %d %0.3f' % (finishes, finish))
    # unfinished boundary
    if finishes:
        xl_uf = xl + finish
        xr_uf = xr - finish
        yu_uf = yu + finish
        yl_uf = yl - finish
    else:
        xl_uf = xl
        xr_uf = xr
        yu_uf = yu
        yl_uf = yl
    
    line()
    comment("chain to lower right corner")
    # clear Y
    y_clear = y + cnc.em/2 + 0.05
    g0(y=y_clear)
    
    for ythis in endrange(y, yu_uf, cnc.em/2, finish=0, com=True):
        if finish_only:
            continue
        line()
        comment('y=%.03f' % ythis)
        # clear
        g0(x=xl_uf)
        # feed into material
        g1(y=ythis)
        # cut
        g1(x=xr_uf)
    
    if finish_only:
        g0(x=xr_uf)
    
    line()
    
    # cutter is at right
    # slowly cut to return y, clearing the nubs
    comment('cut nubs')
    g1(y=y_clear)

    line()
    
    comment('pocket perimeter')
    # Now do finishing pass around
    
    # Return known location
    # WARNING: right side will have nubs
    # clear, moving to lower right avoiding nubs
    #g0(x=xl_uf)
    #g0(y=y_clear)
    #g0(x=xr_uf)
    # and dig in for the perimeter cut
    #g1(x=xr, y=yl)

    # TODO: verify chain
    # chain good
    # line('M1')

    # Now carve out
    def perim(delta):
        comment('perim w/ delta %0.3f' % delta)
        comment('chain to lr')
        g1(x=xr - delta)
        g1(y=yl - delta)
        comment('lr to ur')
        g1(xr - delta, yu + delta)
        comment('ur to ul')
        if finish_only:
            g0(xl + delta, yu + delta)
        else:
            g1(xl + delta, yu + delta)
        comment('ul to ll')
        g1(xl + delta, yl - delta)
        # already cut
        #comment('ll to lr')
        #g1(xr - delta, yl - delta)

    #if finishes:
    #    perim(finish)
    perim(0.0)

    # chain to edge
    g1(y=y_clear)
    
