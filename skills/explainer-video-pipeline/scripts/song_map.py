#!/usr/bin/env python3
"""Render a Northern Song (c. 1100) map background in an ink-on-paper style.

Usage: song_map.py places.json out_dir [--size 1920x1080] [--geo DIR]
places.json: [{"id": "meizhou", "name": "眉州", "lon": 103.83, "lat": 30.05}, ...]
Writes out_dir/song_map.png and out_dir/places_px.json (pixel x, y for each place),
so the scene code can draw markers, routes and labels at the right spots.

Data (download once into --geo, default ~/.cache/explainer-video/geo):
  ne_50m_land.geojson, ne_50m_rivers_lake_centerlines.geojson, ne_50m_lakes.geojson
    from Natural Earth (public domain), github.com/nvkelso/natural-earth-vector
  world_1100.geojson from github.com/aourednik/historical-basemaps (GPL-3.0);
    its borders are approximate (BORDERPRECISION=1) and are drawn as dashed lines.
"""
import argparse, json, math, os, random
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ap = argparse.ArgumentParser(); ap.add_argument('places'); ap.add_argument('out')
ap.add_argument('--size', default='1920x1080')
ap.add_argument('--geo', default=os.path.expanduser('~/.cache/explainer-video/geo'))
ap.add_argument('--bbox', default='101,17.8,124,37.6', help='lon0,lat0,lon1,lat1')
a = ap.parse_args()
W, H = map(int, a.size.split('x'))
lon0, lat0, lon1, lat1 = map(float, a.bbox.split(','))
SERIF = '/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc'

# Equirectangular with cos(mid-lat) correction, fitted into the frame (keep aspect).
k = math.cos(math.radians((lat0 + lat1) / 2))
sx = W / ((lon1 - lon0) * k); sy = H / (lat1 - lat0); s = min(sx, sy)
ox = (W - (lon1 - lon0) * k * s) / 2; oy = (H - (lat1 - lat0) * s) / 2
def px(lon, lat):
    return (ox + (lon - lon0) * k * s, oy + (lat1 - lat) * s)

def load(name):
    return json.load(open(os.path.join(a.geo, name)))['features']

def rings(geom):
    cs = geom['coordinates']
    if geom['type'] == 'Polygon':
        cs = [cs]
    for poly in cs:
        for r in poly:
            yield [px(x, y) for x, y in r]

def lines(geom):
    cs = geom['coordinates']
    if geom['type'] == 'LineString':
        cs = [cs]
    for ln in cs:
        yield [px(x, y) for x, y in ln]

PAPER, SEA, INK, SONG = (236, 224, 198), (210, 214, 200), (74, 58, 42), (226, 205, 160)
img = Image.new('RGB', (W, H), SEA)
d = ImageDraw.Draw(img)
for f in load('ne_50m_land.geojson'):
    for r in rings(f['geometry']):
        if len(r) > 2:
            d.polygon(r, fill=PAPER)

# Song territory tint, then neighbouring states' names; borders dashed (approximate).
hist = {f['properties'].get('NAME'): f for f in load('world_1100.geojson')}
tint = Image.new('L', (W, H), 0); td = ImageDraw.Draw(tint)
for r in rings(hist['Song Empire']['geometry']):
    if len(r) > 2:
        td.polygon(r, fill=255)
tint = tint.filter(ImageFilter.GaussianBlur(6))
img = Image.composite(Image.new('RGB', (W, H), SONG), img, tint.point(lambda v: int(v * 0.55)))
d = ImageDraw.Draw(img)

def dashed(pts, color, width, on=10, off=8):
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0); t = 0.0
        while t < seg:
            e = min(t + on, seg)
            d.line([(x0 + (x1 - x0) * t / seg, y0 + (y1 - y0) * t / seg),
                    (x0 + (x1 - x0) * e / seg, y0 + (y1 - y0) * e / seg)], fill=color, width=width)
            t += on + off

for r in rings(hist['Song Empire']['geometry']):
    dashed(r, (150, 60, 40), max(2, W // 900))
for f in load('ne_50m_lakes.geojson'):
    for r in rings(f['geometry']):
        if len(r) > 2:
            d.polygon(r, fill=SEA)
for f in load('ne_50m_rivers_lake_centerlines.geojson'):
    n = f['properties'].get('name') or ''
    big = n in ('Chang Jiang', 'Yangtze', 'Huang')
    for ln in lines(f['geometry']):
        d.line(ln, fill=(120, 140, 150), width=max(2, W // (500 if big else 1200)))

fs = lambda frac: ImageFont.truetype(SERIF, max(12, int(H * frac)), index=2)
REGION = [('北宋', 113.5, 31.5, 0.075, (150, 60, 40)), ('辽', 118.5, 40.6, 0.05, INK),
          ('西夏', 104.0, 39.0, 0.045, INK), ('大理', 100.2, 25.2, 0.04, INK),
          ('吐蕃', 98.8, 33.0, 0.04, INK), ('交趾', 105.6, 20.4, 0.032, INK),
          ('高丽', 124.2, 40.6, 0.032, INK)]
for name, lon, lat, frac, col in REGION:
    x, y = px(lon, lat)
    if 0 < x < W and 0 < y < H:
        d.text((x, y), name, font=fs(frac), fill=col, anchor='mm')
for name, lon, lat in (('长江', 112.6, 29.4), ('黄河', 111.0, 35.4)):
    x, y = px(lon, lat)
    d.text((x, y), name, font=fs(0.022), fill=(80, 110, 125), anchor='mm')

# Paper grain and vignette, so it reads as an old map rather than a GIS export.
rnd = random.Random(7)
grain = Image.new('L', (W // 4, H // 4))
grain.putdata([128 + rnd.randint(-18, 18) for _ in range(grain.width * grain.height)])
grain = grain.resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.2))
img = Image.blend(img, Image.merge('RGB', (grain, grain, grain)), 0.08)
vig = Image.new('L', (W, H), 0)
ImageDraw.Draw(vig).ellipse((-W * 0.15, -H * 0.2, W * 1.15, H * 1.2), fill=255)
vig = vig.filter(ImageFilter.GaussianBlur(W // 10))
img = Image.composite(img, Image.new('RGB', (W, H), (150, 125, 90)), vig)

os.makedirs(a.out, exist_ok=True)
img.save(os.path.join(a.out, 'song_map.png'), optimize=True)
places = json.load(open(a.places))
out = [dict(p, x=round(px(p['lon'], p['lat'])[0], 1), y=round(px(p['lon'], p['lat'])[1], 1))
       for p in places]
json.dump(out, open(os.path.join(a.out, 'places_px.json'), 'w'), ensure_ascii=False, indent=1)
print('wrote song_map.png', W, 'x', H, 'and', len(out), 'place coordinates')
