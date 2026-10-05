import SwiftUI
import Foundation

struct ServoChannel: Identifiable {
    let id: Int
    let pin: Int
    var active = false
    var holding = false
    var moving = false
    var preparing = false
    var angle: Double?
    var pulseWidthMicros: Int?
}

@MainActor
final class ServoModel: ObservableObject {
    @Published private(set) var connected = false
    @Published private(set) var busy = false
    @Published private(set) var commandChannels: Set<Int> = []
    @Published private(set) var stopping = false
    @Published private(set) var needsUpdate = false
    @Published private(set) var firstManual = false
    @Published private(set) var firstSmooth = false
    @Published private(set) var secondManual = false
    @Published private(set) var secondSlider = false
    @Published private(set) var secondSmooth = false
    @Published private(set) var thirdCalibration = false
    @Published private(set) var supportsSlowProbe = false
    @Published private(set) var slowProbeRunning = false
    @Published private(set) var slowProbePreparing = false
    @Published private(set) var message = "Серви підключаються разом із равликом."
    @Published private(set) var channels = [ServoChannel(id: 1, pin: 11), ServoChannel(id: 2, pin: 13), ServoChannel(id: 3, pin: 15)]
    @Published var error: String?
    private var tunnel: Process?
    private var poll: Task<Void, Never>?
    private var pollID = UUID()
    private var command: Task<Void, Never>?
    private var stopCommand: Task<Void, Never>?
    private var disconnectTask: Task<Void, Never>?
    private var channelCommands: [Int: Task<Void, Never>] = [:]
    private var channelRevisions = [0, 0, 0]
    private var generation = UUID()
    private var ownsHold = false
    private var lastKeepalive = Date.distantPast
    private let requestOverride: ((String, [String: Any]?) async throws -> [String: Any])?

    init(request: ((String, [String: Any]?) async throws -> [String: Any])? = nil) {
        requestOverride = request
    }

    #if SERVO_MODEL_TESTS
    convenience init(state: [String: Any], request: @escaping (String, [String: Any]?) async throws -> [String: Any]) throws {
        self.init(request: request)
        supportsSlowProbe = true
        try apply(state)
        connected = true
    }
    #endif

