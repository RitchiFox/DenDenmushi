#!/usr/bin/env python3
"""Generate the public build's original waiting cue without playing audio."""
import math
from pathlib import Path
import struct
import wave

RATE = 24000
target = Path(__file__).resolve().parents[1] / 'pi' / 'waiting.wav'
frames = bytearray()
for i in range(2 * RATE):
    t = i / RATE
    sample = 0.0
    for start, frequency in ((0.10, 440), (0.42, 554)):
        elapsed = t - start
        if 0 <= elapsed < 0.22:
            envelope = min(1.0, elapsed / 0.015, (0.22 - elapsed) / 0.04)
            sample += 0.18 * envelope * math.sin(2 * math.pi * frequency * elapsed)
    frames.extend(struct.pack('<h', round(sample * 32767)))
with wave.open(str(target), 'wb') as output:
    output.setnchannels(1)
    output.setsampwidth(2)
    output.setframerate(RATE)
    output.writeframes(frames)
print(target)
