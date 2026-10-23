from uscope import cloud_stitch
from uscope import fiji_stitch
from uscope.scan_util import index_scan_images, bucket_group, is_tif_scan
from uscope import config
from uscope.imagep.util import TaskBarrier, EtherealImageR, remove_intermediate_directories, find_qr_code_match, check_valid_image_dir
from uscope.imagep.summary import write_html_viewer, write_snapshot_grid, write_quick_pano
from uscope.util import writej, readj
from uscope.cal.analysis.color import apply_gains, effective_gains
import glob
import shutil
import os
from PIL import Image
"""
Support the following:
-Planner running
-GUI stand alone operations (ex: a single focus stack)
-From filesystem?
"""


def get_image_suffix(dir_in):
    if glob.glob(dir_in + "/*.tif"):
        return ".tif"
    else:
        return ".jpg"


# 28b21ca: planner started capturing "processed" images (FF corrected)
# Older scans are raw
PROCESSED_CAPTURE_DATE = "2023-11-16"


def scan_capture_corrections(directory):
    """
    Return list of corrections (ex: "correct-ff1") already applied to scan
    images at capture time or None if unknown (ex: not a pyuscope scan)
    """
    fn = os.path.join(directory, "uscan.json")
    if not os.path.exists(fn):
        return None
    scanj = readj(fn)
    capturej = scanj.get("image-capture", {})
    if "corrections" in capturej:
        return list(capturej["corrections"])
    # Legacy scan: assume processed capture applied FF if calibrated
    get_mode = scanj.get("pconfig", {}).get("imager",
                                            {}).get("get_mode", "processed")
    if get_mode == "raw":
        return []
    if scanj.get("time_end", "") < PROCESSED_CAPTURE_DATE:
        return []
    return ["correct-ff1"]


class ImageStream:
    def __init__(self):
        pass


# FIXME: how to integrate this?
# might require binding to a plugin and adding a queue
class PlannerImageStream(ImageStream):
    def __init__(self, pconfig, directory, planner_image_queue_plugin):
        self.pconfig = pconfig
        self.directory = directory
        self.planner_image_queue_plugin = planner_image_queue_plugin

    def working_dir(self):
        return self.directory

    def new_images(self):
        """
        Return an iterable of EtherealImageR
        """
        fns = self.planner_image_queue_plugin.new_images()
        return [EtherealImageR(fn=fn) for fn in fns]

    def has_stack(self):
        return "points-stacker" in self.pconfig

    def has_hdr(self):
        return "hdr" in self.pconfig["imager"]

    def bucket_size(self, operation):
        """
        Ex:
        images: c000_r002_z00.jpg, c000_r002_z01.jpg, c000_r002_z02.jpg
        operation: stack
        Check the scan config to see if stacks have 3 elements
        """
        if operation == "stack":
            return self.pconfig["points-stacker"]["number"]
        elif operation == "hdr":
            return len(self.pconfig["imager"]["hdr"]["properties_list"])
        else:
            assert 0, "FIXME"


"""
Older image processor
Simple and hard coded pipeline
Does a barrier between each step
Can tolerate partial captures (ex: bad stacking)
"""


class IPPConfigJ:
    def __init__(self, j=None):
        self.j = j

    def snapshot_correction(self):
        # Usually snapshots are already corrected during normal imaging
        return bool(self.j.get("snapshot_correction", False))

    def ff_correction(self):
        """
        None (default): only if not already applied when capturing
        True / False: force on / off
        """
        ret = self.j.get("ff_correction", None)
        if ret is None:
            return None
        return bool(ret)

    def cloud_stitch(self):
        return bool(self.j.get("cloud_stitch", True))

    def write_html_viewer(self):
        """
        Write a simple .html file at the final image level
        Its not so much stitched as plastered together
        """
        # Very little disk space, easy to distinguish from other image files
        # Turn on by default
        return bool(self.j.get("write_html_viewer", True))

    def write_snapshot_grid(self):
        """
        Write a simple combined image file at the final image level
        There is a gutten between snapshots
        """
        return bool(self.j.get("write_snapshot_grid", False))

    def write_quick_pano(self):
        """
        Write a simple combined image file at the final image level
        Its not so much stitched as plastered together based on estimated positions
        """
        # This takes up disk space => off by default
        return bool(self.j.get("write_quick_pano", False))

    def fiji_stitch(self):
        """
        Stitch locally with Fiji into <scan>/fiji/stitched.tif
        Slow and memory hungry on large scans => off by default
        """
        return bool(self.j.get("fiji_stitch", False))

    def keep_intermediates(self):
        # https://github.com/Labsmore/pyuscope/issues/410
        # Keep GUI default but more conservative here
        # In part due to no lock file which caused GUI / CLI contention
        return bool(self.j.get("keep_intermediates", True))