    private func api(_ path: String, body: [String: Any]? = nil) async throws -> [String: Any] {
        if let requestOverride { return try await requestOverride(path, body) }
        var request = URLRequest(url: URL(string: "http://127.0.0.1:18790" + path)!)
        request.timeoutInterval = 3
        request.setValue("desktop-v1", forHTTPHeaderField: "X-DenDen-Client")
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw DemoError("Сервіс Pi повернув помилку.")
        }
        return result
    }

    private func apply(_ state: [String: Any], only selectedChannels: Set<Int> = [1, 2, 3]) throws {
        guard state["ok"] as? Bool == true,
              let values = state["channels"] as? [[String: Any]], values.count == 3 else {
            throw DemoError("Керування сервами на Pi недоступне. Перевір живлення Pi та оновлення сервісу.")
        }
        firstManual = state["firstManual"] as? Bool == true && (state["fixedMotionSeconds"] as? Double) == 0.5
        secondManual = state["secondManual"] as? Bool == true && (state["fixedMotionSeconds"] as? Double) == 0.5
        secondSlider = state["secondSlider"] as? Bool == true
        secondSmooth = state["secondSmooth"] as? Bool == true
        thirdCalibration = (state["fixedMotionSeconds"] as? Double) == 0.5 && state["thirdCalibration"] as? Bool == true && (state["thirdMinPulse"] as? Int) == 833 && (state["thirdMaxPulse"] as? Int) == 1167
        firstSmooth = state["firstSmooth"] as? Bool == true
        var updated = channels
        for index in updated.indices {
            guard selectedChannels.contains(updated[index].id) else { continue }
            guard values[index]["channel"] as? Int == updated[index].id,
                  values[index]["pin"] as? Int == updated[index].pin,
                  let active = values[index]["active"] as? Bool else {
                throw DemoError("Сервіс Pi повернув помилку.")
            }
            updated[index].active = active
            updated[index].holding = values[index]["holding"] as? Bool ?? false
            if supportsSlowProbe {
                guard let moving = values[index]["moving"] as? Bool,
                      let preparing = values[index]["preparing"] as? Bool else {
                    throw DemoError("Сервіс Pi повернув помилку.")
                }
                updated[index].moving = moving
                updated[index].preparing = preparing
            } else {
                updated[index].moving = false
                updated[index].preparing = false
            }
            updated[index].pulseWidthMicros = values[index]["pulseWidthMicros"] as? Int
            updated[index].angle = ServoAngleScale.displayedAngle(
                channel: updated[index].id, legacyAngle: values[index]["angle"] as? Double,
                pulse: updated[index].pulseWidthMicros)
        }
        channels = updated
        // Motion progresses on Pi. Only acknowledged status may change these
        // indicators; clearing a local task would not stop a remote trajectory.
        slowProbeRunning = updated.contains { $0.active && $0.moving }
        slowProbePreparing = slowProbeRunning && updated[0].preparing
        ownsHold = ownsHold && updated.contains { $0.active && $0.holding }
    }

    func connect(host: String, user: String) {
        guard !busy, !stopping, !connected, disconnectTask == nil else { return }
        busy = true; error = nil; needsUpdate = false; supportsSlowProbe = false
        message = "Підключаю керування сервами…"
        let attempt = UUID(); generation = attempt
        command = Task {
            do {
                let target = try ConnectionRules.destination(user: user, host: host)
                guard FileManager.default.fileExists(atPath: SetupFiles.key.path),
                      FileManager.default.fileExists(atPath: SetupFiles.knownHosts.path) else {
                    throw DemoError("Спочатку підготуй Pi у налаштуваннях DenDenMushi.")
                }
                let logURL = SetupFiles.key.deletingLastPathComponent().appendingPathComponent("servos-ssh.log")
                FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
                let log = try FileHandle(forWritingTo: logURL)
                defer { try? log.close() }
                let process = Process()
                process.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
                process.arguments = ["-F", "/dev/null", "-N", "-T", "-i", SetupFiles.key.path,
                    "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                    "-o", "UserKnownHostsFile=" + SetupFiles.knownHosts.path,
                    "-o", "GlobalKnownHostsFile=/dev/null", "-o", "HostKeyAlgorithms=ssh-ed25519",
                    "-o", "LogLevel=DEBUG1", "-o", "ConnectTimeout=5", "-o", "ExitOnForwardFailure=yes",
                    "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
                    "-L", "127.0.0.1:18790:127.0.0.1:8789", target]
                var environment = ProcessInfo.processInfo.environment
                environment["LC_ALL"] = "C"; process.environment = environment
                process.standardOutput = FileHandle.nullDevice
                process.standardInput = FileHandle.nullDevice
                process.standardError = log
                try process.run(); tunnel = process
                var service: [String: Any]?
                for _ in 0..<30 {
                    try Task.checkCancellation()
                    guard process.isRunning else { break }
                    let output = (try? String(contentsOf: logURL, encoding: .utf8)) ?? ""
                    if SavedNetworkReconnect.tunnelReady(log: output, running: process.isRunning),
                       let state = try? await api("/status"), state["service"] as? String == "denden",
                       state["version"] as? Int == 1 { service = state; break }
                    try await Task.sleep(nanoseconds: 200_000_000)
                }
                guard generation == attempt, let service else {
                    throw DemoError("Pi недоступний. Перевір адресу, Wi-Fi та встановлення сервісу.")
                }
                let features = service["features"] as? [String] ?? []
                guard features.contains("servos-pulse-v2") else {
                    needsUpdate = true
                    throw DemoError("Онови сервіс на Pi, щоб з’явилося керування сервами.")
                }
                supportsSlowProbe = features.contains("servos-sweep-v1")
                needsUpdate = !supportsSlowProbe
                // Connecting only reads status; it never sends a position.
                let state = try await api("/servos/status")
                guard generation == attempt else { return }
                try apply(state)
                needsUpdate = !firstManual || !firstSmooth || !secondManual || !secondSlider || !secondSmooth || !thirdCalibration
                connected = true; busy = false
                message = "Керування готове."
                startPolling()
            } catch {
                if generation == attempt {
                    self.error = error.localizedDescription
                    message = "Керування не підключено."
                    closeTunnel(); busy = false
                }
            }
        }
    }

    private func startPolling() {
        poll?.cancel()
        let session = generation
        let identifier = UUID(); pollID = identifier
        poll = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                // The Pi still executes each 0.5-second trajectory. Read its
                // completion promptly, without locking the other channels.
                let interval: UInt64 = self.slowProbeRunning || !self.commandChannels.isEmpty ? 100_000_000 : 1_000_000_000
                do { try await Task.sleep(nanoseconds: interval) }
                catch { return }
                guard !Task.isCancelled, self.connected, self.generation == session,
                      self.pollID == identifier else { return }
                if self.busy || self.stopping { continue }
                let revisions = self.channelRevisions
                let pendingAtRequest = self.commandChannels
                do {
                    let state: [String: Any]
                    if self.ownsHold && Date().timeIntervalSince(self.lastKeepalive) >= 1 {
                        state = try await self.api("/servos/command", body: ["action": "keepalive", "lease": session.uuidString.lowercased()])
                        self.lastKeepalive = Date()
                    } else {
                        state = try await self.api("/servos/status")
                    }
                    guard !Task.isCancelled, self.generation == session, self.pollID == identifier,
                          !self.stopping else { return }
                    // A request started before a click must never replace its
                    // newer command response, even if it arrives last.
                    let unchanged = Set((1...3).filter {
                        revisions[$0 - 1] == self.channelRevisions[$0 - 1] &&
                        !pendingAtRequest.contains($0) && !self.commandChannels.contains($0)
                    })
                    try self.apply(state, only: unchanged)
                } catch {
                    guard !Task.isCancelled, self.generation == session, self.pollID == identifier else { return }
                    self.error = error.localizedDescription
                    self.message = "З’єднання із сервами втрачено. Імпульси вимкнуться на Pi автоматично."
                    self.closeTunnel()
                    return
                }
            }
        }
    }

    func hasPendingMove(channel: Int) -> Bool {
        commandChannels.contains(channel)
    }

    func isBusy(channel: Int) -> Bool {
        busy || stopping || disconnectTask != nil || commandChannels.contains(channel) || channels.first { $0.id == channel }?.moving == true
    }

    func confirmFirstPose(angle: Double) {
        guard connected, firstManual, !isBusy(channel: 1),
              channels[0].pulseWidthMicros == 0, angle.isFinite, (12.3...45.5).contains(angle) else { return }
        send(["action": "arm_first", "angle": angle])
    }

    func stepFirst(_ delta: Int) {
        guard connected, firstManual, !isBusy(channel: 1), [-1, 1].contains(delta),
              let pulse = channels[0].pulseWidthMicros,
              (637...1005).contains(pulse), (delta < 0 ? pulse > 637 : pulse < 1005) else { return }
        smoothFirst(start: nil, end: max(12.3, min(45.5, Double(pulse + 11 * delta - 500) * 90 / 1000)))
    }

    func confirmThirdPose(angle: Double) {
        guard connected, thirdCalibration, !isBusy(channel: 3), channels[2].pulseWidthMicros == 0,
              angle.isFinite, (30...60).contains(angle) else { return }
        send(["action": "arm_third", "angle": angle])
    }
    func moveThird(_ angle: Double) {
        guard connected, thirdCalibration, !isBusy(channel: 3),
              angle.isFinite, (30...60).contains(angle),
              let pulse = channels[2].pulseWidthMicros, (833...1167).contains(pulse) else { return }
        send(["action": "smooth_third", "expectedPulse": pulse,
              "startAngle": max(30, min(60, Double(pulse - 500) * 180 / 2000)),
              "endAngle": angle, "durationSeconds": 0.5, "lease": generation.uuidString.lowercased()])
    }

    func confirmSecondPose(angle: Double) {
        guard connected, secondManual, !isBusy(channel: 2), channels[1].pulseWidthMicros == 0,
              angle.isFinite, (3...33).contains(angle) else { return }
        send(["action": "arm_second", "angle": angle])
    }
    func moveSecond(_ angle: Double) {
        guard connected, secondSlider, !isBusy(channel: 2), angle.isFinite, (3...33).contains(angle),
              let pulse = channels[1].pulseWidthMicros, (533...867).contains(pulse) else { return }
        send(["action": "move_second", "angle": angle, "expectedPulse": pulse])
    }

    func smoothSecond(start: Double?, end: Double) {
        guard connected, secondSmooth, !isBusy(channel: 2),
              let pulse = channels[1].pulseWidthMicros, (533...867).contains(pulse),
              let current = channels[1].angle else { return }
        let from = start ?? current
        guard from.isFinite, end.isFinite, (3...33).contains(from),
              (3...33).contains(end) else { return }
        send(["action": "smooth_second", "expectedPulse": pulse, "startAngle": from,
              "endAngle": end, "durationSeconds": 0.5, "lease": generation.uuidString.lowercased()])
    }

    func stepSecond(_ delta: Int) {
        guard connected, secondManual, !isBusy(channel: 2), [-1, 1].contains(delta),
              let pulse = channels[1].pulseWidthMicros, (533...867).contains(pulse),
              (delta < 0 ? pulse > 533 : pulse < 867) else { return }
        smoothSecond(start: nil, end: max(3, min(33, Double(pulse + 11 * delta - 500) * 180 / 2000)))
    }

    func smoothFirst(start: Double?, end: Double) {
        guard connected, firstSmooth, !isBusy(channel: 1),
              let pulse = channels[0].pulseWidthMicros, (637...1005).contains(pulse),
              let current = channels[0].angle else { return }
        let from = start ?? current
        guard from.isFinite, end.isFinite,
              (12.3...45.5).contains(from), (12.3...45.5).contains(end) else { return }
        send(["action": "smooth_first", "expectedPulse": pulse, "startAngle": from,
              "endAngle": end, "durationSeconds": 0.5,
              "lease": generation.uuidString.lowercased()])
    }

    func release(channel: Int) {
        guard (1...3).contains(channel) else { return }
        stop(body: ["action": "release", "channel": channel])
    }

    private func send(_ body: [String: Any]) {
        guard let action = body["action"] as? String else { return }
        let channel = action.hasSuffix("_first") ? 1 : action.hasSuffix("_second") ? 2 : action.hasSuffix("_third") ? 3 : nil
        guard let channel, connected, !isBusy(channel: channel) else { return }
        error = nil
        commandChannels.insert(channel)
        channelRevisions[channel - 1] += 1
        let revision = channelRevisions[channel - 1]
        let session = generation
        // Each response acknowledges only its own channel. A complete Pi
        // snapshot from the other eye may already be older than this command.
        channelCommands[channel] = Task {
            defer {
                if generation == session, channelRevisions[channel - 1] == revision {
                    channelCommands[channel] = nil
                    commandChannels.remove(channel)
                }
            }
            do {
                let result = try await api("/servos/command", body: body)
                guard generation == session else { return }
                let rejection = result["error"] as? String
                if result["ok"] as? Bool == false,
                   rejection?.hasPrefix("first_servo_") == true || rejection?.hasPrefix("second_servo_") == true || rejection?.hasPrefix("third_servo_") == true {
                    self.error = "Крок не виконано. Перевір поточне положення та межі серви."
                    let state = try await api("/servos/status")
                    guard generation == session else { return }
                    try apply(state, only: [channel])
                } else {
                    if action.hasPrefix("smooth_"), body["lease"] as? String == session.uuidString.lowercased() {
                        ownsHold = true
                    }
                    try apply(result, only: [channel])
                }
            } catch {
                guard generation == session else { return }
                self.error = error.localizedDescription
                closeTunnel()
            }
        }
        startPolling()
    }

    func stopAll() {
        stop(body: ["action": "stop"])
    }
    private func stop(body: [String: Any]) {
        guard connected, !stopping else { return }
        stopping = true
        let previousPoll = poll; previousPoll?.cancel(); poll = nil
        let previousCommands = Array(channelCommands.values)
        let session = generation
        stopCommand = Task {
            defer { if generation == session { stopCommand = nil } }
            // Complete any earlier move request before sending stop so a
            // delayed in-flight move cannot arrive after the stop request.
            for request in previousCommands { await request.value }
            await previousPoll?.value
            guard generation == session, connected else { return }
            do {
                let state = try await api("/servos/command", body: body)
                guard generation == session else { return }
                try apply(state)
            }
            catch {
                guard generation == session else { return }
                self.error = error.localizedDescription
                closeTunnel()
                return
            }
            // Disconnect owns the barrier until its final stop is acknowledged
            // and the tunnel is closed. This earlier stop must not reopen UI.
            guard disconnectTask == nil else { return }
            stopping = false
            if connected { startPolling() }
        }
    }

    func disconnect() async {
        if let disconnectTask { await disconnectTask.value; return }
        stopping = true
        poll?.cancel(); poll = nil
        let previousStop = stopCommand
        let task = Task {
            defer { disconnectTask = nil }
            await command?.value
            await previousStop?.value
            for request in Array(channelCommands.values) { await request.value }
            if connected { _ = try? await api("/servos/command", body: ["action": "stop"]) }
            closeTunnel(); busy = false; stopping = false
            message = "Серви підключаються разом із равликом."
        }
        disconnectTask = task
        await task.value
    }
    private func closeTunnel() {
        generation = UUID()
        poll?.cancel(); poll = nil
        stopCommand?.cancel(); stopCommand = nil
        channelCommands.values.forEach { $0.cancel() }
        channelCommands.removeAll(); commandChannels.removeAll()
        if tunnel?.isRunning == true { tunnel?.terminate() }
        tunnel = nil; connected = false; busy = false; stopping = false
        supportsSlowProbe = false
        slowProbeRunning = false
        slowProbePreparing = false
        ownsHold = false
        // A disconnected UI cannot confirm the motor state. Clear activity
        // indicators; the broker still enforces the command/hold lease expiry.
        for index in channels.indices {
            channels[index].active = false
            channels[index].holding = false
            channels[index].moving = false
            channels[index].preparing = false
        }
    }
}

