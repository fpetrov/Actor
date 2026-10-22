"""Exclusive per-channel local ownership; no process discovery or signalling.

The lock inode persists after release. Unlinking it would let another controller
lock a replacement while a previous owner still holds the original inode.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat


class LeaseError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


class ChannelLease:
    def __init__(self, channel, directory=None):
        if not isinstance(channel, str) or not re.fullmatch(r"[a-z0-9_]{3,25}", channel):
            raise ValueError("A canonical Twitch channel login is required")
        self.directory = Path(directory) if directory is not None else Path.home() / ".actor" / "broadcast" / "leases"
        self.name = hashlib.sha256(channel.encode("ascii")).hexdigest() + ".lock"
        self._fd = None

    @property
    def held(self): return self._fd is not None

    def _directory(self):
        path = self.directory.absolute()
        # Walk with dirfds and NOFOLLOW, including every ancestor. No symlink
        # alias may accidentally create an independent lock namespace.
        directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in path.parts[1:]:
                try: child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                except FileNotFoundError:
                    try: os.mkdir(component, 0o700, dir_fd=directory)
                    except FileExistsError: pass
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory); directory = child
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise LeaseError("channel_lease_directory_unsafe")
            return directory
        except BaseException:
            os.close(directory); raise

    def acquire(self):
        if self.held: return self
        directory = self._directory(); fd = None
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            # Publish a new inode atomically, or open the already published
            # entry without O_CREAT. Never retry an unexpected missing entry.
            try: fd = os.open(self.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            except FileExistsError: fd = os.open(self.name, flags, dir_fd=directory)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise LeaseError("channel_lease_file_unsafe")
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise LeaseError("channel_already_owned") from None
            current = os.stat(self.name, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino) or current.st_nlink != 1:
                raise LeaseError("channel_lease_inode_changed")
            self._fd = fd; fd = None
            return self
        finally:
            if fd is not None: os.close(fd)
            os.close(directory)

    def release(self):
        fd = self._fd; self._fd = None
        if fd is not None:
            try: fcntl.flock(fd, fcntl.LOCK_UN)
            finally: os.close(fd)

    def __enter__(self): return self.acquire()
    def __exit__(self, *_): self.release()
