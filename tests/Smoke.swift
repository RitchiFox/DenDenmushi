import Foundation

@main
struct Smoke {
    static func main() throws {
        let target = try ConnectionRules.destination(user: "pi", host: "raspberrypi.local")
        assert(target == "pi@raspberrypi.local")
        for host in ["-oProxyCommand=bad", "host;command", "user@host", "host\n", ""] {
            do { _ = try ConnectionRules.destination(user: "pi", host: host); fatalError("accepted invalid host") } catch {}
        }
        for user in ["-root", "pi;bad", "pi\n", ""] {
            do { _ = try ConnectionRules.destination(user: user, host: "pi.local"); fatalError("accepted invalid user") } catch {}
        }
        assert(ConnectionRules.shellQuote("a'b") == "'a'\\''b'")
        print("Swift connection validation tests passed")
    }
}
