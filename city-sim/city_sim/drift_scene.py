"""Versioned exact-grid PKC scene shared by reduced transport and CFD preparation.

Never substitute the raw DSM for estimated ground under explicit buildings.
"""
from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

VERSION='drift-scene-1'


def bilinear(x,y,values,target_x,target_y):
    if (np.diff(x)<=0).any() or (np.diff(y)<=0).any():
        raise ValueError('Source coordinates must increase')
    if target_x.min()<x[0] or target_x.max()>x[-1] or target_y.min()<y[0] or target_y.max()>y[-1]:
        raise ValueError('Scene extends beyond source; extrapolation forbidden')
    xx,yy=np.meshgrid(target_x,target_y)
    i=np.clip(np.searchsorted(x,xx)-1,0,len(x)-2)
    j=np.clip(np.searchsorted(y,yy)-1,0,len(y)-2)
    u=(xx-x[i])/(x[i+1]-x[i]);v=(yy-y[j])/(y[j+1]-y[j])
    return (1-v)*((1-u)*values[j,i]+u*values[j,i+1])+v*((1-u)*values[j+1,i]+u*values[j+1,i+1])


@dataclass
class DriftScene:
    layers: dict
    metadata: dict

    @property
    def sha256(self):
        h=hashlib.sha256(json.dumps(self.metadata,sort_keys=True,allow_nan=False).encode())
        for name,array in sorted(self.layers.items()):
            a=np.asarray(array)
            h.update(json.dumps([name,list(a.shape),str(a.dtype)]).encode())
            h.update(a.astype(a.dtype.newbyteorder('<')).tobytes())
        return h.hexdigest()

    @classmethod
    def read(cls,path):
        path=Path(path)
        manifest=json.loads((path/'scene.json').read_text())
        expected=manifest.pop('scene_sha256')
        archive_hash=manifest.pop('layers_archive_sha256')
        if hashlib.sha256((path/'layers.npz').read_bytes()).hexdigest()!=archive_hash:
            raise ValueError('Scene layer archive hash mismatch')
        with np.load(path/'layers.npz',allow_pickle=False) as data:
            result=cls({k:data[k].copy() for k in data.files},manifest)
        if result.metadata['version']!=VERSION or result.sha256!=expected:
            raise ValueError('Unsupported or modified scene manifest')
        return result

    def export(self,path):
        path=Path(path);path.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(path/'layers.npz',**self.layers)
        manifest={**self.metadata,'scene_sha256':self.sha256,
                  'layers_archive_sha256':hashlib.sha256((path/'layers.npz').read_bytes()).hexdigest()}
        (path/'scene.json').write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n')
        return manifest


def load_quarter(root,loose_kg_m2=5.,density_kg_m3=200.):
    if not np.isfinite([loose_kg_m2,density_kg_m3]).all() or loose_kg_m2<0 or density_kg_m3<=0:
        raise ValueError('Finite nonnegative snow inventory and positive density required')
    root=Path(root)
    paths=[root/'sources/quarter.json',root/'sources/ground.npz',
           root/'results/dec-snow-coarse/wind.npz',root/'results/dec-snow-coarse/summary.json']
    spec=json.loads(paths[0].read_text()); summary=json.loads(paths[3].read_text())
    with np.load(paths[2],allow_pickle=False) as data:
        x,y=data['x'],data['y'];dx,dy=float(np.diff(x)[0]),float(np.diff(y)[0])
        if dx<=0 or dy<=0 or not (np.allclose(np.diff(x),dx) and np.allclose(np.diff(y),dy)):
            raise ValueError('Uniform increasing CFD grid required')
        ix=np.flatnonzero((x>=0)&(x<spec['size_m'][0]));iy=np.flatnonzero((y>=0)&(y<spec['size_m'][1]))
        select=np.ix_(iy,ix); ground=data['ground'][select][::-1].copy()
        roof=data['roof'][select][::-1];mask=roof>ground
        vector=data['U'][0][select][::-1].astype(float)
        if not np.isfinite(vector[~mask]).all():raise ValueError('Missing open-ground wind')
        vector[mask]=0
        height=(float(data['top'])-ground)*float(np.mean(data['z_edges'][:2]))
    with np.load(paths[1],allow_pickle=False) as raw:
        dsm=bilinear(raw['x'],raw['y'],raw['dsm'],x[ix],y[iy])[::-1]
    mass=np.where(mask,0.,loose_kg_m2)
    layers=dict(ground_estimate_m=ground,dsm_egm2008_m=dsm,buildings_mask=mask,
        building_height_m=np.where(mask,roof-ground,0.),
        loose_kg_m2=mass,bound_kg_m2=np.zeros_like(ground),
        snow_surface_m=ground+mass/density_kg_m3,
        obstacle_surface_m=np.maximum(ground+mass/density_kg_m3,np.where(mask,roof,ground)),
        wind_east_south_m_s=np.stack((vector[...,0],-vector[...,1]),axis=-1),
        wind_height_agl_m=height)
    if not all(np.isfinite(a).all() for a in layers.values()):raise ValueError('Missing scene layer data')
    metadata=dict(version=VERSION,shape=list(ground.shape),dx_m=dx,dy_m=dy,
        coordinates='local spherical east/north metres; not UTM',
        origin_wgs84=spec['bounds_wgs84'][:2],projection_m_per_degree=spec['projection_m_per_degree'],
        bounds_local_m=[float(x[ix[0]]-dx/2),float(y[iy[0]]-dy/2),float(x[ix[-1]]+dx/2),float(y[iy[-1]]+dy/2)],
        axes='columns east, rows south; Ux east, -Uy south; m/s',
        datum_egm2008_m=spec['datum_egm2008_m'],
        measured_dtm=None,observations=[],snow_density_kg_m3=density_kg_m3,
        solver='OpenFOAM-v2512-simpleFoam',iterations=summary['iterations'],weather=summary['weather'],static=True,
        sampling='first terrain-following cell centre; variable height AGL, no roof fallback',
        height_agl_range_m=[float(height[~mask].min()),float(height[~mask].max())],
        source_sha256={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        layer_status=dict(ground='DSM footprint-mask fill and smoothing; estimated, not surveyed DTM',
            dsm='saved source interpolated for reference only; not added to buildings',
            buildings='same voxel roof mask as saved RANS; heights partly estimated',
            snow='scenario inventory, not observation; no roof snow',observations='unknown'),
        source_licenses='See docs/drift-engine/DATA_SOURCES.md; no new data fetched',
        evidence='research_unvalidated')
    return DriftScene(layers,metadata)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[2]/'docs/drift-engine/cfd')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--loose-kg-m2',type=float,default=5.)
    args=parser.parse_args()
    scene=load_quarter(args.source,loose_kg_m2=args.loose_kg_m2)
    manifest=scene.export(args.output)
    ledger=dict(scene_sha256=scene.sha256,bed_initial_kg=float(scene.layers['loose_kg_m2'].sum()*
                scene.metadata['dx_m']*scene.metadata['dy_m']),air_initial_kg=0.,
                boundary_in_kg=0.,boundary_out_kg=0.,explicit_loss_kg=0.,
                status='initialization_only_NOT_CFD_snow_budget',roof_snow='not_modelled')
    (args.output/'initial-ledger.json').write_text(json.dumps(ledger,indent=2)+'\n')
    print(json.dumps(ledger))
