import Foundation

@MainActor
private final class ServoRequests {
    struct Request {
        let path: String
        let body: [String: Any]?
        let completion: CheckedContinuation<[String: Any], Error>
        var action: String { body?["action"] as? String ?? "status" }
    }
    var pending: [Request] = []
    var sent: [[String: Any]] = []
    var automatic = false

    func request(_ path: String, _ body: [String: Any]?) async throws -> [String: Any] {
        if let body { sent.append(body) }
        if automatic { return fixture() }
        return try await withCheckedThrowingContinuation { pending.append(Request(path: path, body: body, completion: $0)) }
    }
    func take(_ action: String) -> Request {
        guard let index = pending.firstIndex(where: { $0.action == action }) else { preconditionFailure("Missing \(action)") }
        return pending.remove(at: index)
    }
    func finish() {
        automatic = true
        let outstanding = pending; pending.removeAll()
        for request in outstanding { request.completion.resume(returning: fixture()) }
    }
}

private func fixture(pulses: [Int] = [1005, 533, 1000], moving: Set<Int> = []) -> [String: Any] {
    ["ok": true, "fixedMotionSeconds": 0.5, "firstManual": true, "firstSmooth": true,
     "secondManual": true, "secondSlider": true, "secondSmooth": true,
     "thirdCalibration": true, "thirdMinPulse": 833, "thirdMaxPulse": 1167,
     "channels": (1...3).map { id -> [String: Any] in
         ["channel": id, "pin": [11, 13, 15][id - 1], "active": moving.contains(id),
          "moving": moving.contains(id), "holding": moving.contains(id), "preparing": false,
          "pulseWidthMicros": pulses[id - 1]]
     }]
}

