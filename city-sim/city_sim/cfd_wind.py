"""Offline exact-grid adapter: saved PKC OpenFOAM wind -> conservative saltation.

Local spherical east/north metres, NOT UTM. No interpolation or wind rotation.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from .drift_engine import run


def load_scene(root):
    from .drift_scene import load_quarter
    scene=load_quarter(root)
    layers,meta=scene.layers,scene.metadata
    field=dict(vector_east_south_m_s=layers['wind_east_south_m_s'],
               height_agl_m=layers['wind_height_agl_m'],
               provenance={**meta,'scene_sha256':scene.sha256})
    return layers['ground_estimate_m'],layers['buildings_mask'],meta['dx_m'],meta['dy_m'],field


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
