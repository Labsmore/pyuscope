#!/usr/bin/python
'''
pr0ncnc: IC die image scan
Copyright 2010 John McMaster <JohnDMcMaster@gmail.com>
Licensed under a 2 clause BSD license, see COPYING for details
'''

from collections import OrderedDict
import json
import math
import os
import shutil
import threading
import time


def drange(start, stop, step, inclusive=False):
    r = start
    if inclusive:
        while r <= stop:
            yield r
            r += step
    else:
        while r < stop:
            yield r
            r += step


def drange_at_least(start, stop, step):
    '''Garauntee max is in the output'''
    r = start
    while True:
        yield r
        if r > stop:
            break
        r += step


# tolerance drange
# in output if within a delta
def drange_tol(start, stop, step, delta=None):
    '''Garauntee max is in the output'''
    if delta is None:
        delta = step * 0.05
    r = start
    while True:
        yield r
        if r > stop:
            break
        r += step


class PlannerAxis(object):
    def __init__(
        self,
        name,
        # Desired image overlap
        # Actual may be greater if there is more area
        # than minimum number of pictures would support
        req_overlap,
        # How much the imager can see (in mm)
        view_au,
        # How much the imager can see (in pixels)
        view_pix,
        # start and end absolute positions (in um)
        # Inclusive such that 0:0 means image at position 0 only
        start,
        end,
        log=None):
        if log is None:

            def log(s=''):
                print(s)

        self.log = log
        # How many the pixels the imager sees after scaling
        # XXX: is this global scalar playing correctly with the objective scalar?
        self.view_pixels = view_pix
        #self.pos = 0.0
        self.name = name
        '''
        The naming is somewhat bad on this as it has an anti-intuitive meaning
        
        Proportion of each image that is unique from previous
        Overlap of 1.0 means that images are all unique sections
        Overlap of 0.0 means never move and keep taking the same spot
        '''
        self.req_overlap = req_overlap

        self.start = start
        # Requested end, not necessarily true end
        self.req_end = end
        self.end = end
        if self.delta() < view_au:
            self.log('Axis %s: delta %0.3f < view %0.3f, expanding end' %
                     (self.name, self.delta(), view_au))
            self.end = start + view_au
        self.view_au = view_au

        # Its actually less than this but it seems it takes some stepping
        # to get it out of the system
        self.backlash = 0.050
        # sort out later
        #self.backlash = None
        '''
        Backlash compensation
        0: no compensation
        -1: compensated for decreasing
        1: compensated for increasing
        '''
        self.comp = 0

    def meta(self):
        ret = {}
        ret['backlash'] = self.backlash
        ret['overlap'] = self.step_percent()
        ret['view_pixels'] = self.view_pixels
        # FIXME: find a way to verify au is in pixels
        ret['view_mm'] = self.view_au
        ret['pixels'] = self.delta_pixels()
        # all of my systems are set to mm though, even if they are imperial screws
        ret['pixels_mm'] = self.view_pixels / self.view_au
        mm = self.delta_pixels() / ret['pixels_mm']
        ret['mm'] = mm
        return ret

    def delta(self):
        '''Total distance that will actually be imaged'''
        return self.end - self.start

    def req_delta(self):
        '''Total distance that needs to be imaged (ie requested)'''
        return self.req_end - self.start

    def delta_pixels(self):
        return self.images_ideal() * self.view_pixels

    def images_ideal(self):
        '''
        Always 1 non-overlapped image + the overlapped images
        (can actually go negative though)
        Remaining distance from the first image divided by
        how many pixels of each image are unique to the previously taken image when linear
        '''
        if self.req_delta() <= self.view_au:
            return 1.0 * self.req_delta() / self.view_au
        ret = 1.0 + (self.req_delta() - self.view_au) / (self.req_overlap *
                                                         self.view_au)
        if ret < 0:
            raise Exception('bad number of idea images %s' % ret)
        return ret

    def images(self):
        '''How many images should actually take after considering margins and rounding'''
        ret = int(math.ceil(self.images_ideal()))
        if ret < 1:
            raise Exception('Bad number of images %d' % ret)
        return ret

    def step(self):
        '''How much to move each time we take the next image'''
        '''
        Note that one picture has wider coverage than the others
        Thus its treated specially and subtracted from the remainder
        
        It is okay for the second part to be negative since we could
        try to image less than our sensor size
        However, the entire quantity should not be negative
        '''
        # Note that we don't need to adjust the initial view since its fixed, only the steps
        images_to_take = self.images()
        if images_to_take == 1:
            return self.delta()
        else:
            return (self.delta() - self.view_au) / (images_to_take - 1.0)

    def step_percent(self):
        '''Actual percentage we move to take the next picture'''
        # Contrast with requested value self.req_overlap
        return self.step() / self.view_au

    def points(self):
        step = self.step()
        for i in range(self.images()):
            yield self.start + i * step


