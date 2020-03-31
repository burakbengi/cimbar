from collections import defaultdict
from os import path

import imagehash
from PIL import Image


CIMBAR_ROOT = path.abspath(path.join(path.dirname(path.realpath(__file__)), '..', '..'))


def possible_colors(dark):
    colors = [
        (0, 255, 255, 255),
        (255, 255, 0, 255),
        (255, 0, 255, 255),
        (0, 255, 0, 255),
        (0, 0, 255, 255),
        (255, 0, 0, 255),
        (255, 127, 0, 255),
        (127, 255, 0, 255),
    ]
    return colors


def load_tile(name, dark, replacements={}):
    img = Image.open(name)
    if dark:
        replacements[(255, 255, 255, 255)] = (0, 0, 0, 255)

    pixdata = img.load()
    width, height = img.size
    for y in range(height):
        for x in range(width):
            for current_color, desired_color in replacements.items():
                if pixdata[x, y] == current_color:
                    pixdata[x, y] = desired_color
                    break
    return img


class CimbDecoder:
    def __init__(self, dark, symbol_bits, color_bits=0):
        self.dark = dark
        self.symbol_bits = symbol_bits
        self.hashes = {}
        self.bg_color = (0, 0, 0, 255) if dark else (255, 255, 255, 255)

        all_colors = possible_colors(dark)
        self.colors = {c: all_colors[c] for c in range(2 ** color_bits)}
        for i in range(2 ** symbol_bits):
            name = path.join(CIMBAR_ROOT, 'bitmap', f'{symbol_bits}', f'{i:02x}.png')
            img = load_tile(name, self.dark)
            ahash = imagehash.average_hash(img)
            self.hashes[i] = ahash

    def get_best_fit(self, cell_hash):
        min_distance = 1000
        best_fit = 0
        for i, ihash in self.hashes.items():
            distance = cell_hash - ihash
            if distance < min_distance:
                min_distance = distance
                best_fit = i
                if min_distance == 0:
                    break
        #if min_distance > 0:
        #    print(f'min distance is {min_distance}. best fit {best_fit}')
        return best_fit, min_distance

    def decode_symbol(self, img_cell):
        cell_hash = imagehash.average_hash(img_cell)
        return self.get_best_fit(cell_hash)  # make this return an object that knows how to get the color bits on demand???

    def _check_color(self, c, d):
        return (c[0] - d[0])**2 + (c[1] - d[1])**2 + (c[2] - d[2])**2

    def _best_color(self, r, g, b):
        # probably some scaling will be good.
        # we can do fairly straightforward min/max scaling for everything except black/white

        # bg color check
        best_fit = -1
        best_distance = self._check_color(self.bg_color, (r, g, b))
        if best_distance < 50:
            return best_fit, best_distance

        for i, c in self.colors.items():
            diff = self._check_color(c, (r, g, b))
            if diff < best_distance:
                best_fit = i
                best_distance = diff
                if best_distance < 50:
                    break
        return best_fit, best_distance

    def decode_color(self, img_cell):
        if len(self.colors) <= 1:
            return 0

        candidates = defaultdict(int)
        pixdata = img_cell.load()
        width, height = img_cell.size
        for y in range(1, height - 1):
            for x in range(1, width - 1):
                r, g, b = pixdata[x, y]
                fit, distance = self._best_color(r, g, b)
                if fit < 0:
                    continue
                candidates[fit] += 1
                if candidates[fit] > 5:
                    return fit << self.symbol_bits

        # left shift final result by `symbol_bits`
        bits = max(candidates, key=candidates.get)
        return bits << self.symbol_bits


class CimbEncoder:
    def __init__(self, dark, symbol_bits, color_bits=0):
        self.img = {}
        self.colors = {}

        all_colors = possible_colors(dark)
        num_symbols = 2 ** symbol_bits
        for c in range(2 ** color_bits):
            color = all_colors[c]
            for i in range(num_symbols):
                name = path.join(CIMBAR_ROOT, 'bitmap', f'{symbol_bits}', f'{i:02x}.png')
                self.img[c * num_symbols + i] = self._load_img(name, dark, color)

    def _load_img(self, name, dark, color):
        # replace by color...
        replacements = {
            (0, 255, 255, 255): color,
        }
        return load_tile(name, dark, replacements)

    def encode(self, bits):
        return self.img[bits]


class cell_drift:
    pairs = [(0, 0), (1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)]

    def __init__(self, limit=2):
        self.x = 0
        self.y = 0
        self.limit = limit

    def update(self, dx, dy):
        self.x += dx
        self.y += dy
        self._enforce_limit()

    def _enforce_limit(self):
        if self.x > self.limit:
            self.x = self.limit
        elif self.x < 0-self.limit:
            self.x = 0-self.limit
        if self.y > self.limit:
            self.y = self.limit
        elif self.y < 0-self.limit:
            self.y = 0-self.limit


def cell_positions(spacing, dimensions, offset=0, marker_size=6):
    '''
    ex: if dimensions == 128, and marker_size == 8:
    8 tiles at top is 128-16 == 112
    8 tiles at bottom is also 128-16 == 112

    structure would be:
    112 * 8
    128 * 112
    112 * 8
    '''
    #cells = dimensions * dimensions
    offset_y = offset + 1
    marker_offset_x = spacing * marker_size
    top_width = dimensions - marker_size - marker_size
    top_cells = top_width * marker_size
    for i in range(top_cells):
        x = (i % top_width) * spacing + marker_offset_x + offset
        y = (i // top_width) * spacing + offset_y
        yield x, y

    mid_y = marker_size * spacing
    mid_width = dimensions
    mid_cells = mid_width * top_width  # top_width is also "mid_height"
    for i in range(mid_cells):
        x = (i % mid_width) * spacing + offset
        y = (i // mid_width) * spacing + mid_y + offset_y
        yield x, y

    bottom_y = (dimensions - marker_size) * spacing
    bottom_width = top_width
    bottom_cells = bottom_width * marker_size
    for i in range(bottom_cells):
        x = (i % bottom_width) * spacing + marker_offset_x + offset
        y = (i // bottom_width) * spacing + bottom_y + offset_y
        yield x, y