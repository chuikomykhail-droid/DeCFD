"""Shape outline, computed exactly like the rasterizer in worker_cpp/main.cpp.

The body frame has the chord along x (s = 0 at the nose, s = L at the tail); thickness
points t0..t4 sit at 0, L/4, L/2, 3L/4, L with cosine interpolation between them, the
camber line is a parabola peaking at mid-chord, and the body is rotated by the angle of
attack about the chord midpoint (positive = nose up).
"""
import numpy as np


def thickness(s, L, t_pts):
    nseg = len(t_pts) - 1
    seg_len = L / nseg
    seg = np.minimum((s / seg_len).astype(int), nseg - 1)
    fr = (s - seg * seg_len) / seg_len
    mu = (1.0 - np.cos(fr * np.pi)) / 2.0
    t = np.asarray(t_pts)
    return t[seg] * (1.0 - mu) + t[seg + 1] * mu


def outline(L, t_pts, alpha, camber, n=200):
    """Closed polygon (x, y) in lattice cells relative to the chord midpoint, flow along +x."""
    s = np.linspace(0.0, L, n)
    half = thickness(s, L, t_pts) / 2.0
    yc = 4.0 * camber * (s / L) * (1.0 - s / L)
    xb = np.concatenate([s, s[::-1]]) - L / 2.0
    yb = np.concatenate([yc + half, (yc - half)[::-1]])
    a = np.radians(alpha)
    ca, sa = np.cos(a), np.sin(a)
    # Inverse of the solver's world -> body rotation
    return ca * xb + sa * yb, -sa * xb + ca * yb


def shape_outline(shape, n=200):
    return outline(shape["L"], shape["t_pts"], shape["alpha"], shape["camber"], n)
