"""Offline exact-grid adapter: saved PKC OpenFOAM wind -> conservative saltation.

Local spherical east/north metres, NOT UTM. No interpolation or wind rotation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from .drift_engine import run


def load_scene(root):
    root=Path(root)
    paths=[root/'sources/quarter.json',root/'results/dec-snow-coarse/wind.npz',
           root/'results/dec-snow-coarse/summary.json']
    spec=json.loads(paths[0].read_text()); summary=json.loads(paths[2].read_text())
    with np.load(paths[1],allow_pickle=False) as data:
        x,y=data['x'],data['y']; dx,dy=float(np.diff(x)[0]),float(np.diff(y)[0])
        if not (np.allclose(np.diff(x),dx) and np.allclose(np.diff(y),dy)):
            raise ValueError('Uniform CFD grid required')
        ix=np.flatnonzero((x>=0)&(x<spec['size_m'][0]))
        iy=np.flatnonzero((y>=0)&(y<spec['size_m'][1]))
        select=np.ix_(iy,ix)
        ground=data['ground'][select][::-1].copy(); roof=data['roof'][select][::-1]
        u=data['U'][0][select][::-1].astype(float)
        mask=roof>ground
        if not np.isfinite(u[~mask]).all():
            raise ValueError('Missing CFD air velocity on open ground')
        u[mask]=0
        height=(float(data['top'])-ground)*float(np.mean(data['z_edges'][:2]))
        vector=np.stack((u[...,0],-u[...,1]),axis=-1)
    provenance=dict(solver='OpenFOAM-v2512-simpleFoam',iterations=summary['iterations'],
        source_sha256={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        coordinates='local spherical east/north metres; not UTM',
        origin_wgs84=spec['bounds_wgs84'][:2],projection_m_per_degree=spec['projection_m_per_degree'],
        bounds_local_m=[float(x[ix[0]]-dx/2),float(y[iy[0]]-dy/2),
                        float(x[ix[-1]]+dx/2),float(y[iy[-1]]+dy/2)],
        axes='columns east, rows south; Ux east, -Uy south; m/s',
        sampling='first terrain-following cell centre; variable height AGL, no roof fallback',
        height_agl_range_m=[float(height[~mask].min()),float(height[~mask].max())],
        weather=summary['weather'],static=True)
    return ground,mask,dx,dy,dict(vector_east_south_m_s=vector,height_agl_m=height,provenance=provenance)


def reproduce(root,seconds=600.):
    z,mask,dx,dy,field=load_scene(root); weather=field['provenance']['weather']
    args=dict(dx_m=dx,dy_m=dy,buildings=mask,seconds=seconds,
              wind_speed_m_s=weather['wind_speed_ms'],wind_from_deg=weather['wind_from_deg'])
    baseline=run(z,5.,**args); coupled=run(z,5.,wind_field=field,**args)
    speed=np.linalg.norm(field['vector_east_south_m_s'],axis=-1)
    a=np.deg2rad(weather['wind_from_deg'])
    aligned={**field,'vector_east_south_m_s':np.stack((-speed*np.sin(a),speed*np.cos(a)),axis=-1),
             'provenance':{**field['provenance'],'control':'local speeds aligned to inlet direction'}}
    control=run(z,5.,wind_field=aligned,**args)
    difference=lambda other:float(np.sum(np.abs(np.array(coupled['mass_kg_m2'])-
                                               np.array(other['mass_kg_m2'])))*dx*dy)
    return dict(provenance=field['provenance'],baseline=baseline,coupled=coupled,
        aligned_control=control,buildings_mask=mask.tolist(),
        difference_l1_kg=difference(baseline),turning_difference_l1_kg=difference(control))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[2]/'docs/drift-engine/cfd')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); result=reproduce(args.source)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ('difference_l1_kg','turning_difference_l1_kg')}))
    print(json.dumps(result['coupled']['balance']))
