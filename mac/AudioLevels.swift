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
    var editing: Set<String> = []
    private var revision = 0

    mutating func stage(_ kind: String, db: Double, muted: Bool) {
        revision += 1
        pending[kind] = AudioLevelEdit(revision: revision, db: db, muted: muted)
    }
    mutating func acknowledge(_ kind: String, edit: AudioLevelEdit) {
        if pending[kind]?.revision == edit.revision { pending.removeValue(forKey: kind) }
    }
    func protects(_ kind: String) -> Bool { pending[kind] != nil || editing.contains(kind) }
    var nextKind: String? { ["output", "input"].first { pending[$0] != nil && !editing.contains($0) } }
}
