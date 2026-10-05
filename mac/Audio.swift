import Foundation
import CoreAudio

struct SoundDevice: Identifiable {
    let id: AudioDeviceID
    let name: String
    let input: Bool
    let output: Bool
    let uid: String
    let bluetooth: Bool
    let builtIn: Bool

    init(id: AudioDeviceID, name: String, input: Bool, output: Bool, uid: String, bluetooth: Bool, builtIn: Bool = false) {
        self.id = id; self.name = name; self.input = input; self.output = output
        self.uid = uid; self.bluetooth = bluetooth; self.builtIn = builtIn
    }
}

enum SoundDevices {
    static func list() -> [SoundDevice] {
        var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else { return [] }
        var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids) == noErr else { return [] }
        return ids.compactMap { id in
            let name = UnsafeMutablePointer<CFString?>.allocate(capacity: 1)
            name.initialize(to: nil)
            defer { name.deinitialize(count: 1); name.deallocate() }
            var count = UInt32(MemoryLayout<CFString?>.size)
            var prop = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            guard AudioObjectGetPropertyData(id, &prop, 0, nil, &count, name) == noErr,
                  let title = name.pointee else { return nil }
            func hasStreams(_ scope: AudioObjectPropertyScope) -> Bool {
                var a = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams, mScope: scope, mElement: kAudioObjectPropertyElementMain)
                var n: UInt32 = 0
                return AudioObjectGetPropertyDataSize(id, &a, 0, nil, &n) == noErr && n > 0
            }
            let deviceName = title as String
            prop.mSelector = kAudioDevicePropertyDeviceUID
            count = UInt32(MemoryLayout<CFString?>.size)
            let uidOK = AudioObjectGetPropertyData(id, &prop, 0, nil, &count, name) == noErr
            let uid = uidOK ? (name.pointee as String? ?? "") : ""
            var transport: UInt32 = 0
            prop.mSelector = kAudioDevicePropertyTransportType
            count = UInt32(MemoryLayout<UInt32>.size)
            _ = AudioObjectGetPropertyData(id, &prop, 0, nil, &count, &transport)
            return SoundDevice(id: id, name: deviceName, input: hasStreams(kAudioDevicePropertyScopeInput), output: hasStreams(kAudioDevicePropertyScopeOutput), uid: uid,
                               bluetooth: transport == kAudioDeviceTransportTypeBluetooth || transport == kAudioDeviceTransportTypeBluetoothLE,
                               builtIn: transport == kAudioDeviceTransportTypeBuiltIn)
        }
    }
    static func defaultDevice(input: Bool) -> AudioDeviceID? {
        var id = AudioDeviceID(0)
        var size = UInt32(MemoryLayout.size(ofValue: id))
        var address = AudioObjectPropertyAddress(mSelector: input ? kAudioHardwarePropertyDefaultInputDevice : kAudioHardwarePropertyDefaultOutputDevice, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &id) == noErr, id != 0 else { return nil }
        return id
    }
    static func setDefault(_ id: AudioDeviceID, input: Bool) throws {
        guard list().contains(where: { $0.id == id && (input ? $0.input : $0.output) }) else { throw DemoError("Аудіопристрій уже від’єднано. Онови список.") }
        var value = id
        var address = AudioObjectPropertyAddress(mSelector: input ? kAudioHardwarePropertyDefaultInputDevice : kAudioHardwarePropertyDefaultOutputDevice, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        let result = AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, UInt32(MemoryLayout.size(ofValue: value)), &value)
        guard result == noErr else { throw DemoError("Не вдалося вибрати аудіопристрій (\(result)).") }
    }
}


enum AudioIdentity {
    static func normalize(_ value: String) -> String? {
        let address = value.replacingOccurrences(of: "-", with: ":").uppercased()
        return address.range(of: "\\A[0-9A-F]{2}(:[0-9A-F]{2}){5}\\z", options: .regularExpression) != nil ? address : nil
    }
    static func matches(uid: String, address: String) -> Bool {
        guard let address = normalize(address) else { return false }
        let normalized = uid.replacingOccurrences(of: "-", with: ":").uppercased()
        // CoreAudio Bluetooth UIDs include the adapter address and a suffix.
        return normalized == address || normalized.hasPrefix(address + ":")
    }
    static func output(in devices: [SoundDevice], address: String) -> SoundDevice? {
        let matches = devices.filter { $0.bluetooth && $0.output && Self.matches(uid: $0.uid, address: address) }
        return matches.count == 1 ? matches[0] : nil
    }
    static func input(in devices: [SoundDevice], address: String) -> SoundDevice? {
        let matches = devices.filter { $0.bluetooth && $0.input && Self.matches(uid: $0.uid, address: address) }
        return matches.count == 1 ? matches[0] : nil
    }
    static func unsupportedInput(_ device: SoundDevice, address: String?) -> Bool {
        guard let address else { return false }
        return device.input && device.bluetooth && matches(uid: device.uid, address: address)
    }
    static func builtInInput(in devices: [SoundDevice]) -> SoundDevice? {
        let candidates = devices.filter { $0.input && $0.builtIn && !$0.bluetooth }
        return candidates.count == 1 ? candidates[0] : nil
    }
    static func localAddress(report: String) -> String? {
        guard let data = report.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let adapters = root["SPBluetoothDataType"] as? [[String: Any]],
              let properties = adapters.first?["controller_properties"] as? [String: Any],
              properties["controller_state"] as? String == "attrib_on",
              let value = properties["controller_address"] as? String else { return nil }
        return normalize(value)
    }
}

