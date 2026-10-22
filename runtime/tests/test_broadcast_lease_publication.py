"""Atomic publication with real disposable files/fds and controlled races.

No child process, service, PF, native application or network is used.
"""
import fcntl
import os
from pathlib import Path
import stat
from types import SimpleNamespace as Obj

import pytest

from actor_broadcast import ownership as module
from actor_broadcast.ownership import ChannelLease, LeaseError


def intercept(monkeypatch,lease,opening):
    original=module.os;calls=[]
    class Proxy:
        def __getattr__(self,name):return getattr(original,name)
        def open(self,path,flags,mode=0o777,*,dir_fd=None):
            if path==lease.name:
                calls.append(flags)
                return opening(original,path,flags,mode,dir_fd)
            return original.open(path,flags,mode,dir_fd=dir_fd)
    monkeypatch.setattr(module,"os",Proxy())
    return original,calls


def ordinary(original,path,flags,mode,dir_fd):
    return original.open(path,flags,mode,dir_fd=dir_fd)


def test_first_creator_exclusively_publishes_private_persistent_inode(monkeypatch,tmp_path):
    lease=ChannelLease("pf_query",tmp_path.resolve()/"new")
    original,calls=intercept(monkeypatch,lease,ordinary)
    lease.acquire()
    try:
        path=lease.directory/lease.name;inode=path.stat().st_ino
        assert len(calls)==1 and calls[0]&os.O_CREAT and calls[0]&os.O_EXCL
        assert calls[0]&os.O_NOFOLLOW and calls[0]&os.O_CLOEXEC and calls[0]&os.O_NONBLOCK
        assert stat.S_IMODE(path.stat().st_mode)==0o600
    finally:lease.release()
    assert path.stat().st_ino==inode and not lease.held


def test_existing_entry_is_opened_without_create_and_keeps_same_inode(monkeypatch,tmp_path):
    first=ChannelLease("pf_query",tmp_path.resolve());first.acquire();first.release()
    inode=(first.directory/first.name).stat().st_ino
    second=ChannelLease("pf_query",first.directory)
    _,calls=intercept(monkeypatch,second,ordinary)
    second.acquire()
    try:
        assert len(calls)==2 and calls[0]&os.O_EXCL and calls[0]&os.O_CREAT
        assert not calls[1]&(os.O_CREAT|os.O_EXCL)
        assert calls[1]&os.O_NOFOLLOW and calls[1]&os.O_CLOEXEC and calls[1]&os.O_NONBLOCK
        assert(first.directory/first.name).stat().st_ino==inode
    finally:second.release()


def test_losing_first_creator_opens_winners_inode_and_still_obeys_actual_flock(monkeypatch,tmp_path):
    lease=ChannelLease("pf_query",tmp_path.resolve());winner=[];triggered=False
    def racing(original,path,flags,mode,dir_fd):
        nonlocal triggered
        if not triggered:
            triggered=True
            fd=original.open(path,flags,mode,dir_fd=dir_fd);winner.append(fd)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            raise FileExistsError
        return ordinary(original,path,flags,mode,dir_fd)
    original,calls=intercept(monkeypatch,lease,racing)
    try:
        with pytest.raises(LeaseError,match="channel_already_owned"):lease.acquire()
        assert not lease.held and len(calls)==2 and not calls[1]&os.O_CREAT
        inode=original.fstat(winner[0]).st_ino
        assert(lease.directory/lease.name).stat().st_ino==inode
    finally:
        for fd in winner:fcntl.flock(fd,fcntl.LOCK_UN);original.close(fd)
    lease.acquire()
    try:assert(lease.directory/lease.name).stat().st_ino==inode
    finally:lease.release()


def test_unexpected_exclusive_create_enoent_is_not_retried_or_reclassified(monkeypatch,tmp_path):
    lease=ChannelLease("pf_query",tmp_path.resolve());error=FileNotFoundError(2,"authored race")
    def failing(*args):raise error
    _,calls=intercept(monkeypatch,lease,failing)
    with pytest.raises(FileNotFoundError)as found:lease.acquire()
    assert found.value is error and len(calls)==1 and not lease.held
    assert not(lease.directory/lease.name).exists()


def test_entry_removed_between_eexist_and_existing_open_fails_without_recreation(monkeypatch,tmp_path):
    lease=ChannelLease("pf_query",tmp_path.resolve());path=lease.directory/lease.name
    path.write_bytes(b"");path.chmod(0o600)
    def removed(original,name,flags,mode,dir_fd):
        try:return ordinary(original,name,flags,mode,dir_fd)
        except FileExistsError:
            path.unlink()  # deliberate fixture-only namespace corruption
            raise
    _,calls=intercept(monkeypatch,lease,removed)
    with pytest.raises(FileNotFoundError):lease.acquire()
    assert len(calls)==2 and not calls[1]&os.O_CREAT and not path.exists()and not lease.held


def test_symlink_replacement_after_eexist_is_rejected_without_touching_target(monkeypatch,tmp_path):
    lease=ChannelLease("pf_query",tmp_path.resolve());path=lease.directory/lease.name
    path.write_bytes(b"");path.chmod(0o600)
    target=lease.directory/"protected-target";target.write_bytes(b"unchanged");target.chmod(0o600)
    def replaced(original,name,flags,mode,dir_fd):
        try:return ordinary(original,name,flags,mode,dir_fd)
        except FileExistsError:
            path.unlink();path.symlink_to(target)
            raise
    _,calls=intercept(monkeypatch,lease,replaced)
    with pytest.raises(OSError):lease.acquire()
    assert len(calls)==2 and calls[1]&os.O_NOFOLLOW and not lease.held
    assert target.read_bytes()==b"unchanged"and stat.S_IMODE(target.stat().st_mode)==0o600


@pytest.mark.parametrize("fault",["owner","mode","links","inode"])
def test_metadata_and_inode_guards_still_close_actual_fd_and_keep_published_entry(monkeypatch,tmp_path,fault):
    lease=ChannelLease("pf_query",tmp_path.resolve());original=module.os;opened=[]
    class Proxy:
        def __getattr__(self,name):return getattr(original,name)
        def open(self,path,flags,mode=0o777,*,dir_fd=None):
            fd=original.open(path,flags,mode,dir_fd=dir_fd)
            if path==lease.name:opened.append(fd)
            return fd
        def fstat(self,fd):
            value=original.fstat(fd)
            if not stat.S_ISREG(value.st_mode):return value
            return Obj(st_mode=value.st_mode|0o004 if fault=="mode"else value.st_mode,
                st_uid=value.st_uid+1 if fault=="owner"else value.st_uid,
                st_nlink=2 if fault=="links"else value.st_nlink,st_dev=value.st_dev,st_ino=value.st_ino)
        def stat(self,*args,**kwargs):
            value=original.stat(*args,**kwargs)
            return Obj(st_dev=value.st_dev,st_ino=value.st_ino+1,st_nlink=value.st_nlink)if fault=="inode"else value
    monkeypatch.setattr(module,"os",Proxy())
    with pytest.raises(LeaseError,match="inode_changed"if fault=="inode"else"file_unsafe"):lease.acquire()
    assert not lease.held and(lease.directory/lease.name).exists()
    for fd in opened:
        with pytest.raises(OSError):original.fstat(fd)
