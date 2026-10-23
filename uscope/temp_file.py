'''
pr0ntools
Copyright 2011 John McMaster <JohnDMcMaster@gmail.com>
Licensed under the terms of the LGPL V3 or later, see COPYING for details
'''

import random
import os
import shutil
import atexit
import glob
import tempfile
import threading
from .util import print_debug

PROCESS_ROOT_PREFIX = "pyuscope-proc-"
_process_root = None
_process_root_lock = threading.Lock()


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale_process_roots(parent=None):
    """
    Remove temp roots left behind by pyuscope processes that died without
    cleaning up (ex: hard crash in Qt at exit)
    """
    if parent is None:
        parent = tempfile.gettempdir()
    for path in glob.glob(os.path.join(parent, PROCESS_ROOT_PREFIX + "*-*")):
        pid = os.path.basename(path)[len(PROCESS_ROOT_PREFIX):].split("-")[0]
        try:
            pid = int(pid)
        except ValueError:
            continue
        if pid == os.getpid() or _pid_alive(pid):
            continue
        shutil.rmtree(path, ignore_errors=True)


def process_temp_root():
    """
    Parent directory for all of this process's temp files
    Grouped per process so stale ones can be found and removed after a crash
    """
    global _process_root
    with _process_root_lock:
        if _process_root is None:
            cleanup_stale_process_roots()
            _process_root = tempfile.mkdtemp(
                prefix=f"{PROCESS_ROOT_PREFIX}{os.getpid()}-")
            atexit.register(shutil.rmtree, _process_root, True)
        return _process_root


def TemporaryDirectory(**kwargs):
    kwargs.setdefault("dir", process_temp_root())
    return tempfile.TemporaryDirectory(**kwargs)

g_default_prefix_dir = None
g_default_prefix = None
PREFIX_BASE = "/tmp/uvtemp_"


class TempFile:
    @staticmethod
    def default_prefix():
        global g_default_prefix_dir
        global g_default_prefix

        if g_default_prefix is None:
            g_default_prefix_dir = ManagedTempDir.get(
                TempFile.get(PREFIX_BASE))
            g_default_prefix = os.path.join(g_default_prefix_dir.file_name, '')
            print('TEMP DIR: %s' % g_default_prefix)
        return g_default_prefix

    @staticmethod
    def rand_str(length):
        ret = ''
        for i in range(0, length):
            ret += "%X" % random.randint(0, 15)
        return ret

    @staticmethod
    def get(prefix=None, suffix=None):
        if not prefix:
            prefix = TempFile.default_prefix()
        if not suffix:
            suffix = ""
        # Good enough for now
        return prefix + TempFile.rand_str(16) + suffix


class ManagedTempFile:
    file_name = None

    def __init__(self, file_name):
        if file_name:
            self.file_name = file_name
        else:
            self.file_name = TempFile.get()

    def __repr__(self):
        return self.file_name

    @staticmethod
    def get(prefix=None, suffix=None):
        return ManagedTempFile(TempFile.get(prefix, suffix))

    @staticmethod
    def from_existing(file_name):
        return ManagedTempFile(file_name)

    @staticmethod
    def from_same_extension(reference_file_name, prefix=None):
        return ManagedTempFile.get(prefix,
                                   '.' + reference_file_name.split(".")[-1])

    def __del__(self):
        try:
            if os.path.exists(self.file_name):
                os.remove(self.file_name)
                print_debug('Deleted temp file %s' % self.file_name)
            else:
                print_debug("Didn't delete inexistant temp file %s" %
                            self.file_name)
        # Ignore if it was never created
        except OSError:
            print('WARNING: failed to delete temp file: %s' % self.file_name)


class ManagedTempDir(ManagedTempFile):
    def __init__(self, temp_dir):
        ManagedTempFile.__init__(self, temp_dir)

    @staticmethod
    def get(temp_dir=None):
        ret = ManagedTempDir(temp_dir)
        os.mkdir(ret.file_name)
        return ret

    def get_file_name(self, prefix='', suffix=None):
        # Make it in this dir
        return TempFile.get(os.path.join(self.file_name, prefix), suffix)

    def __del__(self):
        try:
            if os.path.exists(self.file_name):
                shutil.rmtree(self.file_name)
                print_debug('Deleted temp dir %s' % self.file_name)
            else:
                print_debug("Didn't delete inexistant temp dir %s" %
                            self.file_name)
        # Ignore if it was never created
        except OSError:
            print('WARNING: failed to delete temp dir: %s' % self.file_name)


class TempFileSet:
    def __init__(self, prefix=None):
        if not prefix:
            prefix = TempFile.default_prefix()
        self.prefix = prefix
        self.files = []

    def get_file(self):
        pass

    def get_dir(self):
        pass
