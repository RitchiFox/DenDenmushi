import SwiftUI
import CoreWLAN
import CoreLocation

@MainActor
final class MacWiFi: NSObject, ObservableObject, @preconcurrency CLLocationManagerDelegate {
    @Published var ssid: String?
    private let location = CLLocationManager()
    override init() { super.init(); location.delegate = self }
    func refresh() { ssid = CWWiFiClient.shared().interface()?.ssid() }
    func requestAccess() { location.requestWhenInUseAuthorization(); refresh() }
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) { refresh() }
}

struct SavedWiFiNetwork: Identifiable {
    let id: String
    let ssid: String
    let active: Bool
}

struct WiFiNetworksView: View {
    @ObservedObject var model: SetupModel
    @ObservedObject var wifi: MacWiFi
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(L("Мережі Wi-Fi равлика")).font(.title2.bold())
                Spacer()
                Button(L("Оновити")) { model.loadNetworks() }.disabled(model.busy)
                Button(L("Закрити")) { dismiss() }
            }
            Toggle(L("Слідувати за Wi-Fi ноутбука"), isOn: $model.followWiFi)
            Text(L("Після зміни мережі Mac равлик переходить у ту саму збережену мережу через Bluetooth. Після повного від’єднання автоматичне підключення зупиняється."))
                .font(.caption).foregroundStyle(.secondary)
            if let ssid = wifi.ssid { Text("Mac: " + ssid) }
            else {
                Text(L("Назва мережі Mac недоступна. Дозволь доступ до геопозиції для автоматичного вибору Wi-Fi; координати не зберігаються."))
                    .font(.caption)
                Button(L("Дозволити визначення Wi-Fi")) { wifi.requestAccess() }
            }
            Text(L("Збережені на равлику мережі. Паролі залишаються на Pi. Для нової мережі пароль потрібен один раз."))
                .font(.caption).foregroundStyle(.secondary)
            if model.busy { ProgressView() }
            if let error = model.error { Text(L(error)).foregroundStyle(.orange) }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(model.networks) { network in
                        HStack {
                            Text(network.ssid).font(.headline)
                            if network.active { Text(L("Підключено")).foregroundStyle(.green) }
                            Spacer()
                            Button(L("Підключити")) { model.useNetwork(network) }
                                .disabled(model.busy || network.active)
                        }.padding(10)
                    }
                }
            }
            Button(L("Додати мережу…")) { dismiss(); model.ssid = wifi.ssid ?? ""; model.needsWiFi = true }
                .disabled(model.busy)
        }.padding(24).frame(width: 620, height: 430)
            .onAppear { wifi.refresh(); model.loadNetworks() }
    }
}
