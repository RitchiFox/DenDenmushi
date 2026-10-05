import SwiftUI

@main
enum Launcher {
    static func main() {
        if CommandLine.arguments.dropFirst().first == "--askpass" { exit(AskpassClient.main()) }
        DenDenApp.main()
    }
}

struct DenDenApp: App {
    @StateObject private var language = LanguageSettings()
    @StateObject private var model = Controller()
    @StateObject private var setupModel = SetupModel()
    var body: some Scene {
        WindowGroup("DenDenMushi", id: "main") {
            MainView(model: model, setupModel: setupModel).frame(minWidth: 900, minHeight: 680)
                .environmentObject(language).environment(\.locale, language.locale)
        }
        .defaultSize(width: 980, height: 720)
        .commands {
            CommandGroup(replacing: .appTermination) { Button(L("Завершити DenDenMushi")) { model.quit() }.keyboardShortcut("q") }
            CommandGroup(replacing: .help) { Button(L("Інструкція DenDenMushi")) { model.help() } }
        }
        MenuBarExtra("DenDenMushi", systemImage: "video.badge.waveform") {
            MenuView(model: model).environmentObject(language).environment(\.locale, language.locale)
        }
    }
}

struct MenuView: View {
    @EnvironmentObject private var language: LanguageSettings
    @ObservedObject var model: Controller
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        let _ = language.selected
        Text(L(model.status))
        Button(L("Відкрити DenDenMushi")) { openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
        Button(model.connected ? L("Повністю від’єднати") : L("Підключити равлика")) { model.connected ? model.disconnect() : model.connectDevice() }.disabled(model.busy || model.preparing)
        Divider()
        Button(L("Завершити")) { model.quit() }
    }
}

