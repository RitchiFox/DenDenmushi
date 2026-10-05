import Foundation

struct AudioLevelEdit: Equatable {
    let revision: Int
    let db: Double
    let muted: Bool
}

// Main-actor-owned draft queue. Network replies may acknowledge old edits while
// the user is dragging either channel; those replies must not replace drafts.
struct AudioLevelQueue {
    private(set) var pending: [String: AudioLevelEdit] = [:]
    private(set) var failed: Set<String> = []
    var editing: Set<String> = []
    private var revision = 0
    private(set) var revisions: [String: Int] = [:]

    mutating func stage(_ kind: String, db: Double, muted: Bool) {
        revision += 1
        pending[kind] = AudioLevelEdit(revision: revision, db: db, muted: muted)
        revisions[kind] = revision
        failed.remove(kind)
    }
    mutating func acknowledge(_ kind: String, edit: AudioLevelEdit) {
        if pending[kind]?.revision == edit.revision {
            pending.removeValue(forKey: kind)
            failed.remove(kind)
        }
    }
    mutating func fail(_ kind: String, edit: AudioLevelEdit) {
        if pending[kind]?.revision == edit.revision { failed.insert(kind) }
    }
    mutating func retryFailed() { failed.removeAll() }
    func acceptsSnapshot(_ kind: String, revisions snapshot: [String: Int]) -> Bool {
        !protects(kind) && snapshot[kind, default: 0] == revisions[kind, default: 0]
    }
    func protects(_ kind: String) -> Bool { pending[kind] != nil || editing.contains(kind) }
    var nextKind: String? { ["output", "input"].first { pending[$0] != nil && !editing.contains($0) && !failed.contains($0) } }
}
