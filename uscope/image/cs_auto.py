"""
Image processing pipeline summary

CSImageProcessor
High level orchestrator
Can be given things like a directory of images or a stream of incoming images
CSImageProcessor has a pool of CSImageProcessorThread's that do the actual processing
Depending on the orchestrator algorithm choosen (ex: StreamCSIP)
a different image processing pipeline may be constructed

Tasks are centered around IPPlugin's
These are image processing algorithms such as HDR or focus stacking
They generally take one or more images in and produce a single image out
"""

from uscope import cloud_stitch
from uscope.scan_util import index_scan_images, bucket_group, reduce_iindex_filename
import os
import time
import glob
import subprocess
import shutil
from collections import OrderedDict
import traceback
from uscope import config
import multiprocessing
import threading
import queue
from PIL import Image
import tempfile


def need_jpg_conversion(working_dir):
    fns = glob.glob(working_dir + "/*.tif")
    return bool(fns)


def get_image_suffix(dir_in):
    if glob.glob(dir_in + "/*.tif"):
        return ".tif"
    else:
        return ".jpg"


def get_open_set(working_iindex):
    open_set = set()
    for col in range(working_iindex["cols"]):
        for row in range(working_iindex["rows"]):
            open_set.add((col, row))
    for filev in working_iindex["images"].values():
        open_set.remove((filev["col"], filev["row"]))
    return open_set


def already_uploaded(directory):
    # upload metadata file cloud_stitch.json => uploaded
    return len(glob.glob(f'{directory}/**/cloud_stitch.json',
                         recursive=True)) > 0


class EtherealImageR:
    """
    An image that may be on filesystem or in memory
    User tells it what it wants it will munge it into place
    Read only
    """
    def __init__(self, im=None, fn=None, meta=None):
        self.im = im
        self.fn = fn
        self.tmp_files = set()
        self.meta = meta

    def __del__(self):
        self.flush()

    def flush(self):
        """
        Remove all temporary files
        """
        for fn in self.tmp_files:
            os.unlink(fn)
        self.tmp_files.clear()

    def get_filename(self):
        """
        Return any valid filename
        """
        if self.fn:
            return self.fn
        else:
            assert 0, "FIXME"

    def to_filename(self, fn):
        """
        Make image exist at given location
        Image will be in native / existing format
        Image may be written or symlinked to
        """
        assert fn not in self.tmp_files
        if self.im:
            self.im.write(fn)
        else:
            os.symlink(self.fn, fn)
        self.tmp_files.add(fn)

    def to_filename_tif(self, fn):
        """
        Ensure resulting file is a .tif, converting if necessary
        """
        if self.fn:
            subprocess.check_call(["convert", self.fn, fn])
            assert os.path.exists(fn)
        elif self.im:
            self.im.write(fn)
        else:
            assert 0

    def release_filename(self, fn):
        """
        Return the filename allocated earlier
        """
        self.tmp_files.remove(fn)

    def to_im(self):
        """
        Return a read only PIL image
        """
        if self.im:
            return self.im
        else:
            return Image.open(self.fn)


class EtherealImageW:
    """
    An image that will be written to output
    User gives some hints as to how it would like the image to be output
    """
    def __init__(self,
                 want_dir=None,
                 want_basename=None,
                 want_fn=None,
                 meta=None):
        # for now assume will get the desired output file name
        self.im = None
        if want_fn:
            self.want_fn = want_fn
        elif want_dir and want_basename:
            self.want_fn = os.path.join(want_dir, want_basename)
        else:
            assert 0, "FIXME"
        self.meta = meta

    def get_filename(self):
        return self.want_fn

    def get_im(self):
        return Image.open(self.want_fn)


"""
ImageProcessing plugin
"""


class IPPlugin:
    """
    Thread safe: no
    If you want to do multiple in parallel create multiple instances
    """
    def __init__(self, log=None, need_tmp_dir=False, default_options={}):
        if not log:

            def log(s):
                print(s)

        self.log = log
        self.default_options = default_options

        self.tmp_dir = None
        self.need_tmp_dir = need_tmp_dir
        if need_tmp_dir:
            self.create_tmp_dir()
        self.delete_tmp = True

    def __del__(self):
        if self.tmp_dir:
            self.tmp_dir.cleanup()
            self.tmp_dir = None

    def get_tmp_dir(self):
        assert self.tmp_dir
        return self.tmp_dir.name

    def create_tmp_dir(self):
        if self.tmp_dir:
            return
        self.tmp_dir = tempfile.TemporaryDirectory()

    def clear_tmp_dir(self):
        """
        Delete between runs
        """
        assert self.tmp_dir
        for filename in os.listdir(self.get_tmp_dir()):
            file_path = os.path.join(self.get_tmp_dir(), filename)
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)

    def run(self, data_in, data_out, options={}):
        """
        Take images in from images_in and produce one or more images_out
        data_in: dictionary of input items
            simple plugin: a single key called "images" containing a list of EtherealImageR
        data_out: dictionary of output products
            simple plugin: a single key called "image" containing a an EtherealImageW
        """
        if self.tmp_dir:
            self.clear_tmp_dir()
        try:
            self._run(data_in, data_out, options=options)
        finally:
            if self.tmp_dir:
                self.clear_tmp_dir()

    def _run(self, data_in, data_out, options={}):
        assert 0, "required"


