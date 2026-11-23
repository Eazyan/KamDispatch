import os
from scripts.prune_traffic_spool import prune

def test_expired_png_only_and_no_symlink_escape(tmp_path):
    spool=tmp_path/'spool';spool.mkdir()
    outside=tmp_path/'outside.png';outside.write_bytes(b'keep')
    old=spool/'old.png';old.write_bytes(b'expired');os.utime(old,(100,100))
    fresh=spool/'fresh.png';fresh.write_bytes(b'fresh');os.utime(fresh,(11000,11000))
    meta=spool/'capture.json';meta.write_text('{}');os.utime(meta,(100,100))
    (spool/'link.png').symlink_to(outside)
    (spool/'external').symlink_to(tmp_path,target_is_directory=True)
    assert [x['path'] for x in prune(spool,now=11200)]==['old.png']
    assert fresh.exists() and meta.exists() and outside.exists()
    assert prune(spool,now=11200)==[]
    assert (spool/'retention.jsonl').exists()

def test_three_hour_boundary(tmp_path):
    p=tmp_path/'pair.png';p.write_bytes(b'x');os.utime(p,(100,100))
    assert prune(tmp_path,now=10899)==[]
    assert len(prune(tmp_path,now=10900))==1
