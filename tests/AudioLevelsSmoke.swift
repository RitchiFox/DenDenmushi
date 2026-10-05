import Foundation

@main
struct AudioLevelsSmoke {
    static func main() {
        var queue = AudioLevelQueue()
        let beforeDrag = queue.revisions
        queue.editing.insert("output")
        queue.stage("output", db: -30, muted: false)
        precondition(queue.nextKind == nil)
        precondition(!queue.acceptsSnapshot("output", revisions: beforeDrag))
        precondition(queue.acceptsSnapshot("input", revisions: beforeDrag))
        queue.editing.remove("output")
        let first = queue.pending["output"]!

        // A failed/busy reply must not clear the visible choice or retry in
        // an endless loop. The other channel can still be changed.
        queue.fail("output", edit: first)
        precondition(queue.protects("output") && queue.nextKind == nil)
        precondition(queue.pending["output"]?.db == -30)
        queue.stage("input", db: -7, muted: true)
        precondition(queue.nextKind == "input")
        queue.acknowledge("input", edit: queue.pending["input"]!)
        precondition(queue.failed == ["output"])

        // Explicit refresh retries the retained choice; a successful reply
        // acknowledges it. A snapshot started before that edit stays stale.
        queue.retryFailed()
        precondition(queue.nextKind == "output")
        queue.acknowledge("output", edit: first)
        precondition(queue.pending.isEmpty && queue.failed.isEmpty)
        precondition(!queue.acceptsSnapshot("output", revisions: beforeDrag))
        precondition(queue.acceptsSnapshot("output", revisions: queue.revisions))

        // A newer drag or mute always wins over an older write's failure or
        // success, including a snapshot received after the new edit finishes.
        let previousSnapshot = queue.revisions
        queue.stage("output", db: -20, muted: false)
        let earlier = queue.pending["output"]!
        queue.stage("output", db: -10, muted: true)
        let latest = queue.pending["output"]!
        queue.fail("output", edit: earlier)
        precondition(queue.failed.isEmpty && queue.nextKind == "output")
        queue.acknowledge("output", edit: earlier)
        precondition(queue.pending["output"] == latest)
        queue.fail("output", edit: latest)
        queue.stage("output", db: -5, muted: false)
        precondition(queue.failed.isEmpty && queue.nextKind == "output")
        queue.acknowledge("output", edit: queue.pending["output"]!)
        precondition(!queue.acceptsSnapshot("output", revisions: previousSnapshot))

        // A pointer held down without moving also protects the slider from
        // remote availability/value changes until the gesture ends.
        let unchanged = queue.revisions
        queue.editing.insert("input")
        precondition(!queue.acceptsSnapshot("input", revisions: unchanged))
        queue.editing.remove("input")
        precondition(queue.acceptsSnapshot("input", revisions: unchanged))
        print("Audio draft, failure recovery and stale snapshot checks passed")
    }
}
