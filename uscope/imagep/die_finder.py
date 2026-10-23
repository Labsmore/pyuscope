"""
Die (chip) edge / corner finding for automatic panorama setup
Pure numpy / OpenCV: no microscope access (see uscope/script/die_setup.py)

Coordinate conventions
-Image: pixels, x right, y down
-Stage: mm, x right, y up
    Image up is stage +y and image right is stage +x
    (same assumption as the planner: ll origin, row 0 at the top)

Pipeline
-DieClassifier: per block die vs background classifier (Fisher LDA on simple
    color / texture features), trained from the first view where the die is
    known to be in the upper right and the background in the lower left
-clean_mask(): morphological cleanup, single die blob, holes filled
-edge_samples() / fit_line() / find_corner(): die edges and corners in pixels
-Observation: the above in stage coordinates
-find_die(): walk the die edges given an observe(center) callback
-xy3p_corners(): die corners + border => XY3P "Set" button positions
"""

import math

import cv2
import numpy as np


class DieFindError(Exception):
    pass


# Die sides adjacent to each corner
CORNER_SIDES = {
    "ll": ("bottom", "left"),
    "lr": ("bottom", "right"),
    "ul": ("top", "left"),
    "ur": ("top", "right"),
}

# Downscale large images to about this width before analysis
ANALYSIS_WIDTH = 1024
# Analysis blocks per image width
BLOCKS_WIDTH = 64
"""
Image classification
"""


def image_to_array(im):
    """
    PIL image or numpy array => float32 HxWxC array scaled to 0 to 1
    """
    a = np.asarray(im)
    if a.dtype == np.uint16:
        scale = 65535.0
    elif a.dtype == np.uint8:
        scale = 255.0
    else:
        scale = 1.0
    a = a.astype(np.float32) / scale
    if a.ndim == 2:
        a = a[:, :, None]
    # Drop alpha
    return a[:, :, :3]


