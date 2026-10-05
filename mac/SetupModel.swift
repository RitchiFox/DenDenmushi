import Foundation
import AppKit

@MainActor
final class SetupModel: ObservableObject {
    @Published var busy = false { didSet { controller?.preparing = busy } }
    @Published var message = "Увімкни равлика й натисни «Підключити»."
    @Published var error: String?
    @Published var password = ""
    @Published var code = ""
    @Published var ssid = ""
    @Published var wifiPassword = ""
    @Published var selected: UUID?
    @Published var showInstaller = false
    @Published var completed = false
    @Published var saved = SavedPi.current
    @Published var needsCode = false
    @Published var needsWiFi = false
    @Published var needsReset = false
    @Published var resetPassword = ""
    @Published var needsPasswordChange = false
    @Published var newCredential = ""
    @Published var repeatCredential = ""
    @Published var ownerPassword = ""
    @Published var choosingAnother = false
    @Published var networks: [SavedWiFiNetwork] = []
    @Published var followWiFi = UserDefaults.standard.object(forKey: "followWiFi") as? Bool ?? true {
        didSet { UserDefaults.standard.set(followWiFi, forKey: "followWiFi") }
    }
    let macWiFi = MacWiFi()
    private var networkMonitor: Task<Void, Never>?
    private var observedSSID: String?
    private var candidateSSID: String?
    private var forceNetworkSelection = false
    let bluetooth = BluetoothSetup()
    private weak var controller: Controller?
    private var attached = false
    private var triedSavedCode = false
    private var currentPi: NearbyPi?

