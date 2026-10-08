import pathlib
import sys

import verify2_transport_repros as harness

import pytest

root = pathlib.Path(harness.SNAPSHOT)
result = pytest.main([
    '-q', '-p', 'no:cacheprovider',
    '--basetemp=' + str(root.parent / 'pytest-verify2-complete'),
    str(root / 'tests/test_trader_ctf_merge.py'),
    str(root / 'tests/test_trader_pair_merge.py'),
    str(root / 'tests/test_trader_dust_sweep.py'),
])
print('Forbidden external connections attempted:', len(harness.blocked))
assert not harness.blocked
sys.exit(result)
