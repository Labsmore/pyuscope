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

from uscope.scan_util import index_scan_images
from uscope.imagep.util import EtherealImageR, EtherealImageW
from uscope.imagep.streams import StreamCSIP, DirCSIP, SnapshotCSIP
from uscope.imagep.plugins import get_plugins, get_plugin_ctors
from uscope import config

import os
import glob
import subprocess
import shutil
from collections import OrderedDict
import traceback
import multiprocessing
import threading
import queue
import tempfile


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
        self.plugins = get_plugins(log=self.log)
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

            plugin = self.plugins.get(ip_params.task_name)
            if plugin is None:
                self.log(f"Invalid plugin {ip_params.task_name}")
                finish_command("error", "invalid command")
                continue
            try:
                ret = plugin.run(data_in=ip_params.data_in,
                                 data_out=ip_params.data_out,
                                 options=ip_params.options)
                # self.log("Command done")
                finish_command("ok", ret)
            except Exception as e:
                self.log("")
                self.log("WARNING: worker thread crashed")
                self.log(traceback.format_exc())
                finish_command("exception", e)
                continue


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
        self.queue_in = queue.Queue()
        self.queue_out = queue.Queue()
        self.running = threading.Event()
        self.ready = threading.Event()
        self.workers = OrderedDict()

        # Used
        self.temp_dir_object = tempfile.TemporaryDirectory()
        self.temp_dir = self.temp_dir_object.name

        if not nthreads:
            nthreads = multiprocessing.cpu_count()
        for i in range(nthreads):
            name = f"w{i}"
            self.workers[name] = CSImageProcessorThread(self, name)
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

        if self.temp_dir_object:
            # shutil.rmtree(self.temp_dir)
            self.temp_dir_object.cleanup()
            self.temp_dir = None

    def queue_task(self, ip_params, callback=None, block=None):
        assert not block, "fixme"
        if ip_params.tb:
            # Mark task allocated
            # tb callback will be manually invoked on result
            ip_params.tb.allocate_callback()
        self.queue_in.put(ip_params)

    def queue_n_to_1_plugin(self,
                            task_name=None,
                            fns_in=None,
                            fn_out=None,
                            ims_in=None,
                            want_im_out=False,
                            data_in=None,
                            data_out=None,
                            options={},
                            callback=None,
                            tb=None,
                            block=None):
        """
        Use enfuse to HDR process a sequence of images of varying exposures
        """
        if fns_in is not None:
            data_in = {
                "images": [EtherealImageR(fn=fn_in) for fn_in in fns_in]
            }
        if fn_out is not None:
            data_out = {"image": EtherealImageW(want_fn=fn_out)}
        if ims_in is not None:
            data_in = {
                "images": [EtherealImageR(im=im_in) for im_in in ims_in]
            }
        if want_im_out:
            data_out = {
                "image": EtherealImageW(want_im=True, temp_dir=self.temp_dir)
            }
        ip_params = CSIPParams(task_name=task_name,
                               data_in=data_in,
                               data_out=data_out,
                               options=options,
                               callback=callback,
                               tb=tb)
        self.queue_task(ip_params=ip_params, block=block)

    def queue_1_to_1_plugin(self,
                            plugin,
                            fn_in=None,
                            fn_out=None,
                            im_in=None,
                            want_im_out=False,
                            data_in=None,
                            data_out=None,
                            options={},
                            callback=None,
                            tb=None,
                            block=None):
        if plugin not in get_plugin_ctors():
            print("Valid plugins:", get_plugin_ctors().keys())
            assert 0, f"Bad plugin {plugin}"
        if fn_in is not None:
            data_in = {"image": EtherealImageR(fn=fn_in)}
        if fn_out is not None:
            data_out = {"image": EtherealImageW(want_fn=fn_out)}
        if im_in is not None:
            data_in = {"image": EtherealImageR(im=im_in)}
        if want_im_out:
            data_out = {
                "image": EtherealImageW(want_im=True, temp_dir=self.temp_dir)
            }
        ip_params = CSIPParams(task_name=plugin,
                               data_in=data_in,
                               data_out=data_out,
                               options=options,
                               callback=callback,
                               tb=tb)
        self.queue_task(ip_params=ip_params, block=block)
        return data_out

    def queue_hdr_enfuse(self, **kwargs):
        self.queue_n_to_1_plugin(task_name="hdr-enfuse", **kwargs)

    def queue_stack_enfuse(self, **kwargs):
        self.queue_n_to_1_plugin(task_name="stack-enfuse", **kwargs)

    def queue_stabilization(self, **kwargs):
        self.queue_n_to_1_plugin(task_name="stabilization", **kwargs)

    def queue_correct_ff1(self, **kwargs):
        return self.queue_1_to_1_plugin(plugin="correct-ff1", **kwargs)

    def queue_correct_sharp1(self, **kwargs):
        return self.queue_1_to_1_plugin(plugin="correct-sharp1", **kwargs)

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
                args = [
                    "convert", "-quality",
                    str(config.get_usc().imager.save_quality()), fn_in, fn_out
                ]
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

    def process_snapshots(self, *args, **kwargs):
        options = kwargs.pop("options", {})
        return SnapshotCSIP(self, *args, **kwargs).run(options)

    # was run_dir
    def process_dir(self, *args, **kwargs):
        DirCSIP(self, *args, **kwargs).run()

    def run(self):
        if not self.running.is_set():
            return

        for worker in self.workers.values():
            worker.start()

        self.ready.set()

        while self.running.is_set():
            for worker in self.workers.values():
                if worker.simple_idle.is_set():
                    try:
                        ip_params = self.queue_in.get(True, 0.1)
                    except queue.Empty:
                        break
                    worker.queue_command(ip_params)


# was run_dir
def process_dir(directory, *args, nthreads=None, **kwargs):
    # If a microscope hasn't been specified yet
    config.lazy_load_microscope_from_config(directory)

    ip = None
    try:
        ip = CSImageProcessor(nthreads=nthreads)
        ip.start()
        ip.ready.wait(1.0)
        ip.process_dir(directory, *args, **kwargs)
    finally:
        if ip:
            ip.stop()
    del ip


def process_snapshots(images, *args, nthreads=None, **kwargs):
    ip = None
    try:
        ip = CSImageProcessor(nthreads=nthreads)
        ip.start()
        # nothing to process => can try to shutdown before it starts
        ip.ready.wait(1.0)
        ret = ip.process_snapshots(images, *args, **kwargs)
        print("ok, shutting down")
        return ret
    finally:
        if ip:
            ip.stop()
    del ip
