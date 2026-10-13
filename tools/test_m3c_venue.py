#!/usr/bin/env python3
"""Offline tests for the M3c cartridge venue script (tools/m3c_venue.py, 2026-10-13).

MockEngine + the session_file stand-in: the venue's CONTROL SEQUENCE is what gets tested -
restore counts per measure, the wrong-cartridge file actually restored, the leak check
computed, the metrics shape honest, and a control failure exits 2. Timings from the mock
are None/1; the script must record them without inventing numbers."""
from __future__ import annotations
import json, os, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import m3c_venue as V                                          # noqa: E402
from serve.frontend import ChatTemplate                        # noqa: E402
from serve.memory import MemoryService                         # noqa: E402
from serve.server import (ByteTokenizer, Cartridges, MockEngine, Service, serve)  # noqa: E402


class Venue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.slots = tempfile.TemporaryDirectory()
        tok = ByteTokenizer()
        self.engine = MockEngine(tok, "the mock answer", max_context=131072)
        self.svc = Service(self.engine, tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        self.svc.memory = MemoryService(self.tmp.name)
        self.svc.slot_save_path = self.slots.name
        self.svc.cartridges = Cartridges(self.svc, {"enabled": True, "class_prefixes": ["USR"],
                                                    "max_chars": 400})
        self.restores = []
        orig = self.engine.session_file

        def counted(action, path):
            if action == "restore":
                self.restores.append(os.path.basename(path))
            return orig(action, path)
        self.engine.session_file = counted
        open(os.path.join(self.slots.name, "c5_comp.bin"), "wb").write(b"OLDSESSION")
        self.svc.memory.remember("The user's launch ritual is coffee before code", scope="user",
                                 klass="USR")
        self.httpd = serve(self.svc, port=0)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        # one unmuted request boots the cartridge (seed) so the venue preflight passes
        V.call(self.url, "/v1/chat/completions",
               {"model": "m", "messages": [{"role": "user", "content": "warm the cart"}],
                "max_tokens": 64, "reasoning_effort": "off"})
        self.out = os.path.join(self.tmp.name, "m3c.jsonl")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.svc.memory.close()
        self.tmp.cleanup()
        self.slots.cleanup()

    def test_venue_runs_the_protocol_and_passes_the_control(self):
        code = V.main(["--url", self.url, "--ask", "what is my launch ritual?",
                       "--gold", "coffee", "--repeats", "2", "--out", self.out])
        self.assertEqual(code, 0, "the mock never mentions coffee - no leak, control passes")
        rows = [json.loads(l) for l in open(self.out)]
        by = {}
        for r in rows:
            by[r["m"]] = by.get(r["m"], 0) + 1
        self.assertEqual(by, {"M1_restore": 2, "M2_warm": 2, "M2_cold": 2,
                              "M3_wrongcartridge_muted": 2, "M3_rightfile_warm": 2})
        cart_file = [f for f in os.listdir(self.slots.name) if f.startswith("MEM-CRG-")][0]
        # 6 restores of the right file, and the count is the state machine made visible:
        # M1 x2 + the warm asks that follow a muted one (muted invalidates, so the next
        # standing request restores - the law M3b shipped, firing exactly as designed:
        # M2 warm rep1 x1 + positive-control ensure x1) + the script's own explicit
        # restores x2. If this number drifts, the invalidation law changed - read it,
        # do not renumber it.
        self.assertEqual(self.restores.count(cart_file), 6)
        self.assertEqual(self.restores.count("c5_comp.bin"), 2)     # the wrong cartridge ran
        mfile = json.load(open(self.out + ".metrics.json"))
        met = mfile["metrics"]
        self.assertEqual(met["restore_ms_median"], 1)               # the mock's honest 1 ms
        self.assertEqual(met["control"]["leaks"], 0)
        self.assertEqual(met["warm"]["gold_hits"], 0)               # mock answers are not gold
        self.assertIsNone(met["warm"]["prompt_ms_median"])          # no timings from the mock: None, not invented
        self.assertIn("pass shape", met["reading"])

    def test_preflight_refuses_a_quiet_cartridge(self):
        self.svc.cartridges.state = "failed: venue not ready"
        with self.assertRaises(SystemExit):
            V.main(["--url", self.url, "--ask", "q", "--gold", "g", "--repeats", "1",
                    "--out", self.out])

    def test_pending_self_warms_then_the_venue_runs(self):
        # "pending" means the next standing request restores - the venue BECOMES that request
        self.svc.cartridges.state = "pending"
        code = V.main(["--url", self.url, "--ask", "what is my launch ritual?",
                       "--gold", "coffee", "--repeats", "1", "--out", self.out])
        self.assertEqual(code, 0)
        self.assertGreaterEqual(self.restores.count(
            [f for f in os.listdir(self.slots.name) if f.startswith("MEM-CRG-")][0]), 4)  # warm-up + M1 + M3


if __name__ == "__main__":
    unittest.main(verbosity=2)
