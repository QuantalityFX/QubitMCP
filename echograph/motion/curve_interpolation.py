"""Shared time/value tangents for the curve editor and animation playback.

Legacy vectors specify Hermite slopes. A handle's optional ``weighted`` list
marks the sides edited as actual Bezier control points. Keeping this per side
preserves the opposite segment when an untied handle is edited.
"""


def weighted_sides(handle):
    raw = (handle or {}).get('weighted',())
    return [side for side in ('in','out') if side in raw] if isinstance(raw,(list,tuple)) else []


def curve_controls(start,end,value0,value1,handle0=None,handle1=None):
    """Keep legacy slopes; edited weighted sides use their actual control point."""
    span = end-start
    delta = value1-value0
    points = []
    for handle,side,time,value,sign in ((handle0,'out',start,value0,1),(handle1,'in',end,value1,-1)):
        vector = (handle or {}).get(side)
        if vector is None:
            dx,dy = sign*span/3.,sign*delta/3.
        elif side in weighted_sides(handle) or vector[0] == 0:
            dx = sign*max(0.,sign*vector[0])
            # Editor handles can overlap and extend beyond the next key.
            # Shorten only the segment's effective time weight when needed,
            # preserving its slope and the complete authored handle vector.
            # Control times inside the interval stay monotone even if they
            # cross each other, so playback has exactly one value per time.
            scale = min(1.,span/abs(dx)) if dx else 1.
            dx,dy = dx*scale,vector[1]*scale
        else:
            dx = sign*span/3.
            dy = dx*vector[1]/vector[0]
        points.append((time+dx,value+dy))
    return points


def curve_value(start,end,value0,value1,time,handle0=None,handle1=None):
    """Evaluate a monotone-time cubic, including exactly vertical tangents."""
    if time <= start or end <= start:
        return float(value0)
    if time >= end:
        return float(value1)
    span = end-start
    alpha = (time-start)/span
    handles = (handle0,handle1)
    if not any(handles) or any(handle and handle.get('mode') == 'straight' for handle in handles):
        return (1-alpha)*value0+alpha*value1
    (x0,y0),(x1,y1) = curve_controls(start,end,value0,value1,*handles)
    # Normalize time before inverting the Bezier, independent of the frame rate.
    a,b = (x0-start)/span,(x1-start)/span
    # A centered polynomial avoids cancellation when crossed handles make
    # the time derivative vanish near the middle of the interval.
    midpoint = .125+.375*(a+b)
    slope = .75*(1.-a+b)
    quadratic = 1.5*(1.-a-b)
    cubic = 1.+3.*(a-b)
    lo,hi,t = 0.,1.,alpha
    for _ in range(40):
        u = t-.5
        error = (midpoint-alpha)+u*(slope+u*(quadratic+u*cubic))
        derivative = slope+u*(2.*quadratic+3.*u*cubic)
        if abs(error) <= 1e-13*max(1e-12,derivative):
            break
        if error > 0:
            hi = t
        else:
            lo = t
        candidate = t-error/derivative if derivative > 1e-14 else -1.
        t = candidate if lo < candidate < hi else (lo+hi)*.5
    s = 1.-t
    return s*s*s*value0+3*s*s*t*y0+3*s*t*t*y1+t*t*t*value1
