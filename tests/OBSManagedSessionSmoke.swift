import Foundation

@MainActor
private final class MockOBS {
    var terminated = false
    var terminateAccepted = true
    var terminateCalls = 0
    var revealCalls = 0
    var closeCalls = 0
    var requests: [String] = []
    var recording = false
    var streaming = false
    var replay = false
    var camera = true
    var currentCollection = "DenDenMushi Demo"
    var sourceInput = "tcp://127.0.0.1:18554"
    var malformedStatus: Any?
    var failure: String?
    var exitDuringRequest: String?
    var startRecordingAfterSourceCleanup = false
    var pauseFirstRequest = false
    var paused: CheckedContinuation<Void, Never>?

    var application: OBSManagedSession.Application {
        .init(isTerminated: { self.terminated }, terminate: {
            self.terminateCalls += 1
            if self.terminateAccepted { self.terminated = true }
            return self.terminateAccepted
        }, reveal: { self.revealCalls += 1 })
    }

    func request(_ name: String, _ values: [String: Any]) async throws -> [String: Any] {
        requests.append(name)
        if pauseFirstRequest {
            pauseFirstRequest = false
            await withCheckedContinuation { paused = $0 }
        }
        if exitDuringRequest == name { terminated = true }
        if failure == name { throw NSError(domain: "MockOBS", code: 1) }
        switch name {
        case "GetRecordStatus": return ["outputActive": malformedStatus ?? recording]
        case "GetStreamStatus": return ["outputActive": streaming]
        case "GetReplayBufferStatus": return ["outputActive": replay]
        case "GetVirtualCamStatus": return ["outputActive": camera]
        case "GetSceneCollectionList": return ["currentSceneCollectionName": currentCollection]
        case "GetInputSettings":
            precondition(values["inputName"] as? String == "DenDen Camera")
            return ["inputKind": "ffmpeg_source", "inputSettings": ["input": sourceInput]]
        case "StopVirtualCam": camera = false; return [:]
        case "SetInputSettings":
            precondition(values["inputName"] as? String == "DenDen Camera")
            precondition(values["overlay"] as? Bool == true)
            precondition((values["inputSettings"] as? [String: String]) == ["input": ""])
            sourceInput = ""
            if startRecordingAfterSourceCleanup { recording = true }
            return [:]
        default: preconditionFailure("Unexpected OBS request: \(name)")
        }
    }

    func session(owned: Bool = true, source: Bool = true, camera: Bool = true) -> OBSManagedSession {
        let result = OBSManagedSession(request: request, close: { self.closeCalls += 1 })
        result.attach(application, launchedByUs: owned)
        result.sourceConfigured = source
        result.cameraRequested = camera
        return result
    }
}

@main
struct OBSManagedSessionSmoke {
    @MainActor
    private static func expect(_ session: OBSManagedSession, _ outcome: OBSManagedSession.Outcome) async {
        let actual = await session.finish()
        precondition(actual == outcome, "Expected \(outcome), received \(actual)")
    }

