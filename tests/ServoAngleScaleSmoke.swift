import Foundation
@main enum ServoAngleScaleSmoke {
    static func main() {
        for pulse in 637...1000 {
            let angle = ServoAngleScale.displayedAngle(channel: 1, legacyAngle: nil, pulse: pulse)!
            precondition((12.33...45).contains(angle))
            precondition(ServoAngleScale.wireAngle(channel: 1, angle: angle) == nil)
        }
        precondition(ServoAngleScale.displayedAngle(channel: 1, legacyAngle: 45, pulse: 1000) == 45)
        precondition(ServoAngleScale.displayedAngle(channel: 1, legacyAngle: 12.33, pulse: 637) == 12.33)
        for pulse in [0, 499, 636, 1001, 2000] {
            precondition(ServoAngleScale.displayedAngle(channel: 1, legacyAngle: 90, pulse: pulse) == nil)
        }
        for channel in [2,3] {
            for angle in [45.0,90.0,135.0] {
                precondition(ServoAngleScale.wireAngle(channel: channel, angle: angle) == angle)
            }
            precondition(ServoAngleScale.wireAngle(channel: channel, angle: 0) == nil)
        }
        print("Manual servo scale: anchor preserved, first slider rejected, unknown positions rejected, other channels unchanged")
    }
}
