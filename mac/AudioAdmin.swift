import Foundation
import Combine

@MainActor
final class AudioAdminModel: ObservableObject {
    typealias Request = (String, [String: Any]?, String?) async throws -> (Int, [String: Any])
    @Published private(set) var unlocked = false
    @Published private(set) var busy = false
    @Published private(set) var message = ""
    var onLock: (() -> Void)?
    private var token: String?
    private var expiresAt = 0.0
    private var generation = UUID()
    private var expiration: Task<Void, Never>?
    private let requestOverride: Request?

    init(request: Request? = nil) { requestOverride = request }

    // Credentials and sessions deliberately live only in memory. The Pi
    // independently verifies expiry for every level mutation.
    var sessionToken: String? {
        guard unlocked, ProcessInfo.processInfo.systemUptime < expiresAt else { return nil }
        return token
    }

    private static func request(_ path: String, body: [String: Any]?, token: String?) async throws -> (Int, [String: Any]) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:18789" + path)!)
        request.httpMethod = "POST"
        request.timeoutInterval = 10
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("desktop-v1", forHTTPHeaderField: "X-DenDen-Client")
        if let token { request.setValue(token, forHTTPHeaderField: "X-DenDen-Audio-Admin") }
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse,
              let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw URLError(.badServerResponse)
        }
        return (response.statusCode, result)
    }

    func unlock(pin: String, connected: Bool, supported: Bool) async {
        guard !busy else { return }
        lock()
        guard connected else { message = "Підключи равлика, щоб відкрити налаштування звуку."; return }
        guard supported else { message = "Онови сервіс Pi для доступу суперадміна."; return }
        guard (4...64).contains(pin.utf8.count), pin.utf8.allSatisfy({ (48...57).contains($0) }) else {
            message = "Введи код суперадміна."; return
        }
        busy = true
        let current = generation
        let started = ProcessInfo.processInfo.systemUptime
        let request = requestOverride ?? Self.request
        do {
            let (status, result) = try await request("/audio/admin/unlock", ["pin": pin], nil)
            guard generation == current, !Task.isCancelled else {
                if generation == current { lock() }
                if let stale = result["token"] as? String { _ = try? await request("/audio/admin/lock", nil, stale) }
                return
            }
            busy = false
            guard status == 200, result["ok"] as? Bool == true else {
                switch result["error"] as? String {
                case "admin_invalid_pin": message = "Неправильний код суперадміна."
                case "admin_rate_limited": message = "Забагато спроб. Спробуй ще раз пізніше."
                case "admin_unconfigured": message = "Код суперадміна ще не налаштовано на равлику."
                default: message = "Не вдалося відкрити налаштування звуку. Спробуй ще раз."
                }
                return
            }
            guard let token = result["token"] as? String, (32...128).contains(token.utf8.count),
                  token.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95 }),
                  let lifetime = result["expiresIn"] as? Double, lifetime.isFinite, lifetime > 0, lifetime <= 600 else {
                message = "Не вдалося відкрити налаштування звуку. Спробуй ще раз."; return
            }
            expiresAt = started + lifetime
            guard expiresAt > ProcessInfo.processInfo.systemUptime else { invalidate(); return }
            self.token = token
            unlocked = true
            expiration = Task { [weak self] in
                let remaining = max(0, started + lifetime - ProcessInfo.processInfo.systemUptime)
                do { try await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000)) }
                catch { return }
                guard let self, self.generation == current else { return }
                self.invalidate()
            }
        } catch {
            guard generation == current else { return }
            busy = false
            message = "Не вдалося відкрити налаштування звуку. Спробуй ще раз."
        }
    }

    func lock() {
        let previous = token
        generation = UUID()
        expiration?.cancel(); expiration = nil
        token = nil; expiresAt = 0; unlocked = false; busy = false; message = ""
        onLock?()
        if let previous {
            let request = requestOverride ?? Self.request
            Task { _ = try? await request("/audio/admin/lock", nil, previous) }
        }
    }

    func invalidate() {
        lock()
        message = "Доступ суперадміна завершився. Введи код знову."
    }
}