class DirCSIP:
    def __init__(self,
                 csip,
                 directory,
                 cs_info=None,
                 upload=True,
                 lazy=True,
                 fix=False,
                 best_effort=True,
                 ewf=None,
                 configj={},
                 microscope=None,
                 verbose=True):
        self.csip = csip
        self.microscope = microscope
        self.log = csip.log
        self.directory = directory
        self.cs_info = cs_info
        self.upload = upload
        self.fix = fix
        self.lazy = lazy
        # FIXME:
        # self.ewf = ewf
        self.best_effort = best_effort
        self.ipp_config = IPPConfigJ(configj)
        self.verbose = verbose

    def run_n_to_1(self,
                   task_name,
                   bucket_name,
                   iindex_in,
                   dir_out,
                   lazy=True):
        if not os.path.exists(dir_out):
            os.mkdir(dir_out)
        image_suffix = get_image_suffix(iindex_in["dir"])
        buckets = bucket_group(iindex_in, bucket_name)

        tb = TaskBarrier()
        # Must be in exposure order?
        for fn_prefix, hdrs in sorted(buckets.items()):
            fns = [
                os.path.join(iindex_in["dir"], fn)
                for _i, fn in sorted(hdrs.items())
            ]
            fn_out = os.path.join(dir_out, fn_prefix + image_suffix)
            if lazy and os.path.exists(fn_out):
                self.log(f"lazy: skip {fn_out}")
            else:
                self.log("%s %s" % (fn_prefix, fn_out))
                self.log("  %s" % (hdrs.items(), ))
                self.log("Queing task")
                self.csip.queue_n_to_1_plugin(task_name=task_name,
                                              fns_in=fns,
                                              fn_out=fn_out,
                                              tb=tb)
        tb.wait()

    # FIXME: unify this + run_1_to_1
    def correct_plugin_run(self, plugin_config, iindex_in, dir_out, lazy=True):
        # TODO: some options as well?
        plugin = plugin_config["plugin"]
        if not os.path.exists(dir_out):
            os.mkdir(dir_out)
        tb = TaskBarrier()
        for fn_in in iindex_in["images"].keys():
            fn_out = os.path.join(dir_out, os.path.basename(fn_in))
            if lazy and os.path.exists(fn_out):
                self.log(f"lazy: skip {fn_out}")
            else:
                self.csip.queue_1_to_1_plugin(plugin=plugin,
                                              fn_in=os.path.join(
                                                  iindex_in["dir"], fn_in),
                                              fn_out=fn_out,
                                              tb=tb)
        # print("TB: wait w/ alloc %s vs completed %s" % (tb.ntasks_allocated, tb.ntasks_completed))
        tb.wait()

    def run_1_to_1(self, task_name, iindex_in, dir_out, lazy=True):
        if not os.path.exists(dir_out):
            os.mkdir(dir_out)
        tb = TaskBarrier()
        for fn_in in iindex_in["images"].keys():
            fn_out = os.path.join(dir_out, os.path.basename(fn_in))
            if lazy and os.path.exists(fn_out):
                self.log(f"lazy: skip {fn_out}")
            else:
                self.csip.queue_1_to_1_plugin(plugin=task_name,
                                              fn_in=os.path.join(
                                                  iindex_in["dir"], fn_in),
                                              fn_out=fn_out,
                                              tb=tb)
        # print("TB: wait w/ alloc %s vs completed %s" % (tb.ntasks_allocated, tb.ntasks_completed))
        tb.wait()

    def hdr_run(self, **kwargs):
        self.run_n_to_1(task_name="hdr-luminance", bucket_name="hdr", **kwargs)

    def stack_run(self, **kwargs):
        self.run_n_to_1(task_name="stack-enfuse",
                        bucket_name="stack",
                        **kwargs)

    def stabilization_run(self, **kwargs):
        self.run_n_to_1(task_name="stabilization",
                        bucket_name="stabilization",
                        **kwargs)

    def correct_sharp1_run(self, **kwargs):
        self.run_1_to_1(task_name="correct-sharp1", **kwargs)

    def correct_ff1_run(self, **kwargs):
        self.run_1_to_1(task_name="correct-ff1", **kwargs)

    def run(self):
        """
        Process a completed scan into processed images
        Spins off processing to workers where possible
        """

        self.log("Reading metadata...")
        working_iindex = index_scan_images(self.directory)
        capture_corrections = scan_capture_corrections(self.directory)

        self.log("Microscope: %s" % (self.microscope.name, ))
        self.log("Serial: %s" % (self.microscope.serial(), ))
        self.log("Has FF cal: %s" % config.get_usc().imager.has_ff_cal())
        self.log("Capture corrections: %s" % (capture_corrections, ))
        self.log("Options")
        self.log("  Keep intermediates: %s" %
                 self.ipp_config.keep_intermediates())
        self.log("  Write HTML viewer: %s" %
                 self.ipp_config.write_html_viewer())
        self.log("  Write snapshot grid: %s" %
                 self.ipp_config.write_snapshot_grid())
        self.log("  Write quick pano: %s" % self.ipp_config.write_quick_pano())
        self.log("  Snapshot correction: %s" %
                 self.ipp_config.snapshot_correction())
        self.log("  Cloud stitch: %s" % self.ipp_config.cloud_stitch())
        self.log("  Fiji stitch: %s" % self.ipp_config.fiji_stitch())

        self.log("")

        ipp = config.get_usc().ipp.pipeline_first()
        if len(ipp) == 0:
            self.log("Pre corrections: skip")
        else:
            for pipeline_this in ipp:
                plugin = pipeline_this["plugin"]
                this_dir = pipeline_this["dir"]
                self.log(f"{plugin}: start")
                next_dir = os.path.join(working_iindex["dir"], this_dir)
                self.correct_plugin_run(pipeline_this,
                                        iindex_in=working_iindex,
                                        dir_out=next_dir,
                                        lazy=self.lazy)
                working_iindex = index_scan_images(next_dir)

        if working_iindex["stabilization"]:
            self.log("Stabilization: yes. Processing")
            # dir name needs to be reasonable for CloudStitch to name it well
            next_dir = os.path.join(working_iindex["dir"], "stabilization")
            self.stabilization_run(iindex_in=working_iindex,
                                   dir_out=next_dir,
                                   lazy=self.lazy)
            working_iindex = index_scan_images(next_dir)

        if working_iindex["hdrs"]:
            self.log("HDR: yes. Processing")
            # dir name needs to be reasonable for CloudStitch to name it well
            next_dir = os.path.join(working_iindex["dir"], "hdr")
            self.hdr_run(iindex_in=working_iindex,
                         dir_out=next_dir,
                         lazy=self.lazy)
            working_iindex = index_scan_images(next_dir)

        self.log("")

        if working_iindex["stacks"]:
            self.log("Stacker: yes. Processing")
            # dir name needs to be reasonable for CloudStitch to name it well
            next_dir = os.path.join(working_iindex["dir"], "stack")
            # maybe? helps some use cases
            self.stack_run(iindex_in=working_iindex,
                           dir_out=next_dir,
                           lazy=self.lazy)
            working_iindex = index_scan_images(next_dir)
        """
        Now apply custom correction plugins
        TODO: let the user actually determine order for these...ff1 before stack, etc
        """
        if self.ipp_config.snapshot_correction():
            ipp = config.get_usc().ipp.snapshot_correction()
            if len(ipp) == 0:
                self.verbose and self.log("Post corrections: skip")
            else:
                for pipeline_this in ipp:
                    plugin = pipeline_this["plugin"]
                    this_dir = pipeline_this["dir"]
                    if plugin in (capture_corrections or []):
                        self.verbose and self.log(
                            f"{plugin}: skip (applied at capture)")
                        continue
                    self.verbose and self.log(f"{plugin}: start")
                    next_dir = os.path.join(working_iindex["dir"], this_dir)
                    self.correct_plugin_run(pipeline_this,
                                            iindex_in=working_iindex,
                                            dir_out=next_dir,
                                            lazy=self.lazy)
                    working_iindex = index_scan_images(next_dir)

        ff_correction = self.ipp_config.ff_correction()
        if ff_correction is False:
            self.verbose and self.log("FF correction: skip (requested)")
        elif not config.get_usc().imager.has_ff_cal():
            self.verbose and self.log("FF correction: skip")
        elif ff_correction is None and "correct-ff1" in (capture_corrections
                                                         or []):
            self.verbose and self.log(
                "FF correction: skip (applied at capture)")
        else:
            self.verbose and self.log("FF correction: start")
            next_dir = os.path.join(working_iindex["dir"], "ff1")
            self.correct_ff1_run(iindex_in=working_iindex,
                                 dir_out=next_dir,
                                 lazy=self.lazy)
            working_iindex = index_scan_images(next_dir)

        self.verbose and self.log("")
        healthy = self.csip.inspect_final_dir(working_iindex)
        self.verbose and self.log("")

        if not healthy and self.best_effort:
            if not self.fix:
                raise Exception(
                    "Need to fix data to continue, but --fix not specified")
            self.log("WARNING: data is incomplete but trying to patch")
            next_dir = os.path.join(working_iindex["dir"], "fix")
            self.csip.fix_dir(working_iindex, next_dir)
            working_iindex = index_scan_images(next_dir)
            self.verbose and self.log("")
            self.verbose and self.log("re-inspecting new dir")
            healthy = self.csip.inspect_final_dir(working_iindex)
            assert healthy
            self.log("")

        qr_regex = config.bc.qr_regex()
        if qr_regex:
            for fn in working_iindex["images"]:
                fn_full = os.path.join(working_iindex["dir"], fn)
                with Image.open(fn_full) as image:
                    qr_match = find_qr_code_match(image, qr_regex)
                if qr_match:
                    # Rename the scan directory, not the nested
                    # processed directory we happen to be in
                    working_rel = os.path.relpath(working_iindex["dir"],
                                                  self.directory)
                    new_directory = self.directory + "_" + qr_match
                    self.log(
                        f"QR match {qr_match}: renaming {self.directory} => {new_directory}"
                    )
                    os.rename(self.directory, new_directory)
                    self.directory = new_directory
                    working_iindex = index_scan_images(
                        os.path.normpath(
                            os.path.join(new_directory, working_rel)))
                    break
        # Name of the output stitch job
        # After any QR rename so the job gets the meaningful name
        dst_basename = os.path.basename(os.path.abspath(self.directory))

        # https://github.com/Labsmore/pyuscope/issues/416
        # In the future we might make this more error resistant instead of skipping it
        if healthy:
            if self.ipp_config.write_html_viewer():
                self.verbose and self.log("Writing HTML viewer")
                if is_tif_scan(working_iindex["dir"]):
                    # Only Safari supports .tif
                    self.log(
                        "WARNING: HTML viewer only works reliably with jpg")
                write_html_viewer(working_iindex)

            if self.ipp_config.write_snapshot_grid():
                self.verbose and self.log("Writing tile image")
                write_snapshot_grid(working_iindex)

            if self.ipp_config.write_quick_pano():
                self.verbose and self.log("Writing quick pano")
                write_quick_pano(working_iindex)
        else:
            self.log(
                "WARNING: skipping generating summary output on incomplete processed scan"
            )

        outj = {
            "type": "processing",
        }
        writej(os.path.join(self.directory, "processing.json"), outj)

        if not self.ipp_config.keep_intermediates():
            final_dir = working_iindex["dir"]
            if os.path.realpath(final_dir) == os.path.realpath(
                    self.directory):
                self.verbose and self.log(
                    "Clean up intermediates: nothing to do")
            else:
                remove_intermediate_directories(self.directory, final_dir)
                self.log(
                    f"Clean up intermediates: {final_dir} => {self.directory}"
                )
                working_iindex = index_scan_images(self.directory)

        if not self.ipp_config.fiji_stitch():
            pass
        elif not healthy:
            self.log("Fiji stitch: skip (incomplete data)")
        elif len(working_iindex["images"]) == 1:
            self.log("Fiji stitch: skip (only one image)")
        else:
            try:
                fiji_stitch.stitch_dir(working_iindex["dir"],
                                       out_dir=os.path.join(
                                           self.directory,
                                           fiji_stitch.OUT_DIR),
                                       lazy=self.lazy,
                                       log=self.log)
            except Exception as e:
                if not self.best_effort:
                    raise
                # Don't hold up CloudStitch
                self.log(f"WARNING: Fiji stitch failed: {e}")

        if not self.upload:
            self.log("CloudStitch: skip (requested by CLI)")
        elif not self.ipp_config.cloud_stitch():
            self.log("CloudStitch: skip (requested by JSON policy)")
        elif not healthy:
            self.log("CloudStitch: skip (incomplete data)")
        elif not self.cs_info and not config.get_bc(
        ).labsmore_stitch_aws_access_key():
            self.log("CloudStitch: skip (missing credidentials)")
        elif len(working_iindex["images"]) == 1:
            self.log("CloudStitch: skip (only one image)")
        else:
            delete_jpg_dir = None
            main_dir = working_iindex["dir"]

            check_valid_image_dir(working_iindex)

            # CloudStitch currently only supports .jpg
            if is_tif_scan(working_iindex["dir"]):
                self.log("")
                self.log("Converting to jpg")
                next_dir = os.path.join(working_iindex["dir"], "jpg_tmp")
                delete_jpg_dir = next_dir
                # runs inline, not parallelized
                self.csip.tif2jpg_dir(iindex_in=working_iindex,
                                      dir_out=next_dir,
                                      lazy=self.lazy)
                working_iindex = index_scan_images(next_dir)

                check_valid_image_dir(working_iindex)

            try:
                self.log("Ready to stitch " + working_iindex["dir"])
                cloud_stitch.upload_dir(working_iindex["dir"],
                                        cs_info=self.cs_info,
                                        dst_basename=dst_basename,
                                        verbose=self.verbose)
                # Pop the log file up to main dir before deleting tmp dir
                if delete_jpg_dir:
                    shutil.move(
                        os.path.join(working_iindex["dir"],
                                     "cloud_stitch.json"),
                        os.path.join(main_dir, "cloud_stitch.json"))
            finally:
                if delete_jpg_dir:
                    shutil.rmtree(delete_jpg_dir, ignore_errors=True)


