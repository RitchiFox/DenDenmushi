import Foundation

@main
struct Smoke {
    static func main() async throws {
        let target = try ConnectionRules.destination(user: "pi", host: "raspberrypi.local")
        assert(target == "pi@raspberrypi.local")
        for host in ["-oProxyCommand=bad", "host;command", "user@host", "host\n", ""] {
            do { _ = try ConnectionRules.destination(user: "pi", host: host); fatalError("accepted invalid host") } catch {}
        }
        for user in ["-root", "pi;bad", "pi\n", ""] {
            do { _ = try ConnectionRules.destination(user: user, host: "pi.local"); fatalError("accepted invalid user") } catch {}
        }
        assert(ConnectionRules.shellQuote("a'b") == "'a'\\''b'")
        let cancelledSetup = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            do {
                _ = try await OBSConnection().request("StartVirtualCam")
                fatalError("Cancelled setup sent an OBS request")
            } catch is CancellationError {
                // Cancellation must win before trying the socket at all.
            }
        }
        try await cancelledSetup.value
        print("Swift connection validation and cancelled OBS setup tests passed")
    }
}
