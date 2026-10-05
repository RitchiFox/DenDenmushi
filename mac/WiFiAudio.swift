import Foundation
import CoreAudio
import Network
import AVFoundation

// Separate loopbacks prevent speaker audio being mistaken for the microphone.
enum WiFiAudioDevices {
    static let micUID = "DenDenWiFiMic_UID"
    static let speakerUID = "DenDenWiFiSpeaker_UID"
    static func pair() -> (mic: SoundDevice, speaker: SoundDevice)? {
        let devices = SoundDevices.list()
        let m = devices.filter { $0.uid == micUID && $0.input && $0.output }
        let s = devices.filter { $0.uid == speakerUID && $0.input && $0.output }
        guard m.count == 1, s.count == 1 else { return nil }
        return (m[0], s[0])
    }
}

// Bounded, preallocated stereo ring. The realtime callback never waits for a
// lock or allocates. Slow adaptive interpolation accommodates independent USB
// and Mac clocks; a starvation re-buffers, never replays a stale long backlog.
final class WiFiSampleRing {
    private let lock = NSLock()
    private var storage = [Float](repeating: 0, count: 65536)
    private var read = 0.0
    private var written = 0
    private var primed = false
    private let target: Int
    private let maximum: Int
    init(targetFrames: Int = 2880) {
        precondition((2880...12000).contains(targetFrames))
        target = targetFrames; maximum = targetFrames * 2
    }
    private(set) var underflows = 0
    private(set) var overflows = 0
    private var pushedFrames = 0
    private var poppedFrames = 0
    func snapshot() -> [String: Int] {
        lock.lock(); defer { lock.unlock() }
        return ["underflows": underflows, "overflows": overflows,
                "bufferedFrames": max(0, written - Int(read)), "pushedFrames": pushedFrames, "poppedFrames": poppedFrames]
    }
    func push(_ samples: UnsafePointer<Float>, frames: Int) {
        guard frames > 0, frames <= 5760, lock.try() else { return }
        defer { lock.unlock() }
        if written - Int(read) + frames > maximum {
            read = Double(max(0, written - target)); overflows += 1
        }
        for i in 0..<frames {
            let at = ((written + i) % 32768) * 2
            storage[at] = samples[i * 2]; storage[at + 1] = samples[i * 2 + 1]
        }
        written += frames; pushedFrames += frames
    }
    func pop(_ samples: UnsafeMutablePointer<Float>, frames: Int) {
        samples.initialize(repeating: 0, count: frames * 2)
        guard lock.try() else { return }
        defer { lock.unlock() }
        let available = Double(written) - read
        if !primed {
            guard available >= Double(target) else { return }
            primed = true
        }
        let step = 1 + min(0.002, max(-0.002, (available - Double(target)) / Double(target) * 0.001))
        guard available >= Double(frames) * step + 2 else {
            primed = false; underflows += 1; return
        }
        poppedFrames += frames
        for frame in 0..<frames {
            let base = Int(read), fraction = Float(read - Double(base))
            let a = (base % 32768) * 2, b = ((base + 1) % 32768) * 2
            for ch in 0..<2 { samples[frame * 2 + ch] = storage[a + ch] + (storage[b + ch] - storage[a + ch]) * fraction }
            read += step
        }
    }
}

