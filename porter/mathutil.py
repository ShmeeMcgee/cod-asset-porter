"""Small quaternion / vector helpers. Quaternions are (x, y, z, w)."""
import math

IDENTITY_Q = (0.0, 0.0, 0.0, 1.0)


def q_normalize(q):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-12:
        return IDENTITY_Q
    return (x / n, y / n, z / n, w / n)


def q_mul(a, b):
    """a * b: rotation b applied first, then a."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def q_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def q_rotate(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


def q_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]


def q_slerp(a, b, t):
    d = q_dot(a, b)
    if d < 0.0:
        b = (-b[0], -b[1], -b[2], -b[3])
        d = -d
    if d > 0.9995:
        return q_normalize(tuple(a[i] + (b[i] - a[i]) * t for i in range(4)))
    theta0 = math.acos(min(1.0, d))
    sin0 = math.sin(theta0)
    s1 = math.sin(theta0 * t) / sin0
    s0 = math.sin(theta0 * (1.0 - t)) / sin0
    return q_normalize(tuple(s0 * a[i] + s1 * b[i] for i in range(4)))


def q_to_axes(q):
    """Bone axes (X, Y, Z) in parent/world space = matrix columns."""
    x, y, z, w = q_normalize(q)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    X = (1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy))
    Y = (2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx))
    Z = (2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy))
    # "+ 0.0" turns -0.0 into 0.0 so files don't fill up with "-0.000000"
    return tuple(tuple(round(c, 9) + 0.0 for c in axis) for axis in (X, Y, Z))


def axes_to_q(X, Y, Z):
    m00, m10, m20 = X
    m01, m11, m21 = Y
    m02, m12, m22 = Z
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = (( m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(max(1e-12, 1.0 + m00 - m11 - m22)) * 2
        q = (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    elif m11 > m22:
        s = math.sqrt(max(1e-12, 1.0 + m11 - m00 - m22)) * 2
        q = ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    else:
        s = math.sqrt(max(1e-12, 1.0 + m22 - m00 - m11)) * 2
        q = ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)
    return q_normalize(q)


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def v_normalize(v, fallback=(0.0, 0.0, 1.0)):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < 1e-12:
        return fallback
    return (v[0] / n, v[1] / n, v[2] / n)
