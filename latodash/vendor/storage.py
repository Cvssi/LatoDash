"""Portable demo sidecar locks and atomic replacement; real server storage stays Linux-side."""
from contextlib import contextmanager
import os
if os.name != 'nt':
    import fcntl
else:
    import msvcrt
from pathlib import Path
import tempfile


@contextmanager
def locked(path, *, shared=False, blocking=True):
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags | os.O_CREAT | os.O_EXCL, 0o660)
        created = True
    except FileExistsError:
        fd = os.open(str(path), flags)
        created = False
    try:
        # Only the creator owns this inode. Group writers may lock it but cannot chmod it.
        if created and hasattr(os, "fchmod"):
            os.fchmod(fd, 0o660)
        if os.name == 'nt':
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'\0')
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(fd, mode | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            if os.name == 'nt':
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def atomic_write(path, data: bytes, mode=0o660):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            if hasattr(os, "fchmod"):
                os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        if os.name == 'nt':
            return
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