    @MainActor
    static func main() async throws {
        // A successful helper session, including a StartVirtualCam whose reply
        // was lost, stops its camera/source and closes only its own process.
        let owned = MockOBS()
        let ownedSession = owned.session()
        await expect(ownedSession, .none)
        precondition(!owned.camera && owned.sourceInput.isEmpty)
        precondition(owned.terminateCalls == 1 && owned.closeCalls == 1 && owned.revealCalls == 0)
        await expect(ownedSession, .none)
        precondition(owned.terminateCalls == 1 && owned.closeCalls == 1)

        // The user's pre-existing OBS keeps running, but our camera and TCP
        // source no longer reconnect in its background.
        let existing = MockOBS()
        await expect(existing.session(owned: false), .none)
        precondition(existing.terminateCalls == 0 && existing.revealCalls == 0)
        precondition(!existing.camera && existing.sourceInput.isEmpty && existing.closeCalls == 1)

        let untouched = MockOBS()
        await expect(untouched.session(owned: false, source: false, camera: false), .none)
        precondition(untouched.requests.isEmpty && untouched.closeCalls == 1)

        for output in ["recording", "streaming", "replay"] {
            let busy = MockOBS()
            busy.recording = output == "recording"
            busy.streaming = output == "streaming"
            busy.replay = output == "replay"
            await expect(busy.session(), .busy)
            precondition(busy.terminateCalls == 0 && busy.revealCalls == 1 && busy.closeCalls == 1)
            precondition(busy.camera && !busy.sourceInput.isEmpty)
            precondition(!busy.requests.contains("StopVirtualCam") && !busy.requests.contains("SetInputSettings"))
        }

        let foreignCollection = MockOBS()
        foreignCollection.currentCollection = "User recording"
        await expect(foreignCollection.session(), .busy)
        precondition(foreignCollection.terminateCalls == 0 && foreignCollection.camera)
        let defaultAfterFailure = MockOBS()
        defaultAfterFailure.currentCollection = "Untitled"
        await expect(defaultAfterFailure.session(source: false, camera: false), .busy)
        precondition(defaultAfterFailure.revealCalls == 1)
        let idleAfterFailure = MockOBS()
        idleAfterFailure.currentCollection = "Untitled"
        idleAfterFailure.camera = false
        await expect(idleAfterFailure.session(source: false, camera: false), .none)
        precondition(idleAfterFailure.terminateCalls == 1 && idleAfterFailure.closeCalls == 1)
        precondition(!idleAfterFailure.requests.contains("StopVirtualCam") && !idleAfterFailure.requests.contains("SetInputSettings"))

        let foreignCamera = MockOBS()
        await expect(foreignCamera.session(camera: false), .busy)
        precondition(foreignCamera.terminateCalls == 0 && foreignCamera.camera)
        let changedSource = MockOBS()
        changedSource.sourceInput = "other-stream"
        await expect(changedSource.session(), .busy)
        precondition(changedSource.sourceInput == "other-stream" && changedSource.camera)

        // A number or string must never masquerade as a successful inactive
        // status. Unknown authentication/status always preserves the process.
        for badStatus: Any in [NSNumber(value: 0), "false", NSNull()] {
            let malformed = MockOBS()
            malformed.malformedStatus = badStatus
            await expect(malformed.session(), .unavailable)
            precondition(malformed.terminateCalls == 0 && malformed.revealCalls == 1 && malformed.closeCalls == 1)
        }
        let failedAuth = MockOBS()
        failedAuth.failure = "GetRecordStatus"
        await expect(failedAuth.session(source: false, camera: false), .unavailable)
        precondition(failedAuth.terminateCalls == 0 && failedAuth.revealCalls == 1 && failedAuth.closeCalls == 1)

        // An already exited tracked application cannot authorize requests to
        // a later OBS instance that happens to reuse the WebSocket port.
        let exited = MockOBS()
        exited.terminated = true
        await expect(exited.session(), .none)
        precondition(exited.requests.isEmpty && exited.terminateCalls == 0 && exited.closeCalls == 1)
        let exitsDuringRead = MockOBS()
        exitsDuringRead.exitDuringRequest = "GetRecordStatus"
        await expect(exitsDuringRead.session(), .none)
        precondition(exitsDuringRead.requests == ["GetRecordStatus"] && exitsDuringRead.terminateCalls == 0)

        let newlyBusy = MockOBS()
        newlyBusy.startRecordingAfterSourceCleanup = true
        await expect(newlyBusy.session(), .busy)
        precondition(newlyBusy.terminateCalls == 0 && newlyBusy.recording)

        let refused = MockOBS()
        refused.terminateAccepted = false
        await expect(refused.session(), .quitRefused)
        precondition(refused.terminateCalls == 1 && refused.revealCalls == 1 && refused.closeCalls == 1)

        // Concurrent Disconnect/Quit callers join the same operation; neither
        // a second termination nor a second WebSocket close races a new session.
        let simultaneous = MockOBS()
        simultaneous.pauseFirstRequest = true
        let session = simultaneous.session()
        let first = Task { @MainActor in await session.finish() }
        for _ in 0..<1_000 where simultaneous.paused == nil {
            try await Task.sleep(for: .milliseconds(1))
        }
        precondition(simultaneous.paused != nil)
        let second = Task { @MainActor in await session.finish() }
        await Task.yield()
        simultaneous.paused?.resume(); simultaneous.paused = nil
        let firstOutcome = await first.value
        let secondOutcome = await second.value
        precondition(firstOutcome == .none && secondOutcome == .none)
        precondition(simultaneous.terminateCalls == 1 && simultaneous.closeCalls == 1)
        print("OBS helper ownership, safe cleanup, status validation and concurrent finish checks passed")
    }
}
