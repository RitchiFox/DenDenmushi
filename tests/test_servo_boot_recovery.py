import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("recovery", Path(__file__).parents[1] / "scripts/recover-servo-boot.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def test_preserves_root_and_all_other_boot_arguments(self):
        line = "console=serial0,115200 console=tty1 root=PARTUUID=abc-02 rootfstype=ext4 rootwait quiet\n"
        result = recovery.plan(line, "dtparam=audio=on\n")
        self.assertEqual(result["cmdline.txt"].split(), line.split() + [recovery.MASK])
        self.assertEqual(len(result["cmdline.txt"].splitlines()), 1)
        self.assertEqual(result["config.txt"], "dtparam=audio=on\n")

    def test_only_exact_tft_overlay_is_commented_and_operation_is_idempotent(self):
        config = "[all]\ndtoverlay=tft35a:rotate=90\ndtoverlay=tft35a-other\n#dtoverlay=tft35a\ndtparam=spi=on\n"
        result = recovery.plan("root=PARTUUID=abc-02\n", config)
        self.assertEqual(result["config.txt"], config.replace("\ndtoverlay=tft35a:rotate=90", "\n# DenDen removed TFT: dtoverlay=tft35a:rotate=90"))
        self.assertEqual(recovery.plan(result["cmdline.txt"], result["config.txt"]), result)

    def test_refuses_unrecognized_or_multiline_kernel_arguments(self):
        for line in ("", "console=tty1", "root=x\nquiet\n", "root=x\0"):
            with self.subTest(line=line), self.assertRaises(ValueError):
                recovery.plan(line, "")


if __name__ == "__main__": unittest.main()
