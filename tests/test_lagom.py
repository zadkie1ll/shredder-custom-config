import unittest

from lagom import BRIDGE_TAG, build_lagom_outbounds, choose_country_config


def make_profile(remarks: str, address: str, wl_address: str) -> dict:
    return {
        "remarks": remarks,
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {"vnext": [{"address": address, "port": 443}]},
                "streamSettings": {"sockopt": {"dialerProxy": "WL-IN"}},
            },
            {
                "tag": "WL-01-ENTRY",
                "protocol": "vless",
                "settings": {"vnext": [{"address": wl_address, "port": 443}]},
            },
        ],
    }


class LagomPositionTests(unittest.TestCase):
    def setUp(self):
        self.subscription = [
            make_profile("Germany", "de.example", "de-wl.example"),
            make_profile("Sweden", "se.example", "se-wl.example"),
        ]

    def test_position_has_priority_over_country_and_remarks(self):
        selected = choose_country_config(
            self.subscription,
            wanted_country="Germany",
            fallback_remarks="Germany",
            position=1,
        )
        self.assertEqual(selected["remarks"], "Sweden")

    def test_position_wraps_when_shredder_has_more_servers(self):
        selected = choose_country_config(
            self.subscription,
            wanted_country=None,
            fallback_remarks="",
            position=2,
        )
        self.assertEqual(selected["remarks"], "Germany")

    def test_bridge_and_wl_chain_come_from_same_profile(self):
        outbounds = build_lagom_outbounds(
            self.subscription,
            wanted_country=None,
            fallback_remarks="",
            dialer_proxy="ROUTING-IN",
            position=1,
        )

        self.assertEqual(outbounds[0]["tag"], BRIDGE_TAG)
        self.assertEqual(
            outbounds[0]["settings"]["vnext"][0]["address"], "se.example"
        )
        self.assertEqual(
            outbounds[0]["streamSettings"]["sockopt"]["dialerProxy"],
            "ROUTING-IN",
        )
        self.assertEqual(
            outbounds[1]["settings"]["vnext"][0]["address"], "se-wl.example"
        )


if __name__ == "__main__":
    unittest.main()
