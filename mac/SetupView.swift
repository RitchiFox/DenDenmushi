import SwiftUI

struct SetupView: View {
    @EnvironmentObject private var language: LanguageSettings
    @ObservedObject var model: SetupModel
    @ObservedObject var controller: Controller
    @ObservedObject var updater: AppUpdates
    @State private var showWaitingSound = false
    @State private var showDeviceHistory = false
    @State private var showNetworks = false
    var body: some View {
        let _ = language.selected
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Picker(L("Мова програми"), selection: $language.selected) {
                    ForEach(AppLanguage.allCases) { Text($0.name).tag($0) }
                }.frame(maxWidth: 360)
                Text(L("Мова змінюється одразу й запам’ятовується на цьому Mac.")).font(.caption).foregroundStyle(.secondary)
                Divider()
                VStack(alignment: .leading, spacing: 10) {
                    Text(L("Передавання звуку")).font(.title2.bold())
                    Picker(L("Передавання звуку"), selection: Binding(get: { controller.audioTransport }, set: { controller.setAudioTransport($0) })) {
                        Text("Bluetooth").tag("bluetooth")
                        Text("Wi-Fi").tag("wifi")
                    }.pickerStyle(.segmented).frame(maxWidth: 360)
                        .disabled(model.busy || controller.audioBusy || controller.busy || controller.preparing)
                    Text(L("Вибір зберігається. Під час перемикання звук коротко перерветься; камера продовжує працювати.")).font(.caption).foregroundStyle(.secondary)
                    if controller.audioTransport == "wifi" {
                        Text(L("Мікрофон і динаміки через мережу. Bluetooth потрібен лише для першого налаштування або зміни мережі.")).font(.callout)
                        if !controller.wifiDriverReady {
                            Button(L("Встановити аудіопристрої Wi-Fi на Mac")) { controller.installWiFiAudioDevices() }
                            Text(L("Потрібно один раз. Інсталятор macOS попросить дозвіл адміністратора й коротко перезапустить звук Mac.")).font(.caption).foregroundStyle(.secondary)
                            Button(L("Перевірити після встановлення")) { controller.refreshAudio(); if controller.connected { controller.connectAudio() } }
                        }
                        Text(L("У Telegram: DenDenMushi Wi-Fi Microphone та DenDenMushi Wi-Fi Speakers.")).font(.caption)
                    }
                    if controller.audioBusy { ProgressView() }
                    Text(L(controller.audioStatus)).font(.caption).foregroundStyle(controller.audioNeedsAttention ? .orange : .secondary)
                }
                Divider()
                Button(L("Мережі Wi-Fi равлика")) { showNetworks = true }
                    .sheet(isPresented: $showNetworks) { WiFiNetworksView(model: model, wifi: model.macWiFi) }
                Button(L("Історія пристроїв")) { showDeviceHistory = true }
                    .sheet(isPresented: $showDeviceHistory) { DeviceHistoryView(controller: controller) }
                Toggle(L("Вимикати звук, коли равлик не підключений"), isOn: Binding(
                    get: { !controller.waitingEnabled },
                    set: { controller.setWaitingSoundEnabled(!$0) }
                ))
                .disabled(!controller.connected || !controller.waitingAvailable || controller.waitingBusy)
                Text(L("Прибраний прапорець — равлик програє звук очікування. Встановлений — очікує мовчки. Вибір зберігається автоматично на равлику."))
                    .font(.caption).foregroundStyle(.secondary)
                if !controller.connected {
                    Text(L("Підключи равлика, щоб зберегти налаштування.")).font(.caption).foregroundStyle(.secondary)
                }
                if !controller.waitingMessage.isEmpty {
                    Text(L(controller.waitingMessage)).font(.caption).foregroundStyle(.secondary)
                }
                Button(L("Звук очікування…")) {
                    showWaitingSound = true
                    controller.loadWaitingSound()
                }
                .sheet(isPresented: $showWaitingSound) {
                    VStack(alignment: .leading, spacing: 18) {
                        Text(L("Звук очікування")).font(.title2.bold())
                        Text(L("Повторюється після запуску равлика до підключення. Зберігається на Pi та працює без інтернету."))
                        Toggle(L("Увімкнути звук очікування"), isOn: $controller.waitingEnabled)
                            .disabled(controller.waitingBusy)
                        HStack {
                            Text(L("Гучність"))
                            Slider(value: $controller.waitingVolume, in: 0...100, step: 1)
                            Text("\(Int(controller.waitingVolume))%").monospacedDigit().frame(width: 45)
                        }
                        Text(L("Загальна гучність і вимкнення звуку динаміків також діють.")).font(.caption)
                        if !controller.connected {
                            Text(L("Підключи равлика, щоб зберегти налаштування."))
                        }
                        Text(L(controller.waitingMessage)).font(.caption)
                        HStack {
                            Button(L("Зберегти")) { controller.saveWaitingSound() }
                                .disabled(!controller.connected || !controller.waitingAvailable || controller.waitingBusy)
                            Spacer()
                            Button(L("Закрити")) { showWaitingSound = false }
                        }
                    }.padding(24).frame(width: 440)
                }
                Divider()
                Text(L("Твій равлик")).font(.title2.bold())
                if let saved = model.saved {
                    Label(saved.name + L(" збережений на цьому Mac"), systemImage: "checkmark.shield")
                    Text(L("На головному екрані достатньо натиснути «Підключити». Пошук, код і адресу програма підставить сама.")).foregroundStyle(.secondary)
                    HStack {
                        Button(L("Код для іншого Mac")) { model.copyCode() }
                        Button(L("Змінити Wi-Fi равлика")) { model.needsWiFi = true }.disabled(model.busy || controller.connected)
                    }
                    Button(L("Скинути підключення цього Mac")) { model.error = nil; model.resetPassword = ""; model.needsReset = true }.disabled(model.busy || controller.busy)
                } else {
                    Text(L("На головному екрані натисни «Підключити». Програма знайде готового равлика й попросить його код лише один раз.")).foregroundStyle(.secondary)
                }
                Button(L("Підключити іншого равлика")) { model.anotherPi() }.disabled(model.busy || controller.connected)
                Button(L("Змінити пароль равлика")) { model.error = nil; model.needsPasswordChange = true }.disabled(model.busy || controller.busy)
                Divider()
                DisclosureGroup(L("Підготовка нового Pi / оновлення сервісу"), isExpanded: $model.showInstaller) {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(L("Це потрібно власнику під час збирання. Для готового равлика достатньо кнопки «Підключити» та його коду.")).foregroundStyle(.secondary)
                        TextField(L("Адреса Pi"), text: $controller.host).textFieldStyle(.roundedBorder)
                        TextField(L("Користувач Pi"), text: $controller.user).textFieldStyle(.roundedBorder)
                        SecureField(L("Пароль цього користувача Pi"), text: $model.password).textFieldStyle(.roundedBorder)
                        Button(L("Підготувати мій Pi")) { model.install() }.buttonStyle(.borderedProminent).disabled(model.password.isEmpty || model.busy || controller.connected)
                        Text(L("Pi вже має бути в одній мережі з Mac і мати SSH. Пароль потрібен лише для встановлення й не зберігається; далі працюватиме збережений доступ.")).font(.caption).foregroundStyle(.secondary)
                    }.padding(.top, 12).disabled(model.busy)
                }
                if model.busy { ProgressView(L(model.message)) }
                else { Text(L(model.message)).font(.callout).foregroundStyle(.secondary) }
                if let error = model.error { Text(L(error)).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
                Button(L("Інструкція")) { controller.help() }
                Divider()
                AppUpdatesView(updater: updater,
                    unavailable: controller.connected || controller.busy || controller.preparing || controller.audioBusy || model.busy,
                    quit: { controller.quit() })
            }.padding(24)
        }
        .onAppear { controller.loadWaitingSound() }
        .onChange(of: controller.connected) { _, connected in
            if connected { controller.loadWaitingSound() }
        }
    }
}

