import sys
import cv2
import numpy


# should be thought of as a line, not an area
class Anchor:
    __slots__ = 'x', 'xmax', 'y', 'ymax'

    def __init__(self, x, y, xmax=None, ymax=None):
        self.x = x
        self.y = y
        self.xmax = xmax or x
        self.ymax = ymax or y

    def merge(self, rhs):
        self.x = min(self.x, rhs.x)
        self.xmax = max(self.xmax, rhs.xmax)
        self.y = min(self.y, rhs.y)
        self.ymax = max(self.ymax, rhs.ymax)

    @property
    def xavg(self):
        return (self.x + self.xmax) // 2

    @property
    def yavg(self):
        return (self.y + self.ymax) // 2

    @property
    def xrange(self):
        return abs(self.x - self.xmax) // 2

    @property
    def yrange(self):
        return abs(self.y - self.ymax) // 2

    def __repr__(self):
        return f'({self.xavg}+-{self.xrange}, {self.yavg}+-{self.yrange})'

    def __lt__(self, rhs):
        # distance from top left corner
        return self.xavg + self.yavg < rhs.xavg + rhs.yavg


class ScanState:
    def __init__(self):
        self.state = 0
        self.tally = [0]

    def pop_state(self):
        # when state == 6, we need to drop down to state == 4
        self.state -= 2
        self.tally = self.tally[2:]

    def evaluate_state(self):
        if self.state != 6:
            return None
        # ratio should be 1:1:3:1:1
        ones = self.tally[1:6]
        for s in ones:
            if not s:
                return None
        center = ones.pop(2)
        for s in ones:
            ratio = center / s
            if ratio < 2.5 or ratio > 3.5:
                return None
        anchor_width = sum(ones) + center
        return anchor_width

    def process(self, black):
        # transitions first
        is_transition = (self.state in [0, 2, 4] and black) or (self.state in [1, 3, 5] and not black)
        if is_transition:
            self.state += 1
            self.tally.append(0)
            self.tally[-1] += 1

            if self.state == 6:
                res = self.evaluate_state()
                self.pop_state()
                return res
            return None

        if self.state in [1, 3, 5] and black:
            self.tally[-1] += 1
        if self.state in [2, 4] and not black:
            self.tally[-1] += 1
        return None


