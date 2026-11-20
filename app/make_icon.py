"""Draws the VoiceNoah app icon (dark rounded square with a white waveform) into an .iconset."""
import sys
from pathlib import Path

from AppKit import (NSBezierPath, NSBitmapImageRep, NSColor, NSGradient, NSGraphicsContext,
                    NSMakeRect, NSPNGFileType)

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
BARS = [0.30, 0.55, 0.85, 1.0, 0.70, 0.45, 0.80, 0.50, 0.30]


def draw(px):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    s = px / 1024.0
    m = 100 * s
    box = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(m, m, px - 2 * m, px - 2 * m), 185 * s, 185 * s)
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.16, 0.17, 0.22, 1),
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.04, 0.04, 0.06, 1)).drawInBezierPath_angle_(box, -90)
    bw, gap = 44 * s, 30 * s
    total = len(BARS) * bw + (len(BARS) - 1) * gap
    x0, cy, hmax = px / 2 - total / 2, px / 2, 470 * s
    for i, v in enumerate(BARS):
        h = max(bw, hmax * v)
        a = 0.55 + 0.45 * v
        NSColor.colorWithCalibratedWhite_alpha_(1, a).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x0 + i * (bw + gap), cy - h / 2, bw, h), bw / 2, bw / 2).fill()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representationUsingType_properties_(NSPNGFileType, None)


for size in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
        draw(size * scale).writeToFile_atomically_(str(out / name), True)
print("icon ok")
