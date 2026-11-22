"""Conservative bed/air exchange contract for a future transient CFD adapter.

All inventories/transfers are kg, not concentration. The CFD adapter must integrate
face fluxes over dt and convert concentration using actual cell volumes. This
module does not solve transport, prescribe erosion physics, or clip CFD fields.
"""
from dataclasses import dataclass
import numpy as np


def nonnegative(value, name):
    result = np.asarray(value, dtype=float)
    if not np.isfinite(result).all() or (result < 0).any():
        raise ValueError(f'{name} must be finite and nonnegative')
    return result


@dataclass(frozen=True)
class Exchange:
    bed_kg: np.ndarray
    air_kg: np.ndarray
    erosion_kg: np.ndarray
    deposition_kg: np.ndarray


def exchange(bed_kg, air_kg, bed_index, air_index, erosion_kg, deposition_kg):
    """Limit simultaneous requested face transfers by each donor's old inventory.

    Multiple faces may share either donor; proportional scaling preserves their
    requested ratios. Newly deposited/eroded mass is available next step only.
    No input is modified. Indices map each face to a bed element and air cell.
    """
    bed = nonnegative(bed_kg, 'bed inventory')
    air = nonnegative(air_kg, 'air inventory')
    er = nonnegative(erosion_kg, 'erosion request')
    dep = nonnegative(deposition_kg, 'deposition request')
    bi, ai = np.asarray(bed_index), np.asarray(air_index)
    if any(a.ndim != 1 for a in (bed, air, er, dep, bi, ai)):
        raise ValueError('Inventories and face arrays must be one-dimensional')
    if not (bi.shape == ai.shape == er.shape == dep.shape):
        raise ValueError('Face arrays must have equal lengths')
    for indices, size in ((bi, bed.size), (ai, air.size)):
        if indices.dtype.kind not in 'iu' or (indices < 0).any() or (indices >= size).any():
            raise ValueError('Face mapping must contain valid integer indices')

    def limited(request, indices, inventory):
        total = np.bincount(indices, weights=request, minlength=inventory.size)
        if not np.isfinite(total).all():
            raise ValueError('Transfer sum overflow')
        scale = np.ones_like(total)
        np.divide(inventory, total, out=scale, where=total > inventory)
        # Reserve a few ulps when exhausting a donor so summation cannot
        # create a negative inventory; the remainder stays on the donor.
        scale[total >= inventory] *= 1 - 8 * np.finfo(float).eps
        return request * scale[indices]

    er = limited(er, bi, bed)
    dep = limited(dep, ai, air)
    next_bed = (bed - np.bincount(bi, weights=er, minlength=bed.size)) + np.bincount(bi, weights=dep, minlength=bed.size)
    next_air = (air - np.bincount(ai, weights=dep, minlength=air.size)) + np.bincount(ai, weights=er, minlength=air.size)
    # Roundoff is audited, never hidden by clipping.
    nonnegative(next_bed, 'resulting bed inventory')
    nonnegative(next_air, 'resulting air inventory')
    return Exchange(next_bed, next_air, er, dep)


def audit(*, bed_initial_kg, air_initial_kg, bed_final_kg, air_final_kg,
          boundary_in_kg=0., boundary_out_kg=0., explicit_loss_kg=0.,
          absolute_tolerance_kg=1e-8, relative_tolerance=1e-12):
    """Reject negative inventories and report signed final-minus-expected residual.

    Boundary and loss values are cumulative integrated positive kg. Internal
    erosion/deposition cancel and must never be counted as external sources.
    Passing this audit alone does not validate flux integration or CFD physics.
    """
    values = [bed_initial_kg, air_initial_kg, bed_final_kg, air_final_kg,
              boundary_in_kg, boundary_out_kg, explicit_loss_kg,
              absolute_tolerance_kg, relative_tolerance]
    if any(np.asarray(v).ndim != 0 for v in values):
        raise ValueError('Ledger values must be scalar totals in kg')
    values = nonnegative(values, 'ledger totals and tolerances')
    b0, a0, b1, a1, incoming, outgoing, loss, atol, rtol = values
    available = b0 + a0 + incoming
    accounted = b1 + a1 + outgoing + loss
    if not np.isfinite([available, accounted]).all():
        raise ValueError('Ledger sum overflow')
    residual = accounted - available
    tolerance = atol + rtol * available
    return dict(bed_initial_kg=float(b0), air_initial_kg=float(a0),
                bed_final_kg=float(b1), air_final_kg=float(a1),
                boundary_in_kg=float(incoming), boundary_out_kg=float(outgoing),
                explicit_loss_kg=float(loss), residual_kg=float(residual),
                tolerance_kg=float(tolerance), passed=bool(abs(residual) <= tolerance))
