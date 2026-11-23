"""Check pilot integrity offline; accuracy still needs independent review."""
from datetime import datetime
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageStat, ImageChops
from scripts.capture_traffic import digest, write_json
from monitoring.traffic_image import calibration

def verify(root):
    run=json.loads((root/'run.json').read_text())
    if run['status']!='captured_pending_review' or len(run['tiles'])!=3:
        raise ValueError('Expected three completed captures')
    pairs=[]
    for tile in run['tiles']:
        directory=root/tile['id'];meta=json.loads((directory/'capture.json').read_text())
        if set(meta['image_sha256'])!={'traffic-off.png','traffic-on.png'}:
            raise ValueError('Incomplete pair')
        for name,expected in meta['image_sha256'].items():
            if digest(directory/name)!=expected:raise ValueError('PNG hash mismatch')
        _,_,error=calibration(meta)
        gap=(datetime.fromisoformat(meta['captured_at'])-datetime.fromisoformat(meta['reference_captured_at'])).total_seconds()
        if not 0<=gap<=20:raise ValueError('Pair time gap exceeds pilot gate')
        with Image.open(directory/'traffic-off.png') as off,Image.open(directory/'traffic-on.png') as on:
            if list(off.size)!=meta['viewport'] or on.size!=off.size:raise ValueError('Wrong image dimensions')
            x,y,w,h=meta['analysis_region'];box=(x,y,x+w,y+h)
            deviation=ImageStat.Stat(on.crop(box).convert('L')).stddev[0]
            difference=ImageStat.Stat(ImageChops.difference(off.convert('RGB'),on.convert('RGB')).crop(box)).mean
            if deviation<5 or max(difference)<.01:raise ValueError('Blank or unchanged useful region')
        pairs.append(dict(id=tile['id'],holdout_error_px=error,pair_gap_s=gap,duration_s=tile['duration_s']))
    mean=sum(t['duration_s'] for t in run['tiles'])/3
    return dict(integrity='passed',accuracy='not_verified',visual_review='not_verified',pairs=pairs,
                duration_s=run['duration_s'],bytes=run['bytes'],mean_pair_s=mean,
                sequential_31_tile_estimate_s=31*mean,estimate='linear planning only; not a measured city run')

if __name__=='__main__':
    root=Path(sys.argv[1]);report=verify(root);write_json(root/'integrity.json',report);print(json.dumps(report,indent=2))
