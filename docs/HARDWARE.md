# Hardware and motion limits

The current code targets a Pi 3 Model B prototype with three SG92R servos,
a Camera Module 3 and a USB audio adapter. Other boards and kernels need
separate validation. Do not reuse the prototype's eye angles as universal
servo limits: mounting geometry determines the safe range.

| Channel | Function | Physical header pin | BCM GPIO | Application limits |
| --- | --- | --- | --- | --- |
| 1 | Right eye | 11 | 17 | 12.3–45.5°; closed 45.5°, open 12.3° |
| 2 | Left eye | 13 | 27 | 3–33°; closed 3°, open 33° |
| 3 | Camera tilt | 15 | 22 | 30–60° |

These pins carry servo control signals. Servo power comes from an appropriate
regulated supply with a common signal ground; do not power a servo from a GPIO
signal pin or the Pi's 3.3V rail. Verify the actual motor's polarity before
connecting it. Remove power before rewiring. A motor that smoked must not be reused.

Movement takes 0.5 seconds and pulses are released after the configured delay.
Once released, a servo does not actively hold its position. The controller has
no position feedback; initialization/reference confirmation must match the
physical mechanism. Software limits cannot protect a misassembled linkage.

For a different mechanism, disconnect its linkage and establish safe limits
before modifying both the Mac UI and Pi validation. See `mac/Servos.swift`,
`mac/ServoAngleScale.swift`, `pi/servos.py` and the servo tests. Do not expand
to 0–180° with a mounted linkage simply to discover its limits.

The installer claims GPIO17/27/22 through the kernel `pwm-gpio` driver. Remove
conflicting overlays/devices first; an old TFT display may use the same pins.
This prototype deliberately does not use pigpio/DMA. Read `pi/install.sh`
before installing on a Pi that already controls other equipment.

For audio, connect the USB card's output to the amplifier input and its mic
input to a compatible microphone. Ground-loop isolation may be needed in a
shared-power assembly; it does not replace acoustic echo cancellation.