class BlockImage:
    """
    An image reduced to analysis blocks
    px_per_block: original image pixels per block
    """
    def __init__(self, im, blocks_width=BLOCKS_WIDTH):
        a = image_to_array(im)
        self.h, self.w = a.shape[:2]
        scale = min(1.0, ANALYSIS_WIDTH / self.w)
        if scale < 1.0:
            a = cv2.resize(a, (max(1, int(round(self.w * scale))),
                               max(1, int(round(self.h * scale)))),
                           interpolation=cv2.INTER_AREA)
            if a.ndim == 2:
                a = a[:, :, None]
        ah, aw = a.shape[:2]
        block = max(2, aw // blocks_width)
        self.rows = ah // block
        self.cols = aw // block
        self.px_per_block = block * self.w / aw
        self.features = self._features(a[:self.rows * block, :self.cols *
                                         block], block)

    def _features(self, a, block):
        rows, cols = self.rows, self.cols

        def bmean(x):
            return x.reshape(rows, block, cols, block).mean(axis=(1, 3))

        gray = a.mean(axis=2)
        gx = np.abs(np.diff(gray, axis=1, append=gray[:, -1:]))
        gy = np.abs(np.diff(gray, axis=0, append=gray[-1:, :]))
        feats = [bmean(a[:, :, c]) for c in range(a.shape[2])]
        mean = bmean(gray)
        feats.append(np.sqrt(np.maximum(bmean(gray * gray) - mean * mean,
                                        0.0)))
        feats.append(bmean(gx + gy))
        return np.stack(feats, axis=2).astype(np.float64)

    def region(self, x0, y0, x1, y1):
        """
        Features in a region given as fractions of the image size
        Returns (N, F)
        """
        r0 = int(math.floor(y0 * self.rows))
        r1 = max(r0 + 1, int(math.ceil(y1 * self.rows)))
        c0 = int(math.floor(x0 * self.cols))
        c1 = max(c0 + 1, int(math.ceil(x1 * self.cols)))
        f = self.features[r0:r1, c0:c1]
        return f.reshape(-1, f.shape[2])


class DieClassifier:
    """
    Two class Fisher linear discriminant on BlockImage features
    """
    # Fisher separation (d') below which die and background are too similar
    MIN_SEPARATION = 2.0

    def __init__(self):
        self.w = None
        self.threshold = None
        self.separation = None

    def fit(self, die, background):
        die = np.asarray(die, dtype=np.float64)
        background = np.asarray(background, dtype=np.float64)
        if len(die) < 2 or len(background) < 2:
            raise DieFindError("Not enough training samples")
        allf = np.concatenate((die, background))
        # Standardize so regularization is scale independent
        self.offset = allf.mean(axis=0)
        self.scale = allf.std(axis=0) + 1e-6
        d = (die - self.offset) / self.scale
        b = (background - self.offset) / self.scale
        cov = np.cov(d, rowvar=False) + np.cov(b, rowvar=False)
        cov = np.atleast_2d(cov) + 1e-3 * np.eye(d.shape[1])
        self.w = np.linalg.solve(cov, d.mean(axis=0) - b.mean(axis=0))
        pd = d @ self.w
        pb = b @ self.w
        sd = pd.std()
        sb = pb.std()
        # Split the means proportional to spread (equal z-score from each side)
        self.threshold = (pd.mean() * sb + pb.mean() * sd) / (sd + sb + 1e-12)
        self.separation = (pd.mean() - pb.mean()) / math.sqrt(
            (sd * sd + sb * sb) / 2 + 1e-12)
        return self.separation

    def score(self, bim):
        """
        Per block score: > 0 => die
        """
        f = (bim.features - self.offset) / self.scale
        return f @ self.w - self.threshold

    def classify(self, bim):
        return self.score(bim) > 0


def train_classifier(bim, region_frac=0.2, refine=True):
    """
    Train from a view at the die lower left corner:
    die in the upper right, background in the lower left

    refine: retrain once using the full (cleaned) classification
        away from the die boundary
    """
    f = region_frac
    clf = DieClassifier()
    clf.fit(die=bim.region(1.0 - f, 0.0, 1.0, f),
            background=bim.region(0.0, 1.0 - f, f, 1.0))
    if refine:
        mask = clean_mask(clf.classify(bim))
        kernel = np.ones((3, 3), np.uint8)
        die = cv2.erode(mask.astype(np.uint8), kernel, iterations=2) > 0
        bg = cv2.erode((~mask).astype(np.uint8), kernel, iterations=2) > 0
        if die.sum() >= 4 and bg.sum() >= 4:
            feats = bim.features
            clf2 = DieClassifier()
            clf2.fit(die=feats[die], background=feats[bg])
            # The refined model should reproduce the seed regions
            if clf2.separation > clf.separation / 2:
                clf = clf2
    return clf


def clean_mask(mask):
    """
    Boolean die mask => single die blob with holes filled
    """
    m = np.asarray(mask, dtype=np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
    n, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        m, connectivity=4)
    if n <= 1:
        return np.zeros(m.shape, dtype=bool)
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    die = labels == biggest
    # Fill background not connected to the image border (ex: dark pads)
    n, labels = cv2.connectedComponents((~die).astype(np.uint8),
                                        connectivity=4)
    border = set(labels[0, :]) | set(labels[-1, :]) | set(
        labels[:, 0]) | set(labels[:, -1])
    for label in range(1, n):
        if label not in border:
            die[labels == label] = True
    return die


"""
Edges
"""


def _canonical(mask, side):
    """
    View of mask where side is a bottom edge: die above, background below
    """
    if side == "bottom":
        return mask
    if side == "top":
        return mask[::-1, :]
    if side == "right":
        return mask.T
    if side == "left":
        return mask.T[::-1, :]
    raise ValueError(side)


def edge_samples(mask, side, window=None):
    """
    Return (N, 2) array of (x, y) die edge sample points in block units
    One sample per column (top / bottom) or row (left / right) where a clean
    die to background step is found
    Points are on the boundary between blocks
    """
    rows0, cols0 = mask.shape
    m = _canonical(np.asarray(mask, dtype=np.float64), side)
    rows, cols = m.shape
    if window is None:
        window = max(2, rows // 8)
    k = window
    if rows < 2 * k:
        return np.zeros((0, 2))
    cs = np.vstack((np.zeros((1, cols)), np.cumsum(m, axis=0)))
    # Candidate boundaries r in [k, rows - k]
    rs = np.arange(k, rows - k + 1)
    above = (cs[rs] - cs[rs - k]) / k
    below = (cs[rs + k] - cs[rs]) / k
    score = above - below
    valid = (above >= 0.8) & (below <= 0.2)
    score = np.where(valid, score, -np.inf)
    best = np.argmax(score, axis=0)
    ok = np.isfinite(score[best, np.arange(cols)])
    cs_ = np.nonzero(ok)[0]
    rs_ = rs[best[ok]]
    if side == "bottom":
        xs, ys = cs_ + 0.5, rs_
    elif side == "top":
        xs, ys = cs_ + 0.5, rows0 - rs_
    elif side == "right":
        xs, ys = rs_, cs_ + 0.5
    else:
        xs, ys = cols0 - rs_, cs_ + 0.5
    return np.stack((xs, ys), axis=1).astype(np.float64)


def _tls(pts):
    c = pts.mean(axis=0)
    _u, _s, vt = np.linalg.svd(pts - c)
    return c, vt[0]


def fit_line(pts, tol=1.5, min_points=2, max_pairs=2000):
    """
    Robust line fit: RANSAC over point pairs then total least squares on the inliers
    Edge samples next to a sloped perpendicular edge are high leverage outliers
    that defeat a simple iterative fit
    tol: inlier distance (same units as pts)
    Returns (point, unit direction, inlier points) or None
    """
    pts = np.asarray(pts, dtype=np.float64)
    n = len(pts)
    if n < min_points:
        return None
    if n < 3:
        c, d = _tls(pts)
        return c, d, pts
    i, j = np.triu_indices(n, k=1)
    if len(i) > max_pairs:
        rng = np.random.default_rng(0)
        pick = rng.choice(len(i), size=max_pairs, replace=False)
        i, j = i[pick], j[pick]
    d = pts[j] - pts[i]
    norm = np.linalg.norm(d, axis=1)
    good = norm > 0
    i, d, norm = i[good], d[good], norm[good]
    if not len(i):
        return None
    nrm = np.stack((-d[:, 1], d[:, 0]), axis=1) / norm[:, None]
    # (pairs, points) distances
    res = np.abs((pts[None, :, :] - pts[i][:, None, :]) * nrm[:, None, :]).sum(
        axis=2)
    counts = (res <= tol).sum(axis=1)
    inliers = pts[res[np.argmax(counts)] <= tol]
    if len(inliers) < min_points:
        return None
    c, d = _tls(inliers)
    # Refine the inlier set against the least squares line
    nn = np.array((-d[1], d[0]))
    keep = np.abs((pts - c) @ nn) <= tol
    if keep.sum() >= len(inliers):
        inliers = pts[keep]
        c, d = _tls(inliers)
    return c, d, inliers


def intersect_lines(p1, d1, p2, d2):
    """
    Intersection of lines p + t * d or None if (nearly) parallel
    """
    p1, d1, p2, d2 = [np.asarray(x, dtype=np.float64) for x in (p1, d1, p2, d2)]
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < 1e-9:
        return None
    dp = p2 - p1
    t = (dp[0] * d2[1] - dp[1] * d2[0]) / cross
    return p1 + t * d1


def _orient(d, ref):
    d = np.asarray(d, dtype=np.float64)
    return d if np.dot(d, ref) >= 0 else -d


# Image (x right, y down) direction a die edge runs in, by side
SIDE_DIRECTION = {
    "bottom": (1.0, 0.0),
    "top": (1.0, 0.0),
    "left": (0.0, -1.0),
    "right": (0.0, -1.0),
}


def find_edge(mask, side, px_per_block=1.0, min_samples=4, min_span=0.15):
    """
    Fit a die edge
    Returns (point, unit direction) in pixels or None
    Direction is oriented toward image right (top / bottom) or up (left / right)
    min_span: required sample extent as a fraction of the image dimension along the edge
    """
    pts = edge_samples(mask, side)
    if len(pts) < min_samples:
        return None
    fit = fit_line(pts)
    if fit is None:
        return None
    c, d, inliers = fit
    if len(inliers) < min_samples:
        return None
    d = _orient(d, SIDE_DIRECTION[side])
    # Edges are expected roughly axis aligned
    if abs(np.dot(d, SIDE_DIRECTION[side])) < math.cos(math.radians(30)):
        return None
    span = np.ptp(inliers @ d)
    extent = mask.shape[1] if side in ("top", "bottom") else mask.shape[0]
    if span < min_span * extent:
        return None
    return c * px_per_block, d


def find_corner(mask, corner, px_per_block=1.0, margin=0.05):
    """
    Return the die corner position in pixels or None if not visible
    margin: allow the corner this fraction of the image outside the frame
    """
    lines = []
    for side in CORNER_SIDES[corner]:
        line = find_edge(mask, side, px_per_block=px_per_block)
        if line is None:
            return None
        lines.append(line)
    (p1, d1), (p2, d2) = lines
    p = intersect_lines(p1, d1, p2, d2)
    if p is None:
        return None
    h = mask.shape[0] * px_per_block
    w = mask.shape[1] * px_per_block
    if not (-margin * w <= p[0] <= (1 + margin) * w and -margin * h <= p[1] <=
            (1 + margin) * h):
        return None
    return p


"""
Stage geometry
"""


class ViewGeometry:
    """
    Maps image pixels <=> stage mm for an image centered at center
    """
    def __init__(self, center, wh, mm_per_px):
        self.center = np.asarray(center, dtype=np.float64)
        self.w, self.h = wh
        self.mm_per_px = mm_per_px

    def px_to_stage(self, p):
        return np.array(
            (self.center[0] + (p[0] - self.w / 2) * self.mm_per_px,
             self.center[1] - (p[1] - self.h / 2) * self.mm_per_px))

    def stage_to_px(self, p):
        return np.array(
            (self.w / 2 + (p[0] - self.center[0]) / self.mm_per_px,
             self.h / 2 - (p[1] - self.center[1]) / self.mm_per_px))

    def dir_to_stage(self, d):
        return np.array((d[0], -d[1]))

    def view_wh(self):
        return self.w * self.mm_per_px, self.h * self.mm_per_px


class Observation:
    """
    Die analysis of one image in stage coordinates
    """
    def __init__(self, im, center, mm_per_px, classifier=None):
        """
        im: PIL image or numpy array
        center: stage (x, y) at the image center
        mm_per_px: of im
        classifier: None => train assuming the lower left die corner view
        """
        self.bim = BlockImage(im)
        self.view = ViewGeometry(center, (self.bim.w, self.bim.h), mm_per_px)
        if classifier is None:
            classifier = train_classifier(self.bim)
        self.classifier = classifier
        self.mask = clean_mask(classifier.classify(self.bim))

    def die_fraction(self):
        return float(self.mask.mean())

    def edge(self, side):
        """
        Returns (stage point, stage unit direction) or None
        Direction is +x for top / bottom and +y for left / right edges
        """
        line = find_edge(self.mask, side, px_per_block=self.bim.px_per_block)
        if line is None:
            return None
        p, d = line
        return self.view.px_to_stage(p), self.view.dir_to_stage(d)

    def corner(self, corner):
        p = find_corner(self.mask, corner, px_per_block=self.bim.px_per_block)
        if p is None:
            return None
        return self.view.px_to_stage(p)

    def die_bbox_corner(self, corner):
        """
        Stage position of a corner of the die mask bounding box or None if no die
        A rough corner estimate when the edges are too close to the frame to fit
        """
        rows, cols = np.nonzero(self.mask)
        if not len(rows):
            return None
        x = cols.min() if corner[1] == "l" else cols.max() + 1
        y = rows.max() + 1 if corner[0] == "l" else rows.min()
        return self.view.px_to_stage(
            np.array((x, y), dtype=np.float64) * self.bim.px_per_block)

    def mask_image(self):
        """
        Debug image: uint8 mask scaled up to about the original size
        """
        m = self.mask.astype(np.uint8) * 255
        scale = max(1, int(self.bim.px_per_block))
        return np.kron(m, np.ones((scale, scale), dtype=np.uint8))


def walk_to_corner(observe,
                   start,
                   walk_dir,
                   edge_side,
                   corner,
                   step,
                   max_distance,
                   max_turn=20.0,
                   log=None):
    """
    Follow the die edge_side edge from start (a stage point on the edge)
    in direction walk_dir until corner comes into view
    observe(center) => Observation
    step: mm per view
    Returns the corner stage position, measured from a view centered on it
    """
    walk_dir0 = np.asarray(walk_dir, dtype=np.float64)
    walk_dir0 = walk_dir0 / np.linalg.norm(walk_dir0)
    d = walk_dir0
    pos = np.asarray(start, dtype=np.float64)
    traveled = 0.0
    while traveled <= max_distance:
        center = pos + step * d
        obs = observe(center)
        c = obs.corner(corner)
        if c is not None:
            log and log("  %s corner in view near %0.4f, %0.4f" %
                        (corner, c[0], c[1]))
            refined = observe(c).corner(corner)
            if refined is None:
                log and log("  WARNING: lost %s corner while centering" %
                            corner)
                return c
            return refined
        edge = obs.edge(edge_side)
        if edge is None:
            raise DieFindError(
                "Lost the die %s edge near %0.4f, %0.4f (die fraction %0.2f)" %
                (edge_side, center[0], center[1], obs.die_fraction()))
        p, ed = edge
        ed = _orient(ed, walk_dir0)
        # Don't let a bad fit steer far off the original heading
        if math.degrees(math.acos(min(1.0, np.dot(ed, walk_dir0)))) <= max_turn:
            d = ed
        # Re-center on the edge to keep it in the middle of the view
        pos = p + np.dot(center - p, d) * d
        traveled += step
    raise DieFindError("No %s corner within %0.3f mm" % (corner, max_distance))


def find_die(observe, start, view_wh, step_frac=0.6, max_size=50.0, log=None):
    """
    Find the die lower left, lower right and upper left corners
    start: stage (x, y) with the die lower left corner in view
    view_wh: (x, y) mm field of view
    observe(center) => Observation (classifier already trained)
    Returns dict of corner name => stage (x, y) numpy array
    """
    obs = observe(start)
    c = obs.corner("ll")
    if c is None:
        # Corner too close to the frame edge? Center on the die extent and retry
        guess = obs.die_bbox_corner("ll")
        if guess is not None and obs.die_fraction() < 0.95:
            log and log("  ll corner not found, retrying near %0.4f, %0.4f" %
                        (guess[0], guess[1]))
            c = observe(guess).corner("ll")
    if c is None:
        raise DieFindError(
            "Die lower left corner not found in the start view (die fraction %0.2f)"
            % obs.die_fraction())
    obs = observe(c)
    ll = obs.corner("ll")
    if ll is None:
        raise DieFindError("Lost the lower left corner while centering")
    bottom = obs.edge("bottom")
    left = obs.edge("left")
    log and log("  ll corner at %0.4f, %0.4f" % (ll[0], ll[1]))

    xdir = bottom[1] if bottom else np.array((1.0, 0.0))
    ydir = left[1] if left else np.array((0.0, 1.0))
    log and log("Walking bottom edge to the lower right corner")
    lr = walk_to_corner(observe,
                        ll,
                        xdir,
                        "bottom",
                        "lr",
                        step=step_frac * view_wh[0],
                        max_distance=max_size,
                        log=log)
    log and log("  lr corner at %0.4f, %0.4f" % (lr[0], lr[1]))
    log and log("Walking left edge to the upper left corner")
    ul = walk_to_corner(observe,
                        ll,
                        ydir,
                        "left",
                        "ul",
                        step=step_frac * view_wh[1],
                        max_distance=max_size,
                        log=log)
    log and log("  ul corner at %0.4f, %0.4f" % (ul[0], ul[1]))
    return {"ll": ll, "lr": lr, "ul": ul}


def die_summary(corners):
    """
    Width, height (mm) and skew angles (degrees) of a die from ll, lr, ul
    rotation: bottom edge angle vs stage x
    skew: deviation of the ll corner from 90 degrees
    """
    ll, lr, ul = (np.asarray(corners[k]) for k in ("ll", "lr", "ul"))
    u = lr - ll
    v = ul - ll
    rotation = math.degrees(math.atan2(u[1], u[0]))
    cosang = np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v))
    skew = 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
    return {
        "width": float(np.linalg.norm(u)),
        "height": float(np.linalg.norm(v)),
        "rotation": rotation,
        "skew": skew,
    }


def predict_ur(corners):
    return np.asarray(corners["lr"]) + np.asarray(
        corners["ul"]) - np.asarray(corners["ll"])


def xy3p_corners(corners, view_wh, border):
    """
    die corners => XY3P corner positions
    These are view center positions as the Argus XY3P "Set" buttons record
    (the planner then extends each by half a view)

    corners: stage ll, lr, ul die corners
    view_wh: (x, y) mm field of view
    border: (x, y) mm to keep around the die, along the die edges
    """
    ll, lr, ul = (np.asarray(corners[k], dtype=np.float64)
                  for k in ("ll", "lr", "ul"))
    u = (lr - ll) / np.linalg.norm(lr - ll)
    v = (ul - ll) / np.linalg.norm(ul - ll)
    bx, by = border
    xv, yv = view_wh
    # Scan region corners
    sll = ll - bx * u - by * v
    slr = lr + bx * u - by * v
    sul = ul - bx * u + by * v
    return {
        "ll": sll + (xv / 2, yv / 2),
        "ul": sul + (xv / 2, -yv / 2),
        "lr": slr + (-xv / 2, yv / 2),
    }