struct ResetClientView: View {
    @EnvironmentObject private var language: LanguageSettings
    @ObservedObject var model: SetupModel
    var body: some View {
        let _ = language.selected
        VStack(alignment: .leading, spacing: 18) {
            Text(L("Повторити перше підключення")).font(.title2.bold())
            Text(L("Програма зупинить камеру, видалить доступ цього Mac на Pi та забуде збережене підключення.")).foregroundStyle(.secondary)
            Text(L("Для видалення ключа потрібен пароль користувача Pi. Пароль не зберігається.")).font(.callout)
            SecureField(L("Пароль користувача Pi"), text: $model.resetPassword).textFieldStyle(.roundedBorder).disabled(model.busy)
            Text(L("Код равлика залишиться в буфері обміну для повторного введення. Wi-Fi і сервіси Pi залишаться налаштованими.")).font(.caption).foregroundStyle(.secondary)
            if model.busy { ProgressView(L(model.message)) }
            if let error = model.error { Text(L(error)).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
            HStack {
                Button(L("Скасувати")) { model.needsReset = false; model.resetPassword = "" }.disabled(model.busy)
                Spacer()
                Button(L("Скинути підключення")) { model.resetThisMac() }.buttonStyle(.borderedProminent).disabled(model.busy || model.resetPassword.isEmpty)
            }
        }.padding(28).frame(width: 440).interactiveDismissDisabled()
    }
}

struct PairingView: View {
    @EnvironmentObject private var language: LanguageSettings
    @ObservedObject var model: SetupModel
    @ObservedObject var bluetooth: BluetoothSetup
    var body: some View {
        let _ = language.selected
        VStack(alignment: .leading, spacing: 18) {
            Text(L("Запам’ятати равлика")).font(.title2.bold())
            Text(L("Введи пароль або код самого равлика. Цей Mac запам’ятає доступ — наступного разу знадобиться лише «Підключити».")).foregroundStyle(.secondary)
            if bluetooth.nearby.count > 1 {
                Picker(L("Твій равлик"), selection: $model.selected) {
                    Text(L("Обери пристрій")).tag(Optional<UUID>.none)
                    ForEach(bluetooth.nearby) { pi in Text(pi.name + " · " + pi.id.uuidString.prefix(4)).tag(Optional(pi.id)) }
                }
            }
            SecureField(L("Пароль або код равлика"), text: $model.code).textFieldStyle(.roundedBorder).onSubmit { model.confirmCode() }
            Text(L("Це не пароль MacBook і не пароль користувача Raspberry Pi.")).font(.caption).foregroundStyle(.secondary)
            if let error = model.error { Text(L(error)).font(.callout).foregroundStyle(.orange) }
            HStack {
                Button(L("Скасувати")) { model.cancelPairing() }
                Spacer()
                Button(L("Підключити й запам’ятати")) { model.confirmCode() }.buttonStyle(.borderedProminent).disabled(model.code.isEmpty || model.busy)
            }
        }.padding(28).frame(width: 430).interactiveDismissDisabled()
    }
}

struct ChangePasswordView: View {
    @EnvironmentObject private var language: LanguageSettings
    @ObservedObject var model: SetupModel
    var body: some View {
        let _ = language.selected
        VStack(alignment: .leading, spacing: 18) {
            Text(L("Пароль самого равлика")).font(.title2.bold())
            Text(L("Цей пароль вводитимеш при першому підключенні нового Mac. На цьому Mac доступ збережеться автоматично.")).foregroundStyle(.secondary)
            SecureField(L("Новий пароль равлика"), text: $model.newCredential).textFieldStyle(.roundedBorder)
            SecureField(L("Повтори новий пароль"), text: $model.repeatCredential).textFieldStyle(.roundedBorder)
            Text(L("4–128 символів. Для постійного використання краще фраза з кількох слів.")).font(.caption).foregroundStyle(.secondary)
            Divider()
            SecureField(L("Поточний пароль користувача Pi"), text: $model.ownerPassword).textFieldStyle(.roundedBorder)
            Text(L("Потрібен власнику для запису зміни на Pi. Не зберігається. Пароль входу в сам Raspberry Pi залишиться попереднім.")).font(.caption).foregroundStyle(.secondary)
            if model.busy { ProgressView(L(model.message)) }
            if let error = model.error { Text(L(error)).foregroundStyle(.orange).font(.callout).textSelection(.enabled) }
            HStack {
                Button(L("Скасувати")) { model.newCredential = ""; model.repeatCredential = ""; model.ownerPassword = ""; model.needsPasswordChange = false }.disabled(model.busy)
                Spacer()
                Button(L("Зберегти пароль")) { model.changePassword() }.buttonStyle(.borderedProminent).disabled(model.busy || model.ownerPassword.isEmpty || model.newCredential.isEmpty || model.repeatCredential.isEmpty)
            }
        }.padding(28).frame(width: 450).interactiveDismissDisabled()
    }
}

struct WiFiSetupView: View {
    @EnvironmentObject private var language: LanguageSettings
    @ObservedObject var model: SetupModel
    var body: some View {
        let _ = language.selected
        VStack(alignment: .leading, spacing: 18) {
            Text(L("Wi-Fi для равлика")).font(.title2.bold())
            Text(L("Вкажи мережу, до якої підключений цей Mac. Равлик запам’ятає її для наступних увімкнень.")).foregroundStyle(.secondary)
            TextField(L("Назва Wi-Fi"), text: $model.ssid).textFieldStyle(.roundedBorder)
            SecureField(L("Пароль Wi-Fi"), text: $model.wifiPassword).textFieldStyle(.roundedBorder)
            Text(L("Pi 3 використовує мережу 2,4 ГГц із особистим паролем WPA2. Гостьова мережа має дозволяти зв’язок між пристроями.")).font(.caption).foregroundStyle(.secondary)
            if model.busy { ProgressView(L(model.message)) }
            if let error = model.error { Text(L(error)).font(.callout).foregroundStyle(.orange) }
            HStack {
                Button(L("Скасувати")) { model.needsWiFi = false }.disabled(model.busy)
                Spacer()
                Button(L("Зберегти й підключити")) { model.configureWiFi() }.buttonStyle(.borderedProminent).disabled(model.busy || model.ssid.isEmpty || model.wifiPassword.isEmpty)
            }
        }.padding(28).frame(width: 430).interactiveDismissDisabled()
    }
}
