import os
import stat
from pathlib import Path
import pytest

from actor_broadcast.ownership import ChannelLease, LeaseError


def test_same_channel_is_exclusive_until_explicit_release_and_inode_persists(tmp_path):
    folder = tmp_path.resolve() / 'leases'
    first = ChannelLease('fedorr_codex', folder); second = ChannelLease('fedorr_codex', folder)
    first.acquire(); inode = (folder / first.name).stat().st_ino
    assert first.acquire() is first and first.held
    with pytest.raises(LeaseError, match='already_owned'): second.acquire()
    assert first.held and not second.held
    first.release(); first.release()
    assert (folder / first.name).stat().st_ino == inode
    with second:
        assert second.held
        assert stat.S_IMODE((folder / second.name).stat().st_mode) == 0o600
        assert (folder / second.name).stat().st_ino == inode
    assert not second.held


def test_distinct_channels_are_independent_and_exception_cleanup_releases(tmp_path):
    folder = tmp_path.resolve() / 'leases'
    a = ChannelLease('channel_one', folder); b = ChannelLease('channel_two', folder)
    with pytest.raises(RuntimeError):
        with a, b:
            assert a.held and b.held
            raise RuntimeError('owned cleanup')
    assert not a.held and not b.held
    with ChannelLease('channel_one', folder): pass


def test_no_symlink_or_hardlink_file_is_accepted(tmp_path):
    folder = tmp_path.resolve(); lease = ChannelLease('fedorr_codex', folder)
    target = folder / 'target'; target.write_bytes(b'unchanged'); target.chmod(0o600)
    path = folder / lease.name; path.symlink_to(target)
    with pytest.raises(OSError): lease.acquire()
    assert target.read_bytes() == b'unchanged'
    path.unlink(); os.link(target, path)
    with pytest.raises(LeaseError, match='file_unsafe'): lease.acquire()
    assert target.read_bytes() == b'unchanged'


def test_nonregular_lockfile_is_rejected_without_blocking(tmp_path):
    folder = tmp_path.resolve(); lease = ChannelLease('fedorr_codex', folder)
    os.mkfifo(folder / lease.name, 0o600)
    with pytest.raises(LeaseError, match='file_unsafe'): lease.acquire()
    assert not lease.held


def test_insecure_directory_and_existing_file_permissions_are_rejected(tmp_path):
    folder = tmp_path.resolve(); folder.chmod(0o755)
    with pytest.raises(LeaseError, match='directory_unsafe'): ChannelLease('fedorr_codex', folder).acquire()
    folder.chmod(0o700); lease = ChannelLease('fedorr_codex', folder)
    path = folder / lease.name; path.write_bytes(b''); path.chmod(0o644)
    with pytest.raises(LeaseError, match='file_unsafe'): lease.acquire()
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_symlink_ancestor_cannot_create_a_child_in_its_target(tmp_path):
    folder = tmp_path.resolve(); outside = folder / 'outside'; outside.mkdir(mode=0o700)
    alias = folder / 'alias'; alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError): ChannelLease('fedorr_codex', alias / 'must-not-create').acquire()
    assert not (outside / 'must-not-create').exists()


@pytest.mark.parametrize('channel', ['', 'UPPER', '../escape', 'x', 'a'*26, 'white space', None])
def test_channel_is_a_canonical_bounded_identity(channel, tmp_path):
    with pytest.raises(ValueError): ChannelLease(channel, tmp_path)