class SnapshotCSIP:
    def __init__(self,
                 csip,
                 image,
                 best_effort=True,
                 microscope=None,
                 verbose=False):
        self.csip = csip
        self.log = csip.log
        self.image = image
        self.best_effort = best_effort
        self.verbose = verbose
        self.microscope = microscope

    def color_gains(self):
        """Software white balance x software gain, or None if a no-op"""
        usc = config.get_usc()
        try:
            return effective_gains(usc.imager.white_balance_gains(),
                                   usc.imager.software_gain())
        except ValueError as e:
            self.log(f"WARNING: ignoring bad white balance / gain: {e}")
            return None

    def run(self, options):
        # TODO: Write this part
        self.verbose and self.log("SnapshotCSIP verbose")
        self.verbose and self.log("Microscope: %s" % (self.microscope.name, ))
        self.verbose and self.log("Serial: %s" % (self.microscope.serial(), ))
        self.verbose and self.log(
            "Has FF cal: %s" % config.get_usc().imager.has_ff_cal())

        self.verbose and self.log("")
        '''
        current_images = self.images

        if len(current_images) > 1:
            # need to change self.images to have some dict annoation structure
            # or something like that
            assert 0, "FIXME: hdr, stack, or ...?"

            tb = TaskBarrier()
            data_out = self.csip.queue_hdr_enfuse(im_in=current_images,
                                                  want_im_out=True,
                                                  tb=tb)
            tb.wait()
            current_images = data_out["image"].get_im()

            tb = TaskBarrier()
            data_out = self.csip.queue_hdr_stack(im_in=current_images,
                                                 want_im_out=True,
                                                 tb=tb)
            tb.wait()
            current_images = data_out["image"].get_im()
        '''

        current_image = self.image
        capim = options["captured_image"]
        # Only GUI imagers currently have this (EXIF)
        add_meta = getattr(self.microscope.imager, "add_captured_image_meta",
                           None)
        if add_meta:
            add_meta(capim)

        def run_1_to_1(plugin, plugin_options=None):
            self.verbose and self.log(f"{plugin}: start")
            tb = TaskBarrier()
            data_out = self.csip.queue_1_to_1_plugin(
                plugin=plugin,
                im_in=current_image,
                want_im_out=True,
                tb=tb,
                options=options if plugin_options is None else plugin_options)
            tb.wait()
            return data_out["image"].get_im()

        # Copy: snapshot_correction() returns the live config list
        # Appending to it would make per-snapshot plugins (ex: scalebar) permanent
        # and leak into scan image processing
        ipp = list(config.get_usc().ipp.snapshot_correction())
        config_plugins = [p["plugin"] for p in ipp]
        # Lets later processing (ex: DirCSIP on a scan) not apply them again
        corrections = []
        if len(ipp) == 0:
            self.verbose and self.log("Post corrections: skip")
        else:
            for pipeline_this in ipp:
                current_image = run_1_to_1(pipeline_this["plugin"])
                corrections.append(pipeline_this["plugin"])

        if not config.get_usc().imager.has_ff_cal():
            self.verbose and self.log("FF correction: skip")
        else:
            self.verbose and self.log("FF correction: start")
            tb = TaskBarrier()
            data_out = self.csip.queue_correct_ff1(im_in=current_image,
                                                   want_im_out=True,
                                                   tb=tb)
            tb.wait()
            current_image = data_out["image"].get_im()
            corrections.append("correct-ff1")

        # After FF: FF normalizes each channel to itself so color is unchanged
        color_gains = self.color_gains()
        if color_gains is None or "correct-wb" in config_plugins:
            self.verbose and self.log("Color correction: skip")
        else:
            current_image = run_1_to_1("correct-wb",
                                       plugin_options=dict(
                                           options, color_gains=color_gains))
            corrections.append("correct-wb")
            # Native depth data is what gets saved for .tif / .png
            data = getattr(capim, "data", None)
            if data is not None:
                if data.ndim == 2:
                    # Raw bayer mosaic: can't balance, only scale
                    capim.data = apply_gains(
                        data, [config.get_usc().imager.software_gain()] * 3)
                else:
                    capim.data = apply_gains(data, color_gains)

        capim.set_meta_kv("corrections", corrections)

        # Per request plugins (ex: scalebar annotation) go last
        # They may change image geometry which corrections (ex: FF) can't handle
        for plugin in options.get("plugins", []):
            if plugin in config_plugins:
                continue
            current_image = run_1_to_1(plugin)

        return current_image


