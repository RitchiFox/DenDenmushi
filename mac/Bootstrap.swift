import Foundation
import AppKit
import Darwin

// SSH invokes this executable as ASKPASS. Secrets cross a private Unix socket,
// never a file, argv, environment variable, clipboard, or log.
enum AskpassClient {
    static func address(_ path: String) throws -> sockaddr_un {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8) + [0]
        guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else { throw DemoError("Задовгий тимчасовий шлях.") }
        withUnsafeMutableBytes(of: &address.sun_path) { destination in destination.copyBytes(from: bytes) }
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        return address
    }
    static func exchange(path: String, request: Data) throws -> Data {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw DemoError("Не вдалося відкрити локальний канал.") }
        defer { close(fd) }
        var addr = try address(path)
        let result = withUnsafePointer(to: &addr) { pointer in pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) } }
        guard result == 0 else { throw DemoError("Немає вікна підготовки Pi.") }
        let file = FileHandle(fileDescriptor: fd, closeOnDealloc: false)
        try file.write(contentsOf: request)
        shutdown(fd, SHUT_WR)
        return try file.readToEnd() ?? Data()
    }
    static func main() -> Int32 {
        do {
            let env = ProcessInfo.processInfo.environment
            guard let path = env["DENDEN_ASKPASS_SOCKET"] else { return 1 }
            let request = try JSONSerialization.data(withJSONObject: ["prompt": CommandLine.arguments.dropFirst(2).joined(separator: " "), "mode": env["SSH_ASKPASS_PROMPT"] ?? "password"])
            let response = try exchange(path: path, request: request)
            guard let object = try JSONSerialization.jsonObject(with: response) as? [String: Any], object["ok"] as? Bool == true, let answer = object["answer"] as? String else { return 1 }
            try FileHandle.standardOutput.write(contentsOf: Data((answer + "\n").utf8))
            return 0
        } catch { return 1 }
    }
}

final class AskpassServer: @unchecked Sendable {
    let directory: URL
    let helper: URL
    let path: String
    private var fd: Int32 = -1
    init(password: String, executable: String) throws {
        directory = URL(fileURLWithPath: "/tmp/dd-" + UUID().uuidString)
        helper = directory.appendingPathComponent("askpass")
        path = directory.appendingPathComponent("socket").path
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        let script = "#!/bin/sh\nexec " + ConnectionRules.shellQuote(executable) + " --askpass \"$@\"\n"
        try Data(script.utf8).write(to: helper)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helper.path)
        fd = socket(AF_UNIX, SOCK_STREAM, 0)
        var addr = try AskpassClient.address(path)
        let bound = withUnsafePointer(to: &addr) { pointer in pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) } }
        guard fd >= 0, bound == 0, listen(fd, 4) == 0 else { close(fd); try? FileManager.default.removeItem(at: directory); throw DemoError("Не вдалося підготувати безпечне введення пароля.") }
        chmod(path, 0o600)
        let listening = fd
        DispatchQueue.global().async {
            for _ in 0..<24 {
                let client = accept(listening, nil, nil)
                if client < 0 { break }
                defer { close(client) }
                var uid: uid_t = 0; var gid: gid_t = 0
                guard getpeereid(client, &uid, &gid) == 0, uid == getuid() else { continue }
                var timeout = timeval(tv_sec: 5, tv_usec: 0)
                setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
                var buffer = [UInt8](repeating: 0, count: 2048)
                var request = Data()
                while request.count < 8192 {
                    let count = read(client, &buffer, buffer.count)
                    if count <= 0 { break }
                    request.append(contentsOf: buffer.prefix(count))
                }
                guard request.count < 8192, let object = try? JSONSerialization.jsonObject(with: request) as? [String: String] else { continue }
                let prompt = object["prompt"] ?? ""
                var answer: String?
                if object["mode"] == "confirm" || prompt.contains("continue connecting") {
                    DispatchQueue.main.sync {
                        NSApp.activate(ignoringOtherApps: true)
                        let alert = NSAlert()
                        alert.messageText = L("Перше підключення до твого Pi")
                        alert.informativeText = prompt + "\n\n" + L("Підтвердь, якщо це твій Raspberry Pi.")
                        alert.addButton(withTitle: L("Це мій Pi"))
                        alert.addButton(withTitle: L("Скасувати"))
                        if alert.runModal() == .alertFirstButtonReturn { answer = "yes" }
                    }
                } else if prompt.lowercased().contains("password:") { answer = password }
                let response = (try? JSONSerialization.data(withJSONObject: ["ok": answer != nil, "answer": answer ?? ""])) ?? Data()
                // A disconnected helper must not terminate the application (SIGPIPE).
                var one: Int32 = 1
                setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
                response.withUnsafeBytes { bytes in _ = Darwin.write(client, bytes.baseAddress, bytes.count) }
            }
        }
    }
    func stop() { shutdown(fd, SHUT_RDWR); close(fd); fd = -1; try? FileManager.default.removeItem(at: directory) }
    var environment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["SSH_ASKPASS"] = helper.path; env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = "denden-setup"; env["DENDEN_ASKPASS_SOCKET"] = path; env["LC_ALL"] = "C"
        return env
    }
}

