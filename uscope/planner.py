#!/usr/bin/python
"""
See PLANNER.md for configuration info

pr0ncnc: IC die image scan
Copyright 2010 John McMaster <JohnDMcMaster@gmail.com>
Licensed under a 2 clause BSD license, see COPYING for details

au was supposed to be a unitless unit
In practice I do everything in mm
So everything is represented as mm, but should work for any unit
"""

from collections import OrderedDict
import json
import math
import os
import shutil
import threading
import time

from uscope.motion.hal import DryHal
# FIXME: hack, maybe just move the baacklash parsing out
# at least to stand alone function
from uscope.config import USCMotion


def drange(start, stop, step, inclusive=False):
    """
    range function with double argument
    """
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
    """Guarantee max is in the output"""
    r = start
    while True:
        yield r
        if r > stop:
            break
        r += step


def drange_tol(start, stop, step, delta=None):
    """
    tolerance drange
    in output if within a delta
    """
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
        view_mm,
        # How much the imager can see (in pixels)
        view_pix,
        # start and actual_end absolute positions (in um)
        # Inclusive such that 0:0 means image at position 0 only
        start,
        end,
        backlash,
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
        Overlap of 1.0 means that images_actual are all unique sections
        Overlap of 0.0 means never move and keep taking the same spot
        '''
        self.req_overlap = req_overlap

        self.start = start
        # Requested actual_end, not necessarily true actual_end
        self.requested_end = end
        self.actual_end = end
        if self.requested_delta_mm() < view_mm:
            self.log(
                'Axis %s: delta %0.3f < view %0.3f, expanding actual_end' %
                (self.name, self.requested_delta_mm(), view_mm))
            self.actual_end = start + view_mm
        self.view_mm = view_mm

        # Its actually less than this but it seems it takes some stepping
        # to get it out of the system
        self.backlash = backlash
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
        ret['view_mm'] = self.view_mm
        ret['pixels'] = self.requested_delta_pixels()
        # all of my systems are set to mm though, even if they are imperial screws
        ret['pixels_mm'] = self.view_pixels / self.view_mm
        mm = self.requested_delta_pixels() / ret['pixels_mm']
        ret['mm'] = mm
        return ret

    def requested_delta_mm(self):
        '''Total distance that needs to be imaged (ie requested)'''
        return abs(self.requested_end - self.start)

    def actual_delta_mm(self):
        '''Total distance that will actually be imaged'''
        return abs(self.actual_end - self.start)

    def requested_delta_pixels(self):
        # hmm this is wrong
        # this shouldn't include overlap
        # return int(self.images_ideal() * self.view_pixels)
        return int(
            math.ceil(self.requested_delta_mm() / self.view_mm *
                      self.view_pixels))

    def actual_delta_pixels(self):
        return int(
            math.ceil(self.actual_delta_mm() / self.view_mm *
                      self.view_pixels))

    def images_ideal(self):
        '''
        Always 1 non-overlapped image + the overlapped images_actual
        (can actually go negative though)
        Remaining distance from the first image divided by
        how many pixels of each image are unique to the previously taken image when linear
        '''
        if self.requested_delta_mm() <= self.view_mm:
            return 1.0 * self.requested_delta_mm() / self.view_mm
        ret = 1.0 + (self.requested_delta_mm() -
                     self.view_mm) / (self.req_overlap * self.view_mm)
        if ret < 0:
            raise Exception('bad number of idea images_actual %s' % ret)
        return ret

    def images_actual(self):
        '''How many images_actual should actually take after considering margins and rounding'''
        ret = int(math.ceil(self.images_ideal()))
        if ret < 1:
            raise Exception('Bad number of images_actual %d' % ret)
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
        images_to_take = self.images_actual()
        if images_to_take == 1:
            return self.requested_delta_mm()
        else:
            return (self.requested_delta_mm() -
                    self.view_mm) / (images_to_take - 1.0)

    def step_percent(self):
        '''Actual percentage we move to take the next picture'''
        # Contrast with requested value self.req_overlap
        return self.step() / self.view_mm

    def points(self):
        step = self.step()
        for i in range(self.images_actual()):
            yield self.start + i * step


class Stop(Exception):
    pass


class Planner(object):
    """
    config: JSON like configuration settings
    """
    def __init__(
        self,
        # JSON like configuration settings affecting produced data
        # ex: verbosity, dry, objects are not included
        pconfig,
        # Movement HAL
        motion=None,

        # Image parameters
        # Imaging HAL
        # Takes pictures but doesn't know about physical world
        imager=None,
        # Supply one of the following
        # Most users should supply mm_per_pix
        # mm_per_pix=None,
        # (w, h) in movement units
        # image_wh_mm=None,
        out_dir=None,
        # Progress callback
        progress_cb=None,
        # No movement without setting true
        dry=False,
        meta_base=None,
        # Log message callback
        # Inteded for main GUI log window
        # Defaults to printing to stdout
        log=None,
        verbosity=2):
        if log is None:

            def log(msg='', verbosity=None):
                print(msg)

        self._log = log
        self.v = verbosity
        self.out_dir = out_dir

        self.dry = dry
        if self.dry:
            self.motion = DryHal(motion, log=log)
        else:
            self.motion = motion
        assert self.motion, "Required"

        self.imager = imager
        assert self.imager, "Required"

        if not meta_base:
            self.meta_base = {}
        else:
            self.meta_base = meta_base

        # polarity such that can wait on being set
        self.unpaused = threading.Event()
        self.unpaused.set()

        # FIXME: this is better than before but CTypes pickle error from deepcopy
        self.pconfig = pconfig
        self.progress_cb = progress_cb

        start, end = self.init_contour()
        self.init_axes(start, end)
        self.init_stacking()
        self.init_images()

        self.running = True
        self.notify_progress(None, True)

    def init_contour(self):
        contour = self.pconfig["contour"]

        self.ideal_overlap = self.pconfig.get("step") if self.pconfig.get(
            "step") else 0.7
        # Maximum allowable overlap proportion error when trying to fit number of snapshots
        #overlap_max_error = 0.05
        '''
        Planar test run
        plane calibration corner ended at 0.0000, 0.2674, -0.0129
        '''

        start = [float(contour['start']['x']), float(contour['start']['y'])]
        end = [float(contour['end']['x']), float(contour['end']['y'])]

        # Planner coordinates must be increasing
        # Normalize them
        if start[0] > end[0]:
            start[0], end[0] = end[0], start[0]
        if start[1] > end[1]:
            start[1], end[1] = end[1], start[1]

        self.border = float(self.pconfig.get("border", 0.0))
        start[0] -= self.border
        start[1] -= self.border
        end[0] += self.border
        end[1] += self.border

        return start, end

    def image_scalar(self):
        """Multiplier to go from Imager image size to output image size"""
        return float(self.pconfig.get("imager", {}).get("scalar", 1.0))

    def image_wh(self):
        """Final snapshot image width, height after scaling"""
        raww, rawh = self.imager.wh()
        w = int(raww * self.image_scalar())
        h = int(rawh * self.image_scalar())
        return w, h

    def init_axes(self, start, end):
        # CNC convention is origin should be in lower left of sample
        # Increases up and to the right
        # pr0nscope has ul origin though
        self.origin = self.pconfig.get("motion", {}).get("origin", "ll")
        assert self.origin in ("ll", "ul"), "Invalid coordinate origin"

        x_mm = float(self.pconfig["imager"]["x_view"])
        image_wh = self.image_wh()
        mm_per_pix = x_mm / image_wh[0]
        image_wh_mm = (image_wh[0] * mm_per_pix, image_wh[1] * mm_per_pix)

        motionj = self.pconfig.get("motion", {})
        backlash = USCMotion(j=motionj).backlash()
        """
        +1: do a negative move before a positive move
        0: no compensation
        -1: do a positive move before a negative move

        True => 1 => negative move before positive
        """
        self.backlash_compensate = self.pconfig.get("backlash_compensate", 0)
        if self.backlash_compensate:
            self.backlash_compensate = int(self.backlash_compensate) // abs(
                int(self.backlash_compensate))

        self.axes = OrderedDict([
            ('x',
             PlannerAxis('X',
                         self.ideal_overlap,
                         image_wh_mm[0],
                         image_wh[0],
                         start[0],
                         end[0],
                         backlash=backlash["x"],
                         log=self.log)),
            ('y',
             PlannerAxis('Y',
                         self.ideal_overlap,
                         image_wh_mm[1],
                         image_wh[1],
                         start[1],
                         end[1],
                         backlash=backlash["y"],
                         log=self.log)),
        ])
        self.x = self.axes['x']
        self.y = self.axes['y']

    def init_stacking(self):
        """Focus stacking initialization"""
        if 'stack' in self.pconfig:
            stack = self.pconfig['stack']
            self.num_stack = int(stack['num'])
            self.stack_step_size = int(stack['step_size'])
        else:
            self.num_stack = None
            self.stack_step_size = None

    def init_images(self):
        for axisc, axis in self.axes.items():
            self.log('Axis %s' % axisc)
            self.log('  %f to %f' % (axis.start, axis.actual_end), 2)
            self.log(
                '  Ideal overlap: %f, actual %g' %
                (self.ideal_overlap, axis.step_percent()), 2)
            self.log('  full delta: %f' % (axis.requested_delta_mm()), 2)
            self.log('  view: %d pix' % (axis.view_pixels, ), 2)
            self.log('  border: %f' % self.border)

        # A true useful metric of efficieny loss is how many extra pictures we had to take
        # Maybe overhead is a better way of reporting it
        ideal_n_pictures = self.x.images_ideal() * self.y.images_ideal()
        expected_n_pictures = self.x.images_actual() * self.y.images_actual()
        self.log(
            'Ideally taking %g pictures (%g X %g) but actually taking %d (%d X %d), %0.1f%% efficient'
            % (ideal_n_pictures, self.x.images_ideal(), self.y.images_ideal(),
               expected_n_pictures, self.x.images_actual(),
               self.y.images_actual(),
               ideal_n_pictures / expected_n_pictures * 100.0), 2)

        # Try actually generating the points and see if it matches how many we thought we were going to get
        self.pictures_to_take = self.n_xy()
        if self.pconfig.get('exclude', []):
            self.log('Suppressing picture take check on exclusions')
        elif self.pictures_to_take != expected_n_pictures:
            self.log(
                'Going to take %d pictures but thought was going to take %d pictures (x %d X y %d)'
                % (self.pictures_to_take, expected_n_pictures,
                   self.x.images_actual(), self.y.images_actual()))
            self.log('Points:')
            for p in self.gen_xys():
                self.log('    ' + str(p))
            raise Exception('See above')

        # Total number of images_actual taken
        self.all_imgs = 0
        # Number of images_actual taken at unique x, y coordinates
        # May be different than all_imags if image stacking
        self.xy_imgs = 0

        self.img_ext = '.jpg'
        self.tsettle = self.pconfig.get("tsettle", 0.0)

    def check_running(self):
        if not self.running:
            raise Stop()

    def log(self, msg='', verbosity=2):
        if verbosity <= self.v:
            self._log(msg)

    def notify_progress(self, image_file_name, first=False):
        if self.progress_cb:
            self.progress_cb(self.pictures_to_take, self.xy_imgs,
                             image_file_name, first)

    def comment(self, s='', verbosity=2):
        if len(s) == 0:
            self.log(verbosity=verbosity)
        else:
            # self.log('# %s' % s, verbosity=verbosity)
            # really comments should go to the HAL since only raw g-code output matters here
            self.log('%s' % s, verbosity=verbosity)

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
            if self.dry:
                return
            with open(os.path.join(self.out_dir, fn), 'w') as f:
                f.write(
                    json.dumps(j,
                               sort_keys=True,
                               indent=4,
                               separators=(',', ': ')))

        meta = self.gen_meta()
        dumpj(meta, 'out.json')
        return meta

    def prepare_image_output(self):
        if self.dry:
            self.log('DRY: mkdir(%s)' % self.out_dir)
            return

        if not os.path.exists(self.out_dir):
            self.log('Creating output directory %s' % self.out_dir)
            os.mkdir(self.out_dir)

    def img_fn(self, suffix=''):
        """
        Return filename basename excluding extension
        ex: c001_r004
        """

        # XXX: quick hack, look into something more proper
        if self.origin == "ll":
            return os.path.join(
                self.out_dir,
                'c%03u_r%03u%s' % (self.cur_col, self.y.images_actual() -
                                   self.cur_row - 1, suffix))
        elif self.origin == "ul":
            return os.path.join(
                self.out_dir,
                'c%03u_r%03u%s' % (self.cur_col, self.cur_row, suffix))
        else:
            assert 0, self.origin

    def take_picture(self, fn_base):
        def save(image, fn_base, img_ext):
            fn_full = fn_base + img_ext
            if img_ext == ".jpg":
                image.save(fn_full, quality=95)
            else:
                image.save(fn_full)

        if not self.dry:
            time.sleep(self.tsettle)
        self.motion.settle()
        if not self.dry:
            if self.imager.remote():
                self.imager_take.take()
            else:
                images = self.imager.get()
                # HDR, focus stack, etc may give more than one image
                if len(images) == 1:
                    image = list(images.values())[0]
                    save(image, fn_base, self.img_ext)
                else:
                    for k, image in images.items():
                        save(image, fn_base + "_" + k, self.img_ext)
        self.all_imgs += 1

    def move_absolute(self, pos):
        if self.backlash_compensate:
            # TODO: only do these moves if they are significant
            bpos = {}
            for k in pos.keys():
                bpos[k] = pos[k] - self.backlash_compensate * self.axes[k].backlash
            self.motion.move_absolute(bpos)
        self.motion.move_absolute(pos)

    def take_pictures(self):
        if self.num_stack:
            assert 0, "FIXME"
            n = self.num_stack
            if n % 2 != 1:
                raise Exception('Center stacking requires odd n')
            # how much to step on each side
            n2 = (self.num_stack - 1) / 2
            self.motion.move_absolute({'z': -n2 * self.stack_step_size})
            '''
            Say 3 image stack
            Move down 1 step to start and will have to do 2 more
            '''
            for i in range(n):
                img_fn = self.img_fn('_z%02d' % i)
                self.take_picture(img_fn)
                # Avoid moving at actual_end
                if i != n:
                    self.move_relative(None, None, self.stack_step_size)
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
        au_tol = self.y.view_mm / 100
        xmax = cur_x + self.x.view_mm
        ymax = cur_y + self.y.view_mm

        fail = False

        if cur_col < 0 or cur_col >= self.x.images_actual():
            self.log('Col out of range 0 <= %d < %d' %
                     (cur_col, self.x.images_actual()))
            fail = True
        if cur_x < self.x.start - au_tol or xmax > self.x.actual_end + au_tol:
            self.log('X out of range')
            fail = True

        if cur_row < 0 or cur_row >= self.y.images_actual():
            self.log('Row out of range 0 <= %d < %d' %
                     (cur_row, self.y.images_actual()))
            fail = True
        if cur_y < self.y.start - au_tol or ymax > self.y.actual_end + au_tol:
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
                % (cur_x, self.x.view_mm, xmax, cur_y, self.y.view_mm, ymax,
                   self.x.start, self.y.start, self.x.actual_end,
                   self.y.actual_end))

    def exclude(self, p):
        (_xy, (cur_row, cur_col)) = p
        for exclusion in self.pconfig.get('exclude', []):
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
        """
        Return all image coordinates we'll visit
        ((x, y), (col, row))
        """
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

    def print_run_header(self):
        self.comment('Generated by pyuscope on %s' %
                     (time.strftime("%d/%m/%Y %H:%M:%S"), ))
        self.comment("General notes:")
        self.comment(
            "  Pixel counts are for final scaled image as written to disk")

        self.imager.log_planner_header(self.log)
        self.comment("Focus stacking")
        self.comment("  Images: %s" % self.num_stack)
        # the math seems off here. Disabled for now / needs cleanup
        # self.comment("  Z step: %s" % self.stack_step_size)
        self.comment("Full backlash compensation: %d" %
                     self.backlash_compensate)
        self.comment("Output extension: %s" % self.img_ext)
        self.comment("tsettle: %0.2f" % self.tsettle)

        # imgr_mp = self.imager.wh()[0] * self.imager.wh()[1] / 1.e6
        # imagr_mp = self.x.view_pixels * self.y.view_pixels

        def pix_str(pixels):
            pixels = pixels / 1e6
            if pixels >= 1000:
                return "%0.1f GP" % (pixels / 1000, )
            else:
                return "%0.1f MP" % (pixels, )

        """
        Print separate if small pano forces adjusting bounds
        This is rare as panos are usually significantly larger than the image sensor

        Pano size (requested/actual):
           mm: 112.200 x,  75.306 y => 8449.3 mm2
           pix: 2725 x,  1829 y => 5.0 MP
        """
        complex_pano_size = (self.x.requested_delta_pixels(),
                             self.y.requested_delta_pixels()) != (
                                 self.x.actual_delta_pixels(),
                                 self.y.actual_delta_pixels())
        if complex_pano_size:
            self.comment("Pano requested size:")
        else:
            self.comment("Pano size (requested/actual):")
        self.comment(
            "  mm: %0.3f x,  %0.3f y => %0.1f mm2" %
            (self.x.requested_delta_mm(), self.y.requested_delta_mm(),
             self.x.requested_delta_mm() * self.y.requested_delta_mm()))
        self.comment(
            "  pix: %u x,  %u y => %s" %
            (self.x.requested_delta_pixels(), self.y.requested_delta_pixels(),
             pix_str(self.x.requested_delta_pixels() *
                     self.y.requested_delta_pixels())))
        if complex_pano_size:
            self.comment("  end: %u x,  %us" %
                         (self.x.requested_end, self.y.requested_end))

        if complex_pano_size:
            self.comment("Pano actual size:")
            self.comment("  mm: %0.3f x,  %0.3f y => %0.1f mm2" %
                         (self.x.actual_delta_mm(), self.y.actual_delta_mm(),
                          self.x.actual_delta_mm() * self.y.actual_delta_mm()))
            self.comment(
                "  pix: %u x,  %u y => %s" %
                (self.x.actual_delta_pixels(), self.y.actual_delta_pixels(),
                 pix_str(self.x.actual_delta_pixels() *
                         self.y.actual_delta_pixels())))
            self.comment("  end: %u x,  %us" %
                         (self.x.actual_end, self.y.actual_end))

        self.log("Backlash: %0.3f x, %0.3f y" %
                 (self.x.backlash, self.y.backlash))

        self.comment("Image size:")
        self.comment(
            "  mm: %0.3f x,  %0.3f y => %0.1f mm2" %
            (self.x.view_mm, self.y.view_mm, self.x.view_mm * self.y.view_mm))
        self.comment("  pix: %u x,  %u y => %0.1f MP" %
                     (self.x.view_pixels, self.y.view_pixels,
                      self.x.view_pixels * self.y.view_pixels / 1e6))
        self.comment("Derived:")
        self.comment('  Ideal pictures: %0.1f x, %0.1f y => %0.1f' %
                     (self.x.images_ideal(), self.y.images_ideal(),
                      self.x.images_ideal() * self.y.images_ideal()))
        self.comment('  Actual pictures: %u x, %u y => %u' %
                     (self.x.images_actual(), self.y.images_actual(),
                      self.x.images_actual() * self.y.images_actual()))
        self.comment('  Generated positions: %u' % self.pictures_to_take)
        self.comment('  step: %0.3f x, %0.3f y' %
                     (self.x.step(), self.y.step()))
        self.comment("Origin: %s" % self.origin)

    def run(self):
        self.check_running()
        self.start_time = time.time()
        self.max_move = {'x': 0, 'y': 0}
        self.log()
        self.log()
        self.log()
        self.print_run_header()
        self.comment()
        self.prepare_image_output()
        self.motion.begin()
        # Do initial backlash compensation
        self.backlash_init()

        self.last_row = None
        self.last_col = None
        self.cur_col = -1
        # columns
        for ((cur_x, cur_y), (self.cur_col, self.cur_row)) in self.gen_xycr():
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

            self.move_absolute_backlash({'x': cur_x, 'y': cur_y})
            self.take_pictures()

            self.last_row = self.cur_row
            self.last_col = self.cur_col

        # Return to end position
        end_at = self.pconfig.get("end_at", "start")
        if end_at == "start":
            retx = float(self.pconfig["contour"]['start']['x'])
            rety = float(self.pconfig["contour"]['start']['y'])
        elif end_at == "zero":
            retx = 0.0
            rety = 0.0
        else:
            raise Exception("Unknown end_at: %s" % end_at)
        self.move_absolute_backlash({'x': retx, 'y': rety})

        self.end_program()
        self.end_time = time.time()

        self.log()
        self.log()
        self.log()
        self.log('Pictures taken: %d / %d' %
                 (self.xy_imgs, self.pictures_to_take))
        self.log('Max x: %0.3f, y: %0.3f' %
                 (self.max_move['x'], self.max_move['y']))
        self.log('  G0 X%0.3f Y%0.3f' %
                 (self.max_move['x'], self.max_move['y']))
        if self.xy_imgs != self.pictures_to_take:
            if self.pconfig.get('exclude', []):
                self.log(
                    'Suppressing for exclusion: pictures taken mismatch (taken: %d, to take: %d)'
                    % (self.pictures_to_take, self.xy_imgs))
            else:
                raise Exception(
                    'pictures taken mismatch (taken: %d, to take: %d)' %
                    (self.pictures_to_take, self.xy_imgs))
        return self.write_meta()

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

        ret = self.meta_base
        # User scan parameters
        ret['pconfig'] = self.pconfig
        # Calculated scan parameters
        ret['planner'] = plannerj

        ret["images"] = OrderedDict()
        for (x, y), (c, r) in self.gen_xycr():
            k = "%uc_%ur" % (c, r)
            ret["images"][k] = {"x": x, "y": y, "c": c, "r": r}
        return ret

    def backlash_init(self):
        if self.x.backlash:
            self.motion.move_absolute({
                'x':
                self.axes['x'].start - self.x.backlash,
                'y':
                self.axes['y'].start - self.y.backlash
            })
        self.x.comp = 1
        self.y.comp = 1

    def move_absolute_backlash(self, move_to):
        '''Do an absolute move with backlash compensation'''
        if self.backlash_compensate:
            self.move_absolute(move_to)
            return

        def fmt_axis(c):
            if c in move_to:
                self.max_move[c] = max(self.max_move[c], move_to[c])
                return '%c: %0.3f' % (c, move_to[c])
            else:
                return '%c: none'

        self.comment('move_absolute_backlash: %s, %s' %
                     (fmt_axis('x'), fmt_axis('y')))
        """
        Simple model
        Assume starting at top col and moving down serpentine
        Need to correct if we are at a new row
        """
        axisc = 'x'
        axis = self.axes[axisc]
        axis.comp = 0
        if self.last_row != self.cur_row and self.axes['x'].backlash:
            blsh_mv = {}
            blsh_mv['y'] = move_to['y']
            # Starting at left
            if self.cur_col == 0:
                # Go far left
                blsh_mv[axisc] = move_to[axisc] - axis.backlash
                axis.comp = 1
            # Starting at right
            else:
                # Go far right
                blsh_mv[axisc] = move_to[axisc] + axis.backlash
                axis.comp = -1
            self.motion.move_absolute(blsh_mv)
        self.motion.move_absolute(move_to)


def microscope_to_planner(usj, objective=None, objectivei=None, contour=None):
    if objective is None:
        objective = usj["objectives"][objectivei]
    ret = {
        "imager": {
            "x_view": objective["x_view"],
        },
        "motion": {},
        # was scan.json
        "contour": contour,
    }

    v = usj["imager"].get("scalar")
    if v:
        ret["imager"]["scalar"] = float(v)

    # ret["motion"]["hal"] = usj["motion"].get("hal")

    v = usj["motion"].get("origin")
    if v:
        ret["motion"]["origin"] = v

    v = usj["motion"].get("backlash")
    if v:
        ret["motion"]["backlash"] = v

    # By definition anything in planner section is planner config
    # give more thought to precedence at some point
    for k, v in usj.get("planner", {}).items():
        ret[k] = v

    return ret
