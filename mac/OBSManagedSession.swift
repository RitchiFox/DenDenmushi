import Foundation
import CoreFoundation

/// Owns only the OBS application instance returned when this connection starts.
/// The caller must finish its previous connection before attaching another one.
@MainActor
final class OBSManagedSession {
    struct Application {
        let isTerminated: () -> Bool
        let terminate: () -> Bool
        let reveal: () -> Void
    }

    enum Outcome: Equatable {
        case none, busy, unavailable, quitRefused
    }

    private struct Attachment {
        let application: Application
        let launchedByUs: Bool
    }
    private enum CheckFailure: Error { case ended, malformed }

    private let request: (String, [String: Any]) async throws -> [String: Any]
    private let close: () async -> Void
    private var attachment: Attachment?
    private var finishing: (id: UUID, task: Task<Outcome, Never>)?
    var sourceConfigured = false
    /// Set before sending StartVirtualCam: a lost reply may still have started it.
    var cameraRequested = false

    init(request: @escaping (String, [String: Any]) async throws -> [String: Any],
         close: @escaping () async -> Void) {
        self.request = request
        self.close = close
    }

    func attach(_ application: Application, launchedByUs: Bool) {
        precondition(finishing == nil, "Finish the previous OBS session before attaching another.")
        precondition(attachment == nil, "An OBS session is already attached.")
        attachment = Attachment(application: application, launchedByUs: launchedByUs)
        sourceConfigured = false
        cameraRequested = false
    }

    func finish() async -> Outcome {
        if let finishing { return await finishing.task.value }
        guard let attachment else { return .none }
        self.attachment = nil
        let sourceConfigured = self.sourceConfigured
        let cameraRequested = self.cameraRequested
        self.sourceConfigured = false
        self.cameraRequested = false
        let id = UUID()
        let task = Task { @MainActor in
            let outcome = await self.finish(attachment, sourceConfigured: sourceConfigured,
                                            cameraRequested: cameraRequested)
            await self.close()
            // Clear before waking any joined caller, so each may safely start
            // a new session once its awaited finish has returned.
            if self.finishing?.id == id { self.finishing = nil }
            return outcome
        }
        finishing = (id, task)
        let outcome = await task.value
        return outcome
    }

    private func checkedRequest(_ attachment: Attachment, _ name: String,
                                _ values: [String: Any] = [:]) async throws -> [String: Any] {
        // Never use a replacement OBS process that later binds the same port.
        guard !attachment.application.isTerminated() else { throw CheckFailure.ended }
        let response = try await request(name, values)
        guard !attachment.application.isTerminated() else { throw CheckFailure.ended }
        return response
    }

    private func active(_ attachment: Attachment, _ request: String) async throws -> Bool {
        let response = try await checkedRequest(attachment, request)
        // NSNumber(0/1), absent fields and strings are not status confirmations.
        guard let value = response["outputActive"] as? NSNumber,
              CFGetTypeID(value) == CFBooleanGetTypeID() else { throw CheckFailure.malformed }
        return value.boolValue
    }

    private func outputsBusy(_ attachment: Attachment) async throws -> Bool {
        for name in ["GetRecordStatus", "GetStreamStatus", "GetReplayBufferStatus"] {
            if try await active(attachment, name) { return true }
        }
        return false
    }

    private func collection(_ attachment: Attachment) async throws -> String {
        let response = try await checkedRequest(attachment, "GetSceneCollectionList")
        guard let name = response["currentSceneCollectionName"] as? String, !name.isEmpty else {
            throw CheckFailure.malformed
        }
        return name
    }

    private func preserve(_ attachment: Attachment, _ outcome: Outcome) -> Outcome {
        if attachment.launchedByUs, !attachment.application.isTerminated() {
            attachment.application.reveal()
        }
        return outcome
    }

    private func finish(_ attachment: Attachment, sourceConfigured: Bool,
                        cameraRequested: Bool) async -> Outcome {
        let application = attachment.application
        guard !application.isTerminated() else { return .none }
        // A connection failure must not touch an OBS the user already had open.
        if !attachment.launchedByUs, !sourceConfigured, !cameraRequested { return .none }
        do {
            if try await outputsBusy(attachment) { return preserve(attachment, .busy) }
            let originalCollection = try await collection(attachment)
            let cameraActive = try await active(attachment, "GetVirtualCamStatus")
            // A newly launched helper may fail before creating our collection.
            // Known-idle startup is still ours to close; an established session
            // that switched collections must be preserved.
            if originalCollection != "DenDenMushi Demo" && (sourceConfigured || cameraRequested || cameraActive) {
                return preserve(attachment, .busy)
            }
            if cameraActive && !cameraRequested { return preserve(attachment, .busy) }

            if sourceConfigured {
                let source = try await checkedRequest(attachment, "GetInputSettings", ["inputName": "DenDen Camera"])
                guard let kind = source["inputKind"] as? String,
                      let settings = source["inputSettings"] as? [String: Any],
                      let input = settings["input"] as? String else { throw CheckFailure.malformed }
                guard kind == "ffmpeg_source", input == "tcp://127.0.0.1:18554" else {
                    return preserve(attachment, .busy)
                }
            }
            if cameraActive {
                _ = try await checkedRequest(attachment, "StopVirtualCam")
                guard try await active(attachment, "GetVirtualCamStatus") == false else {
                    return preserve(attachment, .busy)
                }
            }
            if sourceConfigured {
                // Clearing just our input stops its reconnect loop even when
                // the user's existing OBS is kept open. Other settings remain.
                _ = try await checkedRequest(attachment, "SetInputSettings", [
                    "inputName": "DenDen Camera", "inputSettings": ["input": ""], "overlay": true
                ])
            }
            guard attachment.launchedByUs else { return .none }

            // Recheck after source cleanup; the user may have started another
            // output while the preceding asynchronous requests were running.
            if try await outputsBusy(attachment) { return preserve(attachment, .busy) }
            guard try await collection(attachment) == originalCollection,
                  try await active(attachment, "GetVirtualCamStatus") == false else {
                return preserve(attachment, .busy)
            }
            guard !application.isTerminated() else { return .none }
            guard application.terminate() else { return preserve(attachment, .quitRefused) }
            let deadline = ContinuousClock.now.advanced(by: .seconds(3))
            while !application.isTerminated(), ContinuousClock.now < deadline {
                try await Task.sleep(for: .milliseconds(100))
            }
            return application.isTerminated() ? .none : preserve(attachment, .quitRefused)
        } catch CheckFailure.ended {
            return .none
        } catch {
            // Lost authentication or an unknown status is not permission to
            // close OBS. Reveal our helper so the user can quit it normally.
            return application.isTerminated() ? .none : preserve(attachment, .unavailable)
        }
    }
}
