import Foundation
import CryptoKit

struct DemoError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

enum ConnectionRules {
    static func destination(user: String, host: String) throws -> String {
        guard user.range(of: "\\A[a-z_][a-z0-9_-]*\\z", options: .regularExpression) != nil,
              host.count <= 253,
              host.range(of: "\\A[A-Za-z0-9][A-Za-z0-9.-]*\\z", options: .regularExpression) != nil else {
            throw DemoError("Вкажи ім’я користувача Pi та адресу на кшталт raspberrypi.local або 192.0.2.10.")
        }
        return user + "@" + host
    }
    static func shellQuote(_ value: String) -> String { "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'" }
    static func authentication(password: String, salt: String, challenge: String) -> String {
        func digest(_ s: String) -> String { Data(SHA256.hash(data: Data(s.utf8))).base64EncodedString() }
        return digest(digest(password + salt) + challenge)
    }
}

actor OBSConnection {
    private var socket: URLSessionWebSocketTask?
    private var requesting = false
    func close() { socket?.cancel(with: .normalClosure, reason: nil); socket = nil }
    private func receive() async throws -> [String: Any] {
        guard let socket else { throw DemoError("Немає з’єднання з OBS.") }
        // URLSession's request timeout alone does not bound a pending receive.
        let deadline = DispatchWorkItem { socket.cancel(with: .goingAway, reason: nil) }
        DispatchQueue.global().asyncAfter(deadline: .now() + 8, execute: deadline)
        defer { deadline.cancel() }
        let result = try await socket.receive()
        let data: Data
        switch result {
        case .data(let d): data = d
        case .string(let s): data = Data(s.utf8)
        @unknown default: throw DemoError("Невідоме повідомлення OBS.")
        }
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw DemoError("Некоректна відповідь OBS.")
        }
        return object
    }
    private func send(_ message: [String: Any]) async throws {
        guard let socket else { throw DemoError("OBS не підключений.") }
        let data = try JSONSerialization.data(withJSONObject: message)
        try await socket.send(.string(String(decoding: data, as: UTF8.self)))
    }
    func connect(port: Int, password: String) async throws {
        close()
        let task = URLSession.shared.webSocketTask(with: URL(string: "ws://127.0.0.1:\(port)")!)
        socket = task
        task.resume()
        let hello = try await receive()
        guard hello["op"] as? Int == 0, let d = hello["d"] as? [String: Any] else {
            throw DemoError("OBS не підтримує потрібний протокол керування.")
        }
        var identify: [String: Any] = ["rpcVersion": 1, "eventSubscriptions": 0]
        if let auth = d["authentication"] as? [String: String],
           let salt = auth["salt"], let challenge = auth["challenge"] {
            identify["authentication"] = ConnectionRules.authentication(password: password, salt: salt, challenge: challenge)
        }
        try await send(["op": 1, "d": identify])
        guard try await receive()["op"] as? Int == 2 else { throw DemoError("OBS відхилив доступ.") }
    }
    func request(_ type: String, _ values: [String: Any] = [:]) async throws -> [String: Any] {
        // Actor methods are reentrant across await: serialize the whole exchange
        // so a screenshot request cannot consume a StopVirtualCam response.
        while requesting { try await Task.sleep(nanoseconds: 20_000_000) }
        requesting = true
        defer { requesting = false }
        for attempt in 0..<20 {
            let id = UUID().uuidString
            try await send(["op": 6, "d": ["requestType": type, "requestId": id, "requestData": values]])
            while true {
                let packet = try await receive()
                guard packet["op"] as? Int == 7, let d = packet["d"] as? [String: Any], d["requestId"] as? String == id else { continue }
                let status = d["requestStatus"] as? [String: Any] ?? [:]
                if status["result"] as? Bool != true {
                    // OBS 5.3+: scene/profile transitions temporarily return 207.
                    if status["code"] as? Int == 207 && attempt < 19 {
                        try await Task.sleep(nanoseconds: 400_000_000)
                        break
                    }
                    throw DemoError("OBS · \(type): \(status["comment"] ?? status["code"] ?? "error")")
                }
                return d["responseData"] as? [String: Any] ?? [:]
            }
        }
        throw DemoError("OBS ще не готовий. Повтори підключення.")
    }
}
