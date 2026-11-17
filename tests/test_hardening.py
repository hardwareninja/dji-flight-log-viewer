"""Defensive checks for untrusted log text and log-file paths.

Log strings are kept intact by the parser (so a real name is not damaged) and
must be escaped by the UI before they reach HTML or a spreadsheet. Paths from
an upload or an open request must stay inside the app directory.
"""
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as viewer
import dji_log_parser as dlp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Metacharacters that show up in aircraft names, serials, places, and errors.
EVIL_NAME = '<img src=x onerror=alert(1)>'
EVIL_SN = 'SN,"<>&\'='
EVIL_CITY = '=1+1,"<b>\n'
EVIL_STREET = "it's <b> café"
EVIL_AREA = "a,b\"c<d>&"


def _z(text: str, n: int) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > n:
        raise AssertionError(f"{text!r} is {len(raw)} bytes, field is {n}")
    return raw + b"\x00" * (n - len(raw))


def make_details(log_version=14, **kw) -> bytes:
    name_n = 24 if log_version <= 5 else 32
    sn_n = 10 if log_version <= 5 else 16
    parts = [
        _z(kw.get("sub_street", ""), 20),
        _z(kw.get("street", ""), 20),
        _z(kw.get("city", ""), 20),
        _z(kw.get("area", ""), 20),
        b"\x00" * 3,
        struct.pack("<i", 0),
        b"\x00" * 4,
        struct.pack("<q", 0),
        struct.pack("<d", 0.0),
        struct.pack("<d", 0.0),
        struct.pack("<f", 0.0),
        struct.pack("<i", 0),
        struct.pack("<f", 0.0),
        struct.pack("<f", 0.0),
        struct.pack("<f", 0.0),
        struct.pack("<i", 0),
        struct.pack("<q", 0),
        b"\x00" * (32 * 4),
        b"\x00" * 8,
        b"\x00" * 16,
        struct.pack("<f", 0.0),
        struct.pack("<B", kw.get("product_type", 61)),
        b"\x00" * 8,
        _z(kw.get("aircraft_name", ""), name_n),
        _z(kw.get("aircraft_sn", ""), sn_n),
        _z(kw.get("camera_sn", ""), sn_n),
        _z(kw.get("rc_sn", ""), sn_n),
        b"\x00" * sn_n,
        struct.pack("<B", 1),
        bytes(kw.get("app_version", (1, 2, 3))),
    ]
    return b"".join(parts)


def make_recover(log_version=14, **kw) -> bytes:
    n = 10 if log_version <= 7 else 16
    parts = [
        struct.pack("<B", kw.get("product_type", 61)),
        struct.pack("<B", 2),
        bytes(kw.get("app_version", (4, 5, 6))),
        _z(kw.get("aircraft_sn", ""), n),
        _z(kw.get("aircraft_name", ""), 32),
        struct.pack("<q", 1_700_000_000_000),
        _z(kw.get("camera_sn", ""), n),
        _z(kw.get("rc_sn", ""), n),
        b"\x00" * n,
    ]
    return b"".join(parts)