class Stop(Exception):
    pass


class Planner(object):
    def __init__(
        self,
        scan_config,
        hal,
        imager,
        # (w, h) in pixels
        img_sz,
        # 10 => each pixel is 10 um x 10 um
        unit_per_pix,
        out_dir,
        progress_cb=None,
        dry=False,
        log=None,
        verbosity=2,
        imagerj={}):
        if log is None:

            def log(msg='', verbosity=None):
                print(msg)

        self._log = log
        self.v = verbosity
        self.hal = hal
        self.imager = imager
        self.dry = dry
        # os.path.join(config['cnc']['out_dir'], self.rconfig.job_name)
        self.out_dir = out_dir
        self.img_sz = img_sz

        # polarity such that can wait on being set
        self.unpaused = threading.Event()
        self.unpaused.set()

        self.running = True

        # FIXME: this is better than before but CTypes pickle error from deepcopy
        self.config = scan_config
        self.progress_cb = progress_cb

        self.imagerj = imagerj

        ideal_overlap = 0.7
        if 'overlap' in scan_config:
            ideal_overlap = float(scan_config['overlap'])
        # Maximum allowable overlap proportion error when trying to fit number of snapshots
        #overlap_max_error = 0.05
        '''
        Planar test run
        plane calibration corner ended at 0.0000, 0.2674, -0.0129
        '''

        start = [
            float(scan_config['start']['x']),
            float(scan_config['start']['y'])
        ]
        end = [float(scan_config['end']['x']), float(scan_config['end']['y'])]
        border = None
        if 'border' in scan_config:
            border = float(scan_config['border'])
            start[0] -= border
            start[1] -= border
            end[0] += border
            end[1] += border
        self.axes = OrderedDict([
            ('x',
             PlannerAxis('X',
                         ideal_overlap,
                         img_sz[0] * unit_per_pix,
                         img_sz[0],
                         start[0],
                         end[0],
                         log=self.log)),
            ('y',
             PlannerAxis('Y',
                         ideal_overlap,
                         img_sz[1] * unit_per_pix,
                         img_sz[1],
                         start[1],
                         end[1],
                         log=self.log)),
        ])
        self.x = self.axes['x']
        self.y = self.axes['y']

        self.stack_init()

        for axisc, axis in self.axes.items():
            self.log('Axis %s' % axisc)
            self.log('  %f to %f' % (axis.start, axis.end), 2)
            self.log(
                '  Ideal overlap: %f, actual %g' %
                (ideal_overlap, axis.step_percent()), 2)
            self.log('  full delta: %f' % (self.x.delta()), 2)
            self.log('  view: %d pix' % (axis.view_pixels, ), 2)
            if border:
                self.log('  border: %f' % border)

        # A true useful metric of efficieny loss is how many extra pictures we had to take
        # Maybe overhead is a better way of reporting it
        ideal_n_pictures = self.x.images_ideal() * self.y.images_ideal()
        expected_n_pictures = self.x.images() * self.y.images()
        self.log(
            'Ideally taking %g pictures (%g X %g) but actually taking %d (%d X %d), %0.1f%% efficient'
            % (ideal_n_pictures, self.x.images_ideal(), self.y.images_ideal(),
               expected_n_pictures, self.x.images(), self.y.images(),
               ideal_n_pictures / expected_n_pictures * 100.0), 2)

        # Try actually generating the points and see if it matches how many we thought we were going to get
        self.pictures_to_take = self.n_xy()
        if self.config.get('exclude', []):
            self.log('Suppressing picture take check on exclusions')
        elif self.pictures_to_take != expected_n_pictures:
            self.log(
                'Going to take %d pictures but thought was going to take %d pictures (x %d X y %d)'
                % (self.pictures_to_take, expected_n_pictures, self.x.images(),
                   self.y.images()))
            self.log('Points:')
            for p in self.gen_xys():
                self.log('    ' + str(p))
            raise Exception('See above')

        # Total number of images taken
        self.all_imgs = 0
        # Number of images taken at unique x, y coordinates
        # May be different than all_imags if image stacking
        self.xy_imgs = 0

        self.img_ext = '.jpg'

        self.notify_progress(None, True)

    def check_running(self):
        if not self.running:
            raise Stop()

    def log(self, msg='', verbosity=2):
        if verbosity <= self.v:
            self._log(msg)

    def stack_init(self):
        if 'stack' in self.config:
            stack = self.config['stack']
            self.num_stack = int(stack['num'])
            self.stack_step_size = int(stack['step_size'])
        else:
            self.num_stack = None
            self.stack_step_size = None

    def notify_progress(self, image_file_name, first=False):
        if self.progress_cb:
            self.progress_cb(self.pictures_to_take, self.xy_imgs,
                             image_file_name, first)

    def comment(self, s='', verbosity=2):
        if len(s) == 0:
            self.log(verbosity=verbosity)
        else:
            self.log('# %s' % s, verbosity=verbosity)

    def end_program(self):
        pass

    def is_paused(self):
        return not self.unpaused.is_set()

    def pause(self):
        '''Used to pause movement'''
        self.unpaused.clear()

    def unpause(self):
        self.unpaused.set()

    def stop(self):
        self.running = False

    def write_meta(self):
        # Copy config for reference
        def dumpj(j, fn):
            open(os.path.join(self.out_dir, fn), 'w').write(
                json.dumps(j, sort_keys=True, indent=4,
                           separators=(',', ': ')))

        dumpj(self.gen_meta(), 'out.json')

        # TODO: write out coordinate map

    def prepare_image_output(self):
        if self.dry:
            self.log('DRY: mkdir(%s)' % self.out_dir)
            return

        if not os.path.exists(self.out_dir):
            self.log('Creating output directory %s' % self.out_dir)
            os.mkdir(self.out_dir)

    def img_fn(self, stack_suffix=''):
        return os.path.join(
            self.out_dir,
            'c%03d_r%03d%s' % (self.cur_col, self.cur_row, stack_suffix))

    def take_picture(self, fn_base):
        self.hal.settle()
        if not self.dry:
            images = self.imager.get()
            if len(images) == 1:
                image = list(images.values())[0]
                image.save(fn_base + self.img_ext)
            else:
                for k, image in images.items():
                    image.save(fn_base + "_" + k + self.img_ext)
        self.all_imgs += 1

    def take_pictures(self):
        if self.num_stack:
            n = self.num_stack
            if n % 2 != 1:
                raise Exception('Center stacking requires odd n')
            # how much to step on each side
            n2 = (self.num_stack - 1) / 2
            self.hal.mv_abs({'z': -n2 * self.stack_step_size})
            '''
            Say 3 image stack
            Move down 1 step to start and will have to do 2 more
            '''
            for i in range(n):
                img_fn = self.img_fn('_z%02d' % i)
                self.take_picture(img_fn)
                # Avoid moving at end
                if i != n:
                    self.mv_rel(None, None, self.stack_step_size)
                    # we now sleep before the actual picture is taken
                    #time.sleep(3)
                self.notify_progress(img_fn)
        else:
            img_fn = self.img_fn()
            self.take_picture(img_fn)
        self.xy_imgs += 1
        self.notify_progress(img_fn)

    def validate_point(self, p):
        (cur_x, cur_y), (cur_col, cur_row) = p
        #self.log('xh: %g vs cur %g, yh: %g vs cur %g' % (xh, cur_x, yh, cur_y))
        # mm vs inch...hmm
        au_tol = 0.005
        au_tol *= 25.4

        # Basic sanity check
        au_tol = self.y.view_au / 100
        xmax = cur_x + self.x.view_au
        ymax = cur_y + self.y.view_au

        fail = False

        if cur_col < 0 or cur_col >= self.x.images():
            self.log('Col out of range 0 <= %d < %d' %
                     (cur_col, self.x.images()))
            fail = True
        if cur_x < self.x.start - au_tol or xmax > self.x.end + au_tol:
            self.log('X out of range')
            fail = True

        if cur_row < 0 or cur_row >= self.y.images():
            self.log('Row out of range 0 <= %d < %d' %
                     (cur_row, self.y.images()))
            fail = True
        if cur_y < self.y.start - au_tol or ymax > self.y.end + au_tol:
            self.log('Y out of range')
            fail = True

        if fail:
            self.log('Bad point:')
            self.log('  X: %g' % cur_x)
            self.log('  Y: %g' % cur_y)
            self.log('  Row: %g' % cur_row)
            self.log('  Col: %g' % cur_col)
            raise Exception(
                'Bad point (%g + %g = %g, %g + %g = %g) for range (%g, %g) to (%g, %g)'
                % (cur_x, self.x.view_au, xmax, cur_y, self.y.view_au, ymax,
                   self.x.start, self.y.start, self.x.end, self.y.end))

    def exclude(self, p):
        (_xy, (cur_row, cur_col)) = p
        for exclusion in self.config.get('exclude', []):
            '''
            If neither limit is specified don't exclude
            maybe later: if one limit is specified but not the other take it as the single bound
            '''
            r0 = exclusion.get('r0', float('inf'))
            r1 = exclusion.get('r1', float('-inf'))
            c0 = exclusion.get('c0', float('inf'))
            c1 = exclusion.get('c1', float('-inf'))
            if cur_row >= r0 and cur_row <= r1 and cur_col >= c0 and cur_col <= c1:
                self.log('Excluding r%d, c%d on r%s:%s, c%s:%s' %
                         (cur_row, cur_col, r0, r1, c0, c1))
                return True
        return False

    def n_xy(self):
        '''Number of unique x, y coordinates'''
        pictures_to_take = 0
        for _p in self.gen_xys():
            pictures_to_take += 1
        return pictures_to_take

    def gen_xys(self):
        for (x, y), _cr in self.gen_xycr():
            yield (x, y)

    def gen_xycr(self):
        for p in self.gen_xycr_serp():
            self.validate_point(p)
            if self.exclude(p):
                continue
            yield p

    def gen_xycr_serp(self):
        '''Generate serpentine pattern'''
        x_list = [x for x in self.x.points()]
        x_list_rev = list(x_list)
        x_list_rev.reverse()
        row = 0

        active = (x_list, 0, 1)
        nexts = (x_list_rev, len(x_list_rev) - 1, -1)

        for cur_y in self.y.points():
            x_list, col, cold = active

            for cur_x in x_list:
                yield ((cur_x, cur_y), (col, row))
                col += cold
            # swap direction
            active, nexts = nexts, active
            row += 1

    def run(self):
        self.check_running()
        self.start_time = time.time()
        self.log()
        self.log()
        self.log()
        hal_dry_old = self.hal.dry
        self.max_mv = {'x': 0, 'y': 0}
        try:
            self.hal.set_dry(self.dry)
            self.comment('Generated by pr0ncnc on %s' %
                         (time.strftime("%d/%m/%Y %H:%M:%S"), ))
            self.comment('Size: x %f um / %d pix, y %f um / %d pix' %
                         (self.x.delta(), self.x.delta_pixels(),
                          self.y.delta(), self.y.delta_pixels()))
            mp = self.x.delta_pixels() * self.y.delta_pixels() / (10**6)
            imgr_mp = self.img_sz[0] * self.img_sz[0] / 1.e6
            self.comment('Imager size: %0.1f MP (%dw x %dh) raw => %0.1f MP' %
                         (imgr_mp, self.img_sz[0], self.img_sz[1], imgr_mp))

            if mp >= 1000:
                self.comment('Image size: %0.1f GP' % (mp / 1000, ))
            else:
                self.comment('Image size: %0.1f MP' % (mp, ))
            self.comment('fov: x %f, y %f' % (self.x.view_au, self.y.view_au))
            self.comment('step: x %f, y %f' % (self.x.step(), self.y.step()))

            self.comment('pictures: %d' % self.pictures_to_take)
            self.comment()

            self.prepare_image_output()

            if not self.dry:
                self.hal.begin()

            # Do initial backlash compensation
            self.backlash_init()

            self.cur_col = -1
            # columns
            for ((cur_x, cur_y), (self.cur_col,
                                  self.cur_row)) in self.gen_xycr():
                self.check_running()

                self.log('')
                self.log('Pictures taken: %d / %d' %
                         (self.xy_imgs, self.pictures_to_take))
                if not self.unpaused.is_set():
                    self.log('Planner paused')
                    self.unpaused.wait()
                    self.log('Planner unpaused')

                #self.log('', 3)
                self.comment(
                    'comp (%d, %d), pos (%f, %f)' %
                    (self.x.comp, self.y.comp, cur_x, cur_y), 3)

                self.mv_abs_backlash({'x': cur_x, 'y': cur_y})
                self.take_pictures()

            self.hal.ret0()
            self.end_program()
            self.end_time = time.time()

            self.log()
            self.log()
            self.log()
            self.log('Pictures taken: %d / %d' %
                     (self.xy_imgs, self.pictures_to_take))
            self.log('Max x: %0.3f, y: %0.3f' %
                     (self.max_mv['x'], self.max_mv['y']))
            self.log('  G0 X%0.3f Y%0.3f' %
                     (self.max_mv['x'], self.max_mv['y']))
            if self.xy_imgs != self.pictures_to_take:
                if self.config.get('exclude', []):
                    self.log(
                        'Suppressing for exclusion: pictures taken mismatch (taken: %d, to take: %d)'
                        % (self.pictures_to_take, self.xy_imgs))
                else:
                    raise Exception(
                        'pictures taken mismatch (taken: %d, to take: %d)' %
                        (self.pictures_to_take, self.xy_imgs))
            if not self.dry:
                self.write_meta()
        finally:
            print(('Planner: restoring old dry %s' % (str(hal_dry_old))))
            self.hal.set_dry(hal_dry_old)

    def gen_meta(self):
        '''Can only be called after run'''

        plannerj = {}

        # plannerj['x'] = ...
        for axisc, axis in self.axes.items():
            plannerj[axisc] = axis.meta()

        # In seconds
        plannerj['time'] = self.end_time - self.start_time
        plannerj['pictures_to_take'] = self.pictures_to_take
        plannerj['pictures_taken'] = self.xy_imgs

        ret = {}
        # User scan parameters
        ret['params'] = self.config
        # Calculated scan parameters
        ret['planner'] = plannerj
        # Misc system metadata
        ret['imager'] = self.imagerj
        return ret

    def backlash_init(self):
        # TODO: rethink this for non-0 start
        if self.x.backlash:
            self.hal.mv_abs({'x': -self.x.backlash, 'y': -self.y.backlash})
        self.x.comp = 1
        self.y.comp = 1

    def mv_abs_backlash(self, move_to):
        '''Do an absolute move with backlash compensation'''
        def fmt_axis(c):
            if c in move_to:
                self.max_mv[c] = max(self.max_mv[c], move_to[c])
                return '%c: %0.3f' % (c, move_to[c])
            else:
                return '%c: none'

        self.comment('mv_abs_backlash: %s, %s' %
                     (fmt_axis('x'), fmt_axis('y')))
        pos = self.hal.pos()
        blsh_mv = {}
        for axisc in list(move_to.keys()):
            axis = self.axes[axisc]
            if not axis.backlash:
                continue
            delta = move_to[axisc] - pos[axisc]
            #print 'planner back', axisc, axis.comp, delta, move_to[axisc], pos[axisc]

            # Going right but was not compensating right?
            if delta > 0 and axis.comp <= 0:
                self.log(
                    'Axis %c: compensate for changing to increasing' % axisc,
                    2)
                blsh_mv[axisc] = -axis.backlash
                axis.comp = 1
            # Going left but was not compensating left?
            elif delta < 0 and axis.comp >= 0:
                self.log(
                    'Axis %c: compensate for changing to decreasing' % axisc,
                    2)
                blsh_mv[axisc] = +axis.backlash
                axis.comp = -1
        self.hal.mv_rel(blsh_mv)
        self.hal.mv_abs(move_to)
