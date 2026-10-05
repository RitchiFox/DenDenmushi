import Foundation

/// Nominal scale for the mounted first-servo mechanism, not measured feedback.
enum ServoAngleScale {
    static func range(channel: Int) -> ClosedRange<Double> {
        channel == 1 ? 12.33...45.5 : (channel == 2 ? 3...33 : 30...60)
    }
    static func wireAngle(channel: Int, angle: Double) -> Double? {
        // First servo must use compare-and-set manual steps, never a slider.
        guard channel == 3, angle.isFinite,
              range(channel: channel).contains(angle) else { return nil }
        return angle
    }
    static func displayedAngle(channel: Int, legacyAngle: Double?, pulse: Int?) -> Double? {
        if channel == 1 {
            guard let pulse, (637...1005).contains(pulse) else { return nil }
            return Double(pulse - 500) * 90 / 1000
        }
        if channel == 2 {
            guard let pulse, (533...867).contains(pulse) else { return nil }
            return max(3, min(33, Double(pulse - 500) * 180 / 2000))
        }
        guard channel == 3, let pulse, (833...1167).contains(pulse) else { return nil }
        return max(30, min(60, Double(pulse - 500) * 180 / 2000))
    }
}
