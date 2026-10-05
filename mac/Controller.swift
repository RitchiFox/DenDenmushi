import SwiftUI
import AppKit
import Network

@MainActor
final class Controller: NSObject, ObservableObject, @preconcurrency NetServiceBrowserDelegate, @preconcurrency NetServiceDelegate {
    let servos = ServoModel()
    let audioAdmin = AudioAdminModel()
    var audioAdminAvailable: Bool { piFeatures.contains("audio-admin-v1") }
    @Published var deviceHistory: [DeviceHistoryEntry] = []
    @Published var historyBusy = false
    @Published var historyMessage = ""
    private let historyDeviceID: String = {
        if let saved = UserDefaults.standard.string(forKey: "historyDeviceID") { return saved }
        let id = UUID().uuidString.lowercased()
        UserDefaults.standard.set(id, forKey: "historyDeviceID")
        return id
    }()
    func savedWiFiPage(_ offset: Int) async throws -> [String: Any] {
        try await api("/wifi/networks?offset=\(offset)")
    }
    func loadDeviceHistory() {
        guard !historyBusy else { return }
        deviceHistory = []
        guard connected else { historyMessage = "Підключи равлика, щоб переглянути історію."; return }
        guard piFeatures.contains("device-history-v1") else { historyMessage = "Онови сервіс Pi для історії пристроїв."; return }
        historyBusy = true; historyMessage = ""
        Task {
            defer { historyBusy = false }
            do {
                let result = try await api("/devices/history")
                guard result["ok"] as? Bool == true, let rows = result["devices"] as? [[String: Any]] else {
                    throw DemoError("Не вдалося прочитати історію пристроїв.")
                }
                deviceHistory = rows.compactMap { row in
                    guard let id = row["id"] as? String, let name = row["name"] as? String,
                          let transport = row["transport"] as? String else { return nil }
                    return DeviceHistoryEntry(id: id, name: name, transport: transport,
                        connected: row["connected"] as? Bool ?? false,
                        first: row["firstSeen"] as? Double, last: row["lastSeen"] as? Double)
                }
                if deviceHistory.isEmpty { historyMessage = "Історія поки порожня." }
            } catch { historyMessage = "Не вдалося прочитати історію пристроїв." }
        }
    }

    @Published private(set) var audioTransport = UserDefaults.standard.string(forKey: "snailAudioTransport") == "bluetooth" ? "bluetooth" : "wifi"
    @Published var wifiDriverReady = WiFiAudioDevices.pair() != nil
    private var wifiAudio: WiFiAudioTransport?
    private var previousWiFiInput: String?
    private var previousWiFiOutput: String?

    @Published var host = UserDefaults.standard.string(forKey: "piHost") ?? "raspberrypi.local"
    @Published var user = UserDefaults.standard.string(forKey: "piUser") ?? ""
    @Published var status = "Готовий до підключення"
    @Published var detail = "Один раз встанови сервіс на Pi. Потім запускай камеру тут."
    @Published var busy = false
    @Published var preparing = false
    var followNetworkSession = false
    var wirelessConnect: (() -> Void)?
    @Published var connected = false
    @Published var preview: NSImage?
    private var confirmedWaitingEnabled = true
    @Published var waitingEnabled = true
    @Published var waitingVolume = 20.0
    @Published var waitingBusy = false
    @Published var waitingMessage = ""
    @Published var waitingAvailable = false
    @Published var devices: [SoundDevice] = []
    @Published var output: UInt32 = 0
    @Published var input: UInt32 = 0
    @Published var found: [String] = []
    @Published var error: String?
    @Published var audioStatus = "Звук підключиться разом із камерою."
    @Published var audioBusy = false
    @Published var audioNeedsAttention = false
    @Published var audioNeedsUpdate = false
    @Published private(set) var snailAudioAddress: String?
    @Published var microphoneNotice = ""
    @Published private(set) var wantsCallMicrophone = UserDefaults.standard.bool(forKey: "snailCallMicrophone")
    @Published private(set) var callMicrophoneReady = false
    @Published private(set) var callMicrophoneArmed = false
    private var callRequested = false
    private var previousInputUID: String?
    private var macAudioAddress: String?
    @Published var speakerDB = -60.0
    @Published var microphoneDB = -60.0
    @Published var speakerMuted = false
    @Published var microphoneMuted = false
    @Published var speakerLevelAvailable = false
    @Published var microphoneLevelAvailable = false
    @Published private(set) var microphoneGainSupported = false
    @Published var levelsBusy = false
    @Published var levelsStatus = "Підключи равлика, щоб регулювати його звук."
    @Published var quietStatus = ""
    private var levelsTask: Task<Void, Never>?
    private var levelQueue = AudioLevelQueue()
    private var levelsReadRequested = false
    private var audioTask: Task<Void, Never>?
    private var audioDiagnosticEvents: [[String: Any]] = []
    private var audioObserver: AudioDeviceObserver?
    private var piFeatures: [String] = [] {
        didSet {
            microphoneGainSupported = piFeatures.contains("audio-mic-gain-v1")
            // Reflect the connected service's limits without writing a gain.
            microphoneDB = AudioGainRange.clamp(microphoneDB, kind: "input", microphoneGainSupported: microphoneGainSupported) ?? -60
        }
    }
    private let obs = OBSConnection()
    private lazy var obsSession = OBSManagedSession(
        request: { [obs] type, values in try await obs.request(type, values) },
        close: { [obs] in await obs.close() })
    private var cleanupTask: Task<Void, Never>?
    private var cameraSetupTask: Task<Void, Error>?
    private var quitting = false
    private var tunnel: Process?
    private var heartbeat: Task<Void, Never>?
    private var previewTask: Task<Void, Never>?
    private var cameraRequested = false
    private var browser = NetServiceBrowser()
    private var services: [NetService] = []
    private var sshLog: URL { state.appendingPathComponent("ssh.log") }
    private var state: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/denden-demo") }
    private var key: URL { state.appendingPathComponent("id_ed25519") }

