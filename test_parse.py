#!/usr/bin/env python3
"""CLI test for the DJI log parser."""
import json
import sys
import time

sys.path.insert(0, ".")
import dji_log_parser as dlp

API_KEY = "5149f682627b9291e483afe66de6fd4"

# sanity: CRC-64 Jones check value from the crc64 crate docs
assert dlp.crc64(0, b"123456789") == 0xE9C6D914C4B8D9CA, hex(dlp.crc64(0, b"123456789"))
print("crc64 check ok")

path = sys.argv[1] if len(sys.argv) > 1 else "FlightRecord_2026-06-27_[16-44-30].txt"
data = open(path, "rb").read()
print(f"file: {path}  ({len(data)} bytes)")

prefix = dlp.parse_prefix(data)
print("prefix:", prefix)

aux = dlp.read_aux_info(data, 100)
print(f"aux info: size={aux['size']} info_data={len(aux['info_data'])} sig={len(aux['signature'])}")

t0 = time.time()
log = dlp.parse_log(data, api_key=API_KEY)
print(f"parsed in {time.time()-t0:.2f}s, records: {len(log.records)}")
print("aux version:", log.aux_version, "department:", log.department)

from collections import Counter
print("record types:", Counter(r["type"] for r in log.records))

# keychain summary
print("keychain chains:", [[(e, len(k[1])) for e, k in ch.items()] for ch in log.keychains][:3])

# show first few OSD frames
frames = [r["content"] for r in log.records if r["type"] == 1 and r["content"]]
print(f"OSD frames: {len(frames)}")
for f in frames[:3]:
    print("  ", {k: f[k] for k in ("lat", "lon", "alt", "battery", "fly_time", "mode", "gps_num")})
if frames:
    print("  last:", {k: frames[-1][k] for k in ("lat", "lon", "alt", "battery", "fly_time", "mode")})

out = dlp.build_frames(log)
d = out["details"]
print("\ndetails:")
for k in ("product", "aircraft_name", "aircraft_sn", "start_time", "total_time",
          "total_distance", "max_height", "max_h_speed", "max_v_speed", "takeoff_altitude",
          "city", "area", "app_version"):
    print(f"  {k}: {d.get(k)}")
print("frames:", len(out["frames"]))
if out["recover"]:
    print("recover:", {k: out["recover"][k] for k in ("product", "aircraft_name", "aircraft_sn", "app_version")})

if "2023-08-22" in path or (out.get("recover") or {}).get("aircraft_name") == "Mavic 3 Classic":
    rec = out["recover"] or {}
    frames = out["frames"]
    assert rec.get("aircraft_name") == "Mavic 3 Classic", rec.get("aircraft_name")
    assert rec.get("product_type") == 110
    assert len(frames) == 6715
    assert all(f.get("gps_valid") and f.get("lat") for f in frames)
    assert 1300 < (d.get("total_time") or 0) < 1400
    volts = [f["voltage"] for f in frames if f.get("voltage")]
    assert volts and min(volts) > 14 and max(volts) < 18
    assert frames[0]["battery"] >= 90 and frames[-1]["battery"] <= 40
    print("mavic 3 classic checks ok:", len(frames), "frames,",
          f"{d.get('total_time'):.0f}s, battery {frames[0]['battery']}%→{frames[-1]['battery']}%")