import Foundation

@main struct LocalizationSmoke {
    static func main() {
        let bundle = Bundle(path: CommandLine.arguments[1])!
        precondition(Localization.text("Підключити", language: .en, bundle: bundle) == "Connect")
        precondition(Localization.text("Підключити", language: .cs, bundle: bundle) == "Připojit")
        precondition(Localization.text("Підключити", language: .uk, bundle: bundle) == "Підключити")
        // A status created before a language change is translated at display time.
        let status = "Готовий до підключення"
        precondition(Localization.text(status, language: .en, bundle: bundle) == "Ready to connect")
        precondition(Localization.text(status, language: .cs, bundle: bundle) == "Připraveno k připojení")
        let diagnostic = "SSH не підключився. host=example.local; token=%@; SSID=Моя мережа"
        precondition(Localization.text(diagnostic, language: .cs, bundle: bundle) == "SSH se nepodařilo připojit. host=example.local; token=%@; SSID=Моя мережа")
        precondition(Localization.text("Не вдалося вибрати аудіопристрій (-50).", language: .en, bundle: bundle) == "Could not select the audio device (-50).")
        let external = "My Headphones / DenDenMushi-Office / 10.0.0.4"
        precondition(Localization.text(external, language: .cs, bundle: bundle) == external)
        precondition(Localization.text("Pi відхилив Bluetooth-повідомлення. Перевір код равлика. Unknown ATT error.", language: .en, bundle: bundle) == "Pi rejected the Bluetooth message. Check the snail’s code. Unknown ATT error.")
        print("Localization: three languages, existing status, formatted errors and external data passed")
    }
}
