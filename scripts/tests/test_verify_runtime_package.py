"""Offline retention checks; no dependency download or package execution."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location("verify_runtime_package",Path(__file__).resolve().parents[1]/"verify-runtime-package.py")
VERIFY=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(VERIFY)


class RetainedWheelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.wheel=self.root/"actor_runtime-0.7.0-py3-none-any.whl"
        self.wheel.write_bytes(b"synthetic-wheel-bytes");self.digest=hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        self.output=self.root/"retained"

    def test_exact_verified_bytes_survive_temporary_source_and_repeat_is_idempotent(self):
        retained=VERIFY.export_wheel(self.wheel,self.output,self.digest)
        self.assertEqual(retained.read_bytes(),self.wheel.read_bytes())
        self.assertEqual(VERIFY.export_wheel(self.wheel,self.output,self.digest),retained)
        self.wheel.unlink();self.assertEqual(hashlib.sha256(retained.read_bytes()).hexdigest(),self.digest)
        self.assertEqual([p.name for p in self.output.iterdir()],[retained.name])

    def test_source_change_or_existing_different_artifact_cannot_be_published_or_replaced(self):
        with self.assertRaises(VERIFY.VerificationError):VERIFY.export_wheel(self.wheel,self.output,"0"*64)
        self.assertFalse(self.output.exists())
        self.output.mkdir();target=self.output/self.wheel.name;target.write_bytes(b"prior-release-proof")
        with self.assertRaises(VERIFY.VerificationError):VERIFY.export_wheel(self.wheel,self.output,self.digest)
        self.assertEqual(target.read_bytes(),b"prior-release-proof")

    def test_symlink_artifact_is_never_accepted_even_when_its_bytes_match(self):
        self.output.mkdir();target=self.output/self.wheel.name;target.symlink_to(self.wheel)
        with self.assertRaises(VERIFY.VerificationError):VERIFY.export_wheel(self.wheel,self.output,self.digest)
        self.assertTrue(target.is_symlink());self.assertTrue(self.wheel.exists())


if __name__=="__main__":unittest.main()