    override init() {
        super.init()
        audioAdmin.onLock = { [weak self] in
            guard let self else { return }
            self.levelsTask?.cancel(); self.levelsTask = nil; self.levelsBusy = false
            self.levelQueue = AudioLevelQueue(); self.levelsReadRequested = false
        }
        devices = SoundDevices.list()
        browser.delegate = self
        audioObserver = AudioDeviceObserver { [weak self] in
            Task { @MainActor [weak self] in self?.refreshAudio() }
        }
    }
    func connectDevice() { if let wirelessConnect { wirelessConnect() } else { connect() } }
    func discover() { browser.stop(); services.removeAll(); found.removeAll(); browser.searchForServices(ofType: "_denden._tcp.", inDomain: "local.") }
    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        services.append(service); service.delegate = self; service.resolve(withTimeout: 5)
    }
    func netServiceDidResolveAddress(_ sender: NetService) {
        if let name = sender.hostName?.trimmingCharacters(in: CharacterSet(charactersIn: ".")), !found.contains(name) { found.append(name) }
    }
    private func startTunnel(savedNetwork: Bool = false) throws {
        let target = try ConnectionRules.destination(user: user, host: host)
        guard FileManager.default.fileExists(atPath: key.path) else { throw DemoError("Відкрий «Перше підключення» й підготуй свій Pi або знайди готового равлика через Bluetooth.") }
        try FileManager.default.createDirectory(at: state, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        FileManager.default.createFile(atPath: sshLog.path, contents: nil, attributes: [.posixPermissions: 0o600])
        let log = try FileHandle(forWritingTo: sshLog)
        defer { try? log.close() }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
        p.arguments = ["-N", "-T", "-i", key.path, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2", "-L", "127.0.0.1:18789:127.0.0.1:8789", "-L", "127.0.0.1:18554:127.0.0.1:8554", target]
        if FileManager.default.fileExists(atPath: SetupFiles.knownHosts.path) { p.arguments?.insert(contentsOf: ["-o", "UserKnownHostsFile=" + SetupFiles.knownHosts.path], at: 0) }
        if savedNetwork {
            // Authenticate only against the app's checked Ed25519 pin. The
            // readiness line follows successful authentication and forwards.
            p.arguments?.insert(contentsOf: ["-F", "/dev/null", "-o", "GlobalKnownHostsFile=/dev/null", "-o", "HostKeyAlgorithms=ssh-ed25519", "-o", "LogLevel=DEBUG1"], at: 0)
            var environment = ProcessInfo.processInfo.environment
            environment["LC_ALL"] = "C"; p.environment = environment
        }
        p.standardError = log; p.standardOutput = FileHandle.nullDevice; p.standardInput = FileHandle.nullDevice
        try p.run(); tunnel = p
    }
    private func api(_ path: String, post: Bool = false, body: [String: Any]? = nil, audioBusyAttempt: Int = 0, timeout: TimeInterval? = nil) async throws -> [String: Any] {
        var req = URLRequest(url: URL(string: "http://127.0.0.1:18789" + path)!)
        req.httpMethod = post ? "POST" : "GET"; req.timeoutInterval = path == "/audio/connect" ? 40 : path == "/audio/status" ? 5 : path.hasPrefix("/audio/") ? 15 : 5
        if let timeout { req.timeoutInterval = min(req.timeoutInterval, max(0.1, timeout)) }
        if let body { req.httpBody = try JSONSerialization.data(withJSONObject: body); req.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        req.setValue("desktop-v1", forHTTPHeaderField: "X-DenDen-Client")
        if path == "/audio/level" {
            guard audioAdmin.unlocked, let token = audioAdmin.sessionToken else {
                throw DemoError("Не вдалося оновити гучність. Натисни оновлення звуку.")
            }
            req.setValue(token, forHTTPHeaderField: "X-DenDen-Audio-Admin")
        }
        if path == "/camera/start" || path == "/heartbeat" {
            req.setValue(historyDeviceID, forHTTPHeaderField: "X-DenDen-Device-ID")
            let name = String((Host.current().localizedName ?? "Mac").prefix(120))
            req.setValue(Data(name.utf8).base64EncodedString(), forHTTPHeaderField: "X-DenDen-Device-Name")
        }
        let (data, response) = try await URLSession.shared.data(for: req)
        if path == "/audio/level", let status = (response as? HTTPURLResponse)?.statusCode, status == 401 || status == 403,
           req.value(forHTTPHeaderField: "X-DenDen-Audio-Admin") == audioAdmin.sessionToken {
            audioAdmin.invalidate()
        }
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw DemoError("Сервіс Pi повернув помилку.") }
        // Both call and playback transitions can collide with Pi's route
        // reconciler. In particular, switching the mic OFF must still restore
        // A2DP when the first request finds the audio lock occupied.
        if path == "/audio/connect", result["error"] as? String == "audio_busy", audioBusyAttempt < 3 {
            try await Task.sleep(nanoseconds: 600_000_000)
            return try await api(path, post: post, body: body, audioBusyAttempt: audioBusyAttempt + 1)
        }
        return result
    }
    private func waitForPi() async throws {
        for _ in 0..<16 {
            if tunnel?.isRunning != true {
                let log = (try? String(contentsOf: sshLog, encoding: .utf8)) ?? ""
                throw DemoError("SSH не підключився. \(log.suffix(1200))")
            }
            if let state = try? await api("/status"), state["service"] as? String == "denden", state["version"] as? Int == 1 { piFeatures = state["features"] as? [String] ?? []; return }
            try await Task.sleep(nanoseconds: 400_000_000)
        }
        throw DemoError("Pi недоступний. Перевір адресу, Wi-Fi та встановлення сервісу.")
    }

    private func waitForSavedPi() async throws {
        let deadline = ProcessInfo.processInfo.systemUptime + 10
        while ProcessInfo.processInfo.systemUptime < deadline {
            try Task.checkCancellation()
            guard tunnel?.isRunning == true else { throw DemoError("Pi недоступний. Перевір адресу, Wi-Fi та встановлення сервісу.") }
            let log = (try? String(contentsOf: sshLog, encoding: .utf8)) ?? ""
            // Do not accept a response from an unrelated process already
            // listening on our local ports while this SSH is still starting.
            if SavedNetworkReconnect.tunnelReady(log: log, running: tunnel?.isRunning == true),
               let state = try? await api("/status", timeout: deadline - ProcessInfo.processInfo.systemUptime),
               tunnel?.isRunning == true,
               state["service"] as? String == "denden", state["version"] as? Int == 1 {
                piFeatures = state["features"] as? [String] ?? []
                return
            }
            try await Task.sleep(nanoseconds: 200_000_000)
        }
        throw DemoError("Pi недоступний. Перевір адресу, Wi-Fi та встановлення сервісу.")
    }

    func reconnectSavedNetwork(_ saved: SavedPi) async -> Bool {
        guard !quitting, !busy, !connected, tunnel == nil, SetupFiles.canReconnect(saved: saved, host: host, user: user) else { return false }
        busy = true; error = nil; status = "Підключаю збереженого равлика…"
        do {
            try startTunnel(savedNetwork: true)
            try await waitForSavedPi()
        } catch {
            // No camera, audio or OBS operation has started. Clean up only
            // this candidate tunnel before the caller falls back to Bluetooth.
            let candidate = tunnel
            if candidate?.isRunning == true { candidate?.terminate() }
            for _ in 0..<10 where candidate?.isRunning == true { try? await Task.sleep(nanoseconds: 100_000_000) }
            if let candidate, candidate.isRunning { kill(candidate.processIdentifier, SIGKILL) }
            tunnel = nil; piFeatures = []; busy = false
            return false
        }
        do { try await startCameraAndAudio() }
        catch {
            self.error = error.localizedDescription
            await cleanup(); busy = false; status = "Потрібна увага"
        }
        // The network connection was authenticated. A later OBS/camera error
        // belongs to that flow and must not trigger another BLE connection.
        return true
    }

    private func openOBS() async throws {
        try Task.checkCancellation()
        let fm = FileManager.default
        let obsURL = URL(fileURLWithPath: "/Applications/OBS.app")
        guard fm.fileExists(atPath: obsURL.path) else { throw DemoError("Встанови OBS із obsproject.com. Це безкоштовний компонент віртуальної камери.") }
        let configURL = fm.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json")
        var config = ((try? Data(contentsOf: configURL)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }) ?? [:]
        let running = NSRunningApplication.runningApplications(withBundleIdentifier: "com.obsproject.obs-studio").first
        let needsConfig = config["server_enabled"] as? Bool != true || config["auth_required"] as? Bool != true || (config["server_password"] as? String ?? "").isEmpty
        if needsConfig {
            guard running == nil else { throw DemoError("Один раз закрий OBS через його меню «Вийти» й натисни «Підключити» знову. DenDenMushi налаштує керування автоматично.") }
            try fm.createDirectory(at: configURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            let backup = configURL.appendingPathExtension("before-denden")
            if fm.fileExists(atPath: configURL.path) && !fm.fileExists(atPath: backup.path) { try fm.copyItem(at: configURL, to: backup); try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: backup.path) }
            config["server_enabled"] = true; config["auth_required"] = true
            config["server_password"] = UUID().uuidString + UUID().uuidString
            config["server_port"] = 4455; config["first_load"] = false; config["alerts_enabled"] = false
            try JSONSerialization.data(withJSONObject: config, options: [.prettyPrinted, .sortedKeys]).write(to: configURL, options: .atomic)
            try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: configURL.path)
        }
        if running == nil {
            let options = NSWorkspace.OpenConfiguration()
            options.activates = false; options.hides = true
            options.arguments = ["--disable-missing-files-check"]
            let launchRequested = Date()
            let application = try await NSWorkspace.shared.openApplication(at: obsURL, configuration: options)
            // openApplication may reuse an OBS instance opened concurrently.
            // An older or unknown launch date is never ours to terminate.
            trackOBS(application, launchedByUs: application.launchDate.map { $0 >= launchRequested } ?? false)
        } else if let running {
            trackOBS(running, launchedByUs: false)
        }
        // If Quit arrived during launch, the returned process is now tracked
        // and cleanup can finish it without letting setup start any camera.
        try Task.checkCancellation()
        let port = config["server_port"] as? Int ?? 4455
        let password = config["server_password"] as? String ?? ""
        var last: Error = DemoError("OBS не відповідає.")
        for _ in 0..<16 {
            try Task.checkCancellation()
            do { try await obs.connect(port: port, password: password); return }
            catch { last = error; try await Task.sleep(nanoseconds: 500_000_000) }
        }
        throw last
    }

    private func trackOBS(_ application: NSRunningApplication, launchedByUs: Bool) {
        obsSession.attach(.init(
            isTerminated: { application.isTerminated },
            terminate: { application.terminate() },
            reveal: {
                application.unhide()
                application.activate(options: [.activateAllWindows])
            }), launchedByUs: launchedByUs)
    }

    private func configureCamera() async throws {
        let collections = try await obs.request("GetSceneCollectionList")
        let current = collections["currentSceneCollectionName"] as? String
        let cam = try await obs.request("GetVirtualCamStatus")
        let recording = try await obs.request("GetRecordStatus")
        let streaming = try await obs.request("GetStreamStatus")
        guard recording["outputActive"] as? Bool != true, streaming["outputActive"] as? Bool != true else { throw DemoError("OBS зараз записує або транслює. Заверши це перед підключенням DenDenMushi.") }
        if cam["outputActive"] as? Bool == true && current != "DenDenMushi Demo" { throw DemoError("В OBS уже працює інша віртуальна камера. Зупини її один раз, потім повтори підключення.") }
        if cam["outputActive"] as? Bool == true { _ = try await obs.request("StopVirtualCam") }
        if !(collections["sceneCollections"] as? [String] ?? []).contains("DenDenMushi Demo") {
            _ = try await obs.request("CreateSceneCollection", ["sceneCollectionName": "DenDenMushi Demo"])
        } else {
            _ = try await obs.request("SetCurrentSceneCollection", ["sceneCollectionName": "DenDenMushi Demo"])
        }
        let profiles = try await obs.request("GetProfileList")
        let names = profiles["profiles"] as? [String] ?? []
        if !names.contains("DenDenMushi Demo") { _ = try await obs.request("CreateProfile", ["profileName": "DenDenMushi Demo"]) }
        _ = try await obs.request("SetCurrentProfile", ["profileName": "DenDenMushi Demo"])
        _ = try await obs.request("SetVideoSettings", ["baseWidth": 1280, "baseHeight": 720, "outputWidth": 1280, "outputHeight": 720, "fpsNumerator": 30, "fpsDenominator": 1])
        let scenes = try await obs.request("GetSceneList")
        if !(scenes["scenes"] as? [[String: Any]] ?? []).contains(where: { $0["sceneName"] as? String == "DenDenMushi" }) {
            _ = try await obs.request("CreateScene", ["sceneName": "DenDenMushi"])
        }
        let settings: [String: Any] = ["is_local_file": false, "input": "tcp://127.0.0.1:18554", "input_format": "mpegts", "buffering_mb": 0, "reconnect_delay_sec": 2, "clear_on_media_end": true]
        let inputs = try await obs.request("GetInputList")
        if (inputs["inputs"] as? [[String: Any]] ?? []).contains(where: { $0["inputName"] as? String == "DenDen Camera" }) {
            _ = try await obs.request("SetInputSettings", ["inputName": "DenDen Camera", "inputSettings": settings, "overlay": true])
        } else {
            _ = try await obs.request("CreateInput", ["sceneName": "DenDenMushi", "inputName": "DenDen Camera", "inputKind": "ffmpeg_source", "inputSettings": settings, "sceneItemEnabled": true])
        }
        obsSession.sourceConfigured = true
        let item = try await obs.request("GetSceneItemId", ["sceneName": "DenDenMushi", "sourceName": "DenDen Camera"])
        guard let id = item["sceneItemId"] as? Int else { throw DemoError("OBS не створив джерело камери.") }
        _ = try await obs.request("SetSceneItemTransform", ["sceneName": "DenDenMushi", "sceneItemId": id, "sceneItemTransform": ["positionX": 0, "positionY": 0, "alignment": 5, "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": 1280, "boundsHeight": 720]])
        _ = try await obs.request("SetCurrentProgramScene", ["sceneName": "DenDenMushi"])
        obsSession.cameraRequested = true
        _ = try await obs.request("StartVirtualCam")
    }

    func connect() {
        guard !quitting && !busy && !connected else { return }
        busy = true; error = nil; status = "Підключаю Pi…"
        UserDefaults.standard.set(host, forKey: "piHost"); UserDefaults.standard.set(user, forKey: "piUser")
        Task {
            do {
                try startTunnel(); try await waitForPi()
                try await startCameraAndAudio()
            } catch {
                self.error = error.localizedDescription
                await cleanup(); busy = false; status = "Потрібна увага"
            }
        }
    }
    private var servoAutoConnect = false

    private func startCameraAndAudio() async throws {
        guard !quitting, cleanupTask == nil else { throw CancellationError() }
        let task = Task { try await performCameraSetup() }
        cameraSetupTask = task
        defer { cameraSetupTask = nil }
        try await task.value
    }

    private func performCameraSetup() async throws {
        try Task.checkCancellation()
        status = "Готую OBS…"
        try await openOBS()
        try Task.checkCancellation()
        cameraRequested = true
        _ = try await api("/camera/start", post: true)
        try Task.checkCancellation()
        // Camera lease is maintained independently during OBS setup.
        connected = true
        startHeartbeat()
        try await configureCamera()
        try Task.checkCancellation()
        status = "Віртуальна камера запущена"
        detail = "У Meet або Telegram вибери OBS Virtual Camera. Перегляд нижче підтвердить надходження кадрів."
        busy = false
        startPreview()
        servoAutoConnect = true
        servos.connect(host: host, user: user)
        connectAudio()
    }
    private func startHeartbeat() {
        heartbeat?.cancel()
        heartbeat = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    _ = try await self.api("/heartbeat", post: true, body: self.piFeatures.contains("audio-call-v1") ? ["microphone": self.callRequested] : nil)
                    await self.refreshCallStatus()
                    if self.servoAutoConnect && self.connected && !self.busy {
                        // Restore status/control only; never replay a movement.
                        self.servos.connect(host: self.host, user: self.user)
                    }
                    if !self.busy {
                        if self.status == "З’єднання втрачено" { self.error = nil }
                        self.status = "Віртуальна камера запущена"

                    }
                } catch {
                    if Task.isCancelled { return }
                    self.error = "З’єднання перерване. Натисни «Зупинити», потім «Підключити». \(error.localizedDescription)"
                    self.status = "З’єднання втрачено"
                    self.preview = nil
                }
                try? await Task.sleep(nanoseconds: 4_000_000_000)
            }
        }
    }
    func loadWaitingSound() {
        guard connected, !waitingBusy else { return }
        waitingBusy = true
        Task {
            defer { waitingBusy = false }
            do {
                let result = try await api("/audio/waiting")
                waitingEnabled = result["enabled"] as? Bool ?? false
                confirmedWaitingEnabled = waitingEnabled
                waitingVolume = result["volume"] as? Double ?? 20
                waitingAvailable = true
                waitingMessage = ""
            } catch {
                waitingAvailable = false
                waitingMessage = "Онови Pi, щоб налаштувати звук очікування."
            }
        }
    }

    func setWaitingSoundEnabled(_ enabled: Bool) {
        guard connected, waitingAvailable, !waitingBusy else { return }
        waitingEnabled = enabled
        saveWaitingSound()
    }
    func saveWaitingSound() {
        guard connected, waitingAvailable, !waitingBusy else { return }
        let values: [String: Any] = ["enabled": waitingEnabled, "volume": waitingVolume]
        waitingBusy = true
        Task {
            defer { waitingBusy = false }
            do {
                _ = try await api("/audio/waiting", post: true, body: values)
                confirmedWaitingEnabled = values["enabled"] as? Bool ?? waitingEnabled
                waitingMessage = "Збережено на равлику."
            } catch {
                waitingEnabled = confirmedWaitingEnabled
                waitingMessage = error.localizedDescription
            }
        }
    }

    private func startPreview() {
        previewTask?.cancel()
        previewTask = Task { [weak self] in
            let clock = ContinuousClock()
            while !Task.isCancelled {
                guard let self, self.connected else { return }
                let started = clock.now
                if self.status != "З’єднання втрачено" {
                    do {
                        // Reuse frames already decoded by OBS on this Mac.
                        // One request at a time: slow frames never build a queue.
                        let shot = try await self.obs.request("GetSourceScreenshot",
                            ["sourceName": "DenDen Camera", "imageFormat": "jpg",
                             "imageWidth": 640, "imageCompressionQuality": 65])
                        guard !Task.isCancelled, self.connected else { return }
                        if self.status != "З’єднання втрачено",
                           let url = shot["imageData"] as? String,
                           let tail = url.split(separator: ",", maxSplits: 1).last,
                           let bytes = Data(base64Encoded: String(tail)),
                           let image = NSImage(data: bytes) {
                            self.preview = image
                            self.detail = "У Meet або Telegram вибери OBS Virtual Camera."
                        }
                    } catch {
                        guard !Task.isCancelled else { return }
                        self.preview = nil
                        self.detail = "Очікую оновлення попереднього перегляду. Камера залишається підключеною."
                        try? await Task.sleep(for: .milliseconds(500))
                    }
                }
                // Up to 15 fps, independently of the four-second Pi lease.
                try? await clock.sleep(until: started.advanced(by: .milliseconds(67)))
            }
        }
    }

    private var pairingOpenedOnDisconnect = false

    private func cleanup(fullDisconnect: Bool = false) async {
        // Quit can arrive during Disconnect. Share one teardown so it cannot
        // close the socket while the first task is checking/finishing OBS.
        if let cleanupTask { await cleanupTask.value; return }
        let task = Task {
            await performCleanup(fullDisconnect: fullDisconnect)
            cleanupTask = nil
        }
        cleanupTask = task
        await task.value
    }

    private func performCleanup(fullDisconnect: Bool) async {
        // A Quit during setup must wait for a pending OBS launch to return and
        // be tracked. The setup task itself never calls cleanup, avoiding a
        // cycle when its caller handles cancellation through this same task.
        let pendingSetup = cameraSetupTask
        pendingSetup?.cancel()
        _ = try? await pendingSetup?.value
        audioAdmin.lock()
        pairingOpenedOnDisconnect = false
        servoAutoConnect = false
        var bluetoothPeer = snailAudioAddress
        let pendingPreview = previewTask
        pendingPreview?.cancel(); previewTask = nil
        await servos.disconnect()
        let hadCallRequested = callRequested
        callRequested = false; callMicrophoneReady = false; callMicrophoneArmed = false
        let pendingAudio = audioTask
        pendingAudio?.cancel(); audioTask = nil
        // A targeted Bluetooth close may still be finishing off the main
        // thread. Join it before restoring playback, or it could close the
        // newly restored connection after cleanup returns.
        await pendingAudio?.value
        await stopWiFiAudio()
        if fullDisconnect, bluetoothPeer == nil, tunnel?.isRunning == true,
           let state = try? await api("/audio/status", timeout: 3),
           let raw = state["adapterAddress"] as? String {
            bluetoothPeer = AudioIdentity.normalize(raw)
        }
        audioBusy = false
        if hadCallRequested {
            callRequested = false
            try? restoreCallInput()
            if !fullDisconnect, let address = macAudioAddress { _ = try? await api("/audio/connect", post: true, body: ["address": address, "mode": "playback"]) }
            callRequested = false
        }
        if fullDisconnect, let peer = bluetoothPeer {
            do {
                try await Task.detached { try BluetoothAudioRecovery.disconnectPaired(peer) }.value
            } catch {
                self.error = L("Не вдалося закрити Bluetooth-з’єднання равлика.") + " " + error.localizedDescription
            }
        }
        levelsTask?.cancel(); levelsTask = nil; levelsBusy = false
        levelQueue = AudioLevelQueue(); levelsReadRequested = false
        speakerLevelAvailable = false; microphoneLevelAvailable = false
        levelsStatus = "Підключи равлика, щоб регулювати його звук."
        quietStatus = ""
        snailAudioAddress = nil; microphoneNotice = ""
        heartbeat?.cancel(); heartbeat = nil
        await pendingPreview?.value
        let obsResult = await obsSession.finish()
        let obsWarning: String?
        switch obsResult {
        case .none: obsWarning = nil
        case .busy: obsWarning = "OBS залишився відкритим, щоб не перервати іншу роботу в ньому."
        case .unavailable: obsWarning = "Не вдалося перевірити стан OBS. Закрий його через OBS → Quit OBS, коли завершиш роботу."
        case .quitRefused: obsWarning = "OBS не завершив роботу. Перевір його вікно й вибери OBS → Quit OBS."
        }
        if cameraRequested { _ = try? await api("/camera/stop", post: true); cameraRequested = false }
        if fullDisconnect, tunnel?.isRunning == true {
            do {
                let pairing = try await api("/bluetooth/pairing/open", post: true, timeout: 8)
                if pairing["active"] as? Bool != true { throw DemoError("Режим Bluetooth-парування не ввімкнувся.") }
                pairingOpenedOnDisconnect = true
            } catch {
                self.error = L("Равлик від’єднаний, але режим парування недоступний. Онови службу Pi або підключайся через програму.")
            }
        }
        if let obsWarning {
            let warning = L(obsWarning)
            self.error = self.error.map { $0 + "\n" + warning } ?? warning
        }
        if tunnel?.isRunning == true { tunnel?.terminate() }
        tunnel = nil; connected = false; preview = nil
    }
    func disconnect() {
        guard !busy else { return }
        followNetworkSession = false
        busy = true; error = nil
        status = "Від’єдную равлика…"
        Task {
            await cleanup(fullDisconnect: true)
            busy = false
            status = error == nil ? "Равлик від’єднаний" : "Від’єднано з попередженням"
            audioStatus = "Звук підключиться разом із камерою."
            detail = pairingOpenedOnDisconnect ? "Равлик видимий у Bluetooth протягом 3 хвилин. На іншому Mac вибери DenDenMushi, потім відкрий програму й введи код равлика." : "Камеру, звук і керування сервами завершено. Равлик залишається увімкненим."
        }
    }
    func stopForReset() async { await cleanup(); status = "Камеру зупинено" }
    func quit() {
        guard !quitting else { return }
        quitting = true; followNetworkSession = false
        busy = true
        Task { await cleanup(); NSApp.terminate(nil) }
    }
    func refreshAudio() {
        wifiDriverReady = WiFiAudioDevices.pair() != nil
        devices = SoundDevices.list()
        if output != 0 && !devices.contains(where: { $0.id == output && $0.output }) {
            output = 0
            if connected && !audioBusy { audioStatus = "Динамік від’єднано. Натисни «Повторити звук»."; audioNeedsAttention = true }
        }
        if input != 0 && !devices.contains(where: { $0.id == input && $0.input }) { input = 0 }
        if let id = SoundDevices.defaultDevice(input: true),
           let selected = devices.first(where: { $0.id == id }),
           unavailableMicrophone(selected) {
            microphoneNotice = "На Mac вибрано непідготовлений мікрофон равлика. Натисни «Повторити звук» або вибери інший мікрофон у дзвінку."
        } else { microphoneNotice = "" }
    }
    func setCallMicrophone(_ enabled: Bool) {
        guard !audioBusy, !busy, !preparing else { return }
        wantsCallMicrophone = enabled
        UserDefaults.standard.set(enabled, forKey: "snailCallMicrophone")
        if !enabled {
            // Stop renewing the microphone lease immediately, before adapter
            // discovery or a possibly slow Bluetooth profile transition.
            callRequested = false; callMicrophoneReady = false; callMicrophoneArmed = false
            do { try restoreCallInput() } catch { self.error = error.localizedDescription }
        }
        if connected { connectAudio() }
    }
    func unavailableMicrophone(_ device: SoundDevice) -> Bool {
        AudioIdentity.unsupportedInput(device, address: snailAudioAddress) && !callMicrophoneArmed
    }
    private func restoreCallInput() throws {
        if let microphone = try AudioInputGuard.restoreBuiltInIfNeeded(address: snailAudioAddress, preferredUID: previousInputUID) { input = microphone.id }
        previousInputUID = nil
    }
    private func selectSnailOutput(_ adapter: String) async throws {
        for _ in 0..<20 {
            try Task.checkCancellation()
            let fresh = SoundDevices.list()
            if let speaker = AudioIdentity.output(in: fresh, address: adapter) {
                try SoundDevices.setDefault(speaker.id, input: false)
                output = speaker.id; refreshAudio(); return
            }
            try await Task.sleep(nanoseconds: 300_000_000)
        }
        throw DemoError("Mac ще не бачить аудіовихід равлика. Натисни «Повторити звук».")
    }
    private func selectCallInput(_ adapter: String) async throws {
        var ambiguous = false
        for _ in 0..<20 {
            try Task.checkCancellation()
            let fresh = SoundDevices.list()
            ambiguous = fresh.filter { $0.bluetooth && $0.input && AudioIdentity.matches(uid: $0.uid, address: adapter) }.count > 1
            if let microphone = AudioIdentity.input(in: fresh, address: adapter) {
                if let id = SoundDevices.defaultDevice(input: true), let current = fresh.first(where: { $0.id == id }),
                   !AudioIdentity.unsupportedInput(current, address: adapter) { previousInputUID = current.uid }
                try SoundDevices.setDefault(microphone.id, input: true)
                input = microphone.id
                try await Task.sleep(nanoseconds: 400_000_000)
                return
            }
            try await Task.sleep(nanoseconds: 300_000_000)
        }
        throw ambiguous ? AudioInputLookupError.ambiguous : AudioInputLookupError.missing
    }
    private func recordAudioDiagnostic(_ stage: String, result: [String: Any]? = nil, error: Error? = nil) {
        var event: [String: Any] = ["time": ISO8601DateFormatter().string(from: Date()), "stage": stage,
            "callRequested": callRequested, "defaultInput": SoundDevices.defaultDevice(input: true) ?? 0,
            "defaultOutput": SoundDevices.defaultDevice(input: false) ?? 0]
        if let result { event["pi"] = result }
        if let error { event["error"] = String(describing: error) }
        if let adapter = snailAudioAddress {
            event["devices"] = SoundDevices.list().filter { AudioIdentity.matches(uid: $0.uid, address: adapter) }.map {
                ["id": $0.id, "uid": $0.uid, "input": $0.input, "output": $0.output, "bluetooth": $0.bluetooth] as [String: Any]
            }
        }
        audioDiagnosticEvents.append(event)
        audioDiagnosticEvents = Array(audioDiagnosticEvents.suffix(24))
        // Bounded local metadata only: no recordings, passwords or API keys.
        if let data = try? JSONSerialization.data(withJSONObject: audioDiagnosticEvents, options: [.prettyPrinted, .sortedKeys]) {
            let file = state.appendingPathComponent("audio-diagnostic.json")
            try? data.write(to: file, options: .atomic)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        }
    }
    private func requestCall(_ address: String, expectedAdapter: String? = nil, afterReconnect: Bool = false) async throws -> AudioCallReply {
        for attempt in 0..<4 {
            try Task.checkCancellation()
            let result = try await api("/audio/connect", post: true, body: ["address": address, "mode": "call"])
            try Task.checkCancellation()
            recordAudioDiagnostic("call_reply", result: result)
            if let call = AudioCallReply(result, expectedAdapter: expectedAdapter) { return call }
            let code = result["error"] as? String ?? "call_unavailable"
            // Closing the baseband also removes the Pi card. Its recreation
            // can lag behind BlueZ's successful ConnectProfile response.
            let retry = afterReconnect && ["bluetooth_card_missing", "hfp_profile_unavailable"].contains(code)
            guard retry && attempt < 3 else { throw DemoError(Self.audioMessage(code)) }
            try await Task.sleep(nanoseconds: 600_000_000)
        }
        throw DemoError(Self.audioMessage("call_unavailable"))
    }
    private func prepareCallInput(_ call: AudioCallReply, address: String) async throws {
        snailAudioAddress = call.adapter
        do { try await selectCallInput(call.adapter) }
        catch {
            recordAudioDiagnostic("input_lookup_failed", error: error)
            guard BluetoothAudioRecovery.shouldRetry(error: error, attempt: 0) else { throw error }
            audioStatus = "Відновлюю Bluetooth-мікрофон равлика. Звук коротко перерветься…"
            let savedInput = previousInputUID
            try restoreCallInput()
            previousInputUID = savedInput
            try Task.checkCancellation()
            let closing = Task.detached {
                try Task.checkCancellation()
                try BluetoothAudioRecovery.disconnectPaired(call.adapter)
            }
            try await withTaskCancellationHandler(operation: { try await closing.value }, onCancel: { closing.cancel() })
            try Task.checkCancellation()
            for _ in 0..<20 {
                let present = SoundDevices.list().contains { $0.bluetooth && AudioIdentity.matches(uid: $0.uid, address: call.adapter) }
                if !present { break }
                try await Task.sleep(nanoseconds: 150_000_000)
            }
            try Task.checkCancellation()
            recordAudioDiagnostic("target_closed")
            _ = try await requestCall(address, expectedAdapter: call.adapter, afterReconnect: true)
            // Exactly one recovery per explicit attempt. A second lookup
            // failure goes to the existing playback/input cleanup.
            try await selectCallInput(call.adapter)
        }
    }
    private func verifyCall(_ adapter: String) async throws -> Bool {
        let deadline = Date().addingTimeInterval(4)
        while Date() < deadline {
            try Task.checkCancellation()
            let result = try await api("/audio/status")
            try Task.checkCancellation()
            recordAudioDiagnostic("verify_call", result: result)
            if result["error"] as? String == "audio_busy" {
                try await Task.sleep(nanoseconds: 500_000_000)
                continue
            }
            guard let call = AudioCallReply(result, expectedAdapter: adapter) else {
                throw DemoError(Self.audioMessage(result["error"] as? String ?? "call_unavailable"))
            }
            let fresh = SoundDevices.list()
            // HFP mode changes device IDs. Look up both endpoints again by the
            // authenticated adapter address; never reuse the pre-switch IDs.
            if call.ready, let mic = AudioIdentity.input(in: fresh, address: adapter),
               let speaker = AudioIdentity.output(in: fresh, address: adapter) {
                guard SoundDevices.defaultDevice(input: true) == mic.id else {
                    throw DemoError("На Mac вибрано інший мікрофон. Повтори звук, щоб увімкнути мікрофон равлика.")
                }
                try SoundDevices.setDefault(speaker.id, input: false)
                input = mic.id; output = speaker.id; callMicrophoneReady = true
                refreshAudio(); return true
            }
            try await Task.sleep(nanoseconds: 500_000_000)
        }
        // macOS can wait for Telegram to open its microphone before creating
        // the SCO stream. Armed is not the same as a verified live audio route.
        return false
    }
    private func refreshCallStatus() async {
        guard callRequested, callMicrophoneArmed, !audioBusy, let adapter = snailAudioAddress else { return }
        do {
            let result = try await api("/audio/status")
            try Task.checkCancellation()
            guard callRequested, !audioBusy else { return }
            // A status read can collide with Pi reconciliation. Busy is not
            // evidence of a disconnected microphone and must not tear it down.
            if result["error"] as? String == "audio_busy" { return }
            guard let call = AudioCallReply(result, expectedAdapter: adapter) else {
                recordAudioDiagnostic("heartbeat_call_failed", result: result)
                throw DemoError(Self.audioMessage(result["error"] as? String ?? "call_unavailable"))
            }
            let fresh = SoundDevices.list()
            callMicrophoneReady = call.ready && AudioIdentity.input(in: fresh, address: adapter) != nil && AudioIdentity.output(in: fresh, address: adapter) != nil
            audioStatus = callMicrophoneReady ? "Мікрофон равлика підключено. Перевір голос пробним записом." : "Мікрофон вибрано. Почни запис у Telegram; очікую аудіоз’єднання."
        } catch {
            guard !Task.isCancelled, callRequested, !audioBusy else { return }
            callMicrophoneReady = false; callMicrophoneArmed = false; callRequested = false
            try? restoreCallInput()
            audioBusy = true
            if let address = macAudioAddress { _ = try? await api("/audio/connect", post: true, body: ["address": address, "mode": "playback"]) }
            guard !Task.isCancelled else { return }
            callRequested = false; audioBusy = false
            audioNeedsAttention = true
            audioStatus = "Мікрофон равлика не запустився через Bluetooth. Камера працює; повтори звук."
            refreshAudio()
        }
    }
    func setAudioTransport(_ value: String) {
        guard ["bluetooth", "wifi"].contains(value), value != audioTransport,
              !audioBusy, !busy, !preparing else { return }
        audioTransport = value
        UserDefaults.standard.set(value, forKey: "snailAudioTransport")
        if connected { connectAudio() }
    }
    func installWiFiAudioDevices() {
        guard let url = Bundle.main.url(forResource: "DenDenMushi-WiFi-Audio", withExtension: "pkg") else { return }
        NSWorkspace.shared.open(url)
    }
    private func restoreWiFiDevices() {
        let all = SoundDevices.list()
        for (isInput, owned, previous) in [(true, WiFiAudioDevices.micUID, previousWiFiInput),
                                           (false, WiFiAudioDevices.speakerUID, previousWiFiOutput)] {
            guard let current = SoundDevices.defaultDevice(input: isInput),
                  all.contains(where: { $0.id == current && $0.uid == owned }) else { continue }
            let replacement = all.first { $0.uid == previous && (isInput ? $0.input : $0.output) }
                ?? all.first { $0.builtIn && (isInput ? $0.input : $0.output) }
            if let replacement { try? SoundDevices.setDefault(replacement.id, input: isInput) }
        }
        previousWiFiInput = nil; previousWiFiOutput = nil
    }
    private func stopWiFiAudio() async {
        guard let active = wifiAudio else { return }
        wifiAudio = nil
        await active.stopAndWait()
        _ = try? await api("/audio/wifi/stop", post: true)
        restoreWiFiDevices()
        callMicrophoneReady = false; callMicrophoneArmed = false
    }
    func connectAudio() {
        guard connected, !audioBusy, !busy, !preparing else { return }
        if audioTransport == "wifi" { connectWiFiAudio(); return }
        if wifiAudio != nil {
            audioBusy = true
            audioTask = Task { [weak self] in
                guard let self else { return }
                await self.stopWiFiAudio()
                guard !Task.isCancelled else { return }
                self.audioBusy = false
                self.connectBluetoothAudio()
            }
        } else { connectBluetoothAudio() }
    }
    private func connectWiFiAudio() {
        wifiDriverReady = WiFiAudioDevices.pair() != nil
        guard wifiDriverReady else {
            audioNeedsAttention = true
            audioStatus = "Встанови аудіопристрої Wi-Fi в налаштуваннях. Поточний звук ще не перемкнено."
            return
        }
        guard piFeatures.contains("audio-wifi-v1") else {
            audioNeedsUpdate = true; audioNeedsAttention = true
            audioStatus = "Онови Pi один раз для звуку через Wi-Fi."
            return
        }
        audioNeedsUpdate = false; audioNeedsAttention = false; audioBusy = true
        audioStatus = "Підключаю звук через Wi-Fi…"
        let microphone = wantsCallMicrophone
        audioTask = Task { [weak self] in
            guard let self else { return }
            defer { if !Task.isCancelled { self.audioBusy = false; self.refreshAudio(); self.refreshLevels() } }
            await self.stopWiFiAudio()
            guard !Task.isCancelled else { return }
            let transport = WiFiAudioTransport()
            self.wifiAudio = transport
            do {
                self.callRequested = false; self.callMicrophoneArmed = false; self.callMicrophoneReady = false
                try self.restoreCallInput()
                try await transport.start(microphone: microphone) { [weak self, weak transport] message in
                    Task { @MainActor in
                        guard let self, let transport, self.wifiAudio === transport else { return }
                        await self.stopWiFiAudio()
                        self.audioNeedsAttention = true
                        self.audioStatus = "Звук Wi-Fi перервався. Натисни «Повторити звук» або вибери Bluetooth."
                    }
                }
                try Task.checkCancellation()
                guard let pair = WiFiAudioDevices.pair() else { throw DemoError("Аудіопристрої Wi-Fi від’єднано.") }
                let all = SoundDevices.list()
                self.previousWiFiInput = all.first { $0.id == SoundDevices.defaultDevice(input: true) }?.uid
                self.previousWiFiOutput = all.first { $0.id == SoundDevices.defaultDevice(input: false) }?.uid
                try SoundDevices.setDefault(pair.speaker.id, input: false)
                self.output = pair.speaker.id
                if microphone {
                    try SoundDevices.setDefault(pair.mic.id, input: true)
                    self.input = pair.mic.id
                }
                self.callMicrophoneReady = microphone; self.callMicrophoneArmed = microphone
                self.snailAudioAddress = nil
                self.audioStatus = "Звук через Wi-Fi підключено. У дзвінку вибери DenDenMushi Wi-Fi або системні пристрої."
                self.audioNeedsAttention = false
            } catch {
                await self.stopWiFiAudio()
                guard !Task.isCancelled else { return }
                self.audioNeedsAttention = true
                self.audioStatus = "Не вдалося ввімкнути Wi-Fi звук. Перевір дозвіл на мікрофон і повтори спробу."
                self.error = error.localizedDescription
            }
        }
    }
    private func connectBluetoothAudio() {
        guard connected, !audioBusy, !busy, !preparing else { return }
        audioNeedsAttention = false; audioNeedsUpdate = false
        guard piFeatures.contains("audio-connect-v1") else {
            audioNeedsUpdate = true; audioNeedsAttention = true
            audioStatus = "Онови службу Pi один раз для автоматичного звуку."; return
        }
        guard piFeatures.contains("audio-split-io-v1") else {
            audioNeedsUpdate = true; audioNeedsAttention = true
            audioStatus = "Онови Pi для динаміків через мініджек і мікрофона через USB."; return
        }
        if wantsCallMicrophone && !piFeatures.contains("audio-call-v1") {
            callRequested = false
            try? restoreCallInput()
            audioNeedsUpdate = true; audioNeedsAttention = true; callMicrophoneReady = false; callMicrophoneArmed = false
            audioStatus = "Онови Pi один раз, щоб передавати мікрофон равлика на Mac."; return
        }
        let callWanted = wantsCallMicrophone
        audioDiagnosticEvents.removeAll()
        audioBusy = true; callMicrophoneReady = false; callMicrophoneArmed = false
        audioStatus = callWanted ? "Підключаю мікрофон і динаміки равлика…" : "Підключаю динаміки равлика…"
        audioTask = Task { [weak self] in
            guard let self else { return }
            defer { if !Task.isCancelled { self.audioBusy = false; self.refreshAudio(); self.refreshLevels() } }
            do {
                let report = try await Task.detached {
                    try Bootstrap.run("/usr/sbin/system_profiler", ["-json", "SPBluetoothDataType"], timeout: 10)
                }.value
                try Task.checkCancellation()
                guard let address = AudioIdentity.localAddress(report: report) else {
                    throw DemoError("Увімкни Bluetooth на Mac і повтори підключення звуку.")
                }
                self.macAudioAddress = address
                if !callWanted { try self.restoreCallInput() }
                // Set before the request so cancellation/cleanup also releases a
                // pending call whose HTTP response has not arrived yet.
                self.callRequested = callWanted
                var request: [String: Any] = ["address": address]
                if self.piFeatures.contains("audio-call-v1") { request["mode"] = callWanted ? "call" : "playback" }
                if callWanted {
                    let call = try await self.requestCall(address)
                    try await self.prepareCallInput(call, address: address)
                    self.callMicrophoneArmed = true
                    try await self.selectSnailOutput(call.adapter)
                    let ready = try await self.verifyCall(call.adapter)
                    self.audioStatus = ready ? "Мікрофон равлика підключено. Перевір голос пробним записом." : "Мікрофон вибрано. Почни запис у Telegram; очікую аудіоз’єднання."
                } else {
                    let result = try await self.api("/audio/connect", post: true, body: request)
                    try Task.checkCancellation()
                    guard result["ok"] as? Bool == true, let rawAdapter = result["adapterAddress"] as? String,
                          let adapter = AudioIdentity.normalize(rawAdapter) else {
                        throw DemoError(Self.audioMessage(result["error"] as? String ?? ""))
                    }
                    self.snailAudioAddress = adapter
                    self.callRequested = false
                    let restored = try AudioInputGuard.restoreBuiltInIfNeeded(address: adapter, preferredUID: self.previousInputUID)
                    if let restored { self.input = restored.id; try await Task.sleep(nanoseconds: 400_000_000) }
                    try await self.selectSnailOutput(adapter)
                    self.audioStatus = "Динаміки равлика підключено. Мікрофон — поточний."
                }
                self.audioNeedsAttention = false
            } catch {
                guard !Task.isCancelled else { return }
                self.recordAudioDiagnostic("connect_failed", error: error)
                let message: String
                if let lookup = error as? AudioInputLookupError {
                    message = lookup == .ambiguous ? "Mac показує кілька входів равлика. Не вдалося вибрати мікрофон однозначно." : "Mac ще не бачить мікрофон равлика. Повтори підключення звуку."
                } else { message = error is DemoError ? error.localizedDescription : "Звук не підключився. Камера працює; повтори підключення звуку." }
                self.callMicrophoneReady = false; self.callMicrophoneArmed = false
                if self.callRequested {
                    self.callRequested = false
                    try? self.restoreCallInput()
                    if let address = self.macAudioAddress {
                        let fallback = try? await self.api("/audio/connect", post: true, body: ["address": address, "mode": "playback"])
                        try? Task.checkCancellation()
                        if !Task.isCancelled, fallback?["ok"] as? Bool == true, let adapter = self.snailAudioAddress { try? await self.selectSnailOutput(adapter) }
                    }
                    self.callRequested = false
                }
                guard !Task.isCancelled else { return }
                self.audioStatus = message; self.audioNeedsAttention = true
            }
        }
    }
    static func audioMessage(_ code: String) -> String {
        switch code {
        case "microphone_pending", "call_unavailable", "call_profile_missing", "call_profile_unavailable", "call_transport_unavailable", "hfp_profile_unavailable", "bluetooth_card_missing": return "Мікрофон равлика не запустився через Bluetooth. Камера працює; повтори звук."
        case "audio_busy": return "Pi ще перемикає звук. Повтори підключення за кілька секунд."
        case "usb_input_missing": return "Підключи мікрофон до червоного входу USB-карти на Pi."
        case "usb_input_ambiguous": return "До Pi підключено кілька USB-мікрофонів. Залиш один для равлика."
        case "pair_required": return "Один раз спаруй равлика з Mac через Bluetooth, потім повтори звук."
        case "usb_output_missing": return "Підключи USB-звукову карту до Raspberry Pi."
        case "usb_output_ambiguous": return "До Pi підключено кілька USB-аудіовиходів. Залиш один для равлика."
        case "headphone_output_missing": return "Вбудований вихід 3,5 мм Pi недоступний. Перевір його налаштування."
        case "headphone_output_ambiguous": return "Pi показує кілька вбудованих аудіовиходів. Потрібно уточнити налаштування."
        case "bluetooth_power_unavailable": return "Bluetooth на Pi вимкнений або заблокований. Не вдалося його ввімкнути."
        case "bluetooth_unavailable": return "Bluetooth-аудіоз’єднання не відновилося. Повтори підключення звуку."
        case "audio_service_unavailable": return "Звукова служба Pi недоступна. Перевір її налаштування."
        default: return "Звук не підключився. Камера працює; повтори підключення звуку."
        }
    }
    func refreshLevels() {
        levelQueue.retryFailed()
        levelsReadRequested = true
        startLevelSync()
    }
    func levelEditing(_ kind: String, _ editing: Bool) {
        if editing { levelQueue.editing.insert(kind) } else { levelQueue.editing.remove(kind); startLevelSync() }
    }
    func levelRange(_ kind: String) -> ClosedRange<Double> {
        AudioGainRange.range(kind: kind, microphoneGainSupported: microphoneGainSupported)
    }
    func stageLevel(_ kind: String, db: Double? = nil, toggleMute: Bool = false) {
        guard audioAdmin.unlocked, audioAdmin.sessionToken != nil,
              kind == "output" || kind == "input" else { return }
        let draft = db ?? (kind == "output" ? speakerDB : microphoneDB)
        guard let clamped = AudioGainRange.clamp(draft, kind: kind, microphoneGainSupported: microphoneGainSupported) else { return }
        if kind == "output" {
            speakerDB = clamped
            if toggleMute { speakerMuted.toggle() }
        } else {
            microphoneDB = clamped
            if toggleMute { microphoneMuted.toggle() }
        }
        levelQueue.stage(kind, db: kind == "output" ? speakerDB : microphoneDB,
            muted: kind == "output" ? speakerMuted : microphoneMuted)
        startLevelSync()
    }
    private func receiveLevels(_ result: [String: Any], revisions: [String: Int]) throws {
        guard result["ok"] as? Bool == true else { throw DemoError("Не вдалося прочитати рівні звуку Pi.") }
        let speaker = result["output"] as? [String: Any] ?? [:]
        let microphone = result["input"] as? [String: Any] ?? [:]
        // A reply acknowledges the captured edit only. Never overwrite a newer
        // drag/mute choice, including an edit on the other channel, or disable
        // a slider while its mouse gesture is still active.
        if levelQueue.acceptsSnapshot("output", revisions: revisions) {
            speakerLevelAvailable = speaker["available"] as? Bool == true
            if let db = speaker["db"] as? Double, let clamped = AudioGainRange.clamp(db, kind: "output", microphoneGainSupported: microphoneGainSupported) { speakerDB = clamped }
            speakerMuted = speaker["muted"] as? Bool ?? false
        }
        if levelQueue.acceptsSnapshot("input", revisions: revisions) {
            microphoneLevelAvailable = microphone["available"] as? Bool == true
            if let db = microphone["db"] as? Double, let clamped = AudioGainRange.clamp(db, kind: "input", microphoneGainSupported: microphoneGainSupported) { microphoneDB = clamped }
            microphoneMuted = microphone["muted"] as? Bool ?? false
        }
        if speakerLevelAvailable {
            levelsStatus = speaker["connection"] as? String == "headphones"
                ? "Динаміки: мініджек Raspberry Pi. Мікрофон: USB-карта. 0 дБ — без приглушення."
                : "Рівні USB-звукової карти Pi. 0 дБ — без приглушення."
        } else {
            levelsStatus = Self.audioMessage(speaker["error"] as? String ?? "")
        }
        if let quiet = result["quiet"] as? [String: Any], quiet["enabled"] as? Bool == true {
            quietStatus = quiet["error"] as? String == nil
                ? "Автоматична тиша: вихід вимикається через 2 секунди після зупинки відтворення."
                : "Автоматична тиша тимчасово недоступна. Відтворення залишається доступним."
        } else {
            quietStatus = "Онови Pi, щоб прибрати шум між відтвореннями."
            audioNeedsUpdate = true
        }
    }
    private func startLevelSync() {
        guard connected, !busy, !preparing, !audioBusy, levelsTask == nil else { return }
        guard piFeatures.contains("audio-levels-v1") else {
            levelsStatus = "Онови службу Pi один раз для регулювання гучності."
            audioNeedsUpdate = true
            return
        }
        levelsTask = Task { [weak self] in
            guard let self else { return }
            defer { if !Task.isCancelled { self.levelsBusy = false; self.levelsTask = nil } }
            while !Task.isCancelled && self.connected && !self.busy && !self.preparing && !self.audioBusy {
                // Coalesce rapid keyboard changes. During a mouse drag keep
                // the draft local until release; do not disable the slider.
                try? await Task.sleep(nanoseconds: 250_000_000)
                guard !Task.isCancelled, !self.busy, !self.preparing, !self.audioBusy else { return }
                let kind = self.audioAdmin.unlocked ? self.levelQueue.nextKind : nil
                let edit = kind.flatMap { self.levelQueue.pending[$0] }
                if edit == nil && !self.levelsReadRequested {
                    // Release or another user edit restarts the task. Failed
                    // drafts wait for a new edit or explicit refresh.
                    return
                }
                self.levelsReadRequested = false
                self.levelsBusy = true
                let revisions = self.levelQueue.revisions
                let body: [String: Any]? = kind.flatMap { key in edit.map { ["kind": key, "db": $0.db, "muted": $0.muted] } }
                do {
                    var result = try await self.api(edit == nil ? "/audio/levels" : "/audio/level", post: edit != nil, body: body)
                    // Pi's route reconciliation can briefly own the audio
                    // lock. Retain the draft while retrying this exact edit.
                    for _ in 0..<2 where result["error"] as? String == "audio_busy" {
                        try await Task.sleep(nanoseconds: 200_000_000)
                        if let kind, let edit, self.levelQueue.pending[kind] != edit { break }
                        result = try await self.api(edit == nil ? "/audio/levels" : "/audio/level", post: edit != nil, body: body)
                    }
                    try Task.checkCancellation()
                    try self.receiveLevels(result, revisions: revisions)
                    if let kind, let edit { self.levelQueue.acknowledge(kind, edit: edit) }
                } catch {
                    guard !Task.isCancelled else { return }
                    // A failed write is not an acknowledgement: keep the
                    // user's position instead of snapping to the old value.
                    if let kind, let edit { self.levelQueue.fail(kind, edit: edit) }
                    let recoveryRevisions = self.levelQueue.revisions
                    if let current = try? await self.api("/audio/levels"), current["ok"] as? Bool == true {
                        guard !Task.isCancelled else { return }
                        try? self.receiveLevels(current, revisions: recoveryRevisions)
                    } else {
                        guard !Task.isCancelled else { return }
                        if !self.levelQueue.protects("output") { self.speakerLevelAvailable = false }
                        if !self.levelQueue.protects("input") { self.microphoneLevelAvailable = false }
                    }
                    self.levelsStatus = "Не вдалося оновити гучність. Натисни оновлення звуку."
                }
                if !self.levelQueue.failed.isEmpty {
                    self.levelsStatus = "Не вдалося оновити гучність. Натисни оновлення звуку."
                }
                self.levelsBusy = false
            }
        }
    }
    // Only Picker setters call these methods. Device discovery and route
    // changes may update published IDs without applying either system default.
    func selectOutput(_ id: UInt32) {
        guard !audioBusy, !busy, !preparing, id != output else { return }
        if id == 0 { output = 0; return }
        applyAudio(selectedInput: nil, selectedOutput: id)
    }
    func selectInput(_ id: UInt32) {
        guard !audioBusy, !busy, !preparing, id != input else { return }
        if id == 0 { input = 0; return }
        // Changing a Bluetooth input can recreate its speaker endpoint. Keep
        // the current output by UID instead of reapplying an old Picker value.
        applyAudio(selectedInput: id, selectedOutput: SoundDevices.defaultDevice(input: false))
    }
    private func applyAudio(selectedInput: UInt32?, selectedOutput: UInt32?) {
        guard !audioBusy, !busy, !preparing else { return }
        let initial = SoundDevices.list()
        // Validate both selections before touching either system default.
        do {
            if let selectedInput {
                try AudioInputGuard.validateSelection(selectedInput, devices: initial, address: snailAudioAddress, callReady: callMicrophoneArmed)
            }
            if let selectedOutput, !initial.contains(where: { $0.id == selectedOutput && $0.output && !$0.uid.isEmpty }) {
                throw DemoError("Аудіопристрій уже від’єднано. Онови список.")
            }
        } catch { self.error = error.localizedDescription; return }
        let initialOutput = initial.first(where: { $0.id == selectedOutput })
        let outputUID = initialOutput?.uid
        let outputAddress = snailAudioAddress.flatMap { address in
            initialOutput.map { $0.bluetooth && AudioIdentity.matches(uid: $0.uid, address: address) } == true ? address : nil
        }
        audioBusy = true
        audioTask = Task { [weak self] in
            guard let self else { return }
            defer { if !Task.isCancelled { self.audioBusy = false; self.refreshAudio(); self.startLevelSync() } }
            do {
                var inputChanged = false
                if let selectedInput {
                    try AudioInputGuard.validateSelection(selectedInput, devices: SoundDevices.list(), address: self.snailAudioAddress, callReady: self.callMicrophoneArmed)
                    inputChanged = SoundDevices.defaultDevice(input: true) != selectedInput
                    try SoundDevices.setDefault(selectedInput, input: true)
                    self.input = selectedInput
                }
                if inputChanged { try await Task.sleep(nanoseconds: 400_000_000) }
                if let outputUID {
                    var chosen = false
                    for _ in 0..<20 {
                        try Task.checkCancellation()
                        let fresh = SoundDevices.list()
                        let candidates = outputAddress.map { address in
                            AudioIdentity.output(in: fresh, address: address).map { [$0] } ?? []
                        } ?? fresh.filter { $0.output && $0.uid == outputUID }
                        if candidates.count == 1 {
                            try SoundDevices.setDefault(candidates[0].id, input: false)
                            self.output = candidates[0].id; chosen = true; break
                        }
                        try await Task.sleep(nanoseconds: 300_000_000)
                    }
                    guard chosen else { throw DemoError("Аудіопристрій уже від’єднано. Онови список.") }
                }
                self.detail = "Системні аудіопристрої вибрано. У Meet обери їх або «За замовчуванням»."
            } catch {
                guard !Task.isCancelled else { return }
                self.error = error.localizedDescription
            }
        }
    }
    func settings(_ page: String) { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:" + page)!) }
    func help() {
        let guide = Bundle.main.resourceURL?.appendingPathComponent(AppLanguage.current.rawValue + ".lproj/Help.md")
        if let guide, FileManager.default.fileExists(atPath: guide.path) { NSWorkspace.shared.open(guide) }
        else if let url = Bundle.main.url(forResource: "README", withExtension: "md") { NSWorkspace.shared.open(url) }
    }
}
