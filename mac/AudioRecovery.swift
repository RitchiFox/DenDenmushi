import Foundation
import IOBluetooth

enum AudioInputLookupError: Error {
    case missing
    case ambiguous
}

enum BluetoothAudioRecoveryError: Error, Equatable {
    case invalidAddress
    case deviceMissing
    case addressMismatch
    case notPaired
    case disconnectFailed(Int32)
}

enum BluetoothAudioRecovery {
    struct Connection {
        let address: String?
        let paired: Bool
        let connected: Bool
        let close: () -> Int32
    }

    // A profile reconnect can leave macOS exposing only its earlier A2DP
    // endpoint. Retry that specific absence once, never an ambiguous device.
    static func shouldRetry(error: Error, attempt: Int) -> Bool {
        guard attempt == 0, let lookup = error as? AudioInputLookupError else { return false }
        if case .missing = lookup { return true }
        return false
    }

    // IOBluetooth closes this paired device's baseband connection only. This
    // synchronous API has no timeout argument; callers must run it off the main
    // thread and await completion before reconnecting, including on cancellation.
    static func disconnectPaired(_ address: String) throws {
        try disconnectPaired(address) { normalized in
            guard let device = IOBluetoothDevice(addressString: normalized) else { return nil }
            return Connection(address: device.addressString, paired: device.isPaired(),
                              connected: device.isConnected(), close: { device.closeConnection() })
        }
    }

    // The injected lookup keeps the identity and lifecycle checks testable
    // without contacting Bluetooth hardware.
    static func disconnectPaired(_ address: String, lookup: (String) -> Connection?) throws {
        guard let expected = AudioIdentity.normalize(address) else {
            throw BluetoothAudioRecoveryError.invalidAddress
        }
        guard let connection = lookup(expected) else { throw BluetoothAudioRecoveryError.deviceMissing }
        guard let actual = connection.address, AudioIdentity.normalize(actual) == expected else {
            throw BluetoothAudioRecoveryError.addressMismatch
        }
        guard connection.paired else { throw BluetoothAudioRecoveryError.notPaired }
        guard connection.connected else { return }
        let result = connection.close()
        guard result == 0 else { throw BluetoothAudioRecoveryError.disconnectFailed(result) }
    }
}