"""
WIP, not tested / used currently
See https://github.com/Labsmore/pyuscope/issues/190

Second generation image processing orchestrator
Constructs a pipeline based on expected input data
Expects all images to be present
If it fails you'll need to fall back to DirCSIP
"""
'''
class StreamCSIP:
    def __init__(self, csip, image_stream, microscope=None, upload=False):
        assert 0, "WIP"
        self.csip = csip
        self.image_stream = image_stream
        self.microscope = microscope

        # TODO: auto figure this out
        # Might also want to move these to be objects
        self.pipeline = []
        if self.image_stream.has_hdr():
            self.pipeline.append({"plugin": "hdr-enfuse"})
        if self.image_stream.has_stack():
            self.pipeline.append({"plugin": "stack-enfuse"})
        # Should this be before or after other steps?
        # Currently expensive so put at the end
        if config.get_usc().imager.has_ff_cal():
            self.pipeline.append({"plugin": "correct-illumination"})

        next_directory = self.image_stream.working_dir()
        for pipe in self.pipeline:
            """
            keys are the next file basename w/o extension
            ex: 
            """
            plugin = pipe["plugin"]

            if plugin == "hdr-enfuse":
                ptype = "hdr"
            elif plugin == "stack-enfuse":
                ptype = "stack"
            elif plugin == "correct-illumination":
                ptype = "correct"
            else:
                assert 0, plugin

            pipe["buckets"] = {}
            pipe["type"] = ptype
            pipe["remove_key"] = ptype
            pipe["dir_in"] = next_directory
            """
            Nest directories like .../mz_mit20x/hdr/stack/
            """
            next_directory = os.path.join(next_directory, ptype)
            pipe["dir_out"] = next_directory

        self.tb = TaskBarrier()

    def bucket_images(self, pipe, images):
        """
        Place new images into given pipeline stage
        Don't trigger any processing, even if it could be done now
        """
        for image in images:
            # iindex_filename_key[image.get_filename]
            fn = image.get_filename()
            bucketk = reduce_iindex_filename(fn, remove_key=pipe["remove_key"])
            pipe.buckets.setdefault(bucketk, set()).add(os.path.basename(fn))

    def bucket_full(self, statei, bucketk):
        plugin = self.pipeline[statei]
        if plugin == "hdr-enfuse":
            expected = self.image_stream.bucket_size("hdr")
        elif plugin == "stack-enfuse":
            expected = self.image_stream.bucket_size("stack")
        elif plugin == "correct-illumination":
            expected = 1
        else:
            assert 0, f"bad plugin {plugin}"
        return len(self.state[statei][bucketk]) >= expected

    def process_bucket(self, pipe, bucketk):
        if not self.bucket_full(pipe, bucketk):
            return
        plugin = pipe["plugin"]
        if plugin == "hdr-enfuse":
            fn_out = os.path.join(pipe["dir_out"], bucketk + ".tif")
            self.csip.queue_hdr_enfuse(fns_in=pipe["buckets"][bucketk],
                                       fn_out=fn_out)
        elif plugin == "stack-enfuse":
            fn_out = os.path.join(pipe["dir_out"], bucketk + ".tif")
            self.csip.queue_stack_enfuse(fns_in=pipe["buckets"][bucketk],
                                         fn_out=fn_out)
        elif plugin == "correct-illumination":
            fn_out = os.path.join(pipe["dir_out"], bucketk + ".tif")
            fns_in = list(pipe["buckets"][bucketk])
            assert len(fns_in) == 1
            self.csip.queue_correct_illumination(fn_in=fns_in[0],
                                                 fn_out=fn_out)
        elif plugin == "save-jpg":
            fns_in = list(pipe["buckets"][bucketk])
            assert len(fns_in) == 1
            fn_out = os.path.join(pipe["dir_out"], bucketk + ".jpg")
            self.csip.queue_convert_jpg(fn_in=fns_in[0], fn_out=fn_out)
        else:
            assert 0, f"bad plugin {plugin}"
        del self.pipe["buckets"][bucketk]

    def run(self):
        """
        Stream images (ie from an in progress capture)
        Two steams of data:
        -Raw images
        -Completed intermediate steps
        Could also just grab from a filesystem hmm
        """
        """
        Keep track of images at each state of the pipeline
        Key them based on file basenames / assume they are written to the filesystem
        """
        self.states = []
        for _element in self.pipeline:
            # Buckets
            self.states.append({})

        # Done when all images have been put into pipeline and all pipeline tasks are processed
        while not self.image_stream.done() or not self.tb.idle():
            # New images for the pipeline?
            new_images = self.image_stream.new_images()
            self.bucket_images(self.pipeline[0], new_images)

            # In progress images
            for statei, new_images in self.pop_completed_images():
                # Move to next pipeline stage if not done
                if statei != len(self.pipeline) - 1:
                    self.bucket_images(self.pipeline[statei + 1], new_images)

            # Since images move down the pipeline, any newly completed images will move to next state
            # Since we check earlier pipeline stages first bias towards BFS
            # Could reorder if wanted DFS
            for statei, pipe in enumerate(self.pipeline):
                for bucketk in list(pipe["state"].keys()):
                    self.process_bucket(pipe, bucketk)
        # Finish all remaining allocated tasks
        self.tb.wait()

        # Last step after all processed
        if self.upload:
            assert 0, "fixme"
            """
            self.log("Ready to stitch " + working_iindex["dir"])
            cloud_stitch.upload_dir(working_iindex["dir"],
                                    cs_info=cs_info,
                                    dst_basename=dst_basename,
                                    verbose=verbose)
            """
'''
