import Foundation
@main enum RingTests {
    static func main() {
        let ring = WiFiSampleRing()
        var silence = [Float](repeating: 1, count: 1920)
        silence.withUnsafeMutableBufferPointer { ring.pop($0.baseAddress!, frames: 960) }
        precondition(silence.allSatisfy { $0 == 0 })
        let source = (0..<7680).map { Float($0 % 2 == 0 ? 0.25 : -0.25) }
        source.withUnsafeBufferPointer { ring.push($0.baseAddress!, frames: 3840) }
        silence.withUnsafeMutableBufferPointer { ring.pop($0.baseAddress!, frames: 960) }
        precondition(silence.enumerated().allSatisfy { abs($0.element - ($0.offset % 2 == 0 ? 0.25 : -0.25)) < 0.0001 })
        for _ in 0..<100 { source.withUnsafeBufferPointer { ring.push($0.baseAddress!, frames: 3840) } }
        precondition(ring.overflows > 0)
        silence.withUnsafeMutableBufferPointer { ring.pop($0.baseAddress!, frames: 960) }
        precondition(silence.allSatisfy { $0.isFinite && abs($0) <= 0.25 })
        let bursty = WiFiSampleRing(targetFrames: 9600)
        // 120 ms packet bursts with continuous playback used to exhaust the
        // 60 ms ring. Keep stereo constant to detect discontinuities.
        let burst = [Float](repeating: 0.2, count: 11520)
        for _ in 0..<3 { burst.withUnsafeBufferPointer { bursty.push($0.baseAddress!, frames: 5760) } }
        var playback = [Float](repeating: 0, count: 1920)
        for step in 0..<600 {
            if step % 6 == 0 { burst.withUnsafeBufferPointer { bursty.push($0.baseAddress!, frames: 5760) } }
            playback.withUnsafeMutableBufferPointer { bursty.pop($0.baseAddress!, frames: 960) }
        }
        precondition(bursty.snapshot()["underflows"] == 0)
        precondition(playback.allSatisfy { abs($0 - 0.2) < 0.0001 })
        print("Wi-Fi ring: silence, stereo separation, resampling and bounded backlog passed")
    }
}
