"""Check saved execution evidence; never implies field accuracy or snow mass closure."""
import json
from pathlib import Path
import re

root=Path(__file__).resolve().parent
for name in ('log.blockMesh','log.moveDynamicMesh','log.driftScalarDyFoam'):
    text=(root/name).read_text()
    assert 'FOAM FATAL' not in text,name
text=(root/'log.driftScalarDyFoam').read_text()
assert 'Stage = 0' in text and 'Stage = 1' in text
assert 'writing cell-centres field C to 10000' in text
assert not re.search(r'\b(?:nan|inf)\b',text,re.IGNORECASE)
hashes=json.loads((root/'tutorial-output-hashes.json').read_text())['mesh_points_sha256']
assert len(set(hashes.values()))==3, 'Expected evolving snow mesh'
for mode in ('smoke','coarse'):
    folder=root.parent/'cfd/results'/f'dec-snow-{mode}'
    meta=json.loads((folder/'summary.json').read_text())
    assert abs(meta['continuity_global_last'])<1e-5
    assert meta['cells']>0
    assert 'FOAM FATAL' not in (folder/'simpleFoam.log').read_text()
print('Saved RANS and driftScalar execution evidence OK; snow mass and field validation NOT checked')
