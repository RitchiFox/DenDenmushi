// Read-only bench diagnostic; does not print the OBS password or screenshot data.
import Foundation
@main struct LiveOBS {
    static func main() async throws {
        let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json")
        let config = try JSONSerialization.jsonObject(with: Data(contentsOf: path)) as! [String: Any]
        let client = OBSConnection()
        try await client.connect(port: config["server_port"] as? Int ?? 4455, password: config["server_password"] as? String ?? "")
        for (name, values) in [("GetVirtualCamStatus", [:]), ("GetProfileList", [:]), ("GetVideoSettings", [:]), ("GetSceneCollectionList", [:]), ("GetSceneList", [:]), ("GetInputSettings", ["inputName": "DenDen Camera"]), ("GetMediaInputStatus", ["inputName": "DenDen Camera"]), ("GetSourceActive", ["sourceName": "DenDen Camera"])] {
            do {
                let result = try await client.request(name, values)
                print(name + ": " + String(decoding: try JSONSerialization.data(withJSONObject: result, options: .sortedKeys), as: UTF8.self))
            } catch { print(name + ": " + error.localizedDescription) }
        }
        do {
            let result = try await client.request("GetSourceScreenshot", ["sourceName": "DenDen Camera", "imageFormat": "jpg", "imageWidth": 640])
            print("Preview data length: \((result["imageData"] as? String)?.count ?? 0)")
        } catch { print(error.localizedDescription) }
        await client.close()
    }
}