class HDREnfusePlugin(IPPlugin):
    def __init__(self, log, default_options={}):
        super().__init__(log=log,
                         default_options=default_options,
                         need_tmp_dir=True)

    def _run(self, data_in, data_out, options={}):
        ewf = options.get("ewf", "gaussian")
        best_effort = options.get("best_effort", False)
        args = [
            "enfuse", "--output", data_out["image"].get_filename(),
            "--exposure-weight-function", ewf
        ]
        for image_in in data_in["images"]:
            fn = image_in.get_filename()
            args.append(fn)
        self.log(" ".join(args))
        try:
            subprocess.check_call(args)
        except subprocess.CalledProcessError:
            if not best_effort:
                raise
            else:
                self.log("WARNING: ignoring exception")
                traceback.print_exc()


"""
Stack using enfuse
Currently skips align
"""


class StackEnfusePlugin(IPPlugin):
    def __init__(self, log, default_options={}):
        super().__init__(log=log,
                         default_options=default_options,
                         need_tmp_dir=True)
        self.skip_align = True

    def _run(self, data_in, data_out, options={}):
        best_effort = options.get("best_effort", False)

        def check_call(args):
            try:
                subprocess.check_call(args)
            except subprocess.CalledProcessError:
                if not best_effort:
                    raise
                else:
                    self.log("WARNING: ignoring exception")
                    traceback.print_exc()

        # Stacking can fail to align features
        # Consider what to do such as filling in a patch image
        # from the middle of the stack
        """
        align_image_stack -m -a OUT $(ls)
        -m  Optimize field of view for all images, except for first. Useful for aligning focus stacks with slightly different magnification.
            might not apply but keep for now
       -a prefix
    
        enfuse --exposure-weight=0 --saturation-weight=0 --contrast-weight=1 --hard-mask --output=baseOpt1.tif OUT*.tif
        """
        """
        tmp_dir = "/tmp/cs_auto"
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        os.mkdir(tmp_dir)
        """

        prefix = "aligned_"
        if self.skip_align:
            for imi, image_in in enumerate(data_in["images"]):
                fn_aligned = os.path.join(self.get_tmp_dir(),
                                          prefix + "%04u.tif" % imi)
                image_in.to_filename_tif(fn_aligned)
        else:
            # Always output as .tif
            args = [
                "align_image_stack", "-l", "-i", "-v", "--use-given-order",
                "-a",
                os.path.join(self.get_tmp_dir(), prefix)
            ]
            for image_in in data_in["images"]:
                args.append(image_in.get_filename())
            # self.log(" ".join(args))
            check_call(args)

        args = [
            "enfuse", "--exposure-weight=0", "--saturation-weight=0",
            "--contrast-weight=1", "--hard-mask",
            "--output=" + data_out["image"].get_filename()
        ]
        for fn in glob.glob(os.path.join(self.get_tmp_dir(), prefix + "*")):
            args.append(fn)
        # self.log(" ".join(args))
        check_call(args)

        if self.delete_tmp:
            # Remove old files
            # This can also confuse globbing to find extra tifs
            for fn in glob.glob(os.path.join(self.get_tmp_dir(),
                                             prefix + "*")):
                os.unlink(fn)


"""
Correct uneven illumination using masks
"""


class CorrectilluminationPlugin(IPPlugin):
    def __init__(self, log, default_options={}):
        super().__init__(log=log,
                         default_options=default_options,
                         need_tmp_dir=True)

    def _run(self, data_in, data_out, options={}):
        assert 0, "FIXME"


