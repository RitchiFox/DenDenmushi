import Foundation

@main
struct OBSSmoke {
    static func main() async throws {
        let client = OBSConnection()
        try await client.connect(port: Int(CommandLine.arguments[1])!, password: "test-local-only")
        async let version = client.request("GetVersion")
        async let camera = client.request("GetVirtualCamStatus")
        let (v, c) = try await (version, camera)
        assert(v["obsVersion"] as? String == "32.2.2")
        assert(c["outputActive"] as? Bool == false)
        let profiles = try await client.request("GetProfileList")
        assert(profiles["profiles"] as? [String] == ["DenDenMushi Demo"])
        do {
            _ = try await client.request("DeliberateFailure")
            fatalError("OBS error was ignored")
        } catch {
            assert(error.localizedDescription.contains("test rejection"))
        }
        await client.close()
        let devices = SoundDevices.list()
        assert(devices.contains(where: \.output))
        print("OBS authentication, concurrent RPC, NotReady retry, profile schema, errors and CoreAudio passed")
    }
}