    func attach(_ controller: Controller) {
        self.controller = controller
        controller.wirelessConnect = { [weak self] in self?.startConnection() }
        guard !attached else { return }
        attached = true
        macWiFi.refresh()
        observedSSID = macWiFi.ssid
        networkMonitor = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                guard let self else { return }
                self.checkNetworkChange()
            }
        }
        if saved != nil { message = "Равлик збережений на цьому Mac. Достатньо натиснути «Підключити»." }
        controller.detail = message
    }
    private func progress(_ text: String) {
        message = text
        if controller?.connected != true { controller?.status = text }
    }
    private func loadCode() {
        guard code.isEmpty, !triedSavedCode else { return }
        triedSavedCode = true
        if !choosingAnother, let saved { code = SetupFiles.loadCode(account: saved.id.uuidString) }
        if code.isEmpty && !choosingAnother { code = SetupFiles.loadCode() } // v0.2 migration / first assembly.
    }
    func copyCode() {
        loadCode()
        guard !code.isEmpty else { error = "Підключи равлика на цьому Mac, щоб зберегти його код."; return }
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(code, forType: .string)
        message = "Код скопійовано. Передай його людині, якій дозволяєш підключатися до равлика."
    }
    func resetThisMac() {
        guard !busy, let controller, !controller.busy, let saved,
              let resources = Bundle.main.resourceURL, let executable = Bundle.main.executableURL?.path, !resetPassword.isEmpty else { return }
        loadCode()
        guard !code.isEmpty else { error = "Спочатку збережи код равлика: він потрібен для повторного підключення."; return }
        let host = controller.host, user = controller.user, password = resetPassword
        resetPassword = ""; busy = true; error = nil
        Task {
            defer { busy = false }
            do {
                progress("Зупиняю камеру й видаляю доступ цього Mac…")
                await controller.stopForReset()
                var keys = [saved.publicKey]
                if let text = try? String(contentsOf: SetupFiles.key.appendingPathExtension("pub"), encoding: .utf8), let key = try? SetupWire.publicKey(text), !keys.contains(key) { keys.append(key) }
                let publicKeys = keys
                _ = try await Task.detached {
                    try Bootstrap.revokeMac(host: host, user: user, password: password, publicKeys: publicKeys, resources: resources, executable: executable)
                }.value
                // Preserve a copy for this requested first-onboarding rehearsal.
                NSPasteboard.general.clearContents()
                guard NSPasteboard.general.setString(code, forType: .string) else { throw DemoError("Доступ на Pi видалено. Не вдалося скопіювати код — повтори скидання.") }
                try SetupFiles.forgetClient(saved)
                bluetooth.disconnect()
                self.saved = nil; code = ""; triedSavedCode = false; currentPi = nil; selected = nil
                completed = false; choosingAnother = false; needsCode = false; needsWiFi = false
                needsReset = false; controller.error = nil
                progress("Готовий до першого підключення")
                controller.detail = "Натисни «Підключити». Код равлика збережено в буфері обміну — встав його через ⌘V."
            } catch {
                self.error = error.localizedDescription.replacingOccurrences(of: password, with: "••••")
                progress("Не вдалося завершити скидання.")
            }
        }
    }
    func changePassword() {
        guard !busy, let controller, !controller.busy,
              let resources = Bundle.main.resourceURL, let executable = Bundle.main.executableURL?.path, !ownerPassword.isEmpty else { return }
        do { try SetupWire.validateCredential(newCredential) }
        catch { self.error = error.localizedDescription; return }
        guard newCredential == repeatCredential else { error = "Новий пароль і повторення мають збігатися."; return }
        let host = controller.host, user = controller.user, password = ownerPassword, credential = newCredential
        ownerPassword = ""; busy = true; error = nil
        Task {
            do {
                progress("Змінюю пароль равлика…")
                await controller.stopForReset()
                bluetooth.disconnect()
                let newCode = try await Task.detached { try SetupWire.credentialCode(credential) }.value
                let info = try await Task.detached {
                    try Bootstrap.changeCode(host: host, user: user, password: password, code: newCode, resources: resources, executable: executable)
                }.value
                code = newCode; triedSavedCode = true
                try SetupFiles.storeCode(newCode)
                if let saved { try SetupFiles.storeCode(newCode, account: saved.id.uuidString) }
                try accept(info, preferredHost: host)
                newCredential = ""; repeatCredential = ""; needsPasswordChange = false
                choosingAnother = false; completed = true
                message = "Пароль равлика змінено й збережено на цьому Mac. Підключаю камеру…"
                // BlueZ needs a moment to publish the restarted setup service.
                try await Task.sleep(nanoseconds: 1_000_000_000)
                busy = false; startConnection()
            } catch {
                self.error = error.localizedDescription.replacingOccurrences(of: password, with: "••••").replacingOccurrences(of: credential, with: "••••")
                progress("Зміну пароля не підтверджено. Повтори цю дію."); busy = false
            }
        }
    }
    func anotherPi() {
        guard !busy, controller?.connected != true else { return }
        // Keep the old record/key in case the new pairing is cancelled or fails.
        choosingAnother = true; code = ""; selected = nil; currentPi = nil; triedSavedCode = true
        startConnection()
    }
    func cancelPairing() {
        needsCode = false
        choosingAnother = false; triedSavedCode = false; code = ""; selected = nil
        progress("Підключення скасовано. Збережений равлик залишається доступним.")
    }
    private func remember(_ info: [String: Any], pi: NearbyPi, publicKey: String) throws {
        guard let hostKey = info["hostKey"] as? String else { throw DemoError("Pi не підтвердив свою адресу.") }
        let record = SavedPi(id: pi.id, name: "DenDenMushi", hostKey: try SetupWire.publicKey(hostKey), publicKey: publicKey)
        if !choosingAnother, let saved, saved.id == record.id, saved.hostKey != record.hostKey {
            throw DemoError("Змінився ключ збереженого Pi. Перевір пристрій перед новим підключенням.")
        }
        try SetupFiles.storeCode(code, account: record.id.uuidString)
        SavedPi.current = record; saved = record
        choosingAnother = false; completed = true
        UserDefaults.standard.set(true, forKey: "setupComplete")
    }
    private func accept(_ info: [String: Any], preferredHost: String? = nil) throws {
        let (host, user) = try SetupFiles.pin(info, preferredHost: preferredHost)
        controller?.host = host; controller?.user = user; controller?.error = nil
        UserDefaults.standard.set(host, forKey: "piHost"); UserDefaults.standard.set(user, forKey: "piUser")
    }
    private func reachable(_ info: [String: Any]) async -> String? {
        var names = info["addresses"] as? [String] ?? []
        if let host = info["host"] as? String, !names.contains(host) { names.append(host) }
        for name in names.prefix(4) {
            if await PiReachability.check(name) { return name }
        }
        return nil
    }
    private func checkNetworkChange() {
        macWiFi.refresh()
        guard let name = macWiFi.ssid else { candidateSSID = nil; return }
        // Require two identical readings; roaming briefly hides SSID.
        guard candidateSSID == name else { candidateSSID = name; return }
        guard !busy, controller?.busy != true else { return }
        let previous = observedSSID
        observedSSID = name
        guard previous != nil, previous != name, followWiFi,
              controller?.followNetworkSession == true else { return }
        busy = true
        Task {
            await controller?.stopForReset()
            forceNetworkSelection = true
            busy = false
            startConnection()
        }
    }
    private func fetchNetworks(overWiFi: Bool = false) async throws {
        var offset = 0
        var result: [SavedWiFiNetwork] = []
        repeat {
            let reply: [String: Any]
            if overWiFi, let controller { reply = try await controller.savedWiFiPage(offset) }
            else { reply = try await bluetooth.request(["op": "wifi-list", "offset": offset], code: code) }
            for row in reply["networks"] as? [[String: Any]] ?? [] {
                if let id = row["id"] as? String, let ssid = row["ssid"] as? String {
                    result.append(SavedWiFiNetwork(id: id, ssid: ssid, active: row["active"] as? Bool ?? false))
                }
            }
            guard let next = reply["next"] as? Int, next > offset else { break }
            offset = next
        } while offset < 500
        networks = result
    }
    func loadNetworks() {
        guard !busy, controller?.busy != true else { return }
        busy = true; error = nil
        Task {
            defer { bluetooth.disconnect(); busy = false }
            do {
                if controller?.connected == true {
                    try await fetchNetworks(overWiFi: true)
                } else {
                    loadCode()
                    guard let pi = try await bluetooth.locate(saved: saved, selected: selected) else { throw DemoError("Обери свого равлика.") }
                    try await bluetooth.connect(pi)
                    try await fetchNetworks()
                }
            } catch { self.error = error.localizedDescription }
        }
    }
    func useNetwork(_ network: SavedWiFiNetwork) {
        guard !busy, let controller, !controller.busy else { return }
        busy = true; error = nil
        Task {
            defer { bluetooth.disconnect(); busy = false }
            do {
                loadCode()
                guard let pi = try await bluetooth.locate(saved: saved, selected: selected) else { throw DemoError("Обери свого равлика.") }
                try await bluetooth.connect(pi)
                await controller.stopForReset()
                let info = try await bluetooth.request(["op": "wifi-use", "uuid": network.id], code: code)
                try accept(info)
                try await fetchNetworks()
                guard let host = await reachable(info) else { throw DemoError("Pi отримав налаштування, але Mac не може до нього дістатися. Перевір, що Mac у цій самій мережі й вона дозволяє зв’язок між пристроями.") }
                controller.host = host; UserDefaults.standard.set(host, forKey: "piHost")
                controller.followNetworkSession = true
                observedSSID = macWiFi.ssid
                controller.connect()
            } catch { self.error = error.localizedDescription }
        }
    }
    private func matchMacNetwork(_ original: [String: Any]) async throws -> [String: Any] {
        macWiFi.refresh()
        guard followWiFi, let name = macWiFi.ssid else { return original }
        // Older Pi services retain the manual connection flow.
        do { try await fetchNetworks() }
        catch {
            if error.localizedDescription.contains("Невідома операція") { return original }
            throw error
        }
        observedSSID = name
        let matches = networks.filter { $0.ssid == name }
        if matches.contains(where: { $0.active }) { return original }
        guard let network = matches.first else { ssid = name; return original }
        progress("Передаю мережу равлику через Bluetooth…")
        return try await bluetooth.request(["op": "wifi-use", "uuid": network.id], code: code)
    }
    func startConnection() {
        guard !busy, let controller, !controller.connected, !controller.busy else { return }
        controller.followNetworkSession = true
        busy = true; error = nil; controller.error = nil
        Task {
            defer { bluetooth.disconnect(); busy = false; wifiPassword = "" }
            do {
                progress(saved == nil || choosingAnother ? "Шукаю равлика через Bluetooth…" : "Підключаю збереженого равлика…")
                if !forceNetworkSelection, !choosingAnother, !needsCode, !needsWiFi, !needsReset, !needsPasswordChange, let saved,
                   await controller.reconnectSavedNetwork(saved) {
                    if controller.connected {
                        message = "Равлик підключений. Код і доступ збережені на цьому Mac."
                        // The saved-network flow awaited camera setup while
                        // preparing was true, so start its audio only now.
                        busy = false
                        controller.connectAudio()
                    } else {
                        message = controller.status; error = controller.error
                    }
                    return
                }
                progress(saved == nil || choosingAnother ? "Шукаю равлика через Bluetooth…" : "Підключаю збереженого равлика…")
                loadCode()
                guard let pi = try await bluetooth.locate(saved: choosingAnother ? nil : saved, selected: selected) else {
                    needsCode = true; progress("Поруч кілька равликів. Обери свого."); return
                }
                currentPi = pi; selected = pi.id
                if code.isEmpty { needsCode = true; progress("Введи код равлика один раз."); return }
                let credential = code
                code = try await Task.detached { try SetupWire.credentialCode(credential) }.value
                try await bluetooth.connect(pi)
                let publicKey = try await Task.detached { try Bootstrap.ensureKey() }.value
                let alreadyAuthorized = !choosingAnother && saved?.id == pi.id && saved?.publicKey == publicKey
                var info = try await bluetooth.request(alreadyAuthorized ? ["op": "info"] : ["op": "authorize", "publicKey": publicKey], code: code)
                info = try await matchMacNetwork(info)
                forceNetworkSelection = false
                try remember(info, pi: pi, publicKey: publicKey)
                try accept(info)
                progress("Знаходжу камеру в Wi-Fi…")
                guard let host = await reachable(info) else {
                    needsWiFi = true; progress("Равлика знайдено. Підключимо його до мережі цього Mac."); return
                }
                controller.host = host; UserDefaults.standard.set(host, forKey: "piHost")
                message = "Равлик підключений. Код і доступ збережені на цьому Mac."
                controller.connect()
            } catch {
                self.error = error.localizedDescription
                controller.error = error.localizedDescription; controller.status = "Не вдалося підключитися"
                // Give the first-time owner a direct correction path, without reinstalling Pi.
                if (saved == nil || choosingAnother) && (currentPi != nil || !bluetooth.nearby.isEmpty) { needsCode = true }
            }
        }
    }
    func confirmCode() {
        do { try SetupWire.validateCredential(code) }
        catch { self.error = error.localizedDescription; return }
        needsCode = false; triedSavedCode = true; startConnection()
    }
    func configureWiFi() {
        guard !busy, let controller, !controller.busy else { return }
        guard !ssid.isEmpty, !wifiPassword.isEmpty else { error = "Введи назву Wi-Fi та пароль."; return }
        busy = true; error = nil
        let ssid = ssid, password = wifiPassword
        Task {
            defer { bluetooth.disconnect(); busy = false; wifiPassword = "" }
            do {
                loadCode()
                guard let pi = try await bluetooth.locate(saved: saved, selected: selected) else { throw DemoError("Обери свого равлика.") }
                progress("Передаю мережу равлику через Bluetooth…")
                try await bluetooth.connect(pi)
                await controller.stopForReset()
                let info = try await bluetooth.request(["op": "wifi", "ssid": ssid, "password": password], code: code)
                try accept(info)
                progress("Перевіряю доступ через Wi-Fi…")
                guard let host = await reachable(info) else { throw DemoError("Pi отримав налаштування, але Mac не може до нього дістатися. Перевір, що Mac у цій самій мережі й вона дозволяє зв’язок між пристроями.") }
                controller.host = host; UserDefaults.standard.set(host, forKey: "piHost")
                observedSSID = macWiFi.ssid
                controller.followNetworkSession = true
                needsWiFi = false; message = "Мережу збережено. Надалі достатньо «Підключити»."
                controller.connect()
            } catch { self.error = error.localizedDescription; progress("Не вдалося підключити Wi-Fi.") }
        }
    }
    func install() {
        guard !busy, let controller, !controller.connected, let resources = Bundle.main.resourceURL, let executable = Bundle.main.executableURL?.path else { return }
        busy = true; error = nil
        let host = controller.host, user = controller.user, password = self.password
        self.password = ""
        Task {
            do {
                await controller.servos.disconnect()
                let info = try await Task.detached { [self] in
                    try Bootstrap.install(host: host, user: user, password: password, resources: resources, executable: executable) { text in
                        Task { @MainActor in self.progress(text) }
                    }
                }.value
                guard let code = info["code"] as? String else { throw DemoError("Pi не повернув код налаштування.") }
                self.code = try SetupWire.normalizedCode(code); triedSavedCode = true
                try accept(info, preferredHost: host)
                try SetupFiles.storeCode(code)
                showInstaller = false; completed = true
                message = "Pi підготовлений. Підключаю равлика й запам’ятовую доступ…"
                busy = false; startConnection()
            } catch {
                self.error = password.isEmpty ? error.localizedDescription : error.localizedDescription.replacingOccurrences(of: password, with: "••••")
                progress("Підготовка потребує уваги."); busy = false
            }
        }
    }
}
