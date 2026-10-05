import Foundation

@main
struct AudioAdminSmoke {
    @MainActor static func waitFor(_ predicate: () -> Bool) async throws {
        let deadline = ProcessInfo.processInfo.systemUptime + 5
        while !predicate() {
            precondition(ProcessInfo.processInfo.systemUptime < deadline,
                         "Audio admin async transition timed out")
            try await Task.sleep(nanoseconds: 1_000_000)
        }
    }

    @MainActor static func main() async throws {
        let token = String(repeating: "a", count: 43)
        var calls = [(String, String?)]()
        var reply = (200, ["ok": true, "token": token, "expiresIn": 600.0] as [String: Any])
        let admin = AudioAdminModel { path, _, token in
            calls.append((path, token))
            return reply
        }
        var resets = 0
        admin.onLock = { resets += 1 }
        await admin.unlock(pin: "246813", connected: false, supported: true)
        precondition(calls.isEmpty && !admin.unlocked)
        await admin.unlock(pin: "246813", connected: true, supported: false)
        precondition(calls.isEmpty && !admin.unlocked)
        await admin.unlock(pin: "nope", connected: true, supported: true)
        precondition(calls.isEmpty && !admin.unlocked)
        await admin.unlock(pin: "246813", connected: true, supported: true)
        precondition(admin.unlocked && admin.sessionToken == token && !admin.busy)
        let beforeLock = resets
        admin.lock()
        precondition(!admin.unlocked && admin.sessionToken == nil && resets == beforeLock + 1)
        try await waitFor { calls.contains(where: { $0.0 == "/audio/admin/lock" && $0.1 == token }) }
        precondition(calls.contains(where: { $0.0 == "/audio/admin/lock" && $0.1 == token }))

        reply = (401, ["ok": false, "error": "admin_invalid_pin"])
        await admin.unlock(pin: "135790", connected: true, supported: true)
        precondition(!admin.unlocked && admin.message == "Неправильний код суперадміна.")
        reply = (429, ["ok": false, "error": "admin_rate_limited"])
        await admin.unlock(pin: "246813", connected: true, supported: true)
        precondition(!admin.unlocked && admin.message == "Забагато спроб. Спробуй ще раз пізніше.")

        reply = (200, ["ok": true, "token": token, "expiresIn": 0.05])
        await admin.unlock(pin: "246813", connected: true, supported: true)
        precondition(admin.unlocked)
        try await Task.sleep(nanoseconds: 100_000_000)
        // Token expiry must not depend on the UI cleanup task being scheduled.
        precondition(admin.sessionToken == nil)
        try await waitFor { !admin.unlocked }
        precondition(!admin.unlocked && admin.sessionToken == nil)

        var resume: CheckedContinuation<(Int, [String: Any]), Never>?
        var revokedStale = false
        let delayed = AudioAdminModel { path, _, given in
            if path == "/audio/admin/lock" { revokedStale = given == token; return (200, ["ok": true]) }
            return await withCheckedContinuation { resume = $0 }
        }
        let unlock = Task { await delayed.unlock(pin: "246813", connected: true, supported: true) }
        while resume == nil { await Task.yield() }
        delayed.lock()
        resume?.resume(returning: (200, ["ok": true, "token": token, "expiresIn": 600.0]))
        await unlock.value
        precondition(!delayed.unlocked && delayed.sessionToken == nil && !delayed.busy && revokedStale)
        print("Audio admin session checks passed")
    }
}