struct EyeControlsView: View {
    @ObservedObject var model: ServoModel
    var update: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(L(model.message)).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button(L("Зупинити всі")) { model.stopAll() }
                    .tint(.red).disabled(!model.connected || model.stopping)
            }
            HStack(alignment: .top, spacing: 10) {
                // Display order is anatomical: left eye (channel 2), right (1).
                ForEach([2, 1], id: \.self) { id in
                    if let channel = model.channels.first(where: { $0.id == id }) {
                        ServoRow(model: model, channel: channel).frame(maxWidth: .infinity)
                    }
                }
            }
            if let error = model.error { Text(L(error)).font(.caption).foregroundStyle(.orange) }
            if model.needsUpdate { Button(L("Оновити сервіс на Pi"), action: update) }
        }
    }
}

struct CameraTiltControl: View {
    @ObservedObject var model: ServoModel
    @State private var angle = 45.0
    @State private var dragging = false
    @State private var showReference = false
    private var channel: ServoChannel? { model.channels.first { $0.id == 3 } }
    private var known: Bool { (channel?.pulseWidthMicros ?? 0) > 0 }
    private func sync() {
        guard !dragging, !model.isBusy(channel: 3),
              let current = channel?.angle, current.isFinite,
              (30...60).contains(current) else { return }
        // Keep the selected target steady through PWM rounding and progress updates.
        if abs(angle - current) > 0.1 { angle = current }
    }
    var body: some View {
        VStack(spacing: 4) {
            CameraTiltSlider(value: $angle, enabled: model.connected && model.thirdCalibration && known && !model.isBusy(channel: 3), onEditingChanged: { editing in
                dragging = editing
                if !editing { model.moveThird(angle) }
            })
            .frame(width: 28, height: 218)
            .accessibilityLabel(L("Поворот камери"))
            .help(L("Поворот камери"))
            if model.connected && !known {
                Button { showReference = true } label: { Image(systemName: "exclamationmark.circle") }
                    .accessibilityLabel(L("Підтвердити положення — без руху"))
                    .popover(isPresented: $showReference) {
                        ThirdServoControlsView(model: model).padding(12).frame(width: 400)
                    }
            }
        }
        .frame(width: 32)
        .onAppear { sync() }
        .onChange(of: channel?.pulseWidthMicros) { _, _ in sync() }
        .onChange(of: model.isBusy(channel: 3)) { _, busy in if !busy { sync() } }
        .onChange(of: known) { _, value in if value { showReference = false } }
    }
}

