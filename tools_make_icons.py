"""Generate tray/splash icon assets from the downloaded DeepSeek files."""
import base64, io, os, struct

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')

def extract_ico_entry(path):
    with open(path, 'rb') as f:
        data = f.read()
    reserved, typ, count = struct.unpack_from('<HHH', data, 0)
    assert typ == 1, 'not an ico'
    entries = []
    for i in range(count):
        w, h, colors, rsv, planes, bitcount, size, offset = struct.unpack_from('<BBBBHHII', data, 6 + 16 * i)
        entries.append((w or 256, h or 256, size, offset))
    entries.sort(key=lambda e: e[0] * e[1], reverse=True)
    return entries[0], data

def decode_ico_bmp(data, size, offset):
    # DIB header inside ICO: BITMAPINFOHEADER with doubled height (AND mask).
    header_size = struct.unpack_from('<I', data, offset)[0]
    if header_size != 40:
        raise SystemExit(f'unsupported DIB header {header_size}')
    width, height2, planes, bitcount = struct.unpack_from('<iiHH', data, offset + 4)
    height = abs(height2) // 2
    assert bitcount in (24, 32), f'unsupported bitcount {bitcount}'
    bytes_pp = bitcount // 8
    row_size = ((width * bytes_pp + 3) // 4) * 4
    pix_start = offset + header_size
    rows = []
    for y in range(height):
        # Bottom-up source; store top-down RGBA.
        src = pix_start + y * row_size
        row = bytearray()
        for x in range(width):
            i = src + x * bytes_pp
            b, g, r = data[i], data[i + 1], data[i + 2]
            a = data[i + 3] if bytes_pp == 4 else 255
            if a > 0:
                # Un-premultiply nothing; ICO stores straight alpha.
                pass
            row += bytes((r, g, b, a))
        rows.insert(0, bytes(row))
    raw = b''.join(rows)
    return width, height, raw

def png_bytes(width, height, raw_rgba):
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf, GLib
    pb = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(raw_rgba), GdkPixbuf.Colorspace.RGB, True, 8, width, height, width * 4)
    buf = io.BytesIO()
    pb.save_to_callbackv(lambda d, _l, _u=None: (buf.write(d), True)[1], None, 'png', [], [])
    return pb, buf.getvalue()

def scale(pb, size):
    w, h = pb.get_width(), pb.get_height()
    s = min(size / w, size / h)
    return pb.scale_simple(max(1, int(w * s)), max(1, int(h * s)), 3)

def recolor_white(pb):
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf, GLib
    w, h, rowstride = pb.get_width(), pb.get_height(), pb.get_rowstride()
    pixels = bytearray(pb.get_pixels())
    for y in range(h):
        row = y * rowstride
        for x in range(w):
            i = row + x * 4
            if pixels[i + 3] > 0:
                pixels[i] = pixels[i + 1] = pixels[i + 2] = 255
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(pixels)), GdkPixbuf.Colorspace.RGB, True, 8, w, h, rowstride)

def load_avatar_rgba(path):
    """Load the GitHub avatar (white background) with the connected
    background removed: BFS from the borders over near-white pixels."""
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    w, h, rs = pb.get_width(), pb.get_height(), pb.get_rowstride()
    px = pb.get_pixels()
    out = bytearray(w * h * 4)
    def near_white(x, y):
        i = y * rs + x * 3
        r, g, b = px[i], px[i+1], px[i+2]
        return r > 235 and g > 235 and b > 235
    visited = bytearray(w * h)
    queue = []
    for x in range(w):
        queue.append((x, 0)); queue.append((x, h-1))
    for y in range(h):
        queue.append((0, y)); queue.append((w-1, y))
    while queue:
        x, y = queue.pop()
        if x < 0 or y < 0 or x >= w or y >= h or visited[y*w+x]:
            continue
        visited[y*w+x] = 1
        if near_white(x, y):
            queue.extend(((x+1,y),(x-1,y),(x,y+1),(x,y-1)))
    for y in range(h):
        for x in range(w):
            i = y * rs + x * 3
            o = (y * w + x) * 4
            out[o], out[o+1], out[o+2] = px[i], px[i+1], px[i+2]
            out[o+3] = 0 if visited[y*w+x] else 255
    from gi.repository import GLib
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(out)),
        GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4)


def png_of(pb):
    buf = io.BytesIO()
    pb.save_to_callbackv(lambda d, _l, _u=None: (buf.write(d), True)[1], None, 'png', [], [])
    return buf.getvalue()

(entry_w, entry_h, entry_size, entry_off), data = extract_ico_entry(os.path.join(ASSETS, 'deepseek-favicon.ico'))
width, height, raw = decode_ico_bmp(data, (entry_w, entry_h), entry_off)
print('glyph source:', width, 'x', height)
pb, glyph_png = png_bytes(width, height, raw)
open(os.path.join(ASSETS, 'deepseek-glyph.png'), 'wb').write(glyph_png)

out = {}
avatar = load_avatar_rgba(os.path.join(ASSETS, '..', 'build', 'avatar512.png'))
for size in sorted((22, 24, 48, 128, 256, 512)):
    # Large sizes come from the 460px avatar source (sharper); small sizes
    # keep the favicon render. Either source ends in the same blue glyph.
    source = avatar if size >= 128 else pb
    scaled = scale(source, size)
    for name, p in (('coloured', scaled), ('symbolic', recolor_white(scaled))):
        key = f'{name}-{size}'
        out[key] = png_of(p)
        open(os.path.join(ASSETS, f'icon-{key}.png'), 'wb').write(out[key])

mod = ['"""Embedded DeepSeek tray icons (PNG, base64). Generated by tools_make_icons.py."""',
       'import base64', '', 'ICONS = {', ]
for key in sorted(out):
    mod.append(f"  '{key}': base64.b64decode({base64.b64encode(out[key])!r}),")
mod.append('}')
mod.append('')
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dsh_linux', 'icons_data.py'), 'w').write('\n'.join(mod))
print('wrote dsh_linux/icons_data.py with', len(out), 'icons')
