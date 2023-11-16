import math
from collections import OrderedDict
import os
import time
from uscope.planner.plugin import PlannerPlugin, register_plugin
from PIL import Image
from uscope.imager.imager_util import get_scaled
from uscope.motion.hal import pos_str
from uscope.kinematics import Kinematics
from scipy import polyfit
from uscope.imager.autofocus import choose_best_image, Autofocus


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
        # After all scaling, cropping, etc applied
        view_pix,
        # start and actual_end absolute positions (in um)
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

        # Basic sanity check
        # mm_tol = 0.005 * 25.4
        # Rounding error where things are considered equivalent
        # ex: may round down scan size if 1.01 images are required
        self.mm_tol = self.view_mm / 100

    def meta(self):
        ret = {}
        ret['step_mm'] = self.step()
        ret['start_mm'] = self.start
        ret['end_mm'] = self.actual_end
        ret['view_pixels'] = self.view_pixels
        ret['view_mm'] = self.view_mm
        ret['delta_mm'] = self.actual_delta_mm()
        ret['delta_pixels'] = self.actual_delta_pixels()
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
        if ideal - min_images < 0.01:
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
            # Imager is referenced to center
            yield self.start + self.view_mm / 2 + i * step

    def rc_pos(self, rc):
        return self.start + self.view_mm / 2 + rc * self.step()


"""
Production point generators are really complicated due to backlash and other optimizations
Sample class showing the basics of a point generator plugin
"""


