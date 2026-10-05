import Foundation

@main
struct AudioSmoke {
    static func main() {
        let a = "AA:BB:CC:DD:EE:FF"
        let intended = SoundDevice(id: 5, name: "Renamed snail", input: false, output: true, uid: "AA-BB-CC-DD-EE-FF:output", bluetooth: true)
        let wrong = SoundDevice(id: 6, name: "DenDenMushi", input: false, output: true, uid: "11-22-33-44-55-66:output", bluetooth: true)
        precondition(AudioIdentity.output(in: [wrong,intended], address: a)?.id == 5)
        precondition(AudioIdentity.output(in: [wrong], address: a) == nil)
        precondition(AudioIdentity.output(in: [intended,intended], address: a) == nil)
        precondition(!AudioIdentity.matches(uid: "AA-BB-CC-DD-EE-FFA:output", address: a))
        precondition(AudioIdentity.normalize(a + "\n") == nil)
        let report = #"{"SPBluetoothDataType":[{"controller_properties":{"controller_state":"attrib_on","controller_address":"AA-BB-CC-DD-EE-FF"}}]}"#
        precondition(AudioIdentity.localAddress(report: report) == a)
        precondition(AudioIdentity.localAddress(report: report.replacingOccurrences(of: "attrib_on", with: "attrib_off")) == nil)
        precondition(AudioIdentity.localAddress(report: "{}") == nil)
        let snailMic = SoundDevice(id: 7, name: "Renamed snail", input: true, output: false, uid: "AA-BB-CC-DD-EE-FF:input", bluetooth: true)
        let otherMic = SoundDevice(id: 8, name: "DenDenMushi", input: true, output: false, uid: "11-22-33-44-55-66:input", bluetooth: true)
        let internalMic = SoundDevice(id: 9, name: "Localized internal microphone", input: true, output: false, uid: "BuiltInMicrophoneDevice", bluetooth: false, builtIn: true)
        let usbMic = SoundDevice(id: 10, name: "USB mic", input: true, output: false, uid: "usb:mic", bluetooth: false)
        let virtualMic = SoundDevice(id: 11, name: "MacBook microphone", input: true, output: false, uid: "virtual:mic", bluetooth: false)
        let fakeMic = SoundDevice(id: 12, name: "DenDenMushi", input: true, output: false, uid: snailMic.uid, bluetooth: false)
        let microphones = [snailMic, otherMic, internalMic, usbMic, virtualMic, fakeMic]
        precondition(AudioIdentity.unsupportedInput(snailMic, address: a))
        precondition(!AudioIdentity.unsupportedInput(otherMic, address: a))
        precondition(!AudioIdentity.unsupportedInput(fakeMic, address: a))
        precondition(!AudioIdentity.unsupportedInput(snailMic, address: nil))
        precondition(AudioIdentity.builtInInput(in: microphones)?.id == internalMic.id)
        precondition(AudioIdentity.builtInInput(in: [virtualMic]) == nil)
        precondition(AudioIdentity.builtInInput(in: [internalMic, internalMic]) == nil)
        do {
            try AudioInputGuard.validateSelection(snailMic.id, devices: microphones, address: a)
            preconditionFailure("Unsupported snail input must be rejected before applying outputs")
        } catch { }
        try! AudioInputGuard.validateSelection(otherMic.id, devices: microphones, address: a)
        try! AudioInputGuard.validateSelection(0, devices: microphones, address: a)
        func recovery(_ defaults: [UInt32?], list: [SoundDevice] = microphones, address: String? = a) throws -> [UInt32] {
            var next = 0, selected: [UInt32] = []
            _ = try AudioInputGuard.restoreBuiltInIfNeeded(address: address, devices: { list }, current: {
                defer { next += 1 }
                return defaults[min(next, defaults.count - 1)]
            }, select: { selected.append($0) })
            return selected
        }
        precondition(try! recovery([snailMic.id]) == [internalMic.id])
        for current in [otherMic.id, internalMic.id, usbMic.id, virtualMic.id, fakeMic.id, UInt32(999)] {
            precondition(try! recovery([current]).isEmpty)
        }
        precondition(try! recovery([nil]).isEmpty)
        precondition(try! recovery([snailMic.id], address: nil).isEmpty)
        precondition(try! recovery([snailMic.id, usbMic.id]).isEmpty)
        precondition(try! recovery([snailMic.id, snailMic.id, otherMic.id]).isEmpty)
        do {
            _ = try recovery([snailMic.id], list: [snailMic, virtualMic])
            preconditionFailure("No arbitrary fallback if physical built-in microphone is absent")
        } catch { }
        do {
            _ = try recovery([snailMic.id], list: [snailMic, internalMic, internalMic])
            preconditionFailure("Ambiguous fallback must not be selected")
        } catch { }
        let newOutput = SoundDevice(id: 55, name: "Renamed snail", input: false, output: true, uid: "AA-BB-CC-DD-EE-FF:output:reconfigured", bluetooth: true)
        precondition(AudioIdentity.output(in: [wrong, newOutput], address: a)?.id == 55)
        precondition(AudioIdentity.input(in: microphones, address: a)?.id == snailMic.id)
        precondition(AudioIdentity.input(in: [otherMic, fakeMic], address: a) == nil)
        precondition(AudioIdentity.input(in: [snailMic, snailMic], address: a) == nil)
        try! AudioInputGuard.validateSelection(snailMic.id, devices: microphones, address: a, callReady: true)
        var pending: [String: Any] = ["mode": "call", "callRequested": true, "ok": false, "error": "microphone_pending", "adapterAddress": a, "microphoneReady": false, "speakerReady": false]
        precondition(AudioCallReply(pending)?.ready == false)
        precondition(AudioCallReply(pending, expectedAdapter: "11:22:33:44:55:66") == nil)
        pending["adapterAddress"] = "not-an-address"
        precondition(AudioCallReply(pending) == nil)
        pending["adapterAddress"] = a
        pending["error"] = "usb_input_missing"
        precondition(AudioCallReply(pending) == nil)
        pending["ok"] = true; pending["microphoneReady"] = true
        precondition(AudioCallReply(pending) == nil) // speaker route is still absent
        pending["speakerReady"] = true
        precondition(AudioCallReply(pending, expectedAdapter: a)?.ready == true)
        pending["mode"] = "playback"
        precondition(AudioCallReply(pending) == nil)
        pending["mode"] = "call"; pending["callRequested"] = false
        precondition(AudioCallReply(pending) == nil)
        precondition(AudioCallReply([:]) == nil) // older backends cannot enable the microphone
        var restored: [UInt32] = []
        _ = try! AudioInputGuard.restoreBuiltInIfNeeded(address: a, preferredUID: usbMic.uid, devices: { microphones }, current: { snailMic.id }, select: { restored.append($0) })
        precondition(restored == [usbMic.id]) // return to the previous USB mic, not always built-in
        restored.removeAll()
        _ = try! AudioInputGuard.restoreBuiltInIfNeeded(address: a, preferredUID: usbMic.uid, devices: { microphones }, current: { otherMic.id }, select: { restored.append($0) })
        precondition(restored.isEmpty) // a user choice made in another application is preserved
        precondition(AudioGainRange.range(kind: "input", microphoneGainSupported: true) == -60...20)
        precondition(AudioGainRange.range(kind: "input", microphoneGainSupported: false) == -60...0)
        precondition(AudioGainRange.range(kind: "output", microphoneGainSupported: true) == -60...0)
        precondition(AudioGainRange.clamp(12, kind: "input", microphoneGainSupported: true) == 12)
        precondition(AudioGainRange.clamp(30, kind: "input", microphoneGainSupported: true) == 20)
        precondition(AudioGainRange.clamp(12, kind: "input", microphoneGainSupported: false) == 0)
        precondition(AudioGainRange.clamp(12, kind: "output", microphoneGainSupported: true) == 0)
        precondition(AudioGainRange.clamp(-80, kind: "input", microphoneGainSupported: true) == -60)
        precondition(AudioGainRange.clamp(.nan, kind: "input", microphoneGainSupported: true) == nil)
        precondition(AudioGainRange.clamp(.infinity, kind: "output", microphoneGainSupported: true) == nil)
        precondition(AudioGainRange.clamp(12, kind: "unknown", microphoneGainSupported: true) == nil)
        var queue = AudioLevelQueue()
        queue.editing.insert("output")
        queue.stage("output", db: -30, muted: false)
        precondition(queue.nextKind == nil && queue.protects("output"))
        queue.editing.remove("output")
        let old = queue.pending["output"]!
        queue.stage("output", db: -10, muted: false)
        queue.stage("input", db: -5, muted: true)
        queue.acknowledge("output", edit: old)
        precondition(queue.pending["output"]?.db == -10)
        precondition(queue.protects("output") && queue.protects("input"))
        queue.acknowledge("output", edit: queue.pending["output"]!)
        precondition(!queue.protects("output") && queue.protects("input"))
        precondition(queue.nextKind == "input")
        let mute = queue.pending["input"]!
        queue.stage("input", db: -5, muted: false)
        queue.acknowledge("input", edit: mute)
        precondition(queue.pending["input"]?.muted == false)
        queue.acknowledge("input", edit: queue.pending["input"]!)
        queue.editing.insert("input")
        precondition(queue.protects("input"))
        queue.editing.remove("input")
        precondition(queue.pending.isEmpty && !queue.protects("input"))
        if CommandLine.arguments.contains("--devices") {
            for device in SoundDevices.list() { print(device.name, device.uid, device.bluetooth, device.output) }

        }
        print("Audio identity and out-of-order volume reply checks passed")
    }
}