class TestParserStrings(unittest.TestCase):
    def test_details_keep_metacharacters(self):
        parsed = dlp.parse_details(make_details(
            aircraft_name=EVIL_NAME,
            aircraft_sn=EVIL_SN,
            camera_sn="cam<id>",
            rc_sn="rc&id",
            city=EVIL_CITY,
            street=EVIL_STREET,
            area=EVIL_AREA,
        ), 14)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["aircraft_name"], EVIL_NAME)
        self.assertEqual(parsed["aircraft_sn"], EVIL_SN)
        self.assertEqual(parsed["camera_sn"], "cam<id>")
        self.assertEqual(parsed["rc_sn"], "rc&id")
        self.assertEqual(parsed["city"], EVIL_CITY)
        self.assertEqual(parsed["street"], EVIL_STREET)
        self.assertEqual(parsed["area"], EVIL_AREA)
        self.assertEqual(parsed["app_version"], "1.2.3")

    def test_recover_keeps_metacharacters_and_known_alias(self):
        parsed = dlp.parse_recover(make_recover(
            aircraft_name=EVIL_NAME,
            aircraft_sn=EVIL_SN,
            camera_sn='=cmd|" /C x',
            rc_sn="rc'<>",
        ), 14)
        self.assertEqual(parsed["aircraft_name"], EVIL_NAME)
        self.assertEqual(parsed["aircraft_sn"], EVIL_SN)
        self.assertEqual(parsed["camera_sn"], '=cmd|" /C x')
        self.assertEqual(parsed["rc_sn"], "rc'<>")
        self.assertEqual(parsed["app_version"], "4.5.6")

        alias = dlp.parse_recover(make_recover(aircraft_name="Magic 3 Classic"), 14)
        self.assertEqual(alias["aircraft_name"], "Mavic 3 Classic")

    def test_truncated_record_does_not_abort(self):
        self.assertIsNone(dlp.parse_details(b"short", 14))
        self.assertIsNone(dlp.parse_recover(b"\x01\x02", 14))
        self.assertIsNone(dlp.parse_osd(b"\x00\x01", 14))
        self.assertIsNone(dlp.parse_custom(b"\x00\x01"))


class TestPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dji-viewer-")
        self.outside = tempfile.mkdtemp(prefix="dji-outside-")
        self.old = (
            viewer.BASE_DIR,
            viewer.CACHE_DIR,
            viewer.API_KEY,
            viewer.app.config["MAX_CONTENT_LENGTH"],
        )
        viewer.BASE_DIR = self.tmp
        viewer.CACHE_DIR = os.path.join(self.tmp, ".cache")
        viewer.API_KEY = ""
        self.client = viewer.app.test_client()

    def tearDown(self):
        viewer.BASE_DIR, viewer.CACHE_DIR, viewer.API_KEY, viewer.app.config["MAX_CONTENT_LENGTH"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    def log_bytes(self, extra=b""):
        return b"\x00" * 10 + bytes([14]) + extra

    def upload(self, name, data, headers=None, **fields):
        payload = {"file": (io.BytesIO(data), name)}
        payload.update(fields)
        return self.client.post(
            "/api/upload",
            data=payload,
            headers=headers,
            content_type="multipart/form-data",
        )

    def write_log(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        os.utime(path, (1_700_000_000, 1_700_000_000))
        return path

    def cache_body(self, name, data, **extra):
        path = os.path.join(self.tmp, name)
        st = os.stat(path)
        self.assertEqual(json.loads(json.dumps(st.st_mtime)), st.st_mtime)
        body = {
            "mtime": st.st_mtime,
            "size": st.st_size,
            "keychains": extra.get("keychains", []),
            "data": {
                "name": extra.get("stored_name", name),
                "version": 14,
                "details": extra.get("details", {}),
                "recover": extra.get("recover", {}),
                "frames": extra.get("frames", []),
            },
        }
        if "sha256" in extra:
            body["sha256"] = extra["sha256"]
        return body

    def assert_no_temps(self):
        if not os.path.isdir(viewer.CACHE_DIR):
            return
        leftover = [n for n in os.listdir(viewer.CACHE_DIR)
                    if n.startswith(("backup-", "upload-", "cache-"))]
        self.assertEqual(leftover, [])

    def test_safe_names_match_real_dji_files(self):
        self.assertTrue(viewer._is_safe_log_name(
            "DJIFlightRecord_2024-11-17_[14-30-00].txt"))
        self.assertTrue(viewer._is_safe_log_name("My Flight.txt"))
        for bad in (
            "", "../x.txt", "..\\x.txt", "/tmp/x.txt", "C:/Windows/win.ini",
            "foo/bar.txt", "foo\\bar.txt", "requirements.txt", "Requirements.TXT",
            "CON.txt", "PRN.txt", ".hidden.txt", "a\x00b.txt", "foo..bar.txt",
            "trail.txt ", "trail.txt.", "a\nb.txt", "ads.txt:stream",
        ):
            self.assertFalse(viewer._is_safe_log_name(bad), bad)

    def test_cache_names_cannot_leave_the_cache_or_collide(self):
        spaced = viewer._cache_path("a b.txt")
        underscored = viewer._cache_path("a_b.txt")
        escaped = viewer._cache_path("../etc/passwd")
        root = os.path.realpath(viewer.CACHE_DIR)
        self.assertNotEqual(spaced, underscored)
        for path in (spaced, underscored, escaped):
            self.assertEqual(os.path.dirname(path), root)
        self.assertEqual(
            viewer._legacy_cache_filename("a b.txt"),
            viewer._legacy_cache_filename("a_b.txt"),
        )

    def test_open_rejects_traversal_and_does_not_read_outside(self):
        secret = os.path.join(self.outside, "secret.txt")
        with open(secret, "w", encoding="utf-8") as f:
            f.write("TOP-SECRET-FLIGHT")
        os.symlink(secret, os.path.join(self.tmp, "link.txt"))
        for name in ("../secret.txt", "..\\secret.txt", "/tmp/x.txt",
                     "C:\\Windows\\win.ini", "requirements.txt", "link.txt"):
            rv = self.client.get("/api/log", query_string={"name": name})
            self.assertEqual(rv.status_code, 400, name)
            self.assertNotIn(b"TOP-SECRET-FLIGHT", rv.data)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tmp), "secret.txt")))

    def test_short_and_unrelated_txt_does_not_break_the_list(self):
        with open(os.path.join(self.tmp, "notes.txt"), "wb") as f:
            f.write(b"hi")
        with open(os.path.join(self.tmp, "requirements.txt"), "wb") as f:
            f.write(self.log_bytes(b"not-a-project-file"))
        rv = self.client.get("/api/logs")
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_json(), [])

    def test_upload_rejects_paths_and_project_files(self):
        keep = os.path.join(self.tmp, "requirements.txt")
        with open(keep, "w", encoding="utf-8") as f:
            f.write("KEEP")
        outside = os.path.join(self.outside, "escaped.txt")
        names = [
            "../escaped.txt", "..\\escaped.txt", "/tmp/escaped.txt",
            "foo/bar.txt", "requirements.txt", "Requirements.TXT",
            "CON.txt", ".hidden.txt", "foo..bar.txt",
        ]
        for name in names:
            rv = self.upload(name, self.log_bytes(b"nope"))
            self.assertEqual(rv.status_code, 400, name)
        with open(keep, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "KEEP")
        self.assertFalse(os.path.exists(outside))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Requirements.TXT")))

    def test_upload_does_not_follow_a_symlink(self):
        secret = os.path.join(self.outside, "secret.txt")
        with open(secret, "w", encoding="utf-8") as f:
            f.write("TOP-SECRET-FLIGHT")
        os.symlink(secret, os.path.join(self.tmp, "link.txt"))
        rv = self.upload("link.txt", self.log_bytes(b"overwrite"))
        self.assertEqual(rv.status_code, 400)
        with open(secret, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "TOP-SECRET-FLIGHT")
        self.assertTrue(os.path.islink(os.path.join(self.tmp, "link.txt")))

    def test_upload_refuses_to_replace_a_non_log(self):
        notes = os.path.join(self.tmp, "notes.txt")
        with open(notes, "w", encoding="utf-8") as f:
            f.write("USER NOTES")
        rv = self.upload("notes.txt", self.log_bytes(b"NEW"))
        self.assertEqual(rv.status_code, 400)
        self.assertIn(b"not a flight log", rv.data)
        with open(notes, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "USER NOTES")

    def test_failed_upload_restores_an_existing_log_and_removes_a_new_one(self):
        existing = self.write_log("kept.txt", self.log_bytes(b"ORIGINAL-UNIQUE"))
        rv = self.upload("kept.txt", self.log_bytes(b"REPLACEMENT"))
        self.assertEqual(rv.status_code, 400)
        with open(existing, "rb") as fh:
            self.assertEqual(fh.read(), self.log_bytes(b"ORIGINAL-UNIQUE"))

        rv = self.upload("brand-new.txt", self.log_bytes(b"WILL-FAIL"))
        self.assertEqual(rv.status_code, 400)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "brand-new.txt")))
        self.assert_no_temps()

    def test_missing_key_keeps_the_uploaded_log(self):
        def boom(data, api_key=None, cached_keychains=None):
            raise RuntimeError(
                "Log version >= 13 requires a DJI developer API key "
                "(or a cached keychain).")

        old = viewer.dlp.parse_log
        viewer.dlp.parse_log = boom
        try:
            data = self.log_bytes(b"ENCRYPTED")
            rv = self.upload("needs-key.txt", data)
        finally:
            viewer.dlp.parse_log = old
        self.assertEqual(rv.status_code, 400)
        self.assertTrue(rv.get_json()["need_key"])
        with open(os.path.join(self.tmp, "needs-key.txt"), "rb") as fh:
            self.assertEqual(fh.read(), data)
        self.assert_no_temps()

    def test_real_dji_filename_is_accepted(self):
        seen = {}

        def ok(data, api_key=None, cached_keychains=None):
            seen["key"] = api_key
            log = dlp.ParsedLog(version=14, details={"aircraft_name": EVIL_NAME},
                                aux_version=14, department=3)
            log.records = []
            return log

        old = viewer.dlp.parse_log
        viewer.dlp.parse_log = ok
        try:
            name = "DJIFlightRecord_2024-11-17_[14-30-00].txt"
            rv = self.upload(name, self.log_bytes(b"ok"), api_key="form-secret")
        finally:
            viewer.dlp.parse_log = old
        self.assertEqual(rv.status_code, 200, rv.data)
        self.assertEqual(rv.get_json()["name"], name)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, name)))
        self.assertEqual(seen["key"], "form-secret")
        self.assert_no_temps()

    def test_api_key_is_not_taken_from_the_query_string(self):
        seen = {}

        def capture(name, api_key=""):
            seen["key"] = api_key
            return {"name": name, "version": 14, "details": {}, "recover": {}, "frames": []}

        old = viewer.parse_cached
        viewer.parse_cached = capture
        try:
            rv = self.client.get(
                "/api/log",
                query_string={"name": "ok.txt", "api_key": "QUERY-SECRET"},
                headers={"X-DJI-Key": "header-secret"},
            )
        finally:
            viewer.parse_cached = old
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(seen["key"], "header-secret")

    def test_cached_log_returns_raw_strings_and_ignores_another_logs_cache(self):
        self.write_log("evil.txt", self.log_bytes(b"body"))
        cache = self.cache_body(
            "evil.txt",
            None,
            details={"aircraft_name": EVIL_NAME, "city": EVIL_CITY, "aircraft_sn": EVIL_SN},
            recover={"aircraft_name": EVIL_NAME, "aircraft_sn": EVIL_SN},
            frames=[{"mode": "<script>", "fly_time": 1, "lat": 1, "lon": 2}],
        )
        with open(viewer._cache_path("evil.txt"), "w", encoding="utf-8") as f:
            json.dump(cache, f)
        rv = self.client.get("/api/log", query_string={"name": "evil.txt"})
        self.assertEqual(rv.status_code, 200, rv.data)
        body = rv.get_json()
        self.assertEqual(body["details"]["aircraft_name"], EVIL_NAME)
        self.assertEqual(body["details"]["city"], EVIL_CITY)
        self.assertEqual(body["details"]["aircraft_sn"], EVIL_SN)
        self.assertEqual(body["frames"][0]["mode"], "<script>")
        self.assertNotIn("&lt;", rv.get_data(as_text=True))

        # Same sanitized legacy file name, but it belongs to the other log.
        self.write_log("a b.txt", self.log_bytes(b"space"))
        legacy_name = viewer._legacy_cache_filename("a b.txt")
        stolen = self.cache_body(
            "a b.txt", None, stored_name="a_b.txt",
            details={"aircraft_name": "STOLEN"},
            keychains=[{"stolen": True}],
        )
        with open(viewer._cache_file(legacy_name), "w", encoding="utf-8") as f:
            json.dump(stolen, f)
        seen = {}

        def parsed(data, api_key=None, cached_keychains=None):
            seen["chains"] = cached_keychains
            log = dlp.ParsedLog(version=14, details={"aircraft_name": "parsed"},
                                aux_version=14, department=3)
            log.records = []
            return log

        old = viewer.dlp.parse_log
        viewer.dlp.parse_log = parsed
        try:
            got = viewer.parse_cached("a b.txt")
        finally:
            viewer.dlp.parse_log = old
        self.assertEqual(got["details"]["aircraft_name"], "parsed")
        self.assertIsNone(seen["chains"])

    def test_legacy_cache_still_opens_offline_and_a_bad_hash_does_not(self):
        self.write_log("plain.txt", self.log_bytes(b"plain"))
        legacy = self.cache_body(
            "plain.txt", None,
            details={"aircraft_name": "Cached Craft", "city": EVIL_CITY},
        )
        with open(viewer._cache_file(viewer._legacy_cache_filename("plain.txt")),
                  "w", encoding="utf-8") as f:
            json.dump(legacy, f)

        def fail(data, api_key=None, cached_keychains=None):
            raise AssertionError("cache hit should not parse")

        old = viewer.dlp.parse_log
        viewer.dlp.parse_log = fail
        try:
            got = viewer.parse_cached("plain.txt")
        finally:
            viewer.dlp.parse_log = old
        self.assertEqual(got["details"]["aircraft_name"], "Cached Craft")
        self.assertEqual(got["details"]["city"], EVIL_CITY)

        self.write_log("hashed.txt", self.log_bytes(b"hashed-body"))
        stale = self.cache_body(
            "hashed.txt", None,
            details={"aircraft_name": "STALE"},
            sha256="0" * 64,
            keychains=[{"from": "stale"}],
        )
        with open(viewer._cache_path("hashed.txt"), "w", encoding="utf-8") as f:
            json.dump(stale, f)
        seen = {}

        def fresh(data, api_key=None, cached_keychains=None):
            seen["chains"] = cached_keychains
            log = dlp.ParsedLog(version=14, details={"aircraft_name": "FRESH"},
                                aux_version=14, department=3)
            log.records = []
            return log

        viewer.dlp.parse_log = fresh
        try:
            got = viewer.parse_cached("hashed.txt")
        finally:
            viewer.dlp.parse_log = old
        self.assertEqual(got["details"]["aircraft_name"], "FRESH")
        self.assertEqual(seen["chains"], [{"from": "stale"}])

    def test_upload_too_large_is_json(self):
        viewer.app.config["MAX_CONTENT_LENGTH"] = 32
        rv = self.upload("big.txt", b"x" * 80)
        self.assertEqual(rv.status_code, 413)
        self.assertEqual(rv.get_json()["error"], "file is too large")


class TestUiWiring(unittest.TestCase):
    def test_page_loads_the_escaper_before_the_app(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertLess(html.index("/static/sanitize.js"), html.index("/static/app.js"))
        row = js[js.index("const row ="):js.index("const row =") + 160]
        self.assertIn("esc(k)", row)
        self.assertIn("esc(v)", row)
        self.assertIn('esc(v ?? "")', js)
        self.assertIn("csvField", js)
        self.assertNotIn("const esc", js)


if __name__ == "__main__":
    unittest.main()
