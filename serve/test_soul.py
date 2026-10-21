"""soul.md wiring (soul plan 06 sec 6): compose laws, slot-0 order, fail-closed boot.

    python -m unittest serve.test_soul -v

The compose laws are pure (no server, no store, no model). The injection rides the real
server over the mock engine (the Cartridge harness shape from serve/test_memory): the
soul must be the FIRST prompt bytes, the journal must never see them, mute must stay the
byte-identical upstream path, and a bad soul file must leave the box pre-soul.
"""
import http.client
import json
import os
import tempfile
import unittest
from pathlib import Path

from serve.frontend import ChatTemplate
from serve.memory import compose_soul, prepend_head, request_state, soul_boot
from serve.server import ByteTokenizer, MockEngine, Service, serve
from serve.test_memory import MemoryService

ROOT = Path(__file__).resolve().parents[1]

SOUL_A = "# soul\n<!-- a load-time instruction -->\n## A. Who I am\nI am Clio, keeper of the archive.\n"
SOUL_B = SOUL_A + "\n## B. The dressed mind\nKnow your dress.\n"


class Compose(unittest.TestCase):
    """the pure laws: budget, comments, the Part B gate, determinism."""

    def test_comments_are_instructions_not_bytes(self):
        b, v, _ = compose_soul(SOUL_A, False)
        self.assertNotIn("<!--", b)
        self.assertNotIn("load-time", b)
        self.assertIn("I am Clio", b)

    def test_gate_off_holds_part_b_back(self):
        b, v, _ = compose_soul(SOUL_B, False)
        self.assertEqual(v, "A")
        self.assertNotIn("dressed mind", b, "a plain-template box never sees the masked section")

    def test_gate_on_composes_both_parts(self):
        b, v, _ = compose_soul(SOUL_B, True)
        self.assertEqual(v, "A+B")
        self.assertIn("Know your dress", b)
        self.assertLess(b.index("Who I am"), b.index("dressed mind"), "A before B, always")

    def test_over_budget_fails_closed(self):
        b, v, why = compose_soul("# x\n" + "y" * 12001, False)
        self.assertEqual(b, "")
        self.assertEqual(v, "off")
        self.assertIn("budget", why)

    def test_all_comment_soul_fails_closed(self):
        b, v, why = compose_soul("<!-- nothing but instructions -->", True)
        self.assertEqual(b, "", "zero soul bytes: the pre-soul path, not an empty system head")

    def test_same_file_same_bytes(self):
        self.assertEqual(compose_soul(SOUL_B, True)[0], compose_soul(SOUL_B, True)[0],
                         "byte-stability by construction: compose is a pure function")


