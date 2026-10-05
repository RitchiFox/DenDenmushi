import Foundation
import CryptoKit
import Security
import CommonCrypto

struct SavedPi: Codable {
    let id: UUID
    let name: String
    let hostKey: String
    let publicKey: String
    static var current: SavedPi? {
        get { UserDefaults.standard.data(forKey: "savedPi").flatMap { try? JSONDecoder().decode(SavedPi.self, from: $0) } }
        set { UserDefaults.standard.set(try? JSONEncoder().encode(newValue), forKey: "savedPi") }
    }
}

enum SavedNetworkReconnect {
    // Accept only the plain, exact pins written by SetupFiles.pin. Hashed,
    // wildcard, alias and marked entries require the normal Bluetooth path.
    static func allowed(saved: SavedPi, host: String, user: String, localPublicKey: String, knownHosts: String) -> Bool {
        guard (try? ConnectionRules.destination(user: user, host: host)) != nil,
              let localKey = try? SetupWire.publicKey(localPublicKey),
              let savedKey = try? SetupWire.publicKey(saved.publicKey), localKey == savedKey,
              let hostKey = try? SetupWire.publicKey(saved.hostKey) else { return false }
        var found = false
        for line in knownHosts.split(whereSeparator: \.isNewline) {
            let parts = line.split(whereSeparator: \.isWhitespace)
            if parts.isEmpty || parts[0].hasPrefix("#") { continue }
            guard parts.count == 3,
                  (try? ConnectionRules.destination(user: user, host: String(parts[0]))) != nil,
                  let entryKey = try? SetupWire.publicKey(parts[1...].joined(separator: " ")) else { return false }
            if parts[0].lowercased() == host.lowercased() {
                guard parts[0] == Substring(host), entryKey == hostKey else { return false }
                found = true
            }
        }
        return found
    }
    static func tunnelReady(log: String, running: Bool) -> Bool {
        running && log.split(whereSeparator: \.isNewline).contains("debug1: Entering interactive session.")
    }
}

enum SetupWire {
    static let service = "1dede001-7a4a-4a0a-a800-55dede000001"
    static let hello = "1dede002-7a4a-4a0a-a800-55dede000001"
    static let request = "1dede003-7a4a-4a0a-a800-55dede000001"
    static let response = "1dede004-7a4a-4a0a-a800-55dede000001"
    static let prefix = Data("denden-setup-v1:".utf8)
    static func normalizedCode(_ code: String) throws -> String {
        let clean = code.replacingOccurrences(of: "-", with: "").replacingOccurrences(of: " ", with: "").lowercased()
        guard clean.range(of: "\\A[0-9a-f]{32}\\z", options: .regularExpression) != nil else { throw DemoError("Введи код равлика: 32 символи. Його показує застосунок після першої підготовки Pi.") }
        return clean
    }
    static func validateCredential(_ value: String) throws {
        if (try? normalizedCode(value)) != nil { return }
        guard (4...128).contains(value.count), !value.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }), value == value.trimmingCharacters(in: .whitespacesAndNewlines) else {
            throw DemoError("Введи пароль равлика: 4–128 символів, без пробілів на початку й у кінці.")
        }
    }
    static func credentialCode(_ value: String) throws -> String {
        if let code = try? normalizedCode(value) { return code }
        try validateCredential(value)
        // A stable, versioned domain permits the same demo PIN on another Mac.
        // This slows guessing; it cannot give a short PIN random-code entropy.
        let password = Array(value.precomposedStringWithCanonicalMapping.utf8)
        let salt = Array("denden-user-password-v1:".utf8)
        var output = [UInt8](repeating: 0, count: 16)
        let status = password.withUnsafeBytes { p in salt.withUnsafeBytes { s in
            CCKeyDerivationPBKDF(CCPBKDFAlgorithm(kCCPBKDF2), p.baseAddress!.assumingMemoryBound(to: Int8.self), password.count,
                                s.baseAddress!.assumingMemoryBound(to: UInt8.self), salt.count,
                                CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA256), 600_000, &output, output.count)
        } }
        guard status == kCCSuccess else { throw DemoError("Не вдалося підготувати пароль равлика.") }
        return output.map { String(format: "%02x", $0) }.joined()
    }
    static func key(_ code: String) throws -> SymmetricKey { SymmetricKey(data: SHA256.hash(data: prefix + Data(try normalizedCode(code).utf8))) }
    static func seal(_ body: [String: Any], code: String, challenge: Data) throws -> Data {
        guard challenge.count == 16 else { throw DemoError("Невірна відповідь Bluetooth. Підключися повторно.") }
        let data = try JSONSerialization.data(withJSONObject: body)
        return try AES.GCM.seal(data, using: key(code), authenticating: prefix + challenge + Data(":request".utf8)).combined!
    }
    static func open(_ data: Data, code: String, challenge: Data, id: String) throws -> [String: Any] {
        let box = try AES.GCM.SealedBox(combined: data)
        let plain = try AES.GCM.open(box, using: key(code), authenticating: prefix + challenge + Data(":response".utf8))
        guard let result = try JSONSerialization.jsonObject(with: plain) as? [String: Any], result["id"] as? String == id else { throw DemoError("Bluetooth повернув невірну відповідь.") }
        guard result["ok"] as? Bool == true, let body = result["data"] as? [String: Any] else { throw DemoError(result["error"] as? String ?? "Pi відхилив налаштування.") }
        return body
    }
    static func publicKey(_ value: String) throws -> String {
        let parts = value.split(whereSeparator: \.isWhitespace)
        let prefix: [UInt8] = [0, 0, 0, 11] + Array("ssh-ed25519".utf8) + [0, 0, 0, 32]
        guard parts.count >= 2, parts[0] == "ssh-ed25519", let bytes = Data(base64Encoded: String(parts[1])), bytes.count == 51, bytes.prefix(19) == Data(prefix) else { throw DemoError("Pi повернув невірний ключ доступу.") }
        return "ssh-ed25519 " + parts[1]
    }
}

