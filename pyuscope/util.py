import datetime
import inspect
import os
import shutil
import sys

def print_debug(s = None):
    if False:
        print 'DEBUG: %s' % s

def add_bool_arg(parser, yes_arg, default=False, **kwargs):
    dashed = yes_arg.replace('--', '')
    dest = dashed.replace('-', '_')
    parser.add_argument(yes_arg, dest=dest, action='store_true', default=default, **kwargs)
    parser.add_argument('--no-' + dashed, dest=dest, action='store_false', **kwargs)

def hexdump(data, label=None, indent='', address_width=8, f=sys.stdout):
    def isprint(c):
        return c >= ' ' and c <= '~'

    if label:
        print label
    
    bytes_per_half_row = 8
    bytes_per_row = 16
    data = bytearray(data)
    data_len = len(data)
    
    def hexdump_half_row(start):
        left = max(data_len - start, 0)
        
        real_data = min(bytes_per_half_row, left)

        f.write(''.join('%02X ' % c for c in data[start:start+real_data]))
        f.write(''.join('   '*(bytes_per_half_row-real_data)))
        f.write(' ')

        return start + bytes_per_half_row

    pos = 0
    while pos < data_len:
        row_start = pos
        f.write(indent)
        if address_width:
            f.write(('%%0%dX  ' % address_width) % pos)
        pos = hexdump_half_row(pos)
        pos = hexdump_half_row(pos)
        f.write("|")
        # Char view
        left = data_len - row_start
        real_data = min(bytes_per_row, left)

        f.write(''.join([c if isprint(c) else '.' for c in str(data[row_start:row_start+real_data])]))
        f.write((" " * (bytes_per_row - real_data)) + "|\n")

'''
    (
    "\x08\x84\xA4\x06\x02\x00\x26\x00\x43\x00\xC0\x03\x00\x08\x10\x24"
    "\x00\x00\xC0\x1E\x00\x00\x85\x00")
'''
def str2hex(buff, prefix='', terse=True):
    if len(buff) == 0:
        return '""'
    buff = bytearray(buff)
    ret = ''
    if terse and len(buff) > 16:
        ret += '\n'
    for i in xrange(len(buff)):
        if i % 16 == 0:
            if i != 0:
                ret += '" \\\n'
            if len(buff) <= 16:
                ret += '"'
            if not terse or len(buff) > 16:
                ret += '%s"' % prefix
            
        ret += "\\x%02X" % (buff[i],)
    return ret + '"'

def where(pos=1):
    # 0 represents this line
    # 1 represents line at caller
    callerframerecord = inspect.stack()[pos]
    frame = callerframerecord[0]
    info = inspect.getframeinfo(frame)
    print '%s.%s():%d' % (info.filename, info.function, info.lineno)

# Print timestamps in front of all output messages
class IOTimestamp(object):
    def __init__(self, obj=sys, name='stdout'):
        self.obj = obj
        self.name = name
        
        self.fd = obj.__dict__[name]
        obj.__dict__[name] = self
        self.nl = True

    def __del__(self):
        if self.obj:
            self.obj.__dict__[self.name] = self.fd

    def flush(self):
        self.fd.flush()
       
    def write(self, data):
        parts = data.split('\n')
        for i, part in enumerate(parts):
            if i != 0:
                self.fd.write('\n')
            # If last bit of text is just an empty line don't append date until text is actually written
            if i == len(parts) - 1 and len(part) == 0:
                break
            if self.nl:
                self.fd.write('%s: ' % datetime.datetime.utcnow().isoformat())
            self.fd.write(part)
            # Newline results in n + 1 list elements
            # The last element has no newline
            self.nl = i != (len(parts) - 1)

# Log file descriptor to file
class IOLog(object):
    def __init__(self, obj=sys, name='stdout', out_fn=None, out_fd=None, mode='a', shift=False, multi=False):
        if not multi:
            if out_fd:
                self.out_fd = out_fd
            else:
                self.out_fd = open(out_fn, 'w')
        else:
            # instead of jamming logs together, shift last to log.txt.1, etc
            if shift and os.path.exists(out_fn):
                i = 0
                while True:
                    dst = out_fn + '.' + str(i)
                    if os.path.exists(dst):
                        i += 1
                        continue
                    shutil.move(out_fn, dst)
                    break
            
            hdr = mode == 'a' and os.path.exists(out_fn)
            self.out_fd = open(out_fn, mode)
            if hdr:
                self.out_fd.write('*' * 80 + '\n')
                self.out_fd.write('*' * 80 + '\n')
                self.out_fd.write('*' * 80 + '\n')
                self.out_fd.write('Log rolled over\n')
        
        self.obj = obj
        self.name = name
        
        self.fd = obj.__dict__[name]
        obj.__dict__[name] = self
        self.nl = True

    def __del__(self):
        if self.obj:
            self.obj.__dict__[self.name] = self.fd

    def flush(self):
        self.fd.flush()
       
    def write(self, data):
        self.fd.write(data)
        self.out_fd.write(data)