class CSImageProcessorThread(threading.Thread):
    """
    A single worker thread that can perform a number of low level corrections
    Intended to be used with CSImageProcessor
    """
    def __init__(self, csip, name):
        super().__init__()
        self.csip = csip
        self.log = self.csip.log
        self.name = name
        self.running = threading.Event()
        # Set for single commands
        self.simple_idle = threading.Event()
        self.simple_idle.set()
        self.queue_in = queue.Queue()
        self.queue_out = queue.Queue()

        # Each thrread gets its own set of correction engines
        self.pluins = {
            "stack-enfuse": StackEnfusePlugin(log=self.log),
            "hdr-enfuse": HDREnfusePlugin(log=self.log),
            "correct-illumination": CorrectilluminationPlugin(log=self.log),
        }

        self.running.set()

    def stop(self):
        self.running.clear()

    def queue_command(self, ip_params):
        self.queue_in.put(ip_params)

    """
    def wait_queue_out(self, command=None, timeout=None):
        (call_info, result, info) = self.queue_in.get(True, timeout)
        (name, data_in, data_out, options) = call_info
        if result != "ok":
            raise Exception(f"Command {name} failed: {result} {info}")
        if command:
            assert name == command
        return info
    """

    def run(self):

        while self.running.is_set():
            try:
                ip_params = self.queue_in.get(True, 0.1)
            except queue.Empty:
                continue

            def finish_command(result, info):
                out = (ip_params, result, info)
                self.queue_out.put(out)
                if ip_params.tb:
                    ip_params.tb.callback()
                if ip_params.callback:
                    ip_params.callback(*out)
                self.simple_idle.set()

            plugin = self.pluins.get(ip_params.task_name)
            if plugin is None:
                self.log(f"Invalid plugin {ip_params.task_name}")
                finish_command("error", "invalid command")
                continue
            try:
                ret = plugin.run(data_in=ip_params.data_in,
                                 data_out=ip_params.data_out,
                                 options=ip_params.options)
                self.log("Command done")
                finish_command("ok", ret)
            except Exception as e:
                self.log("")
                self.log("WARNING: worker thread crashed")
                self.log(traceback.format_exc())
                finish_command("exception", e)
                continue


"""
Support the following:
-Planner running
-GUI stand alone operations (ex: a single focus stack)
-From filesystem?
"""


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


class TaskBarrier:
    """
    Track when all allocated tasks are complete
    """
    def __init__(self):
        self.ntasks_allocated = 0
        self.ntasks_completed = 0

    def callback(self):
        self.ntasks_completed += 1

    def allocate_callback(self):
        self.ntasks_allocated += 1
        return self.callback

    def wait(self, timeout=None):
        tstart = time.time()
        while self.ntasks_allocated > self.ntasks_completed:
            if timeout and time.time() - tstart > timeout:
                raise Exception("Timed out")
            time.sleep(0.1)

    def idle(self):
        return self.ntasks_allocated == self.ntasks_completed


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

    def run(self):
        """
        Process a completed scan into processed images
        Spins off processing to workers where possible
        """

        self.log("Reading metadata...")
        working_iindex = index_scan_images(self.directory)
        dst_basename = os.path.basename(os.path.abspath(self.directory))

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
        # self.pipeline.append({"plugin": "correct-illumination"})

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


"""
Command passed to image processing thread
"""


class CSIPParams:
    def __init__(self,
                 task_name,
                 data_in={},
                 data_out={},
                 options={},
                 callback=None,
                 tb=None):
        self.task_name = task_name
        self.data_in = data_in
        self.data_out = data_out
        self.options = options
        """
        User supplied callback on task completion
        on success
            self.callback(self, "ok", returned result (probably None))
        on failure
            self.callback(self, "exception", exception)
        """
        self.callback = callback
        """
        Task Barrier secondary callback
        a bit of a hack to add a second callback
        used to synchronize when a swarm of workers is done
        self.tb.callback called on completion
        """
        self.tb = tb


"""
Multi-threaded engine to HDR, focus stack, and correct images
Can accept:
-Completed unprocessed scans
-Image stream for in progress scan
-Small jobs like a single stack

Currently only one high level task can be run at a time
ie it can't process two completed scans at the same time or do an image stack while a scan is running 
Probably would need to make this a thread to handle that
"""


