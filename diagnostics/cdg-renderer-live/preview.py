"""Minimal CD+G decoder -> PNG frame. Used to verify output."""
import sys
from PIL import Image

W, H = 300, 216


def render(cdg_bytes, at_seconds):
    npk = int(at_seconds * 300)
    screen = [[0] * W for _ in range(H)]
    clut = [(0, 0, 0)] * 16
    border = 0
    hoff = voff = 0
    for i in range(min(npk, len(cdg_bytes) // 24)):
        p = cdg_bytes[i * 24:(i + 1) * 24]
        if (p[0] & 0x3F) != 9:
            continue
        inst = p[1] & 0x3F
        d = [b & 0x3F for b in p[4:20]]
        if inst == 1:  # memory preset
            c = d[0] & 0x0F
            for y in range(H):
                for x in range(W):
                    screen[y][x] = c
        elif inst == 2:  # border preset
            border = d[0] & 0x0F
        elif inst in (6, 38):  # tile block / xor
            c0, c1 = d[0] & 0x0F, d[1] & 0x0F
            row, col = d[2] & 0x1F, d[3] & 0x3F
            y0, x0 = row * 12, col * 6
            for ty in range(12):
                bits = d[4 + ty]
                for tx in range(6):
                    on = (bits >> (5 - tx)) & 1
                    y, x = y0 + ty, x0 + tx
                    if 0 <= y < H and 0 <= x < W:
                        v = c1 if on else c0
                        screen[y][x] = (screen[y][x] ^ v) if inst == 38 else v
        elif inst in (30, 31):  # load clut
            base = 0 if inst == 30 else 8
            for k in range(8):
                b0, b1 = d[k * 2], d[k * 2 + 1]
                r = (b0 >> 2) & 0x0F
                g = ((b0 & 0x03) << 2) | ((b1 >> 4) & 0x03)
                b = b1 & 0x0F
                clut[base + k] = (r * 17, g * 17, b * 17)
        elif inst in (20, 24):  # scroll
            c = d[0] & 0x0F
            hcmd, hoff2 = (d[1] >> 4) & 0x03, d[1] & 0x07
            vcmd, voff2 = (d[2] >> 4) & 0x03, d[2] & 0x0F
            hoff, voff = hoff2, voff2
            dx = 6 if hcmd == 1 else -6 if hcmd == 2 else 0
            dy = 12 if vcmd == 1 else -12 if vcmd == 2 else 0
            if dx or dy:
                new = [[c] * W for _ in range(H)]
                for y in range(H):
                    for x in range(W):
                        sy, sx = y - dy, x - dx
                        if inst == 24:
                            sy, sx = sy % H, sx % W
                        if 0 <= sy < H and 0 <= sx < W:
                            new[y][x] = screen[sy][sx]
                screen = new
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            px[x, y] = clut[screen[y][x]]
    return img.crop((6 + hoff, 12 + voff, 6 + hoff + 288, 12 + voff + 192)).resize((576, 384), Image.NEAREST)


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    for t in [float(x) for x in sys.argv[2].split(",")]:
        render(data, t).save(f"frame_{t:0.2f}.png")
        print(f"frame_{t:0.2f}.png")
