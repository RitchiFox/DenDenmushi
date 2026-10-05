import SwiftUI

struct AudioLevelControls: View {
    @ObservedObject var model: Controller
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            volumeControl(title: "Гучність динаміків равлика", kind: "output", value: $model.speakerDB, muted: $model.speakerMuted, available: model.speakerLevelAvailable)
            volumeControl(title: "Рівень мікрофона равлика", kind: "input", value: $model.microphoneDB, muted: $model.microphoneMuted, available: model.microphoneLevelAvailable)
            if model.microphoneGainSupported {
                Text(L("Понад 0 дБ підсилює голос і фоновий шум.")).font(.caption).foregroundStyle(.secondary)
            }
            ProgressView().controlSize(.small)
                .opacity(model.levelsBusy ? 1 : 0)
                .accessibilityHidden(!model.levelsBusy)
            Text(L(model.levelsStatus)).font(.caption).foregroundStyle(.secondary)
            if !model.quietStatus.isEmpty { Text(L(model.quietStatus)).font(.caption).foregroundStyle(.secondary) }
        }
    }
    private func volumeControl(title: String, kind: String, value: Binding<Double>, muted: Binding<Bool>, available: Bool) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(L(title)).font(.callout.bold())
                Spacer()
                Text(available ? String(format: value.wrappedValue > 0 ? "+%.1f dB" : "%.1f dB", value.wrappedValue) : "—").font(.caption.monospacedDigit())
            }
            HStack {
                Slider(value: Binding(get: { value.wrappedValue }, set: { model.stageLevel(kind, db: $0) }), in: model.levelRange(kind), step: 1, onEditingChanged: { editing in
                    model.levelEditing(kind, editing)
                }).accessibilityLabel(L(title))
                Button {
                    model.stageLevel(kind, toggleMute: true)
                } label: {
                    Image(systemName: kind == "input" ? (muted.wrappedValue ? "mic.slash.fill" : "mic.fill") : (muted.wrappedValue ? "speaker.slash.fill" : "speaker.wave.2.fill"))
                }.help(L(muted.wrappedValue ? "Увімкнути звук" : "Вимкнути звук"))
                 .accessibilityLabel(L(title) + ": " + L(muted.wrappedValue ? "Увімкнути звук" : "Вимкнути звук"))
            }.disabled(!available || model.audioBusy || !model.connected)
        }
    }
}
