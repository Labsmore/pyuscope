from uscope import cloud_stitch
from uscope.scan_util import index_scan_images, bucket_group, reduce_iindex_filename
import os
from uscope import config
from uscope.imagep.util import TaskBarrier, EtherealImageR, EtherealImageW
import glob
import json
"""
Support the following:
-Planner running
-GUI stand alone operations (ex: a single focus stack)
-From filesystem?
"""


def need_jpg_conversion(working_dir):
    fns = glob.glob(working_dir + "/*.tif")
    return bool(fns)


def get_image_suffix(dir_in):
    if glob.glob(dir_in + "/*.tif"):
        return ".tif"
    else:
        return ".jpg"


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
                 verbose=True):
        self.csip = csip
        self.log = csip.log
        self.directory = directory
        self.cs_info = cs_info
        self.upload = upload
        self.fix = fix
        self.lazy = lazy
        # FIXME:
        # self.ewf = ewf
        self.best_effort = best_effort
        self.verbose = verbose

    def hdr_run(self,
                iindex_in,
                dir_out,
                ewf=None,
                lazy=True,
                best_effort=True):
        if not os.path.exists(dir_out):
            os.mkdir(dir_out)
        image_suffix = get_image_suffix(iindex_in["dir"])
        buckets = bucket_group(iindex_in, "hdr")

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
                self.csip.queue_hdr_enfuse(fns_in=fns, fn_out=fn_out, tb=tb)
        tb.wait()

    def stack_run(self, iindex_in, dir_out, lazy=True, best_effort=True):
        if not os.path.exists(dir_out):
            os.mkdir(dir_out)
        image_suffix = get_image_suffix(iindex_in["dir"])
        buckets = bucket_group(iindex_in, "stack")
        tb = TaskBarrier()
        """
        def clean_tmp_files():
            if self.delete_tmp:
                # Remove old files
                for fn in glob.glob(os.path.join(iindex_in["dir"],
                                                 "aligned_*")):
                    os.unlink(fn)

        clean_tmp_files()
        """

        try:
            # Must be in stack order?
            for fn_prefix, stacks in sorted(buckets.items()):
                self.log(stacks.items())
                fns = [
                    os.path.join(iindex_in["dir"], fn)
                    for _i, fn in sorted(stacks.items())
                ]
                fn_out = os.path.join(dir_out, fn_prefix + image_suffix)
                if lazy and os.path.exists(fn_out):
                    self.log(f"lazy: skip {fn_out}")
                else:
                    self.log("Queing task")
                    self.csip.queue_stack_enfuse(  # dir_in=iindex_in["dir"],
                        fns_in=fns,
                        fn_out=fn_out,
                        # best_effort=best_effort,
                        tb=tb)
            tb.wait()

        finally:
            # clean_tmp_files()
            pass

    def correct_sharp1_run(self, iindex_in, dir_out, lazy=True):
        if not os.path.exists(dir_out):
            os.mkdir(dir_out)
        tb = TaskBarrier()
        for fn_in in iindex_in["images"].keys():
            fn_out = os.path.join(dir_out, os.path.basename(fn_in))
            if lazy and os.path.exists(fn_out):
                self.log(f"lazy: skip {fn_out}")
            else:
                self.csip.queue_correct_sharp1(fn_in=os.path.join(
                    iindex_in["dir"], fn_in),
                                               fn_out=fn_out,
                                               tb=tb)
        # print("TB: wait w/ alloc %s vs completed %s" % (tb.ntasks_allocated, tb.ntasks_completed))
        tb.wait()

    def correct_ff1_run(self, iindex_in, dir_out, lazy=True):
        if not os.path.exists(dir_out):
            os.mkdir(dir_out)
        tb = TaskBarrier()
        for fn_in in iindex_in["images"].keys():
            fn_out = os.path.join(dir_out, os.path.basename(fn_in))
            if lazy and os.path.exists(fn_out):
                self.log(f"lazy: skip {fn_out}")
            else:
                self.csip.queue_correct_ff1(fn_in=os.path.join(
                    iindex_in["dir"], fn_in),
                                            fn_out=fn_out,
                                            tb=tb)
        # print("TB: wait w/ alloc %s vs completed %s" % (tb.ntasks_allocated, tb.ntasks_completed))
        tb.wait()

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
                self.csip.queue_correct_plugin(plugin,
                                               fn_in=os.path.join(
                                                   iindex_in["dir"], fn_in),
                                               fn_out=fn_out,
                                               tb=tb)
        # print("TB: wait w/ alloc %s vs completed %s" % (tb.ntasks_allocated, tb.ntasks_completed))
        tb.wait()

    def run(self):
        """
        Process a completed scan into processed images
        Spins off processing to workers where possible
        """

        self.log("Reading metadata...")
        working_iindex = index_scan_images(self.directory)
        dst_basename = os.path.basename(os.path.abspath(self.directory))

        config.lazy_load_microscope_from_config(self.directory)

        print("Microscope: %s" % (config.default_microscope_name(), ))
        print("  Has FF cal: %s" % config.get_usc().imager.has_ff_cal())

        self.log("")

        if not working_iindex["hdrs"]:
            self.log("HDR: no. Straight pass through")
        else:
            self.log("HDR: yes. Processing")
            # dir name needs to be reasonable for CloudStitch to name it well
            next_dir = os.path.join(working_iindex["dir"], "hdr")
            self.hdr_run(working_iindex,
                         next_dir,
                         lazy=self.lazy,
                         best_effort=self.best_effort)
            working_iindex = index_scan_images(next_dir)

        self.log("")

        if not working_iindex["stacks"]:
            self.log("Stacker: no. Straight pass through")
        else:
            self.log("Stacker: yes. Processing")
            # dir name needs to be reasonable for CloudStitch to name it well
            next_dir = os.path.join(working_iindex["dir"], "stack")
            # maybe? helps some use cases
            self.stack_run(working_iindex,
                           next_dir,
                           lazy=self.lazy,
                           best_effort=self.best_effort)
            working_iindex = index_scan_images(next_dir)
        """
        Now apply custom correction plugins
        TODO: let the user actually determine order for these...ff1 before stack, etc
        """
        ipp = config.get_usc().imager.ipp_last()
        if len(ipp) == 0:
            self.log("Post corrections: skip")
        else:
            for pipeline_this in ipp:
                plugin = pipeline_this["plugin"]
                this_dir = pipeline_this["dir"]
                self.log(f"{plugin}: start")
                next_dir = os.path.join(working_iindex["dir"], this_dir)
                self.correct_plugin_run(pipeline_this, working_iindex,
                                        next_dir)
                working_iindex = index_scan_images(next_dir)
                print("Finishing")

        if not config.get_usc().imager.has_ff_cal():
            self.log("FF correction: skip")
        else:
            self.log("FF correction: start")
            next_dir = os.path.join(working_iindex["dir"], "ff1")
            self.correct_ff1_run(working_iindex, next_dir)
            working_iindex = index_scan_images(next_dir)

        # CloudStitch currently only supports .jpg
        if need_jpg_conversion(working_iindex["dir"]):
            self.log("")
            self.log("Converting to jpg")
            next_dir = os.path.join(working_iindex["dir"], "jpg")
            self.tif2jpg_dir(working_iindex, next_dir, lazy=self.lazy)
            working_iindex = index_scan_images(next_dir)

        self.log("")
        healthy = self.csip.inspect_final_dir(working_iindex)
        self.log("")

        if not healthy and self.best_effort:
            if not self.fix:
                raise Exception(
                    "Need to fix data to continue, but --fix not specified")
            self.log("WARNING: data is incomplete but trying to patch")
            next_dir = os.path.join(working_iindex["dir"], "fix")
            self.fix_dir(working_iindex, next_dir)
            working_iindex = index_scan_images(next_dir)
            self.log("")
            self.log("re-inspecting new dir")
            healthy = self.csip.inspect_final_dir(working_iindex)
            assert healthy
            self.log("")

        if not self.upload:
            self.log("CloudStitch: skip (requested)")
        elif not healthy:
            self.log("CloudStitch: skip (incomplete data)")
        elif not self.cs_info and not config.get_bc(
        ).labsmore_stitch_aws_access_key():
            self.log("CloudStitch: skip (missing credidentials)")
        else:
            self.log("Ready to stitch " + working_iindex["dir"])
            cloud_stitch.upload_dir(working_iindex["dir"],
                                    cs_info=self.cs_info,
                                    dst_basename=dst_basename,
                                    verbose=self.verbose)


"""
WIP, not tested / used currently
See https://github.com/Labsmore/pyuscope/issues/190

Second generation image processing orchestrator
Constructs a pipeline based on expected input data
Expects all images to be present
If it fails you'll need to fall back to DirCSIP
"""


class StreamCSIP:
    def __init__(self, csip, image_stream, upload=False):
        self.csip = csip
        self.image_stream = image_stream

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
