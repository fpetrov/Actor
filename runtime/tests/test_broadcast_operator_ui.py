"""Use packaged production UI assets with an auth-free synthetic JS host."""
from importlib.resources import as_file,files
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_shipped_operator_controls_status_and_dirty_form_survive_real_script_async_boundaries():
    node=shutil.which('node')
    if node is None:pytest.skip('Node is optional for the operator synthetic DOM check')
    resources=files('actor_broadcast.resources')
    with as_file(resources.joinpath('operator.js')) as script,as_file(resources.joinpath('operator.html')) as html:
        result=subprocess.run([node,str(Path(__file__).with_name('broadcast_operator.cjs')),str(script),str(html)],
            capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'passed':True,'checks':6,
        'scope':'production_operator_synthetic_dom_no_browser_network'}