class CSImageProcessor(threading.Thread):
    def __init__(self, nthreads=None, log=None):
        super().__init__()
        if log is None:

            def log(s):
                print(s)

        self.log = log
        if not nthreads:
            nthreads = multiprocessing.cpu_count()
        self.workers = OrderedDict()
        for i in range(nthreads):
            name = f"w{i}"
            self.workers[name] = CSImageProcessorThread(self, name)
        self.queue_in = queue.Queue()
        self.queue_out = queue.Queue()
        self.running = threading.Event()
        self.running.set()

    def __del__(self):
        self.stop()

    def stop(self):
        self.running.clear()

        if self.workers:
            self.log("Shutting down: requesting")
            for worker in self.workers.values():
                worker.stop()
            self.log("Shutting down: joining")
            for worker in self.workers.values():
                worker.join()
            self.workers = None

    def queue_task(self, ip_params, callback=None, block=None):
        assert not block, "fixme"
        if ip_params.tb:
            # Mark task allocated
            # tb callback will be manually invoked on result
            ip_params.tb.allocate_callback()
        self.queue_in.put(ip_params)

    def queue_hdr_enfuse(self,
                         fns_in,
                         fn_out,
                         options={},
                         callback=None,
                         tb=None,
                         block=None):
        """
        Use enfuse to HDR process a sequence of images of varying exposures
        """
        ip_params = CSIPParams(
            task_name="hdr-enfuse",
            data_in={"images": [EtherealImageR(fn=fn_in) for fn_in in fns_in]},
            data_out={"image": EtherealImageW(want_fn=fn_out)},
            options=options,
            callback=callback,
            tb=tb)
        self.queue_task(ip_params=ip_params, block=block)

    def queue_stack_enfuse(self,
                           fns_in,
                           fn_out,
                           options={},
                           callback=None,
                           tb=None,
                           block=None):
        """
        Use enfuse to stack images of varying Z height
        """
        ip_params = CSIPParams(
            task_name="stack-enfuse",
            data_in={"images": [EtherealImageR(fn=fn_in) for fn_in in fns_in]},
            data_out={"image": EtherealImageW(want_fn=fn_out)},
            options=options,
            callback=callback,
            tb=tb)
        self.queue_task(ip_params=ip_params, block=block)

    def fix_dir(self, this_iindex, dir_out):
        """
        Make a best estimate by filling in images from directory above
        For now assumes dir above is focus stack and the output dir is the final upload dir
        Should cover most use cases we care about for now
        """

        if not os.path.exists(dir_out):
            os.mkdir(dir_out)

        open_set = get_open_set(this_iindex)
        stack_iindex = index_scan_images(os.path.dirname(this_iindex["dir"]))
        assert stack_iindex["stacks"], "fixme assumes dir above is stack"
        stacks = stack_iindex["stacks"]
        # Assume middle is mostly in focus
        selected_stack = stacks // 2
        for (col, row) in sorted(open_set):
            src_fn = os.path.join(
                stack_iindex["dir"],
                "c%03u_r%03u_z%02u.jpg" % (col, row, selected_stack))
            dst_fn = os.path.join(dir_out, "c%03u_r%03u.jpg" % (col, row))
            self.log(f"cp {src_fn} {dst_fn}")
            shutil.copyfile(src_fn, dst_fn)

        # Now copy in the "real" files
        for basename in this_iindex["images"].keys():
            src_fn = os.path.join(this_iindex["dir"], basename)
            dst_fn = os.path.join(dir_out, basename)
            self.log(f"cp {src_fn} {dst_fn}")
            shutil.copyfile(src_fn, dst_fn)

    def tif2jpg_dir(self, iindex_in, dir_out, lazy=True):
        if not os.path.exists(dir_out):
            os.mkdir(dir_out)

        self.log(f"Converting tif => jpg {iindex_in['dir']} => {dir_out}")
        for fn_base in iindex_in["images"].keys():
            assert ".tif" in fn_base
            fn_in = os.path.join(iindex_in["dir"], fn_base)
            fn_out = fn_base.replace(".tif", ".jpg")
            assert fn_out != fn_base, (fn_out, fn_base)
            fn_out = os.path.join(dir_out, fn_out)
            if lazy and os.path.exists(fn_out):
                self.log(f"lazy: skip {fn_out}")
            else:
                args = ["convert", "-quality", "90", fn_in, fn_out]
                self.log(" ".join(args))
                subprocess.check_call(args)

    def inspect_final_dir(self, working_iindex):
        healthy = True
        n_healthy = working_iindex["cols"] * working_iindex["rows"]
        n_actual = len(working_iindex["images"])
        self.log("Have %u / %u images" % (n_actual, n_healthy))
        open_set = get_open_set(working_iindex)
        self.log("Failed to find: %u files" % (len(open_set)))
        for (col, row) in sorted(open_set):
            self.log("  c%03u_r%03u.jpg" % (col, row))
            healthy = False
        return healthy

    def process_stream(self, *args, **kwargs):
        StreamCSIP(self, *args, **kwargs).run()

    # was run_dir
    def process_dir(self, *args, **kwargs):
        DirCSIP(self, *args, **kwargs).run()

    def run(self):
        if not self.running.is_set():
            return

        for worker in self.workers.values():
            worker.start()

        while self.running.is_set():
            for worker in self.workers.values():
                if worker.simple_idle.is_set():
                    try:
                        ip_params = self.queue_in.get(True, 0.1)
                    except queue.Empty:
                        break
                    worker.queue_command(ip_params)


# was run_dir
def process_dir(*args, nthreads=None, **kwargs):
    ip = None
    try:
        ip = CSImageProcessor(nthreads=nthreads)
        ip.start()
        ip.process_dir(*args, **kwargs)
    finally:
        if ip:
            ip.stop()
    del ip
