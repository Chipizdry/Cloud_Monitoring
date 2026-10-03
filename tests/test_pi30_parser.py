import unittest

from backend.services.energy.pi30_parser import (
    parse_pi30_event,
    parse_qflag,
    parse_qpiws,
    parse_qpgsn,
)


class PI30ParserTests(unittest.TestCase):
    def test_parse_qflag_enable_disable_groups(self):
        parsed = parse_qflag("(EAbDjuvxyz)")
        self.assertEqual(parsed["silenceBuzzer"], True)
        self.assertEqual(parsed["overloadBypass"], True)
        self.assertEqual(parsed["powerSaving"], False)
        self.assertEqual(parsed["overloadRestart"], False)

    def test_parse_qpiws_fault_and_warning_split(self):
        parsed = parse_qpiws("01000100010000000000000000000000")
        self.assertTrue(any(item["name"] == "inverterFault" for item in parsed["activeFaults"]))
        self.assertTrue(any(item["name"] == "lineFail" for item in parsed["activeWarnings"]))

    def test_parse_qpgsn_minimal_payload(self):
        cmd = "QPGS1"
        ascii_payload = "(1 SERIAL01 P 0 230.0 50.0 230.0 50.0 1000 800 60 52.0 10 95 350.0 15 1000 800)"
        parsed = parse_qpgsn(cmd, ascii_payload)
        self.assertEqual(parsed["unit"], 1)
        self.assertEqual(parsed["serialNumber"], "SERIAL01")
        self.assertEqual(parsed["batterySOC"], 95)

    def test_parse_pi30_event_detects_no_response(self):
        event = parse_pi30_event({"cmd": "QPIGS", "hex_response": "No response from RS485"})
        self.assertEqual(event["status"], "no_response")
        self.assertIsNone(event["parsed"])

    def test_parse_pi30_event_detects_nak(self):
        event = parse_pi30_event({"cmd": "QPIGS", "hex_response": "284e414b0d"})
        self.assertEqual(event["status"], "nak")
        self.assertIsNone(event["parsed"])

    def test_parse_pi30_event_ignores_modbus_read(self):
        event = parse_pi30_event(
            {
                "cmd": "modbus_read",
                "hex_response": "01031802FB00F200CE00001C1300571BFC00261BEA0028000000002DCA",
            }
        )
        self.assertIsNone(event)

    def test_parse_pi30_event_accepts_command_type_field(self):
        event = parse_pi30_event(
            {
                "command_type": "QPIGS",
                "hex_response": "284e414b0d",
            }
        )
        self.assertEqual(event["cmd"], "QPIGS")
        self.assertEqual(event["status"], "nak")

    def test_parse_pi30_event_prefers_command_name_over_transport_type(self):
        event = parse_pi30_event(
            {
                "command_type": "pi30",
                "command_name": "QPIGS",
                "hex_response": "284e414b0d",
            }
        )
        self.assertEqual(event["cmd"], "QPIGS")
        self.assertEqual(event["status"], "nak")

    def test_parse_pi30_event_normalizes_lowercase_command(self):
        event = parse_pi30_event(
            {
                "cmd": "qpigs",
                "hex_response": "284e414b0d",
            }
        )
        self.assertEqual(event["cmd"], "QPIGS")
        self.assertEqual(event["status"], "nak")

    def test_parse_pi30_event_qpigs_accepts_float_like_int_fields(self):
        ascii_payload = "(230.0 50.0 230.0 50.0 0500.0 0400 20 400 52.0 00.0 95 25 00.0 350.0 52.0 00.0 00010000)"
        hex_response = "".join(f"{ord(ch):02x}" for ch in ascii_payload)

        event = parse_pi30_event(
            {
                "cmd": "QPIGS",
                "hex_response": hex_response,
            }
        )

        self.assertEqual(event["status"], "ok")
        self.assertEqual(event["parsed"]["batteryChargeCurrent"], 0)
        self.assertEqual(event["parsed"]["pvChargeCurrent"], 0)
        self.assertEqual(event["parsed"]["batteryDischargeCurrent"], 0)


if __name__ == "__main__":
    unittest.main()
