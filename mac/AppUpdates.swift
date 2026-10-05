import SwiftUI
import AppKit

@MainActor
final class AppUpdates: ObservableObject {
    @Published var checking = false
    @Published var latest: String?
    @Published var message = ""
    let current: String = {
        guard let url = Bundle.main.url(forResource: "SourceCommit", withExtension: "txt"),
              let text = try? String(contentsOf: url, encoding: .utf8) else { return "development" }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }()

    var available: Bool { latest != nil && latest != current }

    func check() async {
        guard !checking else { return }
        checking = true
        defer { checking = false }
        latest = nil
        do {
            var request = URLRequest(url: URL(string: "https://api.github.com/repos/TimeSkipe/DenDenmushi/commits/main")!)
            request.timeoutInterval = 20
            request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                  let value = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let sha = value["sha"] as? String,
                  sha.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil else {
                throw URLError(.badServerResponse)
            }
            latest = sha
            message = available ? "Доступне оновлення з GitHub." : "Установлено актуальну версію."
        } catch {
            message = "Не вдалося перевірити GitHub. Спробуй пізніше."
        }
    }

    func install(quit: @escaping () -> Void) {
        guard available, let sha = latest else { return }
        do {
            let fm = FileManager.default
            let target = Bundle.main.bundleURL
            guard fm.isWritableFile(atPath: target.deletingLastPathComponent().path),
                  let helper = Bundle.main.resourceURL?.appendingPathComponent("scripts/update-app.py"),
                  fm.fileExists(atPath: helper.path) else {
                message = "Немає доступу до папки програми. Перемісти її у власну папку Applications."
                return
            }
            let work = fm.temporaryDirectory.appendingPathComponent("denden-update-" + UUID().uuidString)
            try fm.createDirectory(at: work, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
            let copy = work.appendingPathComponent("update-app.py")
            try fm.copyItem(at: helper, to: copy)
            let script = work.appendingPathComponent("Update.command")
            let arguments = [copy.path, "--target", target.path, "--pid", String(ProcessInfo.processInfo.processIdentifier), "--commit", sha]
            let command = "#!/bin/bash\n/usr/bin/python3 " + arguments.map(ConnectionRules.shellQuote).joined(separator: " ") + "\n"
            try command.write(to: script, atomically: true, encoding: .utf8)
            try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)
            guard NSWorkspace.shared.open(script) else { throw URLError(.cannotOpenFile) }
            quit()
        } catch {
            message = "Не вдалося запустити оновлення. Поточна програма збережена."
        }
    }
}

struct AppUpdatesView: View {
    @ObservedObject var updater: AppUpdates
    var unavailable: Bool
    var quit: () -> Void
    @AppStorage("checkGitHubUpdates") private var automatic = true
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("Оновлення програми")).font(.title2.bold())
            Toggle(L("Перевіряти GitHub під час запуску"), isOn: $automatic)
            Text(String(updater.current.prefix(12))).font(.caption.monospaced())
            HStack {
                Button(L("Перевірити оновлення")) { Task { await updater.check() } }.disabled(updater.checking)
                if updater.checking { ProgressView().controlSize(.small) }
                if updater.available {
                    Button(L("Оновити й перезапустити")) { updater.install(quit: quit) }.disabled(unavailable)
                }
            }
            if !updater.message.isEmpty { Text(L(updater.message)).font(.callout) }
            Text(L("Оновлення закриє програму, збере нову версію в Terminal і відкриє її. Потрібні інструменти розробника та інтернет. Попередня копія збережеться поруч. Pi не оновлюється.")).font(.caption).foregroundStyle(.secondary)
            if unavailable { Text(L("Спочатку від’єднай равлика та дочекайся завершення поточних дій.")).font(.caption) }
        }
    }
}
