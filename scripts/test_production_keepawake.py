import unittest

import install_production_keepawake as keepawake


class ProductionKeepawakeTest(unittest.TestCase):
    def test_build_plist_prevents_system_sleep_on_ac_power(self):
        plist = keepawake.build_plist("07:50:00", 10800)

        arguments = plist["ProgramArguments"]
        self.assertIn("/usr/bin/caffeinate", arguments)
        self.assertTrue(keepawake.has_caffeinate_flag(arguments, "-i"))
        self.assertTrue(keepawake.has_caffeinate_flag(arguments, "-m"))
        self.assertTrue(keepawake.has_caffeinate_flag(arguments, "-s"))
        self.assertEqual(arguments[-2:], ["-t", "10800"])

    def test_status_warning_detects_legacy_idle_only_flags(self):
        plist = {
            "ProgramArguments": [
                "/usr/bin/caffeinate",
                "-im",
                "-t",
                "10800",
            ]
        }

        warnings = keepawake.status_warnings_from_plist(plist)

        self.assertEqual(len(warnings), 1)
        self.assertIn("do not include -s", warnings[0])

    def test_status_warning_accepts_system_sleep_flag(self):
        plist = {
            "ProgramArguments": [
                "/usr/bin/caffeinate",
                "-ims",
                "-t",
                "10800",
            ]
        }

        self.assertEqual(keepawake.status_warnings_from_plist(plist), [])

    def test_wake_schedule_matches_default_daily_schedule(self):
        schedule = "\n".join(
            [
                "Repeating power events:",
                "  wakepoweron at 7:50AM every day",
            ]
        )

        self.assertTrue(keepawake.wake_schedule_matches(schedule, "MTWRFSU", "07:50:00"))

    def test_wake_schedule_does_not_match_different_days(self):
        schedule = "\n".join(
            [
                "Repeating power events:",
                "  wakepoweron at 7:50AM every day",
            ]
        )

        self.assertFalse(keepawake.wake_schedule_matches(schedule, "MTWRF", "07:50:00"))


if __name__ == "__main__":
    unittest.main()