enum AudioInputGuard {
    static func validateSelection(_ id: AudioDeviceID, devices: [SoundDevice], address: String?, callReady: Bool = false) throws {
        guard id != 0 else { return }
        guard let device = devices.first(where: { $0.id == id && $0.input }) else { throw DemoError("Аудіопристрій уже від’єднано. Онови список.") }
        if AudioIdentity.unsupportedInput(device, address: address) && !callReady {
            throw DemoError("Мікрофон равлика ще не передається на Mac. Вибери інший мікрофон для дзвінка.")
        }
    }

    // Called only by explicit Connect/Apply. Never reclaim the input from an
    // unrelated microphone, and recheck the real default before changing it.
    static func restoreBuiltInIfNeeded(address: String?, preferredUID: String? = nil, devices: () -> [SoundDevice] = SoundDevices.list,
                                      current: () -> AudioDeviceID? = { SoundDevices.defaultDevice(input: true) },
                                      select: (AudioDeviceID) throws -> Void = { try SoundDevices.setDefault($0, input: true) }) throws -> SoundDevice? {
        guard let address, AudioIdentity.normalize(address) != nil else { return nil }
        let first = devices()
        guard let id = current(), let selected = first.first(where: { $0.id == id }),
              AudioIdentity.unsupportedInput(selected, address: address) else { return nil }
        let fresh = devices()
        guard let id = current(), let selected = fresh.first(where: { $0.id == id }),
              AudioIdentity.unsupportedInput(selected, address: address) else { return nil }
        let preferred = fresh.filter { $0.input && $0.uid == preferredUID && !AudioIdentity.unsupportedInput($0, address: address) }
        guard let fallback = preferred.count == 1 ? preferred[0] : AudioIdentity.builtInInput(in: fresh) else {
            throw DemoError("Вибери інший мікрофон на Mac або в застосунку дзвінків, потім повтори звук.")
        }
        // A user choice made during discovery takes precedence over recovery.
        guard current() == id else { return nil }
        try select(fallback.id)
        return fallback
    }
}

enum AudioGainRange {
    static func range(kind: String, microphoneGainSupported: Bool) -> ClosedRange<Double> {
        -60...(kind == "input" && microphoneGainSupported ? 20 : 0)
    }
    static func clamp(_ value: Double, kind: String, microphoneGainSupported: Bool) -> Double? {
        guard value.isFinite, kind == "input" || kind == "output" else { return nil }
        let bounds = range(kind: kind, microphoneGainSupported: microphoneGainSupported)
        return min(bounds.upperBound, max(bounds.lowerBound, value))
    }
}

// A pending call response is permission to activate only the authenticated
// peer input, never evidence that a microphone route already works.
struct AudioCallReply {
    let adapter: String
    let ready: Bool
    init?(_ result: [String: Any], expectedAdapter: String? = nil) {
        guard result["mode"] as? String == "call", result["callRequested"] as? Bool == true,
              let raw = result["adapterAddress"] as? String, let adapter = AudioIdentity.normalize(raw),
              expectedAdapter == nil || AudioIdentity.normalize(expectedAdapter!) == adapter else { return nil }
        let ready = result["ok"] as? Bool == true && result["microphoneReady"] as? Bool == true && result["speakerReady"] as? Bool == true
        guard ready || (result["ok"] as? Bool == false && result["error"] as? String == "microphone_pending") else { return nil }
        self.adapter = adapter; self.ready = ready
    }
}

final class AudioDeviceObserver {
    private var addresses = [kAudioHardwarePropertyDevices, kAudioHardwarePropertyDefaultInputDevice].map {
        AudioObjectPropertyAddress(mSelector: $0, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    }
    private let listener: AudioObjectPropertyListenerBlock
    init(changed: @escaping () -> Void) {
        listener = { _, _ in changed() }
        for index in addresses.indices { AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addresses[index], .main, listener) }
    }
    deinit { for index in addresses.indices { AudioObjectRemovePropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addresses[index], .main, listener) } }
}