class Order(unittest.TestCase):
    """soul -> cartridge -> recall -> app: the order law IS prepend order - the soul
    prepends LAST so it lands FIRST (the same one-system merge the cartridge uses)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_soul_lands_at_token_zero_over_block_and_cartridge(self):
        msgs = [{"role": "system", "content": "app text"}, {"role": "user", "content": "q"}]
        m = prepend_head(msgs, "RECALL-BLOCK")            # request_state's merge
        m = prepend_head(m, "CARTRIDGE")                  # M3b standing text
        m = prepend_head(m, "SOUL bytes")                 # slot 0: the last prepend
        self.assertEqual(m[0]["content"], "SOUL bytes\n\nCARTRIDGE\n\nRECALL-BLOCK\n\napp text")
        self.assertEqual(len(m), 2, "still ONE system message (the template's one-system law)")

    def test_soul_creates_the_system_when_the_client_sent_none(self):
        m = prepend_head(prepend_head([{"role": "user", "content": "q"}], "RECALL"), "SOUL")
        self.assertEqual(m[0], {"role": "system", "content": "SOUL\n\nRECALL"})

    def test_the_pin_survives_the_soul(self):
        # the pin keys on the first USER message; the soul rides the SYSTEM head. Same
        # conversation with soul bytes ahead of it must replay the SAME block bytes.
        mem = MemoryService(os.path.join(self.tmp.name, "pinsoul"))
        try:
            mem.remember("the flibber gauge reads torque", scope="user")
            msgs = [{"role": "user", "content": "what does the flibber gauge read?"}]
            o1, c1 = request_state(mem, msgs, {}, {})
            self.assertIn("flibber", o1[0]["content"])                 # block computed fresh
            o2, c2 = request_state(mem, prepend_head(msgs, "SOUL bytes here"), {}, {})
            self.assertTrue(o2[0]["content"].endswith("SOUL bytes here"),
                            "request_state merges the block ahead of the client's system text")
            block2 = o2[0]["content"][: -len("\n\nSOUL bytes here")]
            self.assertEqual(block2, o1[0]["content"], "soul bytes never move a block byte")
            self.assertNotIn("SOUL", c2["user"], "the journal input carries no soul bytes")
        finally:
            mem.close()


class Inject(unittest.TestCase):
    """the real server over the mock engine: soul_boot, the first prompt bytes, the
    clean journal, mute, and the fail-closed file."""

    SCRIPT = ["an answer", "an answer 2", "an answer 3", "an answer 4"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.soulfile = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        self.soulfile.write(SOUL_B)
        self.soulfile.close()
        self.tok = ByteTokenizer()
        self.engine = MockEngine(self.tok, list(self.SCRIPT), max_context=131072)
        self.svc = Service(self.engine, self.tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        self.svc.memory = MemoryService(self.tmp.name)
        self.svc.memory.remember("the launch ritual is coffee before code", scope="user")
        self.httpd = serve(self.svc, port=0)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.svc.memory.close()
        os.unlink(self.soulfile.name)
        self.tmp.cleanup()

    def http(self, verb, path, body=None, headers=()):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdrs = {"Content-Type": "application/json"}
        hdrs.update({k: v for k, v in headers})
        conn.request(verb, path, json.dumps(body) if body is not None else None, hdrs)
        r = conn.getresponse()
        data = r.read().decode()
        conn.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def ask(self, text, headers=()):
        return self.http("POST", "/v1/chat/completions",
                         {"model": "m", "max_tokens": 64, "reasoning_effort": "off",
                          "messages": [{"role": "user", "content": text}]}, headers=list(headers))

    def prompt_text(self):
        return bytes(t for t in self.engine.last_prompt if t < 256).decode("utf-8", "replace")

    def test_soul_boot_gates_part_b_on_the_reason_verdict(self):
        cfg = {"memory": {"soul": self.soulfile.name}}
        self.svc.reason_enabled = False
        soul_boot(self.svc, cfg)
        self.assertEqual(self.svc.soul_variant, "A")
        self.assertNotIn("dressed mind", self.svc.soul)
        self.svc.soul = ""                                   # a boot with the switch on
        self.svc.reason_enabled = True
        soul_boot(self.svc, cfg)
        self.assertEqual(self.svc.soul_variant, "A+B")
        self.assertIn("Know your dress", self.svc.soul)

    def test_soul_is_the_first_prompt_bytes_and_the_journal_never_sees_it(self):
        self.svc.reason_enabled = False
        soul_boot(self.svc, {"memory": {"soul": self.soulfile.name}})
        st, body = self.ask("what is my launch ritual?")
        self.assertEqual(st, 200, body)
        p = self.prompt_text()
        self.assertIn("I am Clio", p)
        self.assertLess(p.index("I am Clio"), p.index("launch ritual"),
                        "soul bytes ahead of the recall block bytes: slot 0 holds")
        self.assertNotIn("dressed mind", p, "Part B stayed home (gate off)")
        row = self.svc.memory.conn.execute(
            "select user_text, assistant_text from turns order by id desc limit 1").fetchone()
        self.assertEqual(row[0], "what is my launch ritual?",
                         "the journal stores the exchange, not the system head")

    def test_mute_is_still_the_byte_identical_upstream_path(self):
        self.svc.reason_enabled = False
        soul_boot(self.svc, {"memory": {"soul": self.soulfile.name}})
        st, body = self.ask("what is my launch ritual?", headers=[("X-Strata-Memory", "off")])
        self.assertEqual(st, 200, body)
        self.assertNotIn("I am Clio", self.prompt_text(),
                         "muted means muted: no memory bytes AND no soul bytes")

    def test_over_budget_soul_leaves_the_box_pre_soul(self):
        big = self.soulfile.name + ".big"
        with open(big, "w", encoding="utf-8") as f:
            f.write("z" * 12001)
        try:
            self.svc.reason_enabled = False
            soul_boot(self.svc, {"memory": {"soul": big}})
            self.assertEqual(self.svc.soul, "")
            self.assertEqual(self.svc.soul_variant, "off")
            st, body = self.ask("what is my launch ritual?")
            self.assertEqual(st, 200, body)
            p = self.prompt_text()
            self.assertNotIn("zzzz", p, "the fail-closed path serves today's bytes")
            self.assertIn("launch ritual", p, "and recall keeps working untouched underneath")
        finally:
            os.unlink(big)


if __name__ == "__main__":
    unittest.main(verbosity=2)
