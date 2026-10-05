import SwiftUI

struct DeviceHistoryEntry: Identifiable {
    let id: String
    let name: String
    let transport: String
    let connected: Bool
    let first: Double?
    let last: Double?
}

struct DeviceHistoryView: View {
    @ObservedObject var controller: Controller
    @Environment(\.dismiss) private var dismiss
    private func date(_ value: Double?) -> String {
        guard let value else { return L("Невідомо") }
        return Date(timeIntervalSince1970: value).formatted(date: .abbreviated, time: .shortened)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(L("Історія пристроїв")).font(.title2.bold())
                Spacer()
                Button(L("Оновити")) { controller.loadDeviceHistory() }.disabled(controller.historyBusy)
                Button(L("Закрити")) { dismiss() }
            }
            Text(L("Історія зберігається на равлику з моменту цього оновлення. Для раніше збережених Bluetooth-пристроїв старі дати невідомі. Один пристрій може мати окремі записи програми та Bluetooth."))
                .font(.caption).foregroundStyle(.secondary)
            if controller.historyBusy { ProgressView() }
            if !controller.historyMessage.isEmpty { Text(L(controller.historyMessage)).foregroundStyle(.secondary) }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(controller.deviceHistory) { device in
                        VStack(alignment: .leading, spacing: 5) {
                            HStack {
                                Text(device.name).font(.headline)
                                Spacer()
                                Text(L(device.connected ? "Підключено" : "Не підключено")).foregroundStyle(device.connected ? .green : .secondary)
                            }
                            Text(device.transport == "bluetooth" ? "Bluetooth" : L("Програма DenDenMushi"))
                            Text(L("Перше зафіксоване підключення") + ": " + date(device.first))
                            Text(L("Останнє підключення") + ": " + date(device.last))
                            Text(device.id).font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
                        }.font(.caption).padding(12).frame(maxWidth: .infinity, alignment: .leading)
                            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
                    }
                }
            }
        }.padding(24).frame(width: 620, height: 470)
            .onAppear { controller.loadDeviceHistory() }
    }
}
