import Foundation
import CryptoKit

@main struct SetupSmoke {
    static func main() throws {
        let code = "00112233445566778899aabbccddeeff"
        let challenge = Data((0..<16).map(UInt8.init))
        let args = CommandLine.arguments
        if args.count > 1 && args[1] == "password-seal" {
            let derived = try SetupWire.credentialCode("4826") // Public synthetic fixture.
            let frame = try SetupWire.seal(["id": "interop-1", "op": "info"], code: derived, challenge: challenge)
            print(frame.base64EncodedString()); return
        }
        if args.count > 2 && args[1] == "password-open" {
            let derived = try SetupWire.credentialCode("4826")
            let info = try SetupWire.open(Data(base64Encoded: args[2])!, code: derived, challenge: challenge, id: "interop-1")
            precondition(info["hello"] as? String == "Pi → Mac"); return
        }
        if args.count > 1 && args[1] == "seal" {
            let frame = try SetupWire.seal(["id": "interop-1", "op": "info"], code: code, challenge: challenge)
            print(frame.base64EncodedString()); return
        }
        if args.count > 2 && args[1] == "open" {
            let info = try SetupWire.open(Data(base64Encoded: args[2])!, code: code, challenge: challenge, id: "interop-1")
            precondition(info["hello"] as? String == "Pi → Mac")
            print("Swift decrypted Python response"); return
        }
        let clean = try SetupWire.normalizedCode("00112233-44556677-8899AABB-CCDDEEFF")
        precondition(clean == code)
        let oldCredential = try SetupWire.credentialCode(code)
        precondition(oldCredential == code)
        let composed = try SetupWire.credentialCode("Café test words")
        let decomposed = try SetupWire.credentialCode("Cafe\u{301} test words")
        precondition(composed == decomposed)
        for invalid in ["", "123", "1234\n", " start", "end ", String(repeating: "a", count: 129)] {
            do { try SetupWire.validateCredential(invalid); fatalError("Accepted invalid credential") } catch {}
        }
        for invalid in ["", "123456", code + "\n", String(repeating: "g", count: 32)] {
            do { _ = try SetupWire.normalizedCode(invalid); fatalError("Accepted invalid code") } catch {}
        }
        let key = Curve25519.Signing.PrivateKey()
        let wire = Data([0, 0, 0, 11]) + Data("ssh-ed25519".utf8) + Data([0, 0, 0, 32]) + key.publicKey.rawRepresentation
        let ssh = "ssh-ed25519 " + wire.base64EncodedString()
        let publicKey = try SetupWire.publicKey(ssh + " comment")
        precondition(publicKey == ssh)
        let hostWire = Data([0, 0, 0, 11]) + Data("ssh-ed25519".utf8) + Data([0, 0, 0, 32]) + Curve25519.Signing.PrivateKey().publicKey.rawRepresentation
        let hostKey = "ssh-ed25519 " + hostWire.base64EncodedString()
        let saved = SavedPi(id: UUID(), name: "Synthetic Pi", hostKey: hostKey, publicKey: ssh)
        let pins = "raspberrypi.local " + hostKey + "\n192.0.2.10 " + hostKey + "\n"
        func allowed(host: String = "raspberrypi.local", user: String = "pi", local: String? = nil, known: String? = nil) -> Bool {
            SavedNetworkReconnect.allowed(saved: saved, host: host, user: user, localPublicKey: local ?? ssh, knownHosts: known ?? pins)
        }
        precondition(allowed(local: ssh + " owner comment\n"))
        precondition(allowed(host: "192.0.2.10"))
        precondition(!allowed(host: "different-pi.local"))
        precondition(!allowed(host: "-oProxyCommand=anything"))
        precondition(!allowed(user: "pi;anything"))
        precondition(!allowed(local: hostKey)) // Different Mac key.
        precondition(!allowed(local: "ssh-ed25519 invalid"))
        precondition(!allowed(known: ""))
        precondition(!allowed(known: "raspberrypi.local " + ssh + "\n")) // Different Pi key.
        precondition(!allowed(known: pins + "raspberrypi.local " + ssh + "\n"))
        precondition(!allowed(known: pins + "RaspberryPi.local " + ssh + "\n"))
        precondition(!allowed(known: "*.local " + hostKey + "\n"))
        precondition(!allowed(known: pins + "*.local " + ssh + "\n"))
        precondition(!allowed(known: "raspberrypi.local,other.local " + hostKey + "\n"))
        precondition(!allowed(known: "@cert-authority raspberrypi.local " + hostKey + "\n"))
        precondition(allowed(known: "# App pins\n" + pins + "raspberrypi.local " + hostKey + "\n"))
        precondition(!SavedNetworkReconnect.tunnelReady(log: "Authenticated to Pi.\n", running: true))
        precondition(!SavedNetworkReconnect.tunnelReady(log: "debug1: Entering interactive session.\n", running: false))
        precondition(!SavedNetworkReconnect.tunnelReady(log: "banner debug1: Entering interactive session.\n", running: true))
        precondition(SavedNetworkReconnect.tunnelReady(log: "debug1: Entering interactive session.\n", running: true))
        do { _ = try SetupWire.publicKey("ssh-ed25519 YWJj"); fatalError("Accepted invalid key") } catch {}
        let combined = try SetupWire.seal(["id": "1", "op": "info"], code: code, challenge: challenge)
        do { _ = try SetupWire.open(combined, code: code, challenge: challenge, id: "1"); fatalError("Reflection accepted") } catch {}
        if args.count > 2 && args[1] == "askpass" {
            let secret = "FAKE password used only by test"
            let server = try AskpassServer(password: secret, executable: args[2])
            defer { server.stop() }
            let reply = try Bootstrap.run(server.helper.path, ["test@pi's password:"], env: server.environment)
            precondition(reply.trimmingCharacters(in: .newlines) == secret)
            do { _ = try Bootstrap.run(server.helper.path, ["Unsupported private-key passphrase prompt"], env: server.environment); fatalError("Unknown prompt accepted") } catch {}
            let script = try String(contentsOf: server.helper, encoding: .utf8)
            precondition(!script.contains(secret))
            precondition(!server.environment.values.contains(secret))
            print("ASKPASS child process / private socket / unknown prompt checks passed")
        }
        print("Setup Swift validation and direction separation passed")
    }
}