class SamplePointGenerator(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        self.rows = 2
        self.cols = 3
        self.x_step = 1.0
        self.y_step = 0.8

    def images_expected(self):
        return self.rows * self.cols

    def iterate(self, state):
        for row in range(self.rows):
            for col in range(self.cols):
                self.motion.move_absolute({
                    'x': col * self.x_step,
                    'y': row * self.y_step
                })

                modifiers = {
                    "filename_part": f"r{row}_c{col}",
                }
                replace_keys = {}
                yield modifiers, replace_keys


def log_scan_xy_begin(self):
    # A true useful metric of efficieny loss is how many extra pictures we had to take
    # Maybe overhead is a better way of reporting it
    ideal_n_pictures = self.x.images_ideal() * self.y.images_ideal()
    expected_n_pictures = self.x.images_actual() * self.y.images_actual()
    self.log(
        '  Ideally taking %g pictures (%g X %g) but actually taking %d (%d X %d), %0.1f%% efficient'
        % (ideal_n_pictures, self.x.images_ideal(), self.y.images_ideal(),
           expected_n_pictures, self.x.images_actual(), self.y.images_actual(),
           ideal_n_pictures / expected_n_pictures * 100.0), 2)

    for axisc, axis in self.axes.items():
        self.log('  Axis %s' % axisc)
        self.log('    %f to %f' % (axis.start, axis.actual_end), 2)
        self.log(
            '    Ideal overlap: %f, actual %g' %
            (self.pc.ideal_overlap(axisc), axis.actual_overlap()), 2)
        self.log('    full delta: %f' % (axis.requested_delta_mm()), 2)
        self.log('    view: %d pix' % (axis.view_pixels, ), 2)
        self.log('    border: %f' % self.pc.border())

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
    complex_pano_size = (
        self.x.requested_delta_pixels(), self.y.requested_delta_pixels()) != (
            self.x.actual_delta_pixels(), self.y.actual_delta_pixels())
    if complex_pano_size:
        self.log("  Pano requested size:")
    else:
        self.log("  Pano size (requested/actual):")
    self.log("    mm: %0.3f x,  %0.3f y => %0.1f mm2" %
             (self.x.requested_delta_mm(), self.y.requested_delta_mm(),
              self.x.requested_delta_mm() * self.y.requested_delta_mm()))
    self.log("    pix: %u x,  %u y => %s" %
             (self.x.requested_delta_pixels(), self.y.requested_delta_pixels(),
              pix_str(self.x.requested_delta_pixels() *
                      self.y.requested_delta_pixels())))
    if complex_pano_size:
        self.log("    end: %u x,  %us" %
                 (self.x.requested_end, self.y.requested_end))

    if complex_pano_size:
        self.log("  Pano actual size:")
        self.log("    mm: %0.3f x,  %0.3f y => %0.1f mm2" %
                 (self.x.actual_delta_mm(), self.y.actual_delta_mm(),
                  self.x.actual_delta_mm() * self.y.actual_delta_mm()))
        self.log("    pix: %u x,  %u y => %s" %
                 (self.x.actual_delta_pixels(), self.y.actual_delta_pixels(),
                  pix_str(self.x.actual_delta_pixels() *
                          self.y.actual_delta_pixels())))
        self.log("    end: %u x,  %us" %
                 (self.x.actual_end, self.y.actual_end))

    self.log("  Image size:")
    self.log("    mm: %0.3f x,  %0.3f y => %0.1f mm2" %
             (self.x.view_mm, self.y.view_mm, self.x.view_mm * self.y.view_mm))
    self.log("    pix: %u x,  %u y => %0.1f MP" %
             (self.x.view_pixels, self.y.view_pixels,
              self.x.view_pixels * self.y.view_pixels / 1e6))
    raw_wh = self.pc.image_raw_wh_hint()
    if raw_wh:
        self.log("      Step 1: raw sensor pixels: %uw x %uh" %
                 (raw_wh[0], raw_wh[1]))
    self.log("      Step 2: crop %s" % (self.pc.image_crop_tblr_hint(), ))
    image_scalar = self.pc.image_scalar_hint()
    if image_scalar:
        self.log("      Step 3: apply scalar %0.2f" % image_scalar)
    final_wh = self.pc.image_final_wh_hint()
    if final_wh:
        self.log("      Step 4: final sensor pixels: %uw x %uh" %
                 (final_wh[0], final_wh[1]))
        assert self.x.view_pixels == final_wh[
            0] and self.y.view_pixels == final_wh[1], (
                "sensor mismatch: expected, got",
                final_wh[0],
                final_wh[1],
                self.x.view_pixels,
                self.y.view_pixels,
            )
    self.log("  Derived:")
    self.log('    Ideal pictures: %0.1f x, %0.1f y => %0.1f' %
             (self.x.images_ideal(), self.y.images_ideal(),
              self.x.images_ideal() * self.y.images_ideal()))
    self.log('    Actual pictures: %u x, %u y => %u' %
             (self.x.images_actual(), self.y.images_actual(),
              self.x.images_actual() * self.y.images_actual()))
    self.log('    Generated positions: %u' % self.points_expected())
    self.log('    step: %0.3f x, %0.3f y' % (self.x.step(), self.y.step()))


class PointGenerator2P(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        start, end = self.init_contour()
        self.init_axes(start, end)
        # Total number of images_actual taken
        # self.all_imgs = 0
        # Number of images_actual taken at unique x, y coordinates
        # May be different than all_imags if image stacking
        self.itered_xy_points = 0

    def init_contour(self):
        contour = self.pc.j["points-xy2p"]["contour"]

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
                         log=self.log)),
            ('y',
             PlannerAxis('Y',
                         self.pc.ideal_overlap("y"),
                         image_wh_mm[1],
                         image_wh[1],
                         start[1],
                         end[1],
                         log=self.log)),
        ])
        self.x = self.axes['x']
        self.y = self.axes['y']
        self.cols = self.x.images_actual()
        self.rows = self.y.images_actual()

    def scan_begin(self, state):
        pass

    def filename_part(self, ul_col, ul_row):
        return 'c%03u_r%03u' % (ul_col, ul_row)

    def calc_pos(self, ll_col, ll_row):
        return {"x": self.x.rc_pos(ll_col), "y": self.y.rc_pos(ll_row)}

    def gen_pos_ll_ul_serp(self):
        for ll_row in range(self.rows):
            for ll_col in range(self.cols):
                if ll_row % 2 == 1:
                    ll_col = self.cols - 1 - ll_col

                pos = self.calc_pos(ll_col, ll_row)
                origin = self.pc.motion_origin()
                if origin == "ul":
                    ul_col = ll_col
                    ul_row = ll_row
                elif origin == "ll":
                    ul_col = ll_col
                    ul_row = self.rows - 1 - ll_row
                elif origin == "ur":
                    ul_col = self.cols - 1 - ll_col
                    ul_row = ll_row
                elif origin == "lr":
                    ul_col = self.cols - 1 - ll_col
                    ul_row = self.rows - 1 - ll_row
                else:
                    assert 0

                yield (pos, (ll_col, ll_row), (ul_col, ul_row))

    """
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
    """

    def gen_xys(self):
        for (x, y), _cr in self.gen_xycr():
            yield (x, y)

    def points_expected(self):
        return self.rows * self.cols

    def images_expected(self):
        return self.rows * self.cols

    def log_scan_begin(self):
        self.log("XY2P")
        # 2023-10-25: only ll origin is in use now
        # self.log("  Origin: %s" % self.origin)
        log_scan_xy_begin(self)
        # Try actually generating the points and see if it matches how many we thought we were going to get
        if self.pc.exclude():
            self.log("  ROI exclusions active")

    def iterate(self, state):
        # columns
        for (pos, _ll, (ul_col, ul_row)) in self.gen_pos_ll_ul_serp():
            self.itered_xy_points += 1
            self.log('')
            self.log(
                "XY2P: %u / %u @ c=%u, r=%u, %s" %
                (self.itered_xy_points, self.images_expected(), ul_col, ul_row,
                 self.microscope.usc.motion.format_positions(pos)))

            self.motion.move_absolute(pos)

            modifiers = {
                "filename_part": self.filename_part(ul_col, ul_row),
            }
            replace_keys = {
                "col": ul_col,
                "row": ul_row,
            }
            yield modifiers, replace_keys

    def scan_end(self, state):
        # Will be at the end of a stack
        # Put it back where it started
        pos = self.calc_pos(0, 0)
        self.log(f"XY2P: returning XY at scan end: %s" %
                 self.microscope.usc.motion.format_positions(pos))
        self.motion.move_absolute(pos)

    def log_scan_end(self):
        self.log('XY2P: generated points: %u / %u' %
                 (self.itered_xy_points, self.points_expected()))
        if self.itered_xy_points != self.points_expected():
            raise Exception(
                'pictures taken mismatch (taken: %d, to take: %d)' %
                (self.itered_xy_points, self.points_expected()))

    def gen_meta(self, meta):
        points = OrderedDict()
        for (pos, _ll, (ul_col, ul_row)) in self.gen_pos_ll_ul_serp():
            k = self.filename_part(ul_col, ul_row)
            v = dict(pos)
            v.update({"col": ul_col, "row": ul_row})
            points[k] = v
        axes = {}
        for axisc, axis in self.axes.items():
            axes[axisc] = axis.meta()
        meta["points-xy2p"] = {
            'points_to_generate': self.points_expected(),
            'points_generated': self.itered_xy_points,
            "points": points,
            "axes": axes,
        }