struct MainView: View {
    @EnvironmentObject private var language: LanguageSettings
    @ObservedObject var model: Controller
    @ObservedObject var setupModel: SetupModel
    @State private var tab = 0
    @StateObject private var updater = AppUpdates()
    @State private var handledLaunchNavigation = false
    var body: some View {
        let _ = language.selected
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("🐌").font(.system(size: 44))
                VStack(alignment: .leading, spacing: 3) {
                    Text("DenDenMushi").font(.largeTitle.bold())
                    Text(L("Камера та звук твого равлика")).foregroundStyle(.secondary)
                }
                Spacer()
                Text("LOCAL DEMO").font(.caption.monospaced()).padding(8).background(.quaternary, in: Capsule())
            }
            HStack(spacing: 10) {
                Circle().fill(model.error != nil ? Color.orange : model.connected ? .green : .gray).frame(width: 9, height: 9)
                Text(L(model.status)).font(.headline)
                if model.busy || setupModel.busy { ProgressView().controlSize(.small) }
                Spacer()
                Button(L("Підключити равлика")) { model.connectDevice() }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.connected || model.busy || setupModel.busy || model.preparing)
                Button(L("Повністю від’єднати")) {
                    setupModel.bluetooth.disconnect()
                    model.disconnect()
                }
                    .disabled(model.busy || setupModel.busy || model.preparing)

            }
            TabView(selection: $tab) {
                HStack(alignment: .top, spacing: 20) {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 18) {
                            camera
                            EyeControlsView(model: model.servos) { tab = 1; setupModel.showInstaller = true }
                        }
                    }.frame(maxWidth: .infinity)
                    Divider()
                    ScrollView { audio }.frame(width: 310)
                }.padding(18).tabItem { Label(L("Камера і звук"), systemImage: "video.badge.waveform") }.tag(0)
                setup.tabItem { Label(L("Налаштування"), systemImage: "gearshape") }.tag(1)
            }
            if let error = model.error {
                HStack(alignment: .top) {
                    Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
                    Text(L(error)).font(.callout).textSelection(.enabled).lineLimit(5)
                    Spacer()
                    Button { model.error = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
                }.padding(12).background(.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
            }
            Text(L(model.detail)).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .padding(24)
        .task {
            if UserDefaults.standard.object(forKey: "checkGitHubUpdates") == nil || UserDefaults.standard.bool(forKey: "checkGitHubUpdates") {
                await updater.check()
            }
        }
        .onAppear {
            setupModel.attach(model); model.refreshAudio()
            if !handledLaunchNavigation {
                handledLaunchNavigation = true
                if CommandLine.arguments.contains("--prepare-pi") { tab = 1; setupModel.showInstaller = true }
                if CommandLine.arguments.contains("--servos") { tab = 0 }
            }
        }
        .onChange(of: setupModel.saved?.id) { _, value in if value == nil { tab = 0 } }
        .sheet(isPresented: $setupModel.needsCode) { PairingView(model: setupModel, bluetooth: setupModel.bluetooth) }
        .sheet(isPresented: $setupModel.needsWiFi) { WiFiSetupView(model: setupModel) }
        .sheet(isPresented: $setupModel.needsReset) { ResetClientView(model: setupModel) }
        .sheet(isPresented: $setupModel.needsPasswordChange) { ChangePasswordView(model: setupModel) }
    }
    private var camera: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label(L("Камера"), systemImage: "video").font(.title2.bold())
            HStack(spacing: 8) {
            ZStack {
                RoundedRectangle(cornerRadius: 14).fill(Color.black.opacity(0.9))
                if let image = model.preview {
                    Image(nsImage: image).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 14))
                } else {
                    VStack(spacing: 10) {
                        Image(systemName: "video.slash").font(.system(size: 34))
                        Text(model.connected ? L("Очікую зображення з Pi…") : L("Камера вимкнена")).font(.headline)
                    }.foregroundStyle(.white.opacity(0.7))
                }
            }.frame(maxWidth: .infinity)
                CameraTiltControl(model: model.servos)
            }.frame(height: 250)
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(L("1280 × 720 · 30 кадрів/с")).font(.callout.bold())
                    Text(L("У дзвінку: OBS Virtual Camera")).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()

            }
            Text(L("Живий перегляд до 15 кадрів/с. У дзвінок передається повний відеопотік.")).font(.caption).foregroundStyle(.secondary)
        }
    }
    private var audio: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label(L("Звук"), systemImage: "speaker.wave.2").font(.title2.bold())
                Spacer()
                Button { model.refreshAudio(); model.refreshLevels() } label: { Image(systemName: "arrow.clockwise") }.help(L("Оновити аудіопристрої")).accessibilityLabel(L("Оновити аудіопристрої"))
            }
            Text(L(model.audioStatus)).font(.callout).foregroundStyle(model.audioNeedsAttention ? .orange : .secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !model.microphoneNotice.isEmpty {
                Text(L(model.microphoneNotice)).font(.caption).foregroundStyle(.orange)
                Button(L("Повторити звук")) { model.connectAudio() }.disabled(model.audioBusy || !model.connected)
            }
            Toggle(L("Мікрофон равлика"), isOn: Binding(get: { model.wantsCallMicrophone }, set: { model.setCallMicrophone($0) }))
                .disabled(model.audioBusy || model.busy || model.preparing)
            if model.audioTransport == "bluetooth" { Text(L("Експериментальний режим дзвінка: мікрофон і динаміки через Bluetooth, моно зі звуком телефонної якості.")).font(.caption).foregroundStyle(.secondary) }
            if !model.connected { Text(L("Режим застосуємо при наступному підключенні.")).font(.caption).foregroundStyle(.secondary) }
            if model.audioBusy { ProgressView().controlSize(.small) }
            if model.audioNeedsUpdate {
                Button(L("Оновити Pi")) { Task { await model.stopForReset(); tab = 1; setupModel.showInstaller = true } }
            } else if model.audioNeedsAttention && model.connected {
                Button(L("Повторити звук")) { model.connectAudio() }.disabled(model.audioBusy)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text(L("Динаміки")).font(.callout.bold())
                Picker(L("Динаміки"), selection: $model.output) {
                    Text(L("Залишити поточні")).tag(UInt32(0))
                    ForEach(model.devices.filter { $0.output && $0.uid != WiFiAudioDevices.micUID }) { Text($0.name).tag($0.id) }
                }.labelsHidden()
            }
            VStack(alignment: .leading, spacing: 6) {
                Text(L("Мікрофон")).font(.callout.bold())
                Picker(L("Мікрофон"), selection: $model.input) {
                    Text(L("Залишити поточний")).tag(UInt32(0))
                    ForEach(model.devices.filter { $0.input && $0.uid != WiFiAudioDevices.speakerUID }) { device in
                        if model.unavailableMicrophone(device) {
                            Text(device.name + " · " + L("Ще недоступний")).tag(device.id).disabled(true)
                        } else { Text(device.name).tag(device.id) }
                    }
                }.labelsHidden()
            }
            Button(L("Застосувати звук")) { model.applyAudio() }.buttonStyle(.bordered).disabled(model.audioBusy || model.busy || model.preparing)
            Divider()
            volumeControl(title: "Гучність динаміків равлика", kind: "output", value: $model.speakerDB, muted: $model.speakerMuted, available: model.speakerLevelAvailable)
            volumeControl(title: "Рівень мікрофона равлика", kind: "input", value: $model.microphoneDB, muted: $model.microphoneMuted, available: model.microphoneLevelAvailable)
            if model.microphoneGainSupported {
                Text(L("Понад 0 дБ підсилює голос і фоновий шум.")).font(.caption).foregroundStyle(.secondary)
            }
            if model.levelsBusy { ProgressView().controlSize(.small) }
            Text(L(model.levelsStatus)).font(.caption).foregroundStyle(.secondary)
            if !model.quietStatus.isEmpty { Text(L(model.quietStatus)).font(.caption).foregroundStyle(.secondary) }
            if model.callMicrophoneArmed && model.audioTransport == "bluetooth" {
                Text(L("У Telegram вибери мікрофон DenDenMushi або системний за замовчуванням.")).font(.caption).foregroundStyle(.secondary)
            }
            Divider()
            if model.audioTransport == "bluetooth" { Button(L("Підключити звук через Bluetooth")) { model.settings("com.apple.BluetoothSettings") }
            Text(L("Перше аудіоспарювання — один раз. Далі кнопка «Підключити» запускає камеру й динаміки.")).font(.caption).foregroundStyle(.secondary)
            }
            Text(L("У дзвінку вибери ці аудіопристрої або «За замовчуванням».")).font(.caption).foregroundStyle(.secondary)
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
    private var setup: some View {
        SetupView(model: setupModel, controller: model, updater: updater)
    }
}
