import Foundation
import Network

@MainActor
enum PiReachability {
    @MainActor private final class Probe {
        let connection: NWConnection
        var continuation: CheckedContinuation<Bool, Never>?
        init(_ host: String, _ continuation: CheckedContinuation<Bool, Never>) {
            self.connection = NWConnection(host: NWEndpoint.Host(host), port: 22, using: .tcp)
            self.continuation = continuation
        }
        func finish(_ available: Bool) {
            guard let continuation else { return }
            self.continuation = nil
            connection.stateUpdateHandler = nil; connection.cancel()
            continuation.resume(returning: available)
        }
    }
    static func check(_ host: String, seconds: Double = 4) async -> Bool {
        return await withCheckedContinuation { continuation in
            let probe = Probe(host, continuation)
            probe.connection.stateUpdateHandler = { [weak probe] state in
                Task { @MainActor in
                    switch state {
                    case .ready: probe?.finish(true)
                    case .failed: probe?.finish(false)
                    default: break
                    }
                }
            }
            probe.connection.start(queue: .main)
            DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { probe.finish(false) }
        }
    }
}