enum SetupFiles {
    static var directory: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/denden-demo") }
    static var key: URL { directory.appendingPathComponent("id_ed25519") }
    static var knownHosts: URL { directory.appendingPathComponent("known_hosts") }
    static func canReconnect(saved: SavedPi, host: String, user: String) -> Bool {
        guard let publicKey = try? String(contentsOf: key.appendingPathExtension("pub"), encoding: .utf8),
              let pins = try? String(contentsOf: knownHosts, encoding: .utf8) else { return false }
        return SavedNetworkReconnect.allowed(saved: saved, host: host, user: user, localPublicKey: publicKey, knownHosts: pins)
    }
    static func prepare() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    }
    static func pin(_ info: [String: Any], preferredHost: String? = nil) throws -> (String, String) {
        guard let user = info["user"] as? String, let host = info["host"] as? String, let value = info["hostKey"] as? String else { throw DemoError("Pi не надіслав адресу для камери.") }
        let key = try SetupWire.publicKey(value)
        let addresses = info["addresses"] as? [String] ?? []
        let names = Array(Set([host] + addresses + (preferredHost.map { [$0] } ?? []))).sorted()
        for name in names { _ = try ConnectionRules.destination(user: user, host: name) }
        try prepare()
        let previous = (try? String(contentsOf: knownHosts, encoding: .utf8)) ?? ""
        var lines = previous.split(separator: "\n").map(String.init)
        for name in names {
            let matches = lines.filter { $0.split(separator: " ").first == Substring(name) }
            guard matches.allSatisfy({ $0 == name + " " + key }) else { throw DemoError("За цією адресою збережений інший Pi. Адресу не замінено автоматично: \(name).") }
            if matches.isEmpty { lines.append(name + " " + key) }
        }
        let text = lines.joined(separator: "\n") + "\n"
        try Data(text.utf8).write(to: knownHosts, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: knownHosts.path)
        return (addresses.first ?? host, user)
    }
    static func storeCode(_ code: String, account: String = "last-device") throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "dev.denden.demo.setup", kSecAttrAccount as String: account]
        let value = [kSecValueData as String: Data(code.utf8)]
        var status = SecItemUpdate(query as CFDictionary, value as CFDictionary)
        if status == errSecItemNotFound {
            var item = query.merging(value) { _, new in new }
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            status = SecItemAdd(item as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw DemoError("Не вдалося зберегти код у В’язці ключів. Збережи його кнопкою «Копіювати код».") }
    }
    static func loadCode(account: String = "last-device") -> String {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "dev.denden.demo.setup", kSecAttrAccount as String: account, kSecReturnData as String: true]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return "" }
        return String(decoding: data, as: UTF8.self)
    }
    static func forgetClient(_ saved: SavedPi) throws {
        for account in [saved.id.uuidString, "last-device"] {
            let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "dev.denden.demo.setup", kSecAttrAccount as String: account]
            let status = SecItemDelete(query as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw DemoError("Доступ на Pi видалено, але В’язка ключів не дозволила очистити код. Повтори скидання.") }
        }
        for file in [key, key.appendingPathExtension("pub"), knownHosts] {
            if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
        }
        for name in ["savedPi", "piHost", "piUser", "setupComplete"] { UserDefaults.standard.removeObject(forKey: name) }
    }
}