// NSSlider tracks vertical pointer coordinates directly. Rotating a horizontal
// SwiftUI Slider also rotates its drawing, but can leave native tracking/layout
// out of step with the pointer when the surrounding view updates.
private struct CameraTiltSlider: NSViewRepresentable {
    @Binding var value: Double
    let enabled: Bool
    var onEditingChanged: (Bool) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> TrackingSlider {
        let slider = TrackingSlider(frame: NSRect(x: 0, y: 0, width: 28, height: 218))
        slider.isVertical = true
        slider.minValue = 30; slider.maxValue = 60
        slider.isContinuous = true
        slider.target = context.coordinator
        slider.action = #selector(Coordinator.changed(_:))
        slider.onEditingChanged = { context.coordinator.parent.onEditingChanged($0) }
        return slider
    }
    func updateNSView(_ slider: TrackingSlider, context: Context) {
        context.coordinator.parent = self
        slider.isEnabled = enabled
        slider.setAccessibilityLabel(L("Поворот камери"))
        if !slider.tracking { slider.doubleValue = 90 - value }
    }
    final class Coordinator: NSObject {
        var parent: CameraTiltSlider
        init(_ parent: CameraTiltSlider) { self.parent = parent }
        @objc func changed(_ slider: TrackingSlider) {
            // Preserve the existing direction: 30° at the top, 60° below.
            parent.value = ((90 - slider.doubleValue) * 10).rounded() / 10
            // Keyboard/accessibility adjustments have no mouse tracking phase.
            if !slider.tracking { parent.onEditingChanged(false) }
        }
    }
    final class TrackingSlider: NSSlider {
        var tracking = false
        var onEditingChanged: ((Bool) -> Void)?
        override func mouseDown(with event: NSEvent) {
            tracking = true
            onEditingChanged?(true)
            super.mouseDown(with: event)
            tracking = false
            onEditingChanged?(false)
        }
    }
}

