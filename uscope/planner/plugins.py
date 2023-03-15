import math
from collections import OrderedDict
import os
import time
from uscope.planner.plugin import PlannerPlugin, register_plugin
from PIL import Image
from uscope.imager.imager_util import get_scaled


def backlash_move_absolute(pos, backlash, direction):
    """
    return an absolute move to proceed pos

    pos: move to absolute position after this
    backlash: amount in each axis
    direction: which way to compensate
    """

    # TODO: only do these moves if they are significant
    bpos = {}
    for k in pos.keys():
        # z is not traditionally well defined, hack around
        ax_backlash = backlash.get(k, 0.0)
        bpos[k] = pos[k] - direction * ax_backlash
    return bpos


class PlannerAxis:
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
        # Proportion of each image that is shared to the next
        self.req_overlap = req_overlap

        self.start = start
        # May extend past if scan area is smaller than view
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
        '''
        Backlash compensation
        0: no compensation
        -1: compensated for decreasing
        1: compensated for increasing
        '''
        # self.backlash_compensation = 0

    def meta(self):
        ret = {}
        ret['backlash_mm'] = self.backlash
        # ret['backlash_compensation'] = self.backlash_compensation
        ret['step_mm'] = self.step()
        ret['start_mm'] = self.start
        ret['end_mm'] = self.actual_end
        ret['view_pixels'] = self.view_pixels
        ret['view_mm'] = self.view_mm
        ret['delta_mm'] = self.actual_delta_mm()
        ret['delta_pixels']: self.actual_delta_pixels()
        ret['pixels_per_mm'] = self.view_pixels / self.view_mm
        ret['overlap_fraction'] = self.actual_overlap()
        # source parameters that might have been modified
        ret["requested"] = {
            'end_mm': self.requested_end,
            'delta_mm': self.requested_delta_mm(),
            'delta_pixels': self.requested_delta_pixels(),
            'overlap_fraction': self.req_overlap,
        }
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
        ret = 1.0 + (self.requested_delta_mm() - self.view_mm) / (
            (1.0 - self.req_overlap) * self.view_mm)
        if ret < 0:
            raise Exception('bad number of idea images_actual %s' % ret)
        return ret

    def images_actual(self):
        '''How many images_actual should actually take after considering margins and rounding'''
        # IDEAL: 1.000000 => 2
        # lets deal with very small rounding errors
        # if within 0.1% of requested image size, take it
        ideal = self.images_ideal()
        min_images = int(ideal)
        if ideal - min_images < 0.0001:
            ret = min_images
        else:
            ret = int(math.ceil(ideal))
        # Always take at least one image
        ret = max(ret, 1)
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

    def actual_overlap(self):
        return 1.0 - self.step() / self.view_mm

    def points(self):
        step = self.step()
        for i in range(self.images_actual()):
            yield self.start + i * step