enum Bootstrap {
    static func run(_ executable: String, _ arguments: [String], env: [String: String]? = nil, input: Data? = nil, timeout: Double = 90) throws -> String {
        let process = Process(); let output = Pipe(); let stdin = Pipe()
        process.executableURL = URL(fileURLWithPath: executable); process.arguments = arguments; process.environment = env
        process.standardOutput = output; process.standardError = output
        process.standardInput = input == nil ? FileHandle.nullDevice : stdin
        try process.run()
        let deadline = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: deadline)
        defer { deadline.cancel() }
        if let input { try stdin.fileHandleForWriting.write(contentsOf: input); try stdin.fileHandleForWriting.close() }
        let result = String(decoding: (try output.fileHandleForReading.readToEnd()) ?? Data(), as: UTF8.self)
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { throw DemoError(String(result.suffix(1600))) }
        return result
    }
    static func ensureKey() throws -> String {
        try SetupFiles.prepare()
        if !FileManager.default.fileExists(atPath: SetupFiles.key.path) {
            _ = try run("/usr/bin/ssh-keygen", ["-q", "-t", "ed25519", "-N", "", "-C", "denden-demo", "-f", SetupFiles.key.path])
        }
        return try SetupWire.publicKey(String(contentsOf: SetupFiles.key.appendingPathExtension("pub"), encoding: .utf8))
    }
    static func changeCode(host: String, user: String, password: String, code: String, resources: URL, executable: String) throws -> [String: Any] {
        let target = try ConnectionRules.destination(user: user, host: host)
        guard !password.isEmpty, !password.contains("\n"), !password.contains("\r") else { throw DemoError("Введи пароль користувача Pi.") }
        let code = try SetupWire.normalizedCode(code)
        let helper = try AskpassServer(password: password, executable: executable)
        defer { helper.stop() }
        var options = ["-o", "StrictHostKeyChecking=ask", "-o", "ConnectTimeout=8", "-o", "NumberOfPasswordPrompts=1", "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3"]
        if FileManager.default.fileExists(atPath: SetupFiles.knownHosts.path) { options += ["-o", "UserKnownHostsFile=" + SetupFiles.knownHosts.path] }
        let script = try String(contentsOf: resources.appendingPathComponent("pi/change_code.py"), encoding: .utf8)
        let command = "sudo -S -p '' /usr/bin/python3 -c " + ConnectionRules.shellQuote(script)
        let input = Data((password + "\nDENDEN_CODE_REQUEST\n").utf8) + (try JSONSerialization.data(withJSONObject: ["code": code]))
        let result = try run("/usr/bin/ssh", options + ["-T", target, command], env: helper.environment, input: input)
        guard let line = result.split(separator: "\n").last(where: { $0.hasPrefix("DENDEN_CHANGED=") }),
              let info = try JSONSerialization.jsonObject(with: Data(line.dropFirst(15).utf8)) as? [String: Any], info["changed"] as? Bool == true else {
            throw DemoError("Pi не підтвердив зміну пароля. Повтори цю дію, щоб узгодити пароль на Pi та Mac.")
        }
        return info
    }
    static func revokeMac(host: String, user: String, password: String, publicKeys: [String], resources: URL, executable: String) throws -> Int {
        let target = try ConnectionRules.destination(user: user, host: host)
        guard !password.isEmpty, !publicKeys.isEmpty else { throw DemoError("Введи пароль користувача Pi.") }
        let keys = try publicKeys.map(SetupWire.publicKey)
        let helper = try AskpassServer(password: password, executable: executable)
        defer { helper.stop() }
        let script = try String(contentsOf: resources.appendingPathComponent("pi/revoke_client.py"), encoding: .utf8)
        let options = ["-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + SetupFiles.knownHosts.path, "-o", "ConnectTimeout=8", "-o", "NumberOfPasswordPrompts=1", "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3"]
        let result = try run("/usr/bin/ssh", options + ["-T", target, "/usr/bin/python3 -c " + ConnectionRules.shellQuote(script)], env: helper.environment, input: JSONSerialization.data(withJSONObject: ["publicKeys": keys]))
        guard let line = result.split(separator: "\n").last(where: { $0.hasPrefix("DENDEN_RESET=") }),
              let object = try JSONSerialization.jsonObject(with: Data(line.dropFirst(13).utf8)) as? [String: Int], object["remaining"] == 0, let removed = object["removed"] else { throw DemoError("Pi не підтвердив видалення доступу. Локальні дані збережено.") }
        return removed
    }
    static func install(host: String, user: String, password: String, resources: URL, executable: String, progress: @escaping @Sendable (String) -> Void) throws -> [String: Any] {
        let target = try ConnectionRules.destination(user: user, host: host)
        guard !password.isEmpty, !password.contains("\n"), !password.contains("\r") else { throw DemoError("Введи пароль облікового запису Pi.") }
        let helper = try AskpassServer(password: password, executable: executable)
        defer { helper.stop() }
        let pub = try ensureKey()
        try Data(pub.utf8).write(to: helper.directory.appendingPathComponent("public_key"))
        let options = ["-o", "StrictHostKeyChecking=ask", "-o", "ConnectTimeout=8", "-o", "NumberOfPasswordPrompts=1", "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3"]
        let env = helper.environment
        progress("Підключаюся до Pi…")
        let remote = try run("/usr/bin/ssh", options + ["-T", target, "mktemp -d /tmp/denden-setup.XXXXXXXX"], env: env).split(separator: "\n").map(String.init).last ?? ""
        guard remote.range(of: "\\A/tmp/denden-setup\\.[A-Za-z0-9]{8}\\z", options: .regularExpression) != nil else { throw DemoError("Pi не створив папку інсталятора.") }
        defer { _ = try? run("/usr/bin/ssh", options + ["-T", target, "rm -rf -- " + ConnectionRules.shellQuote(remote)], env: env, timeout: 15) }
        progress("Передаю налаштування на Pi…")
        let files = ["device_history.py", "waiting.py", "waiting.wav", "wifi_audio.py", "server.py", "audio.py", "quiet.py", "bluetooth_diagnostics.py", "bluetooth_sco.py", "servos.py", "servo_pwm.py", "servo_pwm_setup.py", "install_files.py", "provision.py", "provision_core.py", "provision_backend.py", "provision_admin.py", "install.sh"].map { resources.appendingPathComponent("pi/" + $0).path }
        _ = try run("/usr/bin/scp", options + files + [helper.directory.appendingPathComponent("public_key").path, target + ":" + remote + "/"], env: env)
        progress("Готую камеру й Bluetooth. Це може тривати кілька хвилин…")
        let command = "cd " + ConnectionRules.shellQuote(remote) + " && sudo -S -p '' /bin/bash install.sh"
        let result = try run("/usr/bin/ssh", options + ["-T", target, command], env: env, input: Data((password + "\n").utf8), timeout: 360)
        guard let line = result.split(separator: "\n").last(where: { $0.hasPrefix("DENDEN_RESULT=") }), let object = try JSONSerialization.jsonObject(with: Data(line.dropFirst(14).utf8)) as? [String: Any] else { throw DemoError("Сервіс встановлено, але Pi не повернув код налаштування. Повтори підготовку.") }
        return object
    }
}
