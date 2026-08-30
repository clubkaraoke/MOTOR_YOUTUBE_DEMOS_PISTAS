from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

PACKETS_PER_SECOND = 300
WIDTH = 300
HEIGHT = 216
VISIBLE_BOX = (6, 12, 294, 204)


@dataclass(frozen=True)
class CDGPacketInfo:
    instruction: int
    data: tuple[int, ...]

    @property
    def is_memory_preset(self) -> bool:
        return self.instruction == 1

    @property
    def memory_color(self) -> int | None:
        return (self.data[0] & 0x0F) if self.is_memory_preset else None

    @property
    def memory_repeat(self) -> int | None:
        return (self.data[1] & 0x0F) if self.is_memory_preset else None


class CDGDecoder:
    """Decodificador secuencial CD+G.

    Basado en el núcleo recuperado y probado en CDG_PLAYER_ONLINE.
    Además expone metadata de paquete para que el extractor pueda reconocer
    eventos nativos del CDG, especialmente Memory Preset / Clear Screen.
    """

    def __init__(self) -> None:
        self.pix = [[0] * WIDTH for _ in range(HEIGHT)]
        self.palette = [(0, 0, 0)] * 16
        self.border = 0
        self.transparent = 0
        self.memory_color = 0
        self.h_offset = 0
        self.v_offset = 0

    @staticmethod
    def inspect_packet(packet: bytes) -> CDGPacketInfo | None:
        if len(packet) != 24 or (packet[0] & 0x3F) != 0x09:
            return None

        instruction = packet[1] & 0x3F
        data = tuple(value & 0x3F for value in packet[4:20])
        return CDGPacketInfo(instruction=instruction, data=data)

    def _clear(self, color: int) -> None:
        self.memory_color = color & 0x0F
        self.pix = [[self.memory_color] * WIDTH for _ in range(HEIGHT)]

    def _scroll(self, color: int, hcmd: int, vcmd: int, copy_mode: bool = False) -> None:
        hdir = (hcmd >> 4) & 0x03
        vdir = (vcmd >> 4) & 0x03
        self.h_offset = hcmd & 0x07
        self.v_offset = vcmd & 0x0F

        dx = 6 if hdir == 1 else (-6 if hdir == 2 else 0)
        dy = 12 if vdir == 1 else (-12 if vdir == 2 else 0)
        if not dx and not dy:
            return

        old = [row[:] for row in self.pix]
        fill = color & 0x0F

        for y in range(HEIGHT):
            for x in range(WIDTH):
                sx = x - dx
                sy = y - dy
                if 0 <= sx < WIDTH and 0 <= sy < HEIGHT:
                    self.pix[y][x] = old[sy][sx]
                elif copy_mode:
                    self.pix[y][x] = old[sy % HEIGHT][sx % WIDTH]
                else:
                    self.pix[y][x] = fill

    def packet(self, packet: bytes) -> CDGPacketInfo | None:
        info = self.inspect_packet(packet)
        if info is None:
            return None

        instruction = info.instruction
        data = info.data

        if instruction == 1:
            self._clear(data[0])

        elif instruction == 2:
            self.border = data[0] & 0x0F

        elif instruction in (6, 38):
            c0 = data[0] & 0x0F
            c1 = data[1] & 0x0F
            row = data[2] & 0x1F
            col = data[3] & 0x3F
            y0 = row * 12
            x0 = col * 6

            for r in range(12):
                bits = data[4 + r]
                y = y0 + r
                if y >= HEIGHT:
                    continue
                for c in range(6):
                    x = x0 + c
                    if x >= WIDTH:
                        continue
                    value = c1 if bits & (1 << (5 - c)) else c0
                    self.pix[y][x] = (
                        self.pix[y][x] ^ value
                        if instruction == 38
                        else value
                    )

        elif instruction == 20:
            self._scroll(data[0], data[1], data[2], False)

        elif instruction == 24:
            self._scroll(data[0], data[1], data[2], True)

        elif instruction in (30, 31):
            base = 0 if instruction == 30 else 8
            for i in range(8):
                a = data[i * 2]
                b = data[i * 2 + 1]
                r = (a & 0x3C) >> 2
                g = ((a & 0x03) << 2) | ((b & 0x30) >> 4)
                bl = b & 0x0F
                self.palette[base + i] = (r * 17, g * 17, bl * 17)

        elif instruction == 28:
            self.transparent = data[0] & 0x0F

        return info

    def image(self, crop: bool = True, scale: int = 1) -> Image.Image:
        image = Image.new("RGB", (WIDTH, HEIGHT))
        px = image.load()

        for y, row in enumerate(self.pix):
            for x, value in enumerate(row):
                px[x, y] = self.palette[value & 0x0F]

        if crop:
            image = image.crop(VISIBLE_BOX)

        if scale != 1:
            image = image.resize(
                (image.width * scale, image.height * scale),
                Image.Resampling.NEAREST,
            )

        return image

    def background_rgb(self) -> tuple[int, int, int]:
        return self.palette[self.memory_color & 0x0F]

    def visible_indices(self) -> list[list[int]]:
        """Framebuffer visible como índices CLUT 0..15.

        El extractor usa estos índices para separar texto por color sin
        depender de luminancia RGB ni mezclar gráficos de otros colores.
        """
        left, top, right, bottom = VISIBLE_BOX
        return [
            row[left:right]
            for row in self.pix[top:bottom]
        ]