final class WiFiHAL {
    let incoming = WiFiSampleRing(targetFrames: 9600) // 200 ms for bursty upstream camera/audio traffic
    let outgoing = WiFiSampleRing()
    private var mic: AudioDeviceID = 0
    private var speaker: AudioDeviceID = 0
    private var micProc: AudioDeviceIOProcID?
    private var speakerProc: AudioDeviceIOProcID?
    private func verify(_ id: AudioDeviceID, input: Bool) throws {
        var format = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout.size(ofValue: format))
        var property = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamFormat,
            mScope: input ? kAudioDevicePropertyScopeInput : kAudioDevicePropertyScopeOutput, mElement: kAudioObjectPropertyElementMain)
        guard AudioObjectGetPropertyData(id, &property, 0, nil, &size, &format) == noErr,
              format.mSampleRate == 48000, format.mChannelsPerFrame == 2,
              format.mBitsPerChannel == 32, format.mBytesPerFrame == 8,
              format.mFormatID == kAudioFormatLinearPCM,
              format.mFormatFlags & kAudioFormatFlagIsFloat != 0,
              format.mFormatFlags & kAudioFormatFlagIsNonInterleaved == 0 else {
            throw DemoError("Для Wi-Fi потрібні аудіопристрої DenDenMushi з частотою 48 кГц.")
        }
    }
    func start(microphone: Bool) throws {
        guard let pair = WiFiAudioDevices.pair() else { throw DemoError("Спочатку встанови аудіопристрої DenDenMushi Wi-Fi.") }
        mic = pair.mic.id; speaker = pair.speaker.id
        try verify(speaker, input: true); try verify(mic, input: false)
        do {
            let capture = outgoing
            guard AudioDeviceCreateIOProcIDWithBlock(&speakerProc, speaker, nil, { _, input, _, output, _ in
                let source = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
                if source.count == 1, source[0].mNumberChannels == 2, let data = source[0].mData {
                    capture.push(data.assumingMemoryBound(to: Float.self), frames: Int(source[0].mDataByteSize) / 8)
                }
                for buffer in UnsafeMutableAudioBufferListPointer(output) {
                    if let data = buffer.mData { memset(data, 0, Int(buffer.mDataByteSize)) }
                }
            }) == noErr, AudioDeviceStart(speaker, speakerProc) == noErr else { throw DemoError("Не вдалося відкрити Wi-Fi динаміки.") }
            if microphone {
                let playback = incoming
                guard AudioDeviceCreateIOProcIDWithBlock(&micProc, mic, nil, { _, _, _, output, _ in
                    let target = UnsafeMutableAudioBufferListPointer(output)
                    for buffer in target { if let data = buffer.mData { memset(data, 0, Int(buffer.mDataByteSize)) } }
                    if target.count == 1, target[0].mNumberChannels == 2, let data = target[0].mData {
                        playback.pop(data.assumingMemoryBound(to: Float.self), frames: Int(target[0].mDataByteSize) / 8)
                    }
                }) == noErr, AudioDeviceStart(mic, micProc) == noErr else { throw DemoError("Не вдалося відкрити Wi-Fi мікрофон.") }
            }
        } catch { stop(); throw error }
    }
    func stop() {
        if let proc = speakerProc { AudioDeviceStop(speaker, proc); AudioDeviceDestroyIOProcID(speaker, proc) }
        if let proc = micProc { AudioDeviceStop(mic, proc); AudioDeviceDestroyIOProcID(mic, proc) }
        speakerProc = nil; micProc = nil
    }
    deinit { stop() }
}

final class WiFiAudioTransport: @unchecked Sendable {
    private let queue = DispatchQueue(label: "dev.denden.wifi-audio", qos: .userInitiated)
    private var connection: NWConnection?
    private var timer: DispatchSourceTimer?
    private let hal = WiFiHAL()
    private var pending = Data()
    private var header = false
    private var stopped = false
    private var sending = false
    private var receivedAt = Date()
    private var maxReceiveGapMS = 0
    private var gapsOver100MS = 0
    private var completion: CheckedContinuation<Void, Error>?
    private var errorHandler: (@Sendable (String) -> Void)?
    private var established = false
    private var sentBlocks = 0
    private var receivedBlocks = 0
    private var diagnosticAt = Date.distantPast
    private let diagnosticQueue = DispatchQueue(label: "dev.denden.wifi-diagnostics", qos: .utility)
    private func diagnostic() {
        let state: [String: Any] = ["schema": 1, "time": ISO8601DateFormatter().string(from: Date()),
            "sentBlocks": sentBlocks, "receivedBlocks": receivedBlocks,
            "maxReceiveGapMS": maxReceiveGapMS, "gapsOver100MS": gapsOver100MS,
            "microphone": hal.incoming.snapshot(), "speakers": hal.outgoing.snapshot()]
        diagnosticQueue.async {
            guard let data = try? JSONSerialization.data(withJSONObject: state, options: [.sortedKeys]) else { return }
            let directory = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/denden-demo")
            let file = directory.appendingPathComponent("wifi-audio-diagnostic.json")
            try? data.write(to: file, options: .atomic)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        }
    }

