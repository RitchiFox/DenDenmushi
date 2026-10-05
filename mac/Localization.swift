import Foundation
import Combine

enum AppLanguage: String, CaseIterable, Identifiable {
    case uk, cs, en
    var id: String { rawValue }
    var name: String {
        switch self { case .uk: return "Українська"; case .cs: return "Čeština"; case .en: return "English" }
    }
    static var current: AppLanguage { AppLanguage(rawValue: UserDefaults.standard.string(forKey: "appLanguage") ?? "uk") ?? .uk }
}

@MainActor
final class LanguageSettings: ObservableObject {
    @Published var selected = AppLanguage.current {
        didSet {
            UserDefaults.standard.set(selected.rawValue, forKey: "appLanguage")
            // App-scoped preference: standard AppKit menus follow this language
            // on the next launch; the SwiftUI screens update immediately.
            UserDefaults.standard.set([selected.rawValue], forKey: "AppleLanguages")
        }
    }
    var locale: Locale { Locale(identifier: selected.rawValue) }
}

enum Localization {
    static func text(_ source: String, language: AppLanguage = .current, bundle: Bundle = .main) -> String {
        guard let path = bundle.path(forResource: language.rawValue, ofType: "lproj"), let localized = Bundle(path: path) else { return source }
        let translated = localized.localizedString(forKey: source, value: source, table: "Localizable")
        if translated != source { return translated }
        // App-owned diagnostic envelopes are translated; hostnames, device names
        // and external diagnostic payloads are preserved verbatim.
        let prefixes = [
            "SSH не підключився. ",
            "З’єднання перерване. Натисни «Зупинити», потім «Підключити». ",
            "Pi відхилив Bluetooth-повідомлення. Перевір код равлика. ",
            "За цією адресою збережений інший Pi. Адресу не замінено автоматично: "
        ]
        for prefix in prefixes where source.hasPrefix(prefix) {
            return localized.localizedString(forKey: prefix, value: prefix, table: "Localizable") + text(String(source.dropFirst(prefix.count)), language: language, bundle: bundle)
        }
        let audioPrefix = "Не вдалося вибрати аудіопристрій ("
        if source.hasPrefix(audioPrefix), source.hasSuffix(").") {
            let value = String(source.dropFirst(audioPrefix.count).dropLast(2))
            return String(format: localized.localizedString(forKey: "Не вдалося вибрати аудіопристрій (%@).", value: nil, table: "Localizable"), value)
        }
        if source.contains("\n") { return source.components(separatedBy: "\n").map { text($0, language: language, bundle: bundle) }.joined(separator: "\n") }
        return source
    }
}

func L(_ source: String) -> String { Localization.text(source) }
