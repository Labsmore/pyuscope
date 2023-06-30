import cv2 as cv
import numpy as np


def choose_best_image(images_iter, log=None):
    if not log:

        def log(s):
            print(s)

    scores = {}
    log(" AF choose")
    for fni, (imagek, im_pil) in enumerate(images_iter):

        def get_score(image, blur=9):
            filtered = cv.medianBlur(image, blur)
            laplacian = cv.Laplacian(filtered, cv.CV_64F)
            return laplacian.var()

        def image_pil2cv(im):
            return np.array(im)[:, :, ::-1].copy()

        im_cv = image_pil2cv(im_pil)
        score = get_score(im_cv)
        log("  AF choose %u (%0.6f): %0.3f" % (fni, imagek, score))
        scores[score] = imagek, fni
    _score, (k, fni) = sorted(scores.items())[-1]
    log(" AF choose winner: %s" % k)
    return k, fni