    func start(microphone: Bool, failed: @escaping @Sendable (String) -> Void) async throws {
        let permission = await AVCaptureDevice.requestAccess(for: .audio)
        guard permission else { throw DemoError("Дозволь DenDenMushi доступ до мікрофона в налаштуваннях macOS.") }
        try Task.checkCancellation()
        try hal.start(microphone: microphone)
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                queue.async {
                    guard !self.stopped else { continuation.resume(throwing: CancellationError()); return }
                    self.completion = continuation; self.errorHandler = failed
                    let connection = NWConnection(host: "127.0.0.1", port: 18789, using: .tcp)
                    self.connection = connection
                    connection.stateUpdateHandler = { state in
                        switch state {
                        case .ready:
                            let request = "GET /audio/wifi/stream HTTP/1.1\r\nHost: localhost\r\nX-DenDen-Client: desktop-v1\r\nConnection: Upgrade\r\nUpgrade: denden-pcm-v1\r\nX-DenDen-Microphone: \(microphone ? 1 : 0)\r\n\r\n"
                            connection.send(content: Data(request.utf8), completion: .contentProcessed { error in
                                if let error { self.finish(error.localizedDescription) } else { self.receive() }
                            })
                        case .failed(let error): self.finish(error.localizedDescription)
                        default: break
                        }
                    }
                    connection.start(queue: self.queue)
                    self.queue.asyncAfter(deadline: .now() + 12) {
                        if !self.established && !self.stopped { self.finish("Час підключення Wi-Fi звуку вичерпано.") }
                    }
                }
            }
        }, onCancel: { self.stop() })
    }
    private func receive() {
        connection?.receive(minimumIncompleteLength: 1, maximumLength: 16384) { data, _, complete, error in
            guard !self.stopped else { return }
            if let data, !data.isEmpty {
                let gap = Int(Date().timeIntervalSince(self.receivedAt) * 1000)
                if self.established {
                    self.maxReceiveGapMS = max(self.maxReceiveGapMS, gap)
                    if gap > 100 { self.gapsOver100MS += 1 }
                }
                self.receivedAt = Date(); self.pending.append(data)
                if !self.header {
                    if let range = self.pending.range(of: Data("\r\n\r\n".utf8)) {
                        let text = String(decoding: self.pending[..<range.lowerBound], as: UTF8.self)
                        guard (text.hasPrefix("HTTP/1.0 101") || text.hasPrefix("HTTP/1.1 101")),
                              text.lowercased().contains("upgrade: denden-pcm-v1") else {
                            self.finish("Pi не запустив Wi-Fi звук. Перевір USB-карту або онови Pi."); return
                        }
                        self.pending.removeSubrange(..<range.upperBound); self.header = true
                        self.beginSending()
                    } else if self.pending.count > 8192 { self.finish("Некоректна відповідь Wi-Fi звуку."); return }
                }
                if self.header {
                    while self.pending.count >= 3840 {
                        let block = self.pending.prefix(3840)
                        var floats = [Float](repeating: 0, count: 1920)
                        block.withUnsafeBytes { bytes in
                            for i in 0..<1920 {
                                let lo = UInt16(bytes[i * 2]), hi = UInt16(bytes[i * 2 + 1]) << 8
                                floats[i] = Float(Int16(bitPattern: lo | hi)) / 32768
                            }
                        }
                        floats.withUnsafeBufferPointer { self.hal.incoming.push($0.baseAddress!, frames: 960) }
                        self.pending.removeFirst(3840); self.receivedBlocks += 1
                        if !self.established {
                            self.established = true
                            self.completion?.resume(); self.completion = nil
                        }
                    }
                }
            }
            if complete || error != nil { self.finish("Wi-Fi аудіоз’єднання перервано.") }
            else { self.receive() }
        }
    }
    private func beginSending() {
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now(), repeating: .milliseconds(20), leeway: .milliseconds(1))
        timer.setEventHandler { [weak self] in
            guard let self, !self.stopped else { return }
            if Date().timeIntervalSince(self.receivedAt) > 3 { self.finish("Wi-Fi звук перестав відповідати."); return }
            if Date().timeIntervalSince(self.diagnosticAt) >= 5 {
                self.diagnosticAt = Date(); self.diagnostic()
            }
            guard !self.sending else { return }
            var samples = [Float](repeating: 0, count: 1920)
            samples.withUnsafeMutableBufferPointer { self.hal.outgoing.pop($0.baseAddress!, frames: 960) }
            var bytes = Data(count: 3840)
            bytes.withUnsafeMutableBytes { raw in
                let buffer = raw.bindMemory(to: UInt8.self)
                for i in 0..<1920 {
                    let value = samples[i].isFinite ? samples[i] : 0
                    let signed = Int16(min(32767, max(-32768, Int(value * 32767))))
                    let bits = UInt16(bitPattern: signed)
                    buffer[i * 2] = UInt8(truncatingIfNeeded: bits); buffer[i * 2 + 1] = UInt8(truncatingIfNeeded: bits >> 8)
                }
            }
            self.sending = true
            self.connection?.send(content: bytes, completion: .contentProcessed { error in
                self.sending = false
                if let error { self.finish(error.localizedDescription) } else { self.sentBlocks += 1 }
            })
        }
        self.timer = timer; timer.resume()
    }
    private func finish(_ message: String?) {
        guard !stopped else { return }
        stopped = true; timer?.cancel(); timer = nil
        connection?.stateUpdateHandler = nil; connection?.cancel(); connection = nil
        hal.stop()
        if let completion { completion.resume(throwing: DemoError(message ?? "Wi-Fi звук зупинено.")); self.completion = nil }
        if established, let message { errorHandler?(message) }
        errorHandler = nil
    }
    func stop() { queue.async { self.finish(nil) } }
    func stopAndWait() async {
        await withCheckedContinuation { continuation in
            queue.async { self.finish(nil); continuation.resume() }
        }
    }
}