class PointGenerator2P(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        start, end = self.init_contour()
        self.init_axes(start, end)
        self.pictures_to_take = self.n_xy()
        # Total number of images_actual taken
        # self.all_imgs = 0
        # Number of images_actual taken at unique x, y coordinates
        # May be different than all_imags if image stacking
        self.xy_points = 0

    def init_contour(self):
        contour = self.pc.j["contour"]

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

        border = self.pc.border()
        start[0] -= border
        start[1] -= border
        end[0] += border
        end[1] += border

        return start, end

    def init_axes(self, start, end):
        # CNC convention is origin should be in lower left of sample
        # Increases up and to the right
        # pr0nscope has ul origin though
        self.origin = self.pc.motion_origin()

        x_mm = self.pc.x_view()
        image_wh = self.planner.image_wh()
        mm_per_pix = x_mm / image_wh[0]
        image_wh_mm = (image_wh[0] * mm_per_pix, image_wh[1] * mm_per_pix)

        self.axes = OrderedDict([
            ('x',
             PlannerAxis('X',
                         self.pc.ideal_overlap("x"),
                         image_wh_mm[0],
                         image_wh[0],
                         start[0],
                         end[0],
                         backlash=self.planner.backlash["x"],
                         log=self.log)),
            ('y',
             PlannerAxis('Y',
                         self.pc.ideal_overlap("y"),
                         image_wh_mm[1],
                         image_wh[1],
                         start[1],
                         end[1],
                         backlash=self.planner.backlash["y"],
                         log=self.log)),
        ])
        self.x = self.axes['x']
        self.y = self.axes['y']

    def backlash_init(self):
        if self.x.backlash or self.y.backlash:
            self.motion.move_absolute({
                'x':
                self.axes['x'].start - self.x.backlash,
                'y':
                self.axes['y'].start - self.y.backlash
            })
        self.x.comp = 1
        self.y.comp = 1

    """
    Motion controller workarounds
    Eventually want to integrate better backlash model into motion directly
    """

    def move_absolute(self, pos):
        if self.planner.backlash_compensation:
            bpos = backlash_move_absolute(
                pos,
                backlash=self.planner.backlash,
                direction=self.planner.backlash_compensation)
            self.motion.move_absolute(bpos)
        self.motion.move_absolute(pos)

    def scan_begin(self, state):
        self.backlash_init()

    def filename_part(self):
        """
        Return filename basename excluding extension
        ex: c001_r004
        """

        # XXX: quick hack, look into something more proper
        origin = self.pc.motion_origin()
        if origin == "ll":
            return 'c%03u_r%03u' % (self.cur_col,
                                    self.y.images_actual() - self.cur_row - 1)
        elif origin == "ul":
            return 'c%03u_r%03u' % (self.cur_col, self.cur_row)
        else:
            assert 0, self.origin

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

    def validate_point(self, p):
        (cur_x, cur_y), (cur_col, cur_row) = p

        # Basic sanity check
        # mm_tol = 0.005 * 25.4
        mm_tol = self.y.view_mm / 100
        xmax = cur_x + self.x.view_mm
        ymax = cur_y + self.y.view_mm

        fail = False

        if cur_col < 0 or cur_col >= self.x.images_actual():
            self.log('Col out of range 0 <= %d < %d' %
                     (cur_col, self.x.images_actual()))
            fail = True
        if cur_x < self.x.start - mm_tol or xmax > self.x.actual_end + mm_tol:
            self.log('X out of range')
            fail = True

        if cur_row < 0 or cur_row >= self.y.images_actual():
            self.log('Row out of range 0 <= %d < %d' %
                     (cur_row, self.y.images_actual()))
            fail = True
        if cur_y < self.y.start - mm_tol or ymax > self.y.actual_end + mm_tol:
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
        for exclusion in self.pc.exclude():
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
        return len(list(self.gen_xys()))

    def gen_xys(self):
        for (x, y), _cr in self.gen_xycr():
            yield (x, y)

    def init_images(self):
        for axisc, axis in self.axes.items():
            self.log('Axis %s' % axisc)
            self.log('  %f to %f' % (axis.start, axis.actual_end), 2)
            self.log(
                '  Ideal overlap: %f, actual %g' %
                (self.pc.ideal_overlap(axisc), axis.actual_overlap()), 2)
            self.log('  full delta: %f' % (axis.requested_delta_mm()), 2)
            self.log('  view: %d pix' % (axis.view_pixels, ), 2)
            self.log('  border: %f' % self.pc.border())

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
        if self.pc.exclude():
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

    def images_expected(self):
        return self.pictures_to_take

    def print_run_header(self):
        # FIXME
        return

        self.init_images()

        self.imager.log_planner_header(self.log)
        # the math seems off here. Disabled for now / needs cleanup
        # self.comment("  Z step: %s" % self.stack_step_size)
        self.comment("Full backlash compensation: %d" %
                     self.planner.backlash_compensation)

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

    def move_absolute_backlash(self, move_to):
        '''Do an absolute move with backlash compensation'''
        if self.planner.backlash_compensation:
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

    def iterate(self, state):
        self.max_move = {'x': 0, 'y': 0}
        self.last_row = None
        self.last_col = None
        self.cur_col = -1
        # columns
        for ((cur_x, cur_y), (self.cur_col, self.cur_row)) in self.gen_xycr():
            self.xy_points += 1
            """
            self.log('')
            self.log('Pictures taken: %d / %d' %
                     (self.xy_points, self.pictures_to_take))

            #self.log('', 3)
            self.comment(
                'comp (%d, %d), pos (%f, %f)' %
                (self.x.comp, self.y.comp, cur_x, cur_y), 3)
            """

            self.move_absolute_backlash({'x': cur_x, 'y': cur_y})

            modifiers = {
                "filename_part": self.filename_part(),
            }
            replace_keys = {}
            yield modifiers, replace_keys

            self.last_row = self.cur_row
            self.last_col = self.cur_col

    def scan_end(self, state):
        # Return to end position
        end_at = self.pc.end_at()
        if end_at == "start":
            retx = float(self.pc.contour()['start']['x'])
            rety = float(self.pc.contour()['start']['y'])
        elif end_at == "zero":
            retx = 0.0
            rety = 0.0
        else:
            raise Exception("Unknown end_at: %s" % end_at)
        ret_pos = {'x': retx, 'y': rety}
        # Will be at the end of a stack
        # Put it back where it started
        self.move_absolute_backlash(ret_pos)
        """
        self.log()
        self.log()
        self.log()
        self.log('Pictures taken: %d / %d' %
                 (self.xy_points, self.pictures_to_take))
        self.log('Max x: %0.3f, y: %0.3f' %
                 (self.max_move['x'], self.max_move['y']))
        self.log('  G0 X%0.3f Y%0.3f' %
                 (self.max_move['x'], self.max_move['y']))
        if self.xy_points != self.pictures_to_take:
            if self.pc.j.get('exclude', []):
                self.log(
                    'Suppressing for exclusion: pictures taken mismatch (taken: %d, to take: %d)'
                    % (self.xy_points, self.pictures_to_take))
            else:
                raise Exception(
                    'pictures taken mismatch (taken: %d, to take: %d)' %
                    (self.xy_points, self.pictures_to_take))
        """

    def gen_meta(self, meta):
        images = meta["planner"].setdefault("images", OrderedDict())
        for (x, y), (c, r) in self.gen_xycr():
            k = "%uc_%ur" % (c, r)
            images[k] = {"x": x, "y": y, "col": c, "row": r}
        # meta["planner"]['pictures_to_take'] = self.pictures_to_take
        # meta["planner"]['pictures_taken'] = self.xy_points


"""
Focus around Z axis
"""


# TODO: make more generic so can be used in CLI, etc outside of Planner
class PlannerStacker(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        # Most users will want to stack on z
        config = self.planner.pc.pconfig["stack"]
        self.axis = config.get("axis", "z")
        # Position where stacking is relative to
        # If None will be initialized at start of run
        if "start" in config:
            self.start = float(config["start"])
        else:
            self.start = None
        # If don't have a start position take the beginning position
        self.pos0 = self.planner.motion.pos()[self.axis]
        if self.start is None:
            self.start = self.pos0
        """
        start: relative from absolute position
            ie start + distance
        center: center distance on start
            ie start +/- (distance / 2)
        """
        self.start_mode = config.get("start_mode", "center")
        self.images_per_stack = int(config["number"])
        # How far a stack should be
        # Will go self.start + self.distance
        # To stack down make distance negative
        # You probably want negative backlash compensation as well
        self.distance = float(config["distance"])

    def scan_begin(self, state):
        # No backlash compensation needed here since we need to do it every stack
        pass

    def print_run_header(self):
        self.imager.log_planner_header(self.log)
        self.comment("Focus stacking from \"%s\"" % self.stacker.start_mode)
        self.comment("  Images: %s" % self.stacker.images_per_stack)
        self.comment("  Distance: %0.3f" % self.stacker.distance)

    def images_expected(self):
        return self.images_per_stack

    """
    Motion controller workarounds
    Eventually want to integrate better backlash model into motion directly
    """

    def move_absolute(self, pos):
        if self.planner.backlash_compensation:
            bpos = backlash_move_absolute(
                pos,
                backlash=self.planner.backlash,
                direction=self.planner.backlash_compensation)
            self.motion.move_absolute(bpos)
        self.motion.move_absolute(pos)

    def iterate(self, state):
        if self.start_mode == "center":
            # if self.images_per_stack % 2 != 1:
            #    raise Exception('Center stacking requires odd n')

            # Step in the same distance as backlash compensation
            # Start at bottom and move down
            if self.planner.backlash_compensation > 0:
                start = self.start - self.distance / 2
            # Move to the top of the stack and move down
            else:
                start = self.start + self.distance / 2
        elif self.start_mode == "start":
            # Move to the top of the stack
            start = self.start
        else:
            assert 0, "bad stack mode"

        # Step in the same distance as backlash compensation
        # Default down
        direction = -1
        if self.planner.backlash_compensation:
            direction = self.planner.backlash_compensation
        step_distance = self.distance / (self.images_per_stack - 1) * direction

        self.move_absolute({self.axis: start})
        # self.planner.log("stack: distance %0.3f" % (self.distance, ))
        # self.planner.log("stack: start %0.3f => %0.3f" % (self.start, start))
        for image_number in range(self.images_per_stack):
            # self.planner.move_absolute(
            # bypass backlash compensation to keep movement smooth
            z = start + image_number * step_distance
            self.planner.log("stack: %u / %u @ %0.3f" %
                             (image_number, self.images_per_stack, z))
            self.planner.motion.move_absolute({self.axis: z})

            modifiers = {
                "filename_part": "z%02d" % image_number,
            }
            replace_keys = {}
            yield modifiers, replace_keys


class PlannerHDR(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        self.config = self.pc.j["imager"]["hdr"]

    def iterate(self, state):
        for hdri, hdrv in enumerate(self.config["properties"]):
            # print("hdr: set %u %s" % (hdri, hdrv))
            assert 0, "FIXME: needs review"
            # self.emitter.change_properties.emit(hdrv)
            self.imager.set_properties(hdrv)
            # Wait for setting to take effect
            time.sleep(self.config["tsettle"])
            modifiers = {
                "filename_part": "h%02u" % hdri,
            }
            replace_keys = {}
            yield modifiers, replace_keys


class PlannerKinematics(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        self.tsettle = self.pc.tsettle()

    def print_run_header(self):
        self.comment("tsettle: %0.2f" % self.tsettle)

    def iterate(self, state):
        # FIXME: refine
        if not self.dry:
            time.sleep(self.tsettle)
            self.planner.motion.settle()
        self.planner.wait_imaging_ok()
        yield None


class PlannerCaptureImage(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        self.images_captured = 0

    def scan_end(self, state):
        state["images_captured"] = self.images_captured

    def iterate(self, state):
        im = None
        if not self.planner.dry:
            if self.planner.imager.remote():
                self.planner.imager_take.take()
            else:
                images = self.planner.imager.get()
                assert len(images) == 1, "Expecting single image"
                im = list(images.values())[0]

                # factor = self.imager.scalar()
                factor = self.pc.image_scalar()
                im = get_scaled(im, factor, Image.ANTIALIAS)

        self.images_captured += 1
        modifiers = {}
        replace_keys = {
            "image": im,
            "images_captured": self.images_captured,
        }
        yield modifiers, replace_keys


"""
Just snap an image
Should be at the end of the pipeline
"""


class PlannerSaveImage(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        self.images_saved = 0
        # FIXME: this should come from config, not imager
        self.save_extension = self.pc.imager.save_extension()
        self.save_quality = self.pc.imager.save_quality()
        assert not self.planner.imager.remote()

    def print_run_header(self):
        self.comment("Output dir: %s" % self.planner.out_dir)
        self.comment("Output extension: %s" % self.save_extension)

    def iterate(self, state):
        im = state.get("image")
        if not self.planner.dry:
            assert im, "Asked to save image without image given"

        self.images_saved += 1
        img_prefix = self.planner.filanme_prefix(state)
        fn_full = img_prefix + self.save_extension
        if not self.planner.dry:
            # PIL object
            if self.save_extension == ".jpg" or self.save_extension == ".jpeg":
                im.save(fn_full, quality=self.save_quality)
            else:
                im.save(fn_full)
        # yield {}, self.state_add_dict(state, "image", "filename_rel", fn_full)
        yield {}, {"image_filename_rel": fn_full}


"""
Put at the end of the pipeline to generate things like number of pictures taken
Also allows generic data scraping
Should rename?
"""
'''
class PlannerScraper(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        self.images_captured = 0
        self.progress_callbacks = []

    def register_progress_callback(self, callback):
        self.progress_callbacks.append(callback)

    def iterate(self, state):
        """
        self.progress_cb(self.pictures_to_take, self.xy_points,
                         image_file_name, first)
        """
        self.images_captured += len(state.get("images", []))
        for callback in self.progress_callbacks:
            callback(state)
        yield None
'''


def register_plugins():
    register_plugin("points2p", PointGenerator2P)
    register_plugin("stacker", PlannerStacker)
    # FIXME: needs review / testing
    # register_plugin("hdr", PlannerHDR)
    register_plugin("kinematics", PlannerKinematics)
    register_plugin("capture_image", PlannerCaptureImage)
    register_plugin("save_image", PlannerSaveImage)
    # register_plugin("scraper", PlannerScraper)


register_plugins()
