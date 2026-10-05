import SwiftUI

struct AudioAdminSettings: View {
    @ObservedObject var controller: Controller
    @ObservedObject private var access: AudioAdminModel
    @State private var pin = ""

    init(controller: Controller) {
        self.controller = controller
        self.access = controller.audioAdmin
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L("Налаштування звуку суперадміна")).font(.title2.bold())
            if access.unlocked && controller.connected {
                AudioLevelControls(model: controller)
                Button(L("Заблокувати налаштування звуку")) { access.lock() }
            } else {
                Text(L("Гучність динаміків і підсилення мікрофона доступні після введення коду суперадміна."))
                    .font(.callout).foregroundStyle(.secondary)
                HStack {
                    SecureField(L("Код суперадміна"), text: $pin)
                        .textFieldStyle(.roundedBorder).frame(maxWidth: 240)
                        .onSubmit { unlock() }
                    Button(L("Відкрити налаштування звуку")) { unlock() }
                        .disabled(pin.isEmpty || access.busy || !controller.connected)
                    if access.busy { ProgressView().controlSize(.small) }
                }.disabled(access.busy || !controller.connected)
                if !controller.connected {
                    Text(L("Підключи равлика, щоб відкрити налаштування звуку."))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if !access.message.isEmpty {
                Text(L(access.message)).font(.caption).foregroundStyle(.orange)
            }
        }
        .onChange(of: controller.connected) { _, connected in
            if !connected { pin = "" }
        }
        .onDisappear { pin = "" }
    }

    private func unlock() {
        guard !pin.isEmpty, !access.busy, controller.connected else { return }
        let entered = pin
        pin = ""
        Task {
            await access.unlock(pin: entered, connected: controller.connected,
                                supported: controller.audioAdminAvailable)
            if access.unlocked { controller.refreshLevels() }
        }
    }
}
