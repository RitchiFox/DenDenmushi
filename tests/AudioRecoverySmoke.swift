import Foundation

@main
struct AudioRecoverySmoke {
    static func main() throws {
        let address = "B8:27:EB:93:81:C2"
        precondition(BluetoothAudioRecovery.shouldRetry(error: AudioInputLookupError.missing, attempt: 0))
        for attempt in [-1, 1, 2] {
            precondition(!BluetoothAudioRecovery.shouldRetry(error: AudioInputLookupError.missing, attempt: attempt))
        }
        precondition(!BluetoothAudioRecovery.shouldRetry(error: AudioInputLookupError.ambiguous, attempt: 0))
        precondition(!BluetoothAudioRecovery.shouldRetry(error: CancellationError(), attempt: 0))
        precondition(!BluetoothAudioRecovery.shouldRetry(error: DemoError("Bluetooth unavailable"), attempt: 0))

        func expect(_ expected: BluetoothAudioRecoveryError, _ body: () throws -> Void) {
            do {
                try body()
                preconditionFailure("Expected \(expected)")
            } catch {
                precondition((error as? BluetoothAudioRecoveryError) == expected)
            }
        }

        var lookedUp: [String] = [], closes = 0
        let matching = BluetoothAudioRecovery.Connection(address: "b8-27-eb-93-81-c2", paired: true,
                                                         connected: true, close: { closes += 1; return 0 })
        expect(.invalidAddress) {
            try BluetoothAudioRecovery.disconnectPaired(address + ":other") { value in
                lookedUp.append(value); return matching
            }
        }
        precondition(lookedUp.isEmpty && closes == 0)
        expect(.deviceMissing) {
            try BluetoothAudioRecovery.disconnectPaired(address) { value in lookedUp.append(value); return nil }
        }
        precondition(lookedUp == [address] && closes == 0)

        for actual in [nil, "11:22:33:44:55:66", address + "-tsco"] as [String?] {
            expect(.addressMismatch) {
                try BluetoothAudioRecovery.disconnectPaired(address) { _ in
                    .init(address: actual, paired: true, connected: true, close: { closes += 1; return 0 })
                }
            }
        }
        expect(.notPaired) {
            try BluetoothAudioRecovery.disconnectPaired(address) { _ in
                .init(address: address, paired: false, connected: true, close: { closes += 1; return 0 })
            }
        }
        precondition(closes == 0)

        try BluetoothAudioRecovery.disconnectPaired(address) { _ in
            .init(address: address, paired: true, connected: false, close: { closes += 1; return 0 })
        }
        precondition(closes == 0)
        try BluetoothAudioRecovery.disconnectPaired("b8-27-eb-93-81-c2") { normalized in
            precondition(normalized == address); return matching
        }
        precondition(closes == 1)
        expect(.disconnectFailed(-1)) {
            try BluetoothAudioRecovery.disconnectPaired(address) { _ in
                .init(address: address, paired: true, connected: true, close: { closes += 1; return -1 })
            }
        }
        precondition(closes == 2)
        print("Audio recovery policy and guarded disconnect checks passed (no Bluetooth hardware used).")
    }
}
