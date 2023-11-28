from uscope import cloud_stitch
from uscope.scan_util import index_scan_images, bucket_group, reduce_iindex_filename
import os
from uscope import config
from uscope.imagep.util import TaskBarrier, EtherealImageR, EtherealImageW
from uscope.imagep.summary import write_html_viewer, write_tile_image, write_quick_pano
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
        self.run_n_to_1(task_name="hdr-enfuse", bucket_name="hdr", **kwargs)

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
        dst_basename = os.path.basename(os.path.abspath(self.directory))

        config.lazy_load_microscope_from_config(self.directory)
        bc = config.get_bc()

        print("Microscope: %s" % (config.default_microscope_name(), ))
        print("  Has FF cal: %s" % config.get_usc().imager.has_ff_cal())

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
                                        dir_out=next_dir)
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
        ipp = config.get_usc().ipp.pipeline_last()
        if len(ipp) == 0:
            self.log("Post corrections: skip")
        else:
            for pipeline_this in ipp:
                plugin = pipeline_this["plugin"]
                this_dir = pipeline_this["dir"]
                self.log(f"{plugin}: start")
                next_dir = os.path.join(working_iindex["dir"], this_dir)
                self.correct_plugin_run(pipeline_this,
                                        iindex_in=working_iindex,
                                        dir_out=next_dir)
                working_iindex = index_scan_images(next_dir)

        if not config.get_usc().imager.has_ff_cal():
            self.log("FF correction: skip")
        else:
            self.log("FF correction: start")
            next_dir = os.path.join(working_iindex["dir"], "ff1")
            self.correct_ff1_run(iindex_in=working_iindex, dir_out=next_dir)
            working_iindex = index_scan_images(next_dir)

        if bc.write_html_viewer():
            write_html_viewer(working_iindex)

        if bc.write_tile_image():
            write_tile_image(working_iindex)

        if bc.write_quick_pano():
            write_quick_pano(working_iindex)

        # CloudStitch currently only supports .jpg
        if need_jpg_conversion(working_iindex["dir"]):
            self.log("")
            self.log("Converting to jpg")
            next_dir = os.path.join(working_iindex["dir"], "jpg")
            # runs inline, not parallelized
            self.csip.tif2jpg_dir(iindex_in=working_iindex,
                                  dir_out=next_dir,
                                  lazy=self.lazy)
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


class SnapshotCSIP:
    def __init__(self, csip, images, best_effort=True, verbose=True):
        self.csip = csip
        self.log = csip.log
        self.images = images
        self.best_effort = best_effort
        self.verbose = verbose

    def run(self, options):
        # TODO: Write this part
        self.log("Microscope: %s" % (config.default_microscope_name(), ))
        self.log("  Has FF cal: %s" % config.get_usc().imager.has_ff_cal())

        self.log("")

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

        assert len(current_images) == 1
        current_image = current_images[0]

        ipp = config.get_usc().ipp.pipeline_last()
        current_plugins = [p["plugin"] for p in ipp]
        for plugin in options.get("plugins", []):
            if plugin not in current_plugins:
                ipp.append({"plugin": plugin})
        if len(ipp) == 0:
            self.log("Post corrections: skip")
        else:
            for pipeline_this in ipp:
                plugin = pipeline_this["plugin"]
                self.log(f"{plugin}: start")
                tb = TaskBarrier()
                data_out = self.csip.queue_1_to_1_plugin(plugin=plugin,
                                                         im_in=current_image,
                                                         want_im_out=True,
                                                         tb=tb,
                                                         options=options)
                tb.wait()
                current_image = data_out["image"].get_im()

        if not config.get_usc().imager.has_ff_cal():
            self.log("FF correction: skip")
        else:
            self.log("FF correction: start")
            tb = TaskBarrier()
            self.csip.queue_correct_ff1(im_in=current_image,
                                        want_im_out=True,
                                        tb=tb)
            tb.wait()
            current_image = data_out["image"].get_im()

        return current_image


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
        assert 0, "WIP"
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