def _the_works(img):
    img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    img = cv2.GaussianBlur(img,(17,17),0)
    __,img = cv2.threshold(img,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    return img


class CimbarScanner:
    def __init__(self, img, dark=False, skip=17):
        '''
        image dimensions need to not be divisible by skip
        '''
        self.img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, self.img = cv2.threshold(self.img, 127, 255, cv2.THRESH_BINARY)
        self.height, self.width = self.img.shape
        self.dark = dark
        self.skip = skip

    def _test_pixel(self, x, y):
        if self.dark:
            return self.img[y, x] > 127
        else:
            return self.img[y, x] < 127

    def horizontal_scan(self, y):
        # print('horizontal scan at {}'.format(y))
        # for each column, look for the 1:1:3:1:1 pattern
        state = ScanState()
        for x in range(self.width):
            black = self._test_pixel(x, y)
            res = state.process(black)
            if res:
                #print('found possible anchor at {}-{},{}'.format(x - res, x, y))
                yield Anchor(x=x-res, xmax=x-1, y=y)

        # if the pattern is at the edge of the image
        res = state.process(False)
        if res:
            x = self.width
            yield Anchor(x=x-res, xmax=x-1, y=y)

    def vertical_scan(self, x):
        state = ScanState()
        for y in range(self.height):
            black = self._test_pixel(x, y)
            res = state.process(black)
            if res:
                #print('found possible anchor at {},{}-{}'.format(x, y-res, y))
                yield Anchor(x=x, y=y-res, ymax=y-1)

         # if the pattern is at the edge of the image
        res = state.process(False)
        if res:
            y = self.height
            yield Anchor(x=x, y=y-res, ymax=y-1)

    def diagonal_scan(self, x, y):
        # find top/left point first, then go down right
        offset = abs(x - y)
        if x < y:
            start_y = offset
            start_x = 0
        else:
            start_x = offset
            start_y = 0

        state = ScanState()
        for i in range(self.width - offset):
            x = start_x + i
            y = start_y + i
            black = self._test_pixel(x, y)
            res = state.process(black)
            if res:
                print('confirmed anchor at {}-{},{}-{}'.format(x-res, x, y-res, y))
                yield Anchor(x=x-res, xmax=x, y=y-res, ymax=y)

         # if the pattern is at the edge of the image
        res = state.process(False)
        if res:
            x = start_x + self.width - offset
            y = start_y + self.width - offset
            yield Anchor(x=x-res, xmax=x, y=y-res, ymax=y)

    def t1_scan_horizontal(self):
        '''
        gets a smart answer for Xs
        '''
        results = []
        y = 0
        y += self.skip
        while y < self.height:  # eventually != 0?
            if y > self.height:
                y = y % self.height
            results += list(self.horizontal_scan(y))
            y += self.skip
        return self.deduplicate_candidates(results)

    def t2_scan_vertical(self, candidates):
        '''
        gets a smart answer for Ys
        '''
        results = []
        xs = set([p.xavg for p in candidates])
        for x in xs:
            results += list(self.vertical_scan(x))
        return self.deduplicate_candidates(results)

    def t3_scan_diagonal(self, candidates):
        '''
        confirm tokens
        '''
        results = []
        for p in candidates:
            results += list(self.diagonal_scan(p.xavg, p.yavg))
        return self.deduplicate_candidates(results)

    def deduplicate_candidates(self, candidates):
        # group
        group = []
        for p in candidates:
            done = False
            for i, elem in enumerate(group):
                rep = elem[0]
                if abs(p.xavg - rep.xavg) < 50 and abs(p.yavg - rep.yavg) < 50:
                    group[i].append(p)
                    done = True
                    continue
            if not done:
                group.append([p])

        # average
        average = []
        for c in group:
            area = c[0]
            for p in c:
                area.merge(p)
            average.append(area)
        return average

    def filter_candidates(self, candidates):
        if len(candidates) <= 4:
            return candidates
        xrange = sum([c.xrange for c in candidates])
        yrange = sum([c.yrange for c in candidates])

        xrange = xrange // len(candidates)
        yrange = yrange // len(candidates)
        return [c for c in candidates if c.xrange > xrange // 2 and c.yrange > yrange // 2]


    def sort_top_to_bottom(self, candidates):
        candidates.sort()
        top_left = candidates[0]
        p1 = candidates[1]
        p2 = candidates[2]
        p1_xoff = abs(p1.xavg - top_left.xavg)
        p2_xoff = abs(p2.xavg - top_left.xavg)
        if p2_xoff > p1_xoff:
            candidates = [top_left, p2, p1, candidates[3]]
        return [(p.xavg, p.yavg) for p in candidates]

    def scan(self):
        # do these need to track all known ranges, so we can approximate bounding lines?
        # also not clear if we should dedup at every step or not
        candidates = self.t1_scan_horizontal()
        t2_candidates = self.t2_scan_vertical(candidates)
        # if duplicate candidates (e.g. within 10px or so), deduplicate
        t3_candidates = self.t3_scan_diagonal(t2_candidates)
        print(candidates)
        print(t2_candidates)
        print(t3_candidates)
        final_candidates = self.filter_candidates(t3_candidates)
        return self.sort_top_to_bottom(final_candidates)


def detector(img, dark):
    cs = CimbarScanner(img, dark, 17)
    return cs.scan()


def deskewer(src_image, dst_image, dark=True):
    img = cv2.imread(src_image)
    res = detector(img, dark)
    print(res)

    if len(res) < 4:
        print('didnt detect enough points! :(')
        return

    ''' given a 1024x1024 image, corners should correspond to:
     (28, 28)
     (996, 28)
     (28, 996)
     (996, 996)
    '''
    # i.e. width is CELL_DIMENSIONS * CELL_SPACING
    top_left, top_right, bottom_left, bottom_right = res
    # print(f'top left: {top_left}, top right: {top_right}, bottom right: {bottom_right}, bottom left: {bottom_left}')

    size = 1024
    input_pts = numpy.float32([top_left, top_right, bottom_right, bottom_left])
    output_pts = numpy.float32([[28, 28], [size-28, 28], [size-28, size-28], [28, size-28]])
    transformer = cv2.getPerspectiveTransform(input_pts, output_pts)
    correct_prespective = cv2.warpPerspective(img, transformer, (size, size))
    cv2.imwrite(dst_image, correct_prespective)


def main():
    src_image = sys.argv[1]
    dst_image = sys.argv[2]
    deskewer(src_image, dst_image)


if __name__ == '__main__':
    main()