@main
struct ServoInteractionSmoke {
    @MainActor
    static func waitFor(_ predicate: () -> Bool) async throws {
        for _ in 0..<2_000 {
            if predicate() { return }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        preconditionFailure("Mock request timed out")
    }

    @MainActor
    static func main() async throws {
        // Separate channels may start immediately. Reordered full Pi snapshots
        // must acknowledge only the channel belonging to that command.
        let requests = ServoRequests()
        let model = try ServoModel(state: fixture(), request: requests.request)
        model.smoothFirst(start: nil, end: 12.3)
        model.smoothSecond(start: nil, end: 33)
        model.moveThird(60)
        precondition(model.commandChannels == [1, 2, 3])
        try await waitFor { requests.pending.count == 3 }
        for request in requests.pending {
            precondition(request.body?["durationSeconds"] as? Double == 0.5)
        }
        let first = requests.take("smooth_first")
        let second = requests.take("smooth_second")
        let third = requests.take("smooth_third")
        precondition(first.body?["expectedPulse"] as? Int == 1005)
        precondition(second.body?["expectedPulse"] as? Int == 533)
        precondition(third.body?["expectedPulse"] as? Int == 1000)
        try await waitFor { !requests.pending.isEmpty }
        let beforeAcknowledgment = requests.pending.removeFirst()
        second.completion.resume(returning: fixture(moving: [2]))
        try await waitFor { !model.hasPendingMove(channel: 2) }
        first.completion.resume(returning: fixture(moving: [1]))
        third.completion.resume(returning: fixture(moving: [3]))
        try await waitFor { model.commandChannels.isEmpty }
        // The request began after the click but before the command reached Pi.
        beforeAcknowledgment.completion.resume(returning: fixture())
        for _ in 0..<10 { await Task.yield() }
        precondition(model.channels.allSatisfy { $0.moving })
        precondition(model.isBusy(channel: 3)) // Camera target stays protected while moving.
        model.smoothSecond(start: nil, end: 3) // Same channel must not overlap.
        precondition(requests.sent.count == 3)
        try await waitFor { !requests.pending.isEmpty }
        let completed = requests.pending.removeFirst()
        completed.completion.resume(returning: fixture(pulses: [637, 867, 1167]))
        try await waitFor { !model.slowProbeRunning }
        precondition((1...3).allSatisfy { !model.isBusy(channel: $0) })
        precondition(model.channels[2].angle == 60)
        requests.finish()
        await model.disconnect()

        // A status/keepalive captured before a different-channel click can
        // arrive after its response; it must not roll that channel backwards.
        let staleRequests = ServoRequests()
        let staleModel = try ServoModel(state: fixture(), request: staleRequests.request)
        staleModel.smoothFirst(start: nil, end: 12.3)
        try await waitFor { !staleRequests.pending.isEmpty }
        staleRequests.take("smooth_first").completion.resume(returning: fixture(moving: [1]))
        try await waitFor { !staleModel.hasPendingMove(channel: 1) && !staleRequests.pending.isEmpty }
        let delayedPoll = staleRequests.pending.removeFirst()
        staleModel.moveThird(60)
        try await waitFor { staleRequests.pending.contains { $0.action == "smooth_third" } }
        staleRequests.take("smooth_third").completion.resume(returning: fixture(moving: [3]))
        try await waitFor { !staleModel.hasPendingMove(channel: 3) }
        delayedPoll.completion.resume(returning: fixture())
        for _ in 0..<10 { await Task.yield() }
        precondition(staleModel.channels[0].moving && staleModel.channels[2].moving)
        precondition(staleModel.connected)
        staleRequests.finish()
        await staleModel.disconnect()

        // Stop waits for every in-flight channel, then sends the final command.
        // No delayed move can land after it, and new clicks are blocked meanwhile.
        let stopRequests = ServoRequests()
        let stopModel = try ServoModel(state: fixture(), request: stopRequests.request)
        stopModel.smoothFirst(start: nil, end: 12.3)
        stopModel.smoothSecond(start: nil, end: 33)
        try await waitFor { stopRequests.pending.count == 2 }
        stopModel.stopAll()
        stopModel.moveThird(60)
        for _ in 0..<10 { await Task.yield() }
        precondition(stopRequests.sent.count == 2)
        stopRequests.take("smooth_second").completion.resume(returning: fixture(moving: [2]))
        try await waitFor { !stopModel.hasPendingMove(channel: 2) }
        precondition(!stopRequests.pending.contains { $0.action == "stop" })
        stopRequests.take("smooth_first").completion.resume(returning: fixture(moving: [1]))
        try await waitFor { stopRequests.pending.contains { $0.action == "stop" } }
        precondition(stopRequests.sent.last?["action"] as? String == "stop")
        stopRequests.take("stop").completion.resume(returning: fixture())
        try await waitFor { !stopModel.stopping }
        precondition(stopModel.channels.allSatisfy { !$0.active && !$0.moving })
        stopRequests.finish()
        await stopModel.disconnect()

        // An earlier Stop completion cannot reopen controls while disconnect
        // is waiting for its own final stop. Repeated disconnect calls join it.
        let teardownRequests = ServoRequests()
        let teardownModel = try ServoModel(state: fixture(), request: teardownRequests.request)
        teardownModel.stopAll()
        try await waitFor { teardownRequests.pending.contains { $0.action == "stop" } }
        let firstStop = teardownRequests.take("stop")
        let disconnect = Task { await teardownModel.disconnect() }
        let repeatedDisconnect = Task { await teardownModel.disconnect() }
        for _ in 0..<10 { await Task.yield() }
        precondition(teardownRequests.sent.count == 1)
        firstStop.completion.resume(returning: fixture())
        try await waitFor { teardownRequests.pending.contains { $0.action == "stop" } }
        precondition(teardownModel.stopping && (1...3).allSatisfy { teardownModel.isBusy(channel: $0) })
        teardownModel.smoothFirst(start: nil, end: 12.3)
        teardownModel.smoothSecond(start: nil, end: 33)
        teardownModel.moveThird(60)
        for _ in 0..<10 { await Task.yield() }
        precondition(teardownRequests.sent.count == 2)
        precondition(teardownRequests.pending.count == 1)
        teardownRequests.take("stop").completion.resume(returning: fixture())
        await disconnect.value
        await repeatedDisconnect.value
        precondition(!teardownModel.connected && !teardownModel.stopping)
        precondition(teardownRequests.sent.count == 2 && teardownRequests.pending.isEmpty)
        print("Servo concurrency, stale replies, motion completion, stop ordering and disconnect barrier passed (mock requests only).")
    }
}