struct ThirdServoControlsView: View {
    @ObservedObject var model: ServoModel
    var body: some View {
        if let channel = model.channels.first(where: { $0.id == 3 }) {
            ServoRow(model: model, channel: channel)
        }
    }
}

private struct ServoRow: View {
    @ObservedObject var model: ServoModel
    let channel: ServoChannel
    @State private var angle = 90.0
    @State private var thirdReference = 45.0
    @State private var thirdDragging = false

    private func syncCommandedAngle() {
        // Status is the last commanded angle, not a measured shaft position.
        // Assigning local state bypasses the control binding and cannot actuate.
        guard !thirdDragging, !model.isBusy(channel: channel.id),
              let value = channel.angle, value.isFinite, ServoAngleScale.range(channel: channel.id).contains(value) else { return }
        if abs(angle - value) > 0.1 { angle = value }
    }
    // Endpoint meanings confirmed by the user for each installed mechanism.
    private func eyeAngle(open: Bool) -> Double {
        channel.id == 1 ? (open ? 12.3 : 45.5) : (open ? 33 : 3)
    }
    private func moveEye(open: Bool) {
        if channel.id == 1 { model.smoothFirst(start: nil, end: eyeAngle(open: open)) }
        else { model.smoothSecond(start: nil, end: eyeAngle(open: open)) }
    }
    private func confirmEye(open: Bool) {
        if channel.id == 1 { model.confirmFirstPose(angle: eyeAngle(open: open)) }
        else { model.confirmSecondPose(angle: eyeAngle(open: open)) }
    }
    private func eyeButton(open: Bool) -> some View {
        Button { moveEye(open: open) } label: {
            VStack(spacing: 2) {
                if let url = Bundle.main.url(forResource: open ? "eye-open-law" : "eye-closed-law", withExtension: "png"),
                   let artwork = NSImage(contentsOf: url) {
                    Image(nsImage: artwork).resizable().scaledToFit()
                        .frame(width: 64, height: 64).accessibilityHidden(true)
                }
                Text(L(open ? "Відкрити" : "Закрити")).font(.callout)
            }.padding(.horizontal, 6).padding(.vertical, 4)
        }
        .accessibilityLabel(L(open ? "Відкрити" : "Закрити"))
        .frame(maxWidth: .infinity)
    }
    private var disabled: Bool { !model.connected || model.isBusy(channel: channel.id) }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(channel.id == 1 ? L("Праве око") : channel.id == 2 ? L("Ліве око") : L("Поворот камери")).font(.headline)
                if channel.id == 3 { Text(String(format: L("Контакт %d"), channel.pin)).font(.caption).foregroundStyle(.secondary) }
                Spacer()
                if channel.id == 3 {
                Circle().fill(model.connected && channel.active ? Color.green : .gray).frame(width: 7, height: 7)
                Text(L(!model.connected ? "Не підключено" : channel.moving ? "Рухається" : channel.holding ? "Утримує положення" : channel.active ? "Імпульси увімкнені" : "Відпущена"))
                    .font(.caption).foregroundStyle(.secondary)
                }
            }
            if channel.id <= 2 {
                Text(L("Час руху: 0,5 с — фіксований")).font(.caption).foregroundStyle(.secondary)
                if !(channel.id == 1 ? model.firstManual && model.firstSmooth : model.secondManual && model.secondSmooth) {
                    Text(L("Оновити сервіс на Pi"))
                } else if (channel.pulseWidthMicros ?? 0) == 0 {
                    Text(L("Підтвердь, як зараз розташоване око. Це не запускає рух. Обирай лише якщо воно повністю закрите або відкрите."))
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button(L("Зараз закрите")) { confirmEye(open: false) }
                        Button(L("Зараз відкрите")) { confirmEye(open: true) }
                    }.disabled(disabled)
                }
                HStack(spacing: 12) {
                    eyeButton(open: false)
                    eyeButton(open: true)
                }
                .controlSize(.regular)
                .disabled(disabled || (channel.pulseWidthMicros ?? 0) == 0 ||
                    !(channel.id == 1 ? model.firstManual && model.firstSmooth : model.secondManual && model.secondSmooth))
            } else {
                Text(L("Заданий діапазон механізму: 30–60°. Рух за ці межі заблоковано. Кут застосовується після відпускання повзунка."))
                    .font(.caption).foregroundStyle(.secondary)
                Text(L("Час руху: 0,5 с — фіксований")).font(.caption)
                if !model.thirdCalibration {
                    Text(L("Оновити сервіс на Pi"))
                } else if (channel.pulseWidthMicros ?? 0) == 0 {
                    Text(L("Після перезапуску вибери фактичне поточне положення. Підтвердження лише запам’ятовує його й не рухає серву. Якщо положення невідоме — не підтверджуй навмання."))
                        .font(.caption)
                    HStack {
                        Text(L("Поточне положення, °"))
                        TextField("30–60", value: $thirdReference, format: .number).frame(width: 70)
                        Button(L("Підтвердити положення — без руху")) { model.confirmThirdPose(angle: thirdReference) }
                            .disabled(disabled || !thirdReference.isFinite || !(30...60).contains(thirdReference))
                    }
                } else {
                    HStack(spacing: 16) {
                        Slider(value: $angle, in: 30...60, step: 0.1, onEditingChanged: { editing in
                            thirdDragging = editing
                            if !editing { model.moveThird(angle) }
                        }).accessibilityLabel(String(format: L("Кут серви %d"), channel.id))
                        Text(String(format: "%.1f°", angle)).monospacedDigit().frame(width: 60)
                    }.disabled(disabled)
                }
                Button(L("Зупинити рух")) { model.release(channel: 3) }
                    .disabled(!model.connected || model.stopping)
                Text(L("Після команди імпульси вимикаються через 0,5 с. Якщо вал змістився після відпускання, перше ввімкнення може бути різким."))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.padding(14).background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 12))
            .onAppear { syncCommandedAngle() }
            .onChange(of: channel.angle) { _, _ in syncCommandedAngle() }
            .onChange(of: model.isBusy(channel: channel.id)) { _, busy in
                if !busy { syncCommandedAngle() }
            }
            .onChange(of: model.stopping) { _, stopping in
                if !stopping { syncCommandedAngle() }
            }
    }
}
