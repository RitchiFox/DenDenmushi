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
    private var command: Task<Void, Never>?
    private var generation = UUID()
    private var ownsHold = false
    private var lastKeepalive = Date.distantPast
    private var pendingMoves: [Int: (angle: Double, hold: Bool)] = [:]
    private var inFlightMoveChannel: Int?
    private var moveSender: Task<Void, Never>?
    private var moveSenderID = UUID()

    private func api(_ path: String, body: [String: Any]? = nil) async throws -> [String: Any] {
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

    private func apply(_ state: [String: Any]) throws {
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
        guard !busy, !stopping, !connected else { return }
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
                try apply(try await api("/servos/status"))
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
        poll = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard let self, !Task.isCancelled, self.connected else { return }
                // Skip reads during a write so an old status cannot replace
                // the command's newer result in the UI.
                if self.busy || self.stopping { continue }
                do {
                    if self.ownsHold {
                        try self.apply(try await self.api("/servos/command", body: ["action": "keepalive", "lease": self.generation.uuidString.lowercased()]))
                        self.lastKeepalive = Date()
                    } else {
                        try self.apply(try await self.api("/servos/status"))
                    }
                }
                catch {
                    if Task.isCancelled { return }
                    self.error = error.localizedDescription
                    self.message = "З’єднання із сервами втрачено. Імпульси вимкнуться на Pi автоматично."
                    self.closeTunnel()
                    return
                }
            }
        }
    }

    func move(channel: Int, angle: Double, hold: Bool) {
        guard let wireAngle = ServoAngleScale.wireAngle(channel: channel, angle: angle) else { return }
        var body: [String: Any] = ["action": "move", "channel": channel, "angle": wireAngle]
        if channel == 1 { body["wide"] = true }
        if hold { body["lease"] = generation.uuidString.lowercased() }
        send(body)
    }

    func queueMove(channel: Int, angle: Double, hold: Bool) {
        guard connected, !stopping, ServoAngleScale.wireAngle(channel: channel, angle: angle) != nil else { return }
        pendingMoves[channel] = (angle, hold)
        guard moveSender == nil else { return }
        let sender = UUID(); moveSenderID = sender
        moveSender = Task {
            defer { if moveSenderID == sender { moveSender = nil } }
            while !Task.isCancelled && connected && !stopping {
                if busy {
                    await command?.value
                    if busy { try? await Task.sleep(nanoseconds: 20_000_000) }
                    continue
                }
                guard let channel = pendingMoves.keys.sorted().first,
                      let target = pendingMoves.removeValue(forKey: channel) else { return }
                move(channel: channel, angle: target.angle, hold: target.hold)
                await command?.value
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
    }

    func hasPendingMove(channel: Int) -> Bool {
        pendingMoves[channel] != nil || inFlightMoveChannel == channel
    }

    func confirmFirstPose(angle: Double) {
        guard connected, firstManual, !busy, !stopping,
              channels[0].pulseWidthMicros == 0, angle.isFinite, (12.3...45.5).contains(angle) else { return }
        send(["action": "arm_first", "angle": angle])
    }

    func stepFirst(_ delta: Int) {
        guard connected, firstManual, !busy, !stopping, [-1, 1].contains(delta),
              let pulse = channels[0].pulseWidthMicros,
              (637...1005).contains(pulse), (delta < 0 ? pulse > 637 : pulse < 1005) else { return }
        smoothFirst(start: nil, end: max(12.3, min(45.5, Double(pulse + 11 * delta - 500) * 90 / 1000)))
    }

    func confirmThirdPose(angle: Double) {
        guard connected, thirdCalibration, !busy, !stopping, channels[2].pulseWidthMicros == 0,
              angle.isFinite, (30...60).contains(angle) else { return }
        send(["action": "arm_third", "angle": angle])
    }
    func moveThird(_ angle: Double) {
        guard connected, thirdCalibration, !busy, !stopping, !slowProbeRunning,
              angle.isFinite, (30...60).contains(angle),
              let pulse = channels[2].pulseWidthMicros, (833...1167).contains(pulse) else { return }
        send(["action": "smooth_third", "expectedPulse": pulse,
              "startAngle": max(30, min(60, Double(pulse - 500) * 180 / 2000)),
              "endAngle": angle, "durationSeconds": 0.5, "lease": generation.uuidString.lowercased()])
    }

    func confirmSecondPose(angle: Double) {
        guard connected, secondManual, !busy, !stopping, channels[1].pulseWidthMicros == 0,
              angle.isFinite, (3...33).contains(angle) else { return }
        send(["action": "arm_second", "angle": angle])
    }
    func moveSecond(_ angle: Double) {
        guard connected, secondSlider, !busy, !stopping, angle.isFinite, (3...33).contains(angle),
              let pulse = channels[1].pulseWidthMicros, (533...867).contains(pulse) else { return }
        send(["action": "move_second", "angle": angle, "expectedPulse": pulse])
    }

    func smoothSecond(start: Double?, end: Double) {
        guard connected, secondSmooth, !busy, !stopping, !slowProbeRunning,
              let pulse = channels[1].pulseWidthMicros, (533...867).contains(pulse),
              let current = channels[1].angle else { return }
        let from = start ?? current
        guard from.isFinite, end.isFinite, (3...33).contains(from),
              (3...33).contains(end) else { return }
        send(["action": "smooth_second", "expectedPulse": pulse, "startAngle": from,
              "endAngle": end, "durationSeconds": 0.5, "lease": generation.uuidString.lowercased()])
    }

    func stepSecond(_ delta: Int) {
        guard connected, secondManual, !busy, !stopping, [-1, 1].contains(delta),
              let pulse = channels[1].pulseWidthMicros, (533...867).contains(pulse),
              (delta < 0 ? pulse > 533 : pulse < 867) else { return }
        smoothSecond(start: nil, end: max(3, min(33, Double(pulse + 11 * delta - 500) * 180 / 2000)))
    }

    func smoothFirst(start: Double?, end: Double) {
        guard connected, firstSmooth, !busy, !stopping, !slowProbeRunning,
              let pulse = channels[0].pulseWidthMicros, (637...1005).contains(pulse),
              let current = channels[0].angle else { return }
        let from = start ?? current
        guard from.isFinite, end.isFinite,
              (12.3...45.5).contains(from), (12.3...45.5).contains(end) else { return }
        send(["action": "smooth_first", "expectedPulse": pulse, "startAngle": from,
              "endAngle": end, "durationSeconds": 0.5,
              "lease": generation.uuidString.lowercased()])
    }

    private func cancelPendingMoves() {
        moveSenderID = UUID()
        moveSender?.cancel(); moveSender = nil
        pendingMoves.removeAll()
    }

    func release(channel: Int) {
        guard (1...3).contains(channel) else { return }
        stop(body: ["action": "release", "channel": channel])
    }
    private func send(_ body: [String: Any]) {
        guard connected, !busy, !stopping else { return }
        busy = true; error = nil
        let action = body["action"] as? String
        inFlightMoveChannel = (action == "move_third" || action == "smooth_third") ? 3 : (action == "step_second" || action == "move_second" || action == "smooth_second") ? 2 : (action == "step_first" || action == "smooth_first") ? 1 : ((action == "move" || action == "sweep") ? body["channel"] as? Int : nil)
        // Join the current status request before a write; this also avoids
        // displaying stale active/idle state after a command completes.
        let previousPoll = poll; previousPoll?.cancel(); poll = nil
        command = Task {
            defer { inFlightMoveChannel = nil }
            await previousPoll?.value
            do {
                let result = try await api("/servos/command", body: body)
                let rejection = result["error"] as? String
                if result["ok"] as? Bool == false, (rejection?.hasPrefix("first_servo_") == true || rejection?.hasPrefix("second_servo_") == true || rejection?.hasPrefix("third_servo_") == true) {
                    self.error = "Крок не виконано. Перевір поточне положення та межі серви."
                    try apply(try await api("/servos/status"))
                } else if action == "sweep", result["ok"] as? Bool == false,
                   rejection == "invalid_start_pulse" || rejection == "sweep_requires_kernel_pwm" {
                    // A service restart can invalidate cached starting metadata.
                    // A rejected probe neither grants ownership nor initializes
                    // a position; keep the connection available for manual setup.
                    if rejection == "invalid_start_pulse" {
                        self.error = "Спочатку задай першій серві початкове положення повзунком у вибраному розмаху."
                    } else {
                        supportsSlowProbe = false
                        needsUpdate = true
                        self.error = "Для плавного руху на Raspberry Pi онови сервіс у налаштуваннях."
                    }
                } else {
                    // Record ownership only after this command was acknowledged.
                    // An older status request must not clear a newer hold intent.
                    if (action == "move" || action == "sweep" || action == "smooth_first" || action == "smooth_second" || action == "smooth_third"),
                       body["lease"] as? String == generation.uuidString.lowercased() {
                        ownsHold = true
                    }
                    try apply(result)
                    // A long slider drag must not starve another channel's hold
                    // or autonomous trajectory.
                    if ownsHold && Date().timeIntervalSince(lastKeepalive) >= 1 {
                        try apply(try await api("/servos/command", body: ["action": "keepalive", "lease": generation.uuidString.lowercased()]))
                        lastKeepalive = Date()
                    }
                }
            }
            catch {
                self.error = error.localizedDescription
                closeTunnel()
            }
            busy = false
            if connected && !stopping { startPolling() }
        }
    }

    func stopAll() {
        stop(body: ["action": "stop"])
    }
    private func stop(body: [String: Any]) {
        guard connected, !stopping else { return }
        stopping = true
        cancelPendingMoves()
        let previousPoll = poll; previousPoll?.cancel(); poll = nil
        Task {
            // Complete any earlier move request before sending stop so a
            // delayed in-flight move cannot arrive after the stop request.
            await command?.value
            await previousPoll?.value
            do { try apply(try await api("/servos/command", body: body)) }
            catch {
                self.error = error.localizedDescription
                closeTunnel()
            }
            stopping = false
            if connected { startPolling() }
        }
    }

    func disconnect() async {
        stopping = true
        cancelPendingMoves()
        poll?.cancel(); poll = nil
        await command?.value
        if connected { _ = try? await api("/servos/command", body: ["action": "stop"]) }
        generation = UUID()
        closeTunnel(); busy = false; stopping = false
        message = "Серви підключаються разом із равликом."
    }
    private func closeTunnel() {
        cancelPendingMoves()
        if tunnel?.isRunning == true { tunnel?.terminate() }
        tunnel = nil; connected = false
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
        guard !dragging, !model.busy, !model.stopping,
              channel?.moving != true, !model.hasPendingMove(channel: 3),
              let current = channel?.angle, current.isFinite,
              (30...60).contains(current) else { return }
        // Keep the selected target steady through PWM rounding and progress updates.
        if abs(angle - current) > 0.1 { angle = current }
    }
    var body: some View {
        VStack(spacing: 4) {
            Slider(value: $angle, in: 30...60, step: 0.1, onEditingChanged: { editing in
                dragging = editing
                if !editing { model.moveThird(angle) }
            })
            .frame(width: 210)
            .rotationEffect(.degrees(90))
            .frame(width: 28, height: 218)
            .accessibilityLabel(L("Поворот камери"))
            .help(L("Поворот камери"))
            .disabled(!model.connected || !model.thirdCalibration || !known || model.busy || model.stopping || model.slowProbeRunning)
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
        .onChange(of: model.busy) { _, busy in if !busy { sync() } }
        .onChange(of: channel?.moving) { _, moving in if moving != true { sync() } }
        .onChange(of: known) { _, value in if value { showReference = false } }
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
    @AppStorage private var hold: Bool

    init(model: ServoModel, channel: ServoChannel) {
        self.model = model
        self.channel = channel
        // Persist preferences only. A saved preference must never replay a move.
        _hold = AppStorage(wrappedValue: false, "servoHoldAfterMove.\(channel.id)")
    }

    private var angleControl: Binding<Double> {
        Binding(get: { angle }, set: { value in
            angle = value
            model.queueMove(channel: channel.id, angle: value, hold: hold)
        })
    }

    private func syncCommandedAngle() {
        // Status is the last commanded angle, not a measured shaft position.
        // Assigning local state bypasses the control binding and cannot actuate.
        guard !thirdDragging, !model.hasPendingMove(channel: channel.id),
              let value = channel.angle, value.isFinite, ServoAngleScale.range(channel: channel.id).contains(value) else { return }
        angle = value
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
    private var disabled: Bool { !model.connected || model.busy || model.stopping || model.slowProbeRunning }
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
            .onChange(of: model.busy) { _, busy in
                if !busy { syncCommandedAngle() }
            }
            .onChange(of: model.stopping) { _, stopping in
                if !stopping { syncCommandedAngle() }
            }
    }
}
