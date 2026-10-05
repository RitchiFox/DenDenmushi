"""Catch missing translations and unsafe format changes before building."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def catalog(language, table="Localizable"):
    result = {}
    for line in (ROOT / "Resources" / (language + ".lproj") / (table + ".strings")).read_text().splitlines():
        if not line.strip():
            continue
        key, value = line.removesuffix(";").split(" = ", 1)
        key, value = json.loads(key), json.loads(value)
        if key in result:
            raise AssertionError("Duplicate translation: " + key)
        result[key] = value
    return result


class LocalizationTests(unittest.TestCase):
    def test_all_languages_cover_every_key_and_format(self):
        base = catalog("uk")
        for lang in ["en", "cs"]:
            values = catalog(lang)
            self.assertEqual(base.keys(), values.keys())
            for key in base:
                self.assertTrue(values[key].strip())
                self.assertEqual(re.findall(r"%[@df]", base[key]), re.findall(r"%[@df]", values[key]), key)
                self.assertFalse(re.search(r"[А-Яа-яІіЇїЄєҐґ]", values[key]), key)

    def test_app_literals_have_translations(self):
        keys = catalog("uk")
        for path in (ROOT / "mac").glob("*.swift"):
            if path.name == "Localization.swift":
                continue
            for token in re.finditer(r'"(?:[^"\\]|\\.)*"', path.read_text()):
                source = token.group()[1:-1]
                if re.search(r"[А-Яа-яІіЇїЄєҐґ]", source) and "\\(" not in source:
                    self.assertIn(source, keys, f"{path.name}: {source}")

    def test_permission_descriptions_and_user_help_exist(self):
        for lang in ["uk", "cs", "en"]:
            values = catalog(lang, "InfoPlist")
            self.assertEqual(set(values), {"NSBluetoothAlwaysUsageDescription", "NSLocalNetworkUsageDescription", "NSMicrophoneUsageDescription"})
            self.assertTrue(all(values.values()))
            guide = ROOT / "Resources" / (lang + ".lproj") / "Help.md"
            self.assertIn("OBS Virtual Camera", guide.read_text())


if __name__ == '__main__':
    unittest.main()