"""
PoC using much more restrictive parameters than PointGenerator2P:
-Only ll origin supported
-Will overscan if needed in lieu of shrinking canvas

TODO: consider leaving Z along if all three points are the same or is omitted entirely
"""


class PointGenerator3P(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        """
        x,y must be defined
        z is optional
        
        corners:
        -upper left
        -lower left (origin)
        -lower right
        """
        self.setup_bounds()
        self.setup_axes()
        self.calc_per_rc()
        self.itered_xy_points = 0
        assert self.pc.motion_origin() == "ll"

    def has_z(self, corners):
        ret = None
        for corner in corners.values():
            this = corner.get("z") is not None
            if ret is None:
                ret = this
            elif ret != this:
                raise ValueError("Inconsistent z keys")
        return ret

    def refocus_corners(self, corners):
        """
        Refocus corners before starting scan
        Intended for large batch jobs that may drift before the scan starts
        """
        af = Autofocus(self.microscope,
                       move_absolute=self.motion.move_absolute,
                       pos=self.planner.motion.pos,
                       imager=self.imager,
                       kinematics=self.kinematics,
                       log=self.log,
                       poll=self.planner.check_yield)

        for corner in ("ul", "ll", "lr"):
            self.planner.check_yield()
            self.motion.move_absolute(corners[corner])
            self.planner.check_yield()
            af.coarse()
            self.planner.check_yield()
            corners[corner] = self.planner.motion.pos()

    def setup_bounds(self):
        corners = self.pc.j["points-xy3p"]["corners"]
        if self.pc.j["points-xy3p"].get("refocus", False):
            self.refocus_corners(corners)
        assert len(corners) == 3
        pos0 = self.planner.motion.pos()
        self.corners = {}
        self.ax_min = {}
        self.ax_max = {}
        self.tracking_z = self.has_z(corners)
        self.log("Corners (tracking_z=%u)" % (self.tracking_z, ))
        for cornerk, corner in corners.items():
            corner = dict(corner)
            # Fill in a dummy consistent value to make matrix solve
            # It will be dropped during moves
            # Its also accurate in the telemetry
            if not self.tracking_z:
                corner["z"] = pos0["z"]
            self.log("  %s x=%0.3f, y=%0.3f, z=%0.3f" %
                     (cornerk, corner["x"], corner["y"], corner["z"]))
            self.corners[cornerk] = corner
            for ax in "xyz":
                self.ax_min[ax] = min(self.ax_min.get(ax, +float('inf')),
                                      corner[ax])
                self.ax_max[ax] = max(self.ax_max.get(ax, -float('inf')),
                                      corner[ax])

        self.log("Bounding box")
        for axis in "xyz":
            self.log("  %c: %0.3f to %0.3f" %
                     (axis, self.ax_min[axis], self.ax_max[axis]))

    def setup_axes(self):
        # FIXME: w/h should be sin/cos distances
        # Over estimated as is
        x_mm = self.pc.x_view()
        image_wh = self.planner.image_wh()
        mm_per_pix = x_mm / image_wh[0]
        image_wh_mm = (image_wh[0] * mm_per_pix, image_wh[1] * mm_per_pix)
        """
        Assuming angles are small this should be a good approximation
        Revisit later / as figure out something better
        The actual soution might involve some trig to get linear distances
        """
        # The actual coordinates aren't used directly
        # Think of it as a different coordinate space
        # Just get the magnitude correct so it can calculate effective rows/cols
        x_min = 0.0
        y_min = 0.0

        def xy_distance(p1, p2):
            return ((p1["x"] - p2["x"])**2 + (p1["y"] - p2["y"])**2)**0.5

        # absolute min/max isn't correct as we are tracking skew
        # however these should be accurate
        x_max = xy_distance(self.corners["ll"], self.corners["lr"])
        y_max = xy_distance(self.corners["ll"], self.corners["ul"])

        self.axes = OrderedDict([
            ('x',
             PlannerAxis('X',
                         self.pc.ideal_overlap("x"),
                         image_wh_mm[0],
                         image_wh[0],
                         x_min,
                         x_max,
                         log=self.log)),
            ('y',
             PlannerAxis('Y',
                         self.pc.ideal_overlap("y"),
                         image_wh_mm[1],
                         image_wh[1],
                         y_min,
                         y_max,
                         log=self.log)),
        ])
        self.x = self.axes['x']
        self.y = self.axes['y']
        self.cols = self.x.images_actual()
        self.rows = self.y.images_actual()

    def calc_per_rc(self):
        """
        Next: calculate linear solution
        Need to get dependence of xyz on row and column
        Do this by assuming self.corners[1] as origin and using linear parameters from above

        The three points form a plane
        However as we have two angles vs origin the linear solution is slightly overconstrained
        Calculate both and average them maybe
        Or maybe just take one for now
        """
        """
        dy = self.corners[2]["y"] - self.corners[1]["y"]
        dx = self.corners[2]["x"] - self.corners[1]["x"]
        # corner2 should be to the right
        assert dx > 0
        """

        # Compute one axis as a time
        # Dependence of x on col
        self.per_col = {}
        self.per_row = {}
        for axis in "xyz":

            def corner_trim(corner):
                """
                Need to be linear across movement so take out image size
                """
                ret = dict(self.corners[corner])
                if corner == "ul":
                    ret["y"] -= self.y.view_mm
                if corner == "lr":
                    ret["x"] -= self.x.view_mm
                return ret

            # Discard constants here and use corners[1] instead
            # At right => use to calculate col dependency
            if self.cols == 1:
                self.per_col[axis] = 0.0
            else:
                xs = (0, self.cols - 1)
                ys = (self.corners["ll"][axis], corner_trim("lr")[axis])
                self.log("per_col xs %s, ys %s" % (xs, ys))
                self.per_col[axis] = polyfit(xs, ys, 1)[0]
            # At top => use to calculate row dependency
            if self.rows == 1:
                self.per_row[axis] = 0.0
            else:
                xs = (0, self.rows - 1)
                ys = (self.corners["ll"][axis], corner_trim("ul")[axis])
                self.log("per_row xs %s, ys %s" % (xs, ys))
                self.per_row[axis] = polyfit(xs, ys, 1)[0]

    def points_expected(self):
        return self.rows * self.cols

    def images_expected(self):
        return self.rows * self.cols

    def calc_pos(self, ll_col, ll_row):
        ret = {}
        for axis in "xyz":
            # Project from corner
            # Adjust from center of imager
            offset = self.corners["ll"][axis]
            if axis == "x":
                offset += self.x.view_mm / 2
            elif axis == "y":
                offset += self.y.view_mm / 2
            ret[axis] = self.per_row[axis] * ll_row + self.per_col[
                axis] * ll_col + offset
        return ret

    def filename_part(self, ul_col, ul_row):
        return 'c%03u_r%03u' % (ul_col, ul_row)

    def gen_pos_ll_ul_serp(self):
        for ll_row in range(self.rows):
            for ll_col in range(self.cols):
                if ll_row % 2 == 1:
                    ll_col = self.cols - 1 - ll_col

                pos = self.calc_pos(ll_col, ll_row)
                ul_col = ll_col
                ul_row = self.rows - 1 - ll_row
                yield (pos, (ll_col, ll_row), (ul_col, ul_row))

    def move_absolute(self, pos):
        pos = dict(pos)
        if not self.tracking_z and "z" in pos:
            del pos["z"]
        # Really setting z. Does this interact with stacking?
        if "z" in pos:
            self.planner.z_center = pos["z"]
            # XXX: maybe also drop the z movement if stacking?
            # ideally we'd also aggregate the XY and first Z movements together
            if self.planner.stacking():
                del pos["z"]
        self.motion.move_absolute(pos)

    def iterate(self, state):
        for (pos, _ll, (ul_col, ul_row)) in self.gen_pos_ll_ul_serp():
            self.log('')
            self.itered_xy_points += 1
            if "z" in pos and not self.tracking_z:
                del pos["z"]
            self.log(
                "XY3P: %u / %u @ c=%u, r=%u, %s" %
                (self.itered_xy_points, self.images_expected(), ul_col, ul_row,
                 self.microscope.usc.motion.format_positions(pos)))
            self.move_absolute(pos)

            modifiers = {
                "filename_part": 'c%03u_r%03u' % (ul_col, ul_row),
            }
            replace_keys = {
                "col": ul_col,
                "row": ul_row,
            }
            yield modifiers, replace_keys

    def scan_end(self, state):
        # Return to start position
        pos = self.calc_pos(0, 0)
        if not self.tracking_z and "z" in pos:
            del pos["z"]
        self.log("XY3P: returning XYZ at scan end to %s" %
                 (self.microscope.usc.motion.format_positions(pos)))
        self.motion.move_absolute(pos)

    def log_scan_begin(self):
        self.log("XY3P")
        log_scan_xy_begin(self)

    def gen_meta(self, meta):
        points = OrderedDict()
        for (pos, _ll, (ul_col, ul_row)) in self.gen_pos_ll_ul_serp():
            k = self.filename_part(ul_col, ul_row)
            v = dict(pos)
            v.update({"col": ul_col, "row": ul_row})
            points[k] = v
        axes = {}
        for axisc, axis in self.axes.items():
            axes[axisc] = axis.meta()
        meta["points-xy2p"] = {
            'points_to_generate': self.points_expected(),
            'points_generated': self.itered_xy_points,
            "points": points,
            "axes": axes,
        }


"""
Focus around Z axis
"""


# TODO: make more generic so can be used in CLI, etc outside of Planner
class PlannerStacker(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        # Most users will want to stack on z
        config = self.planner.pc.j["points-stacker"]
        self.axis = config.get("axis", "z")
        # Must be done per iteration as other plugins may move z
        self.start = None
        # self.start_mode = config.get("start_mode", "center")
        self.mode = "center"
        self.total_number = int(config["number"])
        assert self.total_number >= 1
        # How far a stack should be
        # Will go self.start + self.distance
        # To stack down make distance negative
        # You probably want negative backlash compensation as well
        self.distance = float(config["distance"])
        assert self.distance > 0

        self.direction = self.get_direction()
        if self.total_number > 1:
            self.step = self.distance / (self.total_number -
                                         1) * self.direction
        else:
            self.step = 0.0

        self.first_start = None
        self.first_end = None
        self.first_reference = None
        # Used by drift plugin to correct thermal drift
        self.drift_offset = 0.0

    def log_scan_begin(self):
        self.imager.log_planner_header(self.log)
        self.log("Focus stacking from \"%s\"" % self.mode)
        self.log("  Images: %s" % self.total_number)
        self.log("  Distance: %0.3f" % self.distance)

    def images_expected(self):
        return self.total_number

    def get_direction(self):
        direction = -1
        if "backlash" in self.motion.modifiers:
            direction = self.motion.modifiers["backlash"].compensation.get(
                self.axis, -1)
        if direction == 0:
            direction = -1
        return direction

    def points(self):
        for image_number in range(self.total_number):
            z = self.start + self.drift_offset + image_number * self.step
            yield {self.axis: z}

    def filename_part(self, image_number):
        return "z%02d" % image_number

    def iterate(self, state):
        # Take the original center point as the reference for stacking
        # used on XY2P and XY3P w/o z tracking
        if self.planner.z_center is None:
            cur_pos = self.planner.motion.pos()
            if "z" in cur_pos:
                self.planner.z_center = cur_pos["z"]

        # Position where stacking is relative to
        # If None will be initialized at start of run
        # https://github.com/Labsmore/pyuscope/issues/180
        # self.reference = self.planner.motion.pos()[self.axis]
        assert self.axis == "z"
        self.reference = self.planner.z_center

        # From center
        self.start = self.reference - self.step * (self.total_number - 1) / 2
        self.end = self.start + (self.total_number - 1) * self.step

        for pointi, point in enumerate(self.points()):
            if pointi == 0:
                self.planner.log(
                    "stack %c @ reference %0.6f, start %0.6f, end %0.6f, step %0.6f, %u images, offset %s"
                    % (self.axis, self.reference, self.start, self.end,
                       self.step, self.total_number, self.drift_offset))
                self.first_reference = self.reference
                self.first_start = self.start
                self.first_end = self.end
            self.planner.log("stack: %u / %u @ %0.6f" %
                             (pointi + 1, self.total_number, point[self.axis]))

            self.planner.motion.move_absolute(point)
            modifiers = {
                "filename_part": self.filename_part(pointi),
            }
            replace_keys = {
                "stacki": pointi,
            }
            yield modifiers, replace_keys

    def scan_end(self, state):
        """
        Only restore if something of greater authority isn't present
        don't restore:
            XY3P w/ Z control (common case)
        restore when:
            XY2P
            XY3P w/o Z control
        """
        restore = True
        xy3p = self.planner.pipeline.get("points-xy3p")
        if xy3p and xy3p.tracking_z:
            restore = False
        if restore:
            self.log("Stacker: restoring %s = %0.3f" %
                     (self.axis, self.first_reference))
            self.motion.move_absolute({self.axis: self.first_reference})

    def gen_meta(self, meta):
        meta["points-stacker"] = {
            "per_stack": self.total_number,
            "distance": self.distance,
            "mode": self.mode,
            # Compromise on sometimes variable z
            # Add the starting point
            "first_reference": self.first_reference,
            "first_start": self.first_start,
            "first_end": self.first_end,
            "step": self.step,
            "direction": self.direction,
            # Usually z
            "axis": self.axis,
        }


"""
Track things like thermal drift and die curvature by monitoring focus stacks
Assumes small drift / serpentine pattern
"""


class StackerDrift(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        # Fairly computationally expensive
        # Just analyze stacks every once in a while
        self.frequency = 3
        self.freq_count = 0

    def scan_begin(self, state):
        self.stacker = self.planner.pipeline["points-stacker"]
        assert self.stacker.mode == "center"
        self.stack = []

    def process_stack(self):
        target_pos, fni = choose_best_image(self.stack)
        drift1 = target_pos - self.stacker.reference
        drift2 = target_pos - (self.stacker.reference +
                               self.stacker.drift_offset)
        self.log(
            "stacker drift: best %0.6f at %u / %u vs expected %0.6f => %0.6f abs delta, %0.6f rel delta"
            % (target_pos, fni + 1, len(
                self.stack), self.stacker.reference, drift1, drift2))
        # Don't allow jumping more than one step per image
        if drift2 == 0:
            delta = 0
        else:
            # If it drifts up we need to bring it down
            delta = -drift2 / abs(drift2) * min(self.stacker.step, drift2)
        self.stacker.drift_offset += delta
        self.log("stacker drift: delta %0.6f => %0.6f offset" %
                 (delta, self.stacker.drift_offset))
        # XXX: is there a reasonable bound we can put here?
        # 2023-10-12: now with inspection scope this is triggering
        #assert abs(self.stacker.drift_offset
        #           ) < 0.5, "Drift offset correction out of reasonable bounds"

    def iterate(self, state):
        if state["stacki"] == 0:
            self.stack = []
        im = state.get("image")
        self.stack.append((self.motion.pos()["z"], im))
        # Last image in stack?
        if state["stacki"] == self.stacker.total_number - 1:
            self.log("stacker drift: checking stack")
            if not self.dry:
                self.process_stack()

        modifiers = {}
        replace_keys = {}
        yield modifiers, replace_keys


class PlannerHDR(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        config = self.pc.j["imager"]["hdr"]
        self.properties_list = config["properties_list"]
        self.tsettle = config.get("tsettle", 0.0)
        self.begin_properties = None

    def scan_begin(self, state):
        self.begin_properties = self.imager.get_properties()

    def properties_used(self):
        ret = set()
        for properties in self.properties_list:
            ret.update(properties.keys())
        return ret

    def scan_end(self, state):
        # Only set the ones we touch to reduce the chance of collisions
        properties = {}
        for k in self.properties_used():
            properties[k] = self.begin_properties[k]
        self.log("HDR: restoring %u imager properties" % len(properties))
        if not self.dry:
            self.imager.set_properties(properties)

    def iterate(self, state):
        for hdri, hdrv in enumerate(self.properties_list):
            self.log("HDR: setting %s" % (hdrv, ))
            if not self.dry:
                self.imager.set_properties(hdrv)
                time.sleep(self.tsettle)
            modifiers = {
                "filename_part": "h%02u" % hdri,
            }
            replace_keys = {
                "image-properties": dict(hdrv),
                "hdri": hdri,
            }
            yield modifiers, replace_keys

    def images_expected(self):
        return max(1, len(self.properties_list))

    def gen_meta(self, meta):
        meta["image-hdr"] = {
            "properties_list": self.properties_list,
            "tsettle": self.tsettle,
        }


class PlannerImageStabilization(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        # Most users will want to stack on z
        config = self.planner.pc.j["image-stabilization"]
        self.n = config["n"]
        # n == 1 is legal although somewhat useless
        # Post processing requires self.n % 2 == 0
        # but this isn't a strict requirement
        assert self.n >= 1

    def log_scan_begin(self):
        self.log(f"Image stablization factor: {self.n}")

    def images_expected(self):
        return self.n

    def filename_part(self, image_number):
        return "is%02d" % image_number

    def iterate(self, state):
        for pointi in range(self.n):
            self.planner.log(f"image stabilization: {pointi + 1} / {self.n}")
            # TODO: figure out a reasonable time here
            # Needs to be > 0 to have some time for vibration to move
            if pointi and not self.dry:
                time.sleep(0.1)

            modifiers = {
                "filename_part": self.filename_part(pointi),
            }
            replace_keys = {
                "image_stabilization_i": pointi,
            }
            yield modifiers, replace_keys

    def gen_meta(self, meta):
        meta["image-stabilization"] = {
            "n": self.n,
        }


# FIXME: move kinematics to dedicated object
# Argus would like to use this without planner overhead
class PlannerKinematics(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        assert self.microscope
        self.kinematics = Kinematics(
            microscope=self.microscope,
            log=self.log,
        )
        self.kinematics.configure(
            tsettle_motion=self.pc.kinematics.tsettle_motion(),
            tsettle_hdr=self.pc.kinematics.tsettle_hdr(),
        )

    def log_scan_begin(self):
        self.log("tsettle_motion: %0.3f" % self.kinematics.tsettle_motion)
        self.log("tsettle_hdr: %0.3f" % self.kinematics.tsettle_hdr)

    def iterate(self, state):
        # wait for movement + flush image
        if not self.dry:
            tstart = time.time()
            self.kinematics.wait_imaging_ok()
            tend = time.time()
            self.verbose and self.log("FIXME TMP: net kinematics took %0.3f" %
                                      (tend - tstart, ))
        yield None


class PlannerCaptureImage(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        self.images_captured = 0

    def scan_begin(self, state):
        properties = self.pc.j["imager"].get("properties")
        if not properties:
            return
        self.log("Imager: setting %u properties" % (len(properties), ))
        self.imager.set_properties(properties)

    def scan_end(self, state):
        state["images_captured"] = self.images_captured

    def iterate(self, state):
        im = None
        assert state.get("image") is None, "Pipeline already took an image"
        # self.log("Capturing at %s" % pos_str(self.motion.pos()))
        if not self.planner.dry:
            if self.planner.imager.remote():
                self.planner.imager.take()
            else:
                tstart = time.time()
                im = self.planner.imager.get_processed()
                tend = time.time()
                self.verbose and self.log(
                    "FIXME TMP: actual capture took %0.3f" % (tend - tstart, ))

        final_wh_hint = self.pc.image_final_wh_hint()
        if im and final_wh_hint is not None:
            assert final_wh_hint[0] == im.size[0] and final_wh_hint[
                1] == im.size[
                    1], "Unexpected image size: expected %s, got %s" % (
                        final_wh_hint, im.size)

        self.images_captured += 1
        modifiers = {}
        replace_keys = {
            "image": im,
            "images_captured": self.images_captured,
        }
        yield modifiers, replace_keys

    def gen_meta(self, meta):
        meta["image-capture"] = {
            "captured": self.images_captured,
        }


"""
Just snap an image
Should be at the end of the pipeline
"""


class PlannerSaveImage(PlannerPlugin):
    def __init__(self, planner):
        super().__init__(planner=planner)
        self.images_saved = 0
        self.extension = self.pc.imager.save_extension()
        self.quality = self.pc.imager.save_quality()
        assert not self.planner.imager.remote()
        self.metadata = {}

    def log_scan_begin(self):
        self.log("Output dir: %s" % self.planner.out_dir)
        self.log("Output extension: %s" % self.extension)

    def iterate(self, state):
        im = state.get("image")
        if not self.planner.dry:
            assert im, "Asked to save image without image given"

        self.images_saved += 1
        img_prefix = self.planner.filanme_prefix(state)
        fn_full = img_prefix + self.extension
        if not self.planner.dry:
            # PIL object
            if self.extension == ".jpg" or self.extension == ".jpeg":
                im.save(fn_full, quality=self.quality)
            else:
                im.save(fn_full)
            meta = {
                "position": self.motion.pos(),
            }
            # FIXME: move this to modifiers so its more automatic per plugin
            if "col" in state:
                meta["col"] = state["col"]
                meta["row"] = state["row"]
            if "stacki" in state:
                meta["stacki"] = state["stacki"]
            if "image-properties" in state:
                meta["image-properties"] = state["image-properties"]
            if "hdri" in state:
                meta["hdri"] = state["hdri"]
            self.metadata[os.path.basename(fn_full)] = meta

        # yield {}, self.state_add_dict(state, "image", "filename_rel", fn_full)
        yield {}, {"image_filename_rel": fn_full}

    def gen_meta(self, meta):
        meta["image-save"] = {
            "extension": self.extension,
            "quality": self.quality,
            "saved": self.images_saved,
        }
        meta["files"] = self.metadata


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
        self.progress_cb(self.n_xy_cache, self.itered_xy_points,
                         image_file_name, first)
        """
        self.images_captured += len(state.get("images", []))
        for callback in self.progress_callbacks:
            callback(state)
        yield None
'''


def register_plugins():
    register_plugin("points-xy2p", PointGenerator2P)
    register_plugin("points-xy3p", PointGenerator3P)
    register_plugin("points-stacker", PlannerStacker)
    register_plugin("stacker-drift", StackerDrift)
    register_plugin("hdr", PlannerHDR)
    register_plugin("kinematics", PlannerKinematics)
    register_plugin("image-capture", PlannerCaptureImage)
    register_plugin("image-save", PlannerSaveImage)
    # register_plugin("image-gcode", PlannerGcodeImage)
    # register_plugin("scraper", PlannerScraper)
    register_plugin("image-stabilization", PlannerImageStabilization)


register_plugins()